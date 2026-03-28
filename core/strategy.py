import asyncio
import json
import time
import math
import collections
import pandas as pd
import numpy as np

import websockets
from binance.um_futures import UMFutures
from binance.error import ClientError

from core.logger import logger
import core.config as cfg

class RobustMakerBot:
    def __init__(self):
        self.symbol = cfg.SYMBOL
        logger.info(f"Initializing bot for {self.symbol} on {'TESTNET' if cfg.USE_TESTNET else 'MAINNET'}")
        
        self.rest_client = UMFutures(
            key=cfg.API_KEY, 
            secret=cfg.API_SECRET, 
            base_url=cfg.REST_URL
        )
        
        # 缓存 K 线数据
        self.klines_1m = collections.deque(maxlen=100)
        self.klines_5m = collections.deque(maxlen=100)
        self.current_price = 0.0
        
        # 交易精度缓冲
        self.price_precision = 2
        self.qty_precision = 3
        self.tick_size = 0.01
        self.step_size = 0.001
        
        # 策略状态
        self.is_running = False
        self.tp_order_id = None
        self.tp_order_time = None
        
        # 获取精度
        self._init_exchange_info()
        # 初始化历史K线
        self._fetch_history_klines()

    def _init_exchange_info(self):
        try:
            info = self.rest_client.exchange_info()
            for s in info['symbols']:
                if s['symbol'] == self.symbol:
                    self.price_precision = s['pricePrecision']
                    self.qty_precision = s['quantityPrecision']
                    for f in s['filters']:
                        if f['filterType'] == 'PRICE_FILTER':
                            self.tick_size = float(f['tickSize'])
                        if f['filterType'] == 'LOT_SIZE':
                            self.step_size = float(f['stepSize'])
            logger.info(f"Loaded Exchange Info | tick_size={self.tick_size}, step_size={self.step_size}")
            logger.info(f"Precision | price={self.price_precision}, qty={self.qty_precision}")
        except Exception as e:
            logger.error(f"Failed to fetch exchange info: {e}")

    def _fetch_history_klines(self):
        try:
            # 1m
            k1m = self.rest_client.klines(self.symbol, "1m", limit=100)
            for k in k1m:
                # [open_time, open, high, low, close, volume, ...]
                self.klines_1m.append({
                    "time": int(k[0]),
                    "close": float(k[4])
                })
            # 5m
            k5m = self.rest_client.klines(self.symbol, "5m", limit=100)
            for k in k5m:
                self.klines_5m.append({
                    "time": int(k[0]),
                    "close": float(k[4])
                })
            self.current_price = self.klines_1m[-1]['close'] if self.klines_1m else 0.0
            logger.info(f"Historical Klines loaded. Current price: {self.current_price}")
        except Exception as e:
            logger.error(f"Failed to fetch history klines: {e}")

    def get_indicators(self):
        if len(self.klines_1m) < 20 or len(self.klines_5m) < 55:
            return None, None, None, None
            
        df_1m = pd.DataFrame(self.klines_1m)
        df_5m = pd.DataFrame(self.klines_5m)
        
        # 1分钟 MA(7)
        ma7_seq = df_1m['close'].rolling(7).mean().values
        ma7_cur = ma7_seq[-1]
        ma7_prev = ma7_seq[-2] if len(ma7_seq) > 1 else ma7_cur
        
        # 5分钟 EMA(20) & EMA(50)
        ema20 = df_5m['close'].ewm(span=20, adjust=False).mean().iloc[-1]
        ema50 = df_5m['close'].ewm(span=50, adjust=False).mean().iloc[-1]
        
        return ma7_cur, ma7_prev, ema20, ema50

    def format_price(self, price: float) -> float:
        return round(math.floor(price / self.tick_size) * self.tick_size, self.price_precision)
        
    def format_qty(self, qty: float) -> float:
        return round(math.floor(qty / self.step_size) * self.step_size, self.qty_precision)

    async def ws_loop(self):
        stream_url = f"{cfg.WS_URL}/stream?streams={self.symbol.lower()}@kline_1m/{self.symbol.lower()}@kline_5m/{self.symbol.lower()}@bookTicker"
        while self.is_running:
            try:
                async with websockets.connect(stream_url) as ws:
                    logger.info("WebSocket Connected.")
                    while self.is_running:
                        msg = await ws.recv()
                        data = json.loads(msg)
                        self._handle_ws_data(data)
            except Exception as e:
                logger.error(f"WS error: {e}. Reconnecting in 3s...")
                await asyncio.sleep(3)

    def _handle_ws_data(self, data):
        stream = data.get("stream", "")
        payload = data.get("data", {})
        
        if stream.endswith("@bookTicker"):
            bids = float(payload.get('b', 0))
            if bids > 0:
                self.current_price = bids
                
        elif "@kline" in stream:
            k = payload.get("k", {})
            kline_interval = k.get("i")
            is_closed = k.get("x")
            close_price = float(k.get("c"))
            open_time = int(k.get("t"))
            
            target_list = self.klines_1m if kline_interval == "1m" else self.klines_5m
            
            if len(target_list) > 0 and target_list[-1]['time'] == open_time:
                target_list[-1]['close'] = close_price
            else:
                target_list.append({"time": open_time, "close": close_price})
            
            if kline_interval == "1m":
                self.current_price = close_price

    async def trade_loop(self):
        while self.is_running:
            try:
                await asyncio.to_thread(self._tick)
            except ClientError as e:
                logger.error(f"Binance API ClientError: {e.error_message}")
            except Exception as e:
                logger.error(f"Trade loop error: {e}")
            await asyncio.sleep(3)

    def _tick(self):
        """核心交易决策"""
        ma7_cur, ma7_prev, ema20, ema50 = self.get_indicators()
        if ma7_cur is None or self.current_price == 0:
            return

        # 1. 获取当前持仓与订单情况
        pos_res = self.rest_client.get_position_risk(symbol=self.symbol)
        position = next((p for p in pos_res if p['symbol'] == self.symbol), None)
        if not position:
            return

        pos_amt = float(position['positionAmt'])
        entry_price = float(position['entryPrice'])
        open_orders = self.rest_client.get_orders(symbol=self.symbol)
        
        # 对订单按类型分类
        buy_orders = [o for o in open_orders if o['side'] == 'BUY']
        sell_orders = [o for o in open_orders if o['side'] == 'SELL']

        # ==== 状态 A：持有仓位 ====
        if pos_amt > 0:
            # 取消所有买单 (已经接到了仓位，防守期间不要乱入)
            if len(buy_orders) > 0:
                self.rest_client.cancel_open_orders(symbol=self.symbol)
                logger.info("Cancelled all buy orders because we are in position.")
                return # 下一轮再处理止损止盈
                
            # A1. 检查硬止损
            if self.current_price <= entry_price * (1 - cfg.SL_OFFSET):
                logger.warning(f"STOP LOSS TRIGGERED. Pos: {pos_amt}, Entry: {entry_price}, Cur: {self.current_price}")
                self.rest_client.cancel_open_orders(symbol=self.symbol)
                self.rest_client.new_order(
                    symbol=self.symbol,
                    side="SELL",
                    type="MARKET",
                    quantity=abs(pos_amt)
                )
                self.tp_order_id = None
                return

            # A2. 管理止盈 (判断是一档还是二档)
            target_qty1 = self.format_qty((cfg.CAPITAL * cfg.TIER_1_SIZE_PCT) / entry_price)
            target_qty2 = self.format_qty((cfg.CAPITAL * (cfg.TIER_1_SIZE_PCT + cfg.TIER_2_SIZE_PCT)) / entry_price)

            expected_tp_price = 0.0
            if pos_amt > target_qty1 * 1.2: # 稍微放大以判断是否触发二档
                expected_tp_price = entry_price * (1 + cfg.TP_2_OFFSET)
            else:
                expected_tp_price = entry_price * (1 + cfg.TP_1_OFFSET)
                
            expected_tp_price = self.format_price(expected_tp_price)

            # 找不到卖单，可能刚才被撤，或者新成交
            if len(sell_orders) == 0:
                logger.info(f"Placing new TP order at {expected_tp_price} for {pos_amt}")
                res = self.rest_client.new_order(
                    symbol=self.symbol,
                    side="SELL",
                    type="LIMIT",
                    timeInForce="GTC",
                    quantity=abs(pos_amt),
                    price=expected_tp_price
                )
                self.tp_order_id = res['orderId']
                self.tp_order_time = time.time()
            else:
                # 检查是否超时，转为保本
                open_tp = sell_orders[0]
                self.tp_order_id = open_tp['orderId']
                order_time = open_tp['updateTime'] / 1000.0 # ms to s
                if self.tp_order_time is None:
                    self.tp_order_time = order_time
                    
                elapsed_mins = (time.time() - self.tp_order_time) / 60.0
                if elapsed_mins > cfg.TIMEOUT_MINUTES:
                    new_be_price = self.format_price(entry_price * (1 + cfg.TP_BE_OFFSET))
                    if float(open_tp['price']) != new_be_price:
                        logger.info(f"TP Timeout {elapsed_mins:.1f}m. Modifying to Break-Even at {new_be_price}")
                        self.rest_client.cancel_order(symbol=self.symbol, orderId=self.tp_order_id)
                        self.tp_order_id = None
                        self.tp_order_time = time.time() # resets timer against infinite loops
                        
        # ==== 状态 B：空仓阶段 ====
        else:
            # 清理残留的卖单 (止盈单)
            if len(sell_orders) > 0:
                self.rest_client.cancel_open_orders(symbol=self.symbol)
                logger.info("Cancelled stale sell orders.")
                self.tp_order_id = None
                return

            # 判断形态：多头排列 + 动能向上
            trend_ok = ema20 > ema50
            momentum_ok = ma7_cur >= ma7_prev
            
            if trend_ok and momentum_ok:
                price_t1 = self.format_price(ma7_cur * (1 - cfg.TIER_1_OFFSET))
                price_t2 = self.format_price(ma7_cur * (1 - cfg.TIER_2_OFFSET))
                
                qty_t1 = self.format_qty((cfg.CAPITAL * cfg.TIER_1_SIZE_PCT) / price_t1)
                qty_t2 = self.format_qty((cfg.CAPITAL * cfg.TIER_2_SIZE_PCT) / price_t2)

                if len(buy_orders) == 0:
                    if qty_t1 > 0 and qty_t2 > 0:
                        logger.info(f"Trend OK. Placing Buy T1: {qty_t1}@{price_t1}, T2: {qty_t2}@{price_t2}")
                        # Batch orders or individual setup
                        self.rest_client.new_order(symbol=self.symbol, side="BUY", type="LIMIT", timeInForce="GTC", quantity=qty_t1, price=price_t1)
                        self.rest_client.new_order(symbol=self.symbol, side="BUY", type="LIMIT", timeInForce="GTC", quantity=qty_t2, price=price_t2)
                else:
                    # 检查是否偏离过大
                    first_buy = float(buy_orders[0]['price'])
                    drift = abs(self.current_price - first_buy) / first_buy
                    if drift > cfg.MAX_BUY_DRIFT:
                        logger.info(f"Price drifted {drift*100:.2f}%. Cancelling buy orders. Current: {self.current_price}, Order: {first_buy}")
                        self.rest_client.cancel_open_orders(symbol=self.symbol)
            else:
                # 形态破坏，撤销所有买单防止接飞刀
                if len(buy_orders) > 0:
                    logger.info(f"Trend or Momentum lost (ema20>50:{trend_ok}, ma7_up:{momentum_ok}). Cancelling buys.")
                    self.rest_client.cancel_open_orders(symbol=self.symbol)

    async def run(self):
        self.is_running = True
        logger.info("Bot started. Running loops...")
        await asyncio.gather(
            self.ws_loop(),
            self.trade_loop()
        )

    def stop(self):
        self.is_running = False
        logger.info("Stopping bot...")
