import asyncio
import collections
import json
import math
import time
from decimal import Decimal, ROUND_DOWN, ROUND_UP
import numpy as np

import websockets
from binance.um_futures import UMFutures
from binance.error import ClientError

from core.logger import logger
import core.config_mean as cfg

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
        self.close_prices = collections.deque(maxlen=cfg.KLINE_WINDOW)
        self.current_price = 0.0
        
        # State
        self.state = "NO_POS"  # NO_POS, WAITING_ENTRY, WAITING_EXIT, BLOCKED
        self.entry_orders = [] # Unfilled entry limit orders
        self.exit_order_id = None
        self.blocked_reason = ""
        self.opposite_position_handling = "AUTO_FLATTEN" if cfg.AUTO_FLATTEN_OPPOSITE_POSITION else "BLOCK"
        
        self.order_placed_time = 0.0
        self.last_retry_time = 0.0
        self.last_flatten_attempt_time = 0.0
        self.position_amt = 0.0
        self.avg_cost = 0.0
        
        # Config 
        self.kline_window = cfg.KLINE_WINDOW
        self.tranche_size = cfg.TRANCHE_SIZE
        self.entry_std_multiplier_1 = cfg.ENTRY_STD_MULTIPLIER_1
        self.entry_std_multiplier_2 = cfg.ENTRY_STD_MULTIPLIER_2
        self.exit_std_multiplier = cfg.EXIT_STD_MULTIPLIER
        self.sigma_floor_pct = cfg.SIGMA_FLOOR_PCT
        self.timeout_sec = cfg.ORDER_TIMEOUT_SEC
        self.retry_cooldown_sec = cfg.RETRY_COOLDOWN_SEC
        self.fee_rate = cfg.ESTIMATED_FEE_RATE
        self.stop_loss_pct = cfg.STOP_LOSS_PCT
        
        self.fetch_initial_data()
        self.fetch_exchange_info()
        self.sync_account_info()
        
    def fetch_exchange_info(self):
        try:
            info = self.rest_client.exchange_info()
            for s in info['symbols']:
                if s['symbol'] == self.symbol:
                    self.price_precision = s['pricePrecision']
                    self.qty_precision = s['quantityPrecision']
                    self.tick_size = Decimal("0.1")
                    self.step_size = Decimal("0.001")
                    self.min_qty = Decimal("0.001")
                    self.min_notional = Decimal("100")

                    for f in s.get('filters', []):
                        if f['filterType'] == 'PRICE_FILTER':
                            self.tick_size = Decimal(f['tickSize'])
                        elif f['filterType'] == 'LOT_SIZE':
                            self.step_size = Decimal(f['stepSize'])
                            self.min_qty = Decimal(f['minQty'])
                        elif f['filterType'] == 'MIN_NOTIONAL':
                            self.min_notional = Decimal(f['notional'])

                    logger.info(f"Loaded precision for {self.symbol}: Price({self.price_precision}), Qty({self.qty_precision})")
                    break
        except Exception as e:
            logger.error(f"Error fetching exchange info: {e}")
            self.price_precision = 1
            self.qty_precision = 3
            self.tick_size = Decimal("0.1")
            self.step_size = Decimal("0.001")
            self.min_qty = Decimal("0.001")
            self.min_notional = Decimal("100")

    def _round_to_step(self, value: float, step: Decimal, rounding) -> float:
        value_dec = Decimal(str(value))
        steps = (value_dec / step).quantize(Decimal("1"), rounding=rounding)
        normalized = steps * step
        return float(normalized)

    def _normalize_price(self, price: float) -> float:
        return self._round_to_step(price, self.tick_size, ROUND_DOWN)

    def _normalize_qty_down(self, qty: float) -> float:
        qty = self._round_to_step(qty, self.step_size, ROUND_DOWN)
        return max(qty, float(self.min_qty))

    def _normalize_qty_up(self, qty: float) -> float:
        qty = self._round_to_step(qty, self.step_size, ROUND_UP)
        return max(qty, float(self.min_qty))

    def _build_order_qty(self, price: float) -> float:
        target_qty = max(self.tranche_size / price, float(self.min_notional / Decimal(str(price))))
        qty = self._normalize_qty_up(target_qty)
        return qty

    def _format_price(self, price: float) -> str:
        return f"{price:.{self.price_precision}f}"

    def _has_retry_cooldown(self) -> bool:
        return (time.time() - self.last_retry_time) < self.retry_cooldown_sec

    def _mark_retry(self):
        self.last_retry_time = time.time()

    def _can_attempt_flatten(self) -> bool:
        return (time.time() - self.last_flatten_attempt_time) >= self.retry_cooldown_sec

    def _mark_flatten_attempt(self):
        self.last_flatten_attempt_time = time.time()

    def fetch_initial_data(self):
        try:
            klines = self.rest_client.klines(self.symbol, "1m", limit=self.kline_window)
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
            self.position_amt, self.avg_cost = self._extract_position_info(positions)
            
            if self.position_amt > 0:
                self.state = "WAITING_EXIT"
                logger.info(f"Recovered existing position: {self.position_amt} @ {self.avg_cost}")
            elif self.position_amt < 0:
                self._handle_opposite_position("startup")
            else:
                self.state = "NO_POS"
                self.position_amt = 0.0
                self.avg_cost = 0.0
                self.blocked_reason = ""
        except Exception as e:
            logger.error(f"Error syncing account info: {e}")

    def _extract_position_info(self, account_data):
        position_amt = 0.0
        avg_cost = 0.0

        for pos in account_data.get("positions", []):
            if pos.get('symbol') != self.symbol:
                continue

            position_amt = float(pos.get('positionAmt', 0.0))
            if position_amt == 0:
                return 0.0, 0.0

            raw_entry = pos.get('entryPrice') or pos.get('avgPrice')
            if raw_entry not in (None, "", "0", "0.0"):
                avg_cost = float(raw_entry)
            else:
                notional = float(pos.get('notional', 0.0))
                if position_amt != 0 and notional != 0:
                    avg_cost = abs(notional / position_amt)
                else:
                    avg_cost = 0.0

            return position_amt, avg_cost

        return 0.0, 0.0

    def _set_blocked_for_short(self):
        self.state = "BLOCKED"
        self.blocked_reason = f"Detected existing short position {self.position_amt} @ {self.avg_cost}. Mean strategy is long-only."
        logger.error(self.blocked_reason)

    def _handle_opposite_position(self, source: str):
        if self.position_amt >= 0:
            return

        if not cfg.AUTO_FLATTEN_OPPOSITE_POSITION:
            self._set_blocked_for_short()
            return

        if not self._can_attempt_flatten():
            self.state = "BLOCKED"
            return

        short_qty = self._normalize_qty_up(abs(self.position_amt))
        self._mark_flatten_attempt()
        logger.warning(f"Detected opposite short position during {source}. Sending reduce-only MARKET BUY to flatten {short_qty} {self.symbol}.")

        try:
            self.rest_client.cancel_open_orders(symbol=self.symbol)
        except Exception as e:
            logger.error(f"Error canceling orders before flattening short: {e}")

        try:
            self.rest_client.new_order(
                symbol=self.symbol,
                side="BUY",
                type="MARKET",
                quantity=short_qty,
                reduceOnly="true",
            )
            time.sleep(0.5)
            account_data = self.rest_client.account(symbol=self.symbol)
            self.position_amt, self.avg_cost = self._extract_position_info(account_data)

            if self.position_amt < 0:
                self._set_blocked_for_short()
                return

            if self.position_amt > 0:
                self.state = "WAITING_EXIT"
                self.blocked_reason = ""
                logger.info(f"Flatten completed with residual long position: {self.position_amt} @ {self.avg_cost}")
            else:
                self.state = "NO_POS"
                self.avg_cost = 0.0
                self.blocked_reason = ""
                self._mark_retry()
                logger.info("Opposite short position flattened. Strategy resumed from flat state.")
        except Exception as e:
            self._set_blocked_for_short()
            logger.error(f"Failed to flatten opposite short position: {e}")

    def on_tick(self):
        if len(self.close_prices) < self.kline_window:
            return
        if self.current_price <= 0:
            return
        if self.state == "BLOCKED" and self.position_amt < 0 and cfg.AUTO_FLATTEN_OPPOSITE_POSITION:
            self._handle_opposite_position("runtime")
            if self.state == "BLOCKED":
                return
        elif self.state == "BLOCKED":
            return
            
        prices = list(self.close_prices)
        p_mean = np.mean(prices)
        sigma = np.std(prices)
        sigma_min = self.current_price * self.sigma_floor_pct
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
                if self.position_amt > 0:
                    self.state = "WAITING_EXIT"
                else:
                    self.state = "NO_POS"
                    self._mark_retry()

        # --- State Machine ---
        if self.state == "NO_POS":
            if self._has_retry_cooldown():
                return

            # Place entry limits
            b1 = self._normalize_price(p_mean - self.entry_std_multiplier_1 * sigma)
            b2 = self._normalize_price(p_mean - self.entry_std_multiplier_2 * sigma)

            if b1 <= 0 or b2 <= 0:
                logger.error("Computed invalid entry price, skip this round.")
                self._mark_retry()
                return
            
            qty_b1 = self._build_order_qty(b1)
            qty_b2 = self._build_order_qty(b2)
            
            try:
                logger.info(f"Placing ENTRY orders... B1: {self._format_price(b1)} (qty:{qty_b1}), B2: {self._format_price(b2)} (qty:{qty_b2})")
                self.entry_orders = []

                for price, qty in ((b1, qty_b1), (b2, qty_b2)):
                    notional = price * qty
                    if qty < float(self.min_qty) or notional < float(self.min_notional):
                        logger.warning(f"Skip invalid entry order: price={self._format_price(price)}, qty={qty}, notional={notional:.4f}")
                        continue

                    order = self.rest_client.new_order(
                        symbol=self.symbol,
                        side="BUY",
                        type="LIMIT",
                        timeInForce="GTC",
                        quantity=qty,
                        price=price,
                    )
                    self.entry_orders.append(order['orderId'])

                if self.entry_orders:
                    self.order_placed_time = time.time()
                    self.state = "WAITING_ENTRY"
                else:
                    logger.warning("No valid entry orders were submitted.")
                    self.state = "NO_POS"
                    self._mark_retry()
            except Exception as e:
                logger.error(f"Error placing entry orders: {e}")
                self.state = "NO_POS"
                self.entry_orders = []
                self._mark_retry()

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
            elif not self.entry_orders:
                self.state = "NO_POS"
                self._mark_retry()

        elif self.state == "WAITING_EXIT":
            if self.exit_order_id is None:
                # Place limit exit
                raw_exit = self._normalize_price(p_mean + self.exit_std_multiplier * sigma)
                
                # Patch 1: Breakeven check (Cost + Fee)
                breakeven_price = self.avg_cost * (1 + self.fee_rate * 2) # roundtrip fee
                exit_price = self._normalize_price(max(raw_exit, breakeven_price))
                exit_qty = self._normalize_qty_down(self.position_amt)

                if exit_qty <= 0:
                    logger.error("Invalid exit quantity, skip placing exit order.")
                    self._mark_retry()
                    return
                
                logger.info(f"Placing EXIT order... AvgCost: {self._format_price(self.avg_cost)}, Target: {self._format_price(raw_exit)}, Adjusted (Breakeven): {self._format_price(exit_price)}")
                try:
                    r = self.rest_client.new_order(symbol=self.symbol, side="SELL", type="LIMIT", timeInForce="GTC", quantity=exit_qty, price=exit_price)
                    self.exit_order_id = r['orderId']
                    self.order_placed_time = time.time()
                except Exception as e:
                    logger.error(f"Error placing exit order: {e}")
                    self._mark_retry()
                    
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
            self.position_amt, self.avg_cost = self._extract_position_info(positions)
            if self.position_amt < 0:
                self._handle_opposite_position("runtime")
            elif self.state == "BLOCKED" and self.position_amt == 0:
                self.state = "NO_POS"
                self.blocked_reason = ""
        except Exception as e:
            logger.error(f"Error updating position state: {e}")

    def _market_sell_all(self):
        try:
            self.rest_client.cancel_open_orders(symbol=self.symbol)
            if self.position_amt > 0:
                self.rest_client.new_order(symbol=self.symbol, side="SELL", type="MARKET", quantity=self._normalize_qty_down(self.position_amt))
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
