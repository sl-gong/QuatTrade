import os
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.getenv("BINANCE_API_KEY", "")
API_SECRET = os.getenv("BINANCE_API_SECRET", "")

USE_TESTNET = os.getenv("USE_TESTNET", "True").lower() in ("true", "1", "yes")

# REST & WS URLs 默认为 U本位合约
if USE_TESTNET:
    REST_URL = "https://testnet.binancefuture.com"
    WS_URL = "wss://stream.binancefuture.com"
else:
    REST_URL = "https://fapi.binance.com"
    WS_URL = "wss://fstream.binance.com"

SYMBOL = os.getenv("SYMBOL", "BTCUSDC").upper()
CAPITAL = float(os.getenv("CAPITAL", "280.0"))

# 策略参数
TIER_1_OFFSET = float(os.getenv("TIER_1_OFFSET", "0.0015"))
TIER_1_SIZE_PCT = float(os.getenv("TIER_1_SIZE_PCT", "0.30"))

TIER_2_OFFSET = float(os.getenv("TIER_2_OFFSET", "0.0035"))
TIER_2_SIZE_PCT = float(os.getenv("TIER_2_SIZE_PCT", "0.50"))

SL_OFFSET = float(os.getenv("STOP_LOSS_PCT", "0.010"))
MAX_BUY_DRIFT = float(os.getenv("MAX_BUY_DRIFT_PCT", "0.005"))

TP_1_OFFSET = float(os.getenv("TP_TIER_1_PCT", "0.0015"))
TP_2_OFFSET = float(os.getenv("TP_TIER_2_PCT", "0.0020"))
TP_BE_OFFSET = float(os.getenv("TP_BREAK_EVEN_PCT", "0.0002"))

TIMEOUT_MINUTES = int(os.getenv("TIMEOUT_MINUTES", "10"))

AUTO_FLATTEN_OPPOSITE_POSITION = os.getenv("AUTO_FLATTEN_OPPOSITE_POSITION", "True").lower() in ("true", "1", "yes")
