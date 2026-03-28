import asyncio
import collections
import json
import math
import time
import numpy as np

import websockets
from binance.um_futures import UMFutures
from binance.error import ClientError

from core.logger import logger
import core.config as cfg

class MeanReversionBot:
    def __init__(self):
        self.symbol = cfg.SYMBOL
        logger.info(f"Initializing MeanReversionBot for {self.symbol} on {'TESTNET' if cfg.USE_TESTNET else 'MAINNET'}")
        
        self.rest_client = UMFutures(
            key=cfg.API_KEY, 
            secret=cfg.API_SECRET, 
            base_url=cfg.REST_URL
        )
        
        # Prices
        self.close_prices = collections.deque(maxlen=10)
        self.current_price = 0.0
        
        # State
        self.state = "NO_POS"  # NO_POS, WAITING_ENTRY, WAITING_EXIT
        self.entry_orders = [] # Unfilled entry limit orders
        self.exit_order_id = None
        
        self.order_placed_time = 0.0
        self.position_amt = 0.0
        self.avg_cost = 0.0
        
        # Config 
        self.tranche_size = 93.0  # About 1/3 of total 2000 RBM (~280 USDT)
        self.timeout_sec = 5 * 60 # 5 minutes
        self.fee_rate = 0.001     # estimated taker fee rate 0.05% + slip = 0.1% safe margin
        self.stop_loss_pct = 0.015 # 1.5% Hard Stop
        
        self.fetch_initial_data()
        self.sync_account_info()
        self.fetch_exchange_info()
        
    def fetch_exchange_info(self):
        try:
            info = self.rest_client.exchange_info()
            for s in info['symbols']:
                if s['symbol'] == self.symbol:
                    self.price_precision = s['pricePrecision']
                    self.qty_precision = s['quantityPrecision']
                    logger.info(f"Loaded precision for {self.symbol}: Price({self.price_precision}), Qty({self.qty_precision})")
                    break
        except Exception as e:
            logger.error(f"Error fetching exchange info: {e}")
            self.price_precision = 1
            self.qty_precision = 3

    def fetch_initial_data(self):
        try:
            klines = self.rest_client.klines(self.symbol, "1m", limit=10)
            for k in klines:
                close = float(k[4])
                self.close_prices.append(close)
            logger.info(f"Loaded {len(self.close_prices)} historical 1m klines.")
        except ClientError as e:
            logger.error(f"Failed to fetch initial klines: {e}")
            
    def sync_account_info(self):
        try:
            self.rest_client.cancel_open_orders(symbol=self.symbol)
            positions = self.rest_client.account(symbol=self.symbol)
            # Find the position
            for pos in positions.get("positions", []):
                if pos['symbol'] == self.symbol:
                    self.position_amt = float(pos['positionAmt'])
                    self.avg_cost = float(pos['entryPrice'])
                    break
            
            if self.position_amt > 0:
                self.state = "WAITING_EXIT"
                logger.info(f"Recovered existing position: {self.position_amt} @ {self.avg_cost}")
            else:
                self.state = "NO_POS"
                self.position_amt = 0.0
                self.avg_cost = 0.0
        except Exception as e:
            logger.error(f"Error syncing account info: {e}")

    def on_tick(self):
        if len(self.close_prices) < 10:
            return
            
        prices = list(self.close_prices)
        p_mean = np.mean(prices)
        sigma = np.std(prices)
        sigma_min = self.current_price * 0.0005
        sigma = max(sigma, sigma_min)

        logger.debug(f"P_mean: {p_mean:.4f}, Sigma: {sigma:.4f}, Price: {self.current_price}")

        # --- Hard Stop Logic ---
        if self.state == "WAITING_EXIT" and self.position_amt > 0:
            stop_price = self.avg_cost * (1 - self.stop_loss_pct)
            if self.current_price <= stop_price:
                logger.error(f"🚨 HARD STOP TRIGGERED! Price {self.current_price} <= Stop {stop_price:.4f}. Market Selling!")
                self._market_sell_all()
                self.state = "NO_POS"
                return

        # --- Timeout Logic ---
        now = time.time()
        if self.state in ["WAITING_ENTRY", "WAITING_EXIT"]:
            if now - self.order_placed_time > self.timeout_sec:
                logger.info("Order timed out (5 mins). Canceling all pending orders...")
                try:
                    self.rest_client.cancel_open_orders(symbol=self.symbol)
                except Exception as e:
                    logger.error(f"Error canceling orders: {e}")
                self.entry_orders = []
                self.exit_order_id = None
                
                # Check status
                self._update_position_state()

        # --- State Machine ---
        if self.state == "NO_POS":
            # Place entry limits
            b1 = p_mean - 1 * sigma
            b2 = p_mean - 3 * sigma
            
            qty_b1 = round(self.tranche_size / b1, self.qty_precision)
            qty_b2 = round(self.tranche_size / b2, self.qty_precision)
            
            try:
                logger.info(f"Placing ENTRY orders... B1: {b1:.{self.price_precision}f} (qty:{qty_b1}), B2: {b2:.{self.price_precision}f} (qty:{qty_b2})")
                r1 = self.rest_client.new_order(symbol=self.symbol, side="BUY", type="LIMIT", timeInForce="GTC", quantity=qty_b1, price=round(b1, self.price_precision))
                r2 = self.rest_client.new_order(symbol=self.symbol, side="BUY", type="LIMIT", timeInForce="GTC", quantity=qty_b2, price=round(b2, self.price_precision))
                self.entry_orders = [r1['orderId'], r2['orderId']]
                self.order_placed_time = time.time()
                self.state = "WAITING_ENTRY"
            except Exception as e:
                logger.error(f"Error placing entry orders: {e}")

        elif self.state == "WAITING_ENTRY":
            # Check if partially or fully filled via WS feed or aggressive sync timeout
            self._update_position_state()
            if self.position_amt > 0:
                logger.info("Order filled! Cancelling remaining entry orders...")
                try:
                    self.rest_client.cancel_open_orders(symbol=self.symbol)
                except:
                    pass
                self.entry_orders = []
                self.state = "WAITING_EXIT"
                
                # Immediately move to EXIt logic
                self.on_tick()

        elif self.state == "WAITING_EXIT":
            if self.exit_order_id is None:
                # Place limit exit
                raw_exit = p_mean + 1 * sigma
                
                # Patch 1: Breakeven check (Cost + Fee)
                breakeven_price = self.avg_cost * (1 + self.fee_rate * 2) # roundtrip fee
                exit_price = max(raw_exit, breakeven_price)
                
                logger.info(f"Placing EXIT order... AvgCost: {self.avg_cost:.{self.price_precision}f}, Target: {raw_exit:.{self.price_precision}f}, Adjusted (Breakeven): {exit_price:.{self.price_precision}f}")
                try:
                    r = self.rest_client.new_order(symbol=self.symbol, side="SELL", type="LIMIT", timeInForce="GTC", quantity=round(self.position_amt, self.qty_precision), price=round(exit_price, self.price_precision))
                    self.exit_order_id = r['orderId']
                    self.order_placed_time = time.time()
                except Exception as e:
                    logger.error(f"Error placing exit order: {e}")
                    
            else:
                # Need to check if exit filled
                self._update_position_state()
                if self.position_amt == 0:
                    logger.info("Exit order fully filled! Position closed.")
                    self.state = "NO_POS"
                    self.exit_order_id = None
                    try:
                        self.rest_client.cancel_open_orders(symbol=self.symbol)
                    except:
                        pass

    def _update_position_state(self):
        try:
            positions = self.rest_client.account(symbol=self.symbol)
            for pos in positions.get("positions", []):
                if pos['symbol'] == self.symbol:
                    self.position_amt = float(pos['positionAmt'])
                    self.avg_cost = float(pos['entryPrice'])
                    break
        except Exception as e:
            pass

    def _market_sell_all(self):
        try:
            self.rest_client.cancel_open_orders(symbol=self.symbol)
            if self.position_amt > 0:
                self.rest_client.new_order(symbol=self.symbol, side="SELL", type="MARKET", quantity=self.position_amt)
            self.position_amt = 0
            self.avg_cost = 0
        except Exception as e:
            logger.error(f"Failed to market sell: {e}")

    async def run(self):
        ws_url = f"{cfg.WS_URL}/ws/{self.symbol.lower()}@kline_1m"
        logger.info(f"Connecting to WS: {ws_url}")
        
        while True:
            try:
                async with websockets.connect(ws_url) as ws:
                    logger.info("WS Connected Successfully!")
                    while True:
                        msg = await ws.recv()
                        data = json.loads(msg)
                        kline = data.get('k')
                        if kline:
                            self.current_price = float(kline['c'])
                            
                            # Update queue if kline is closed
                            if kline['x']:  # is_closed
                                self.close_prices.append(self.current_price)
                                logger.debug(f"1m Kline closed. {len(self.close_prices)} items. Latest closed: {self.current_price}")
                        
                        # Trigger evaluation
                        self.on_tick()
                        
            except Exception as e:
                logger.error(f"WS error: {e}, reconnecting in 5s...")
                await asyncio.sleep(5)
                
async def main():
    bot = MeanReversionBot()
    await bot.run()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Shuting down MeanReversionBot...")
