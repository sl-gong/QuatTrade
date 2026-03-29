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
CAPITAL = float(os.getenv("CAPITAL", "1000.0"))

# ===== 均值回归策略参数（直接在此处维护，不从环境变量读取） =====

# 使用最近多少根 1 分钟 K 线计算均值与波动率。
KLINE_WINDOW = 10

# 每笔分批挂单的目标名义金额（USDT/USDC）。
# 需要大于交易所最小名义价值限制，避免下单被拒。
TRANCHE_SIZE = 200.0

# 入场价格：均值 - N * 标准差。
# 第一档更靠近均值，第二档更激进。
ENTRY_STD_MULTIPLIER_1 = 1.0
ENTRY_STD_MULTIPLIER_2 = 4.0

# 出场价格：均值 + N * 标准差。
EXIT_STD_MULTIPLIER = 1.0

# 波动率下限，避免横盘时标准差过小导致挂单过于接近现价。
# 例如 0.001 代表最小波动率按当前价格的 0.1% 计算。
SIGMA_FLOOR_PCT = 0.001

# 订单超时时间（秒）。超过后取消未完成挂单并重新评估。
ORDER_TIMEOUT_SEC = 5 * 60

# 重试冷却时间（秒）。用于限制重复下单、重复处理反向持仓的频率。
RETRY_COOLDOWN_SEC = 5

# 估算单边交易成本（手续费 + 滑点冗余）。
# 用于计算保本卖出价，避免卖出后实际亏损。
ESTIMATED_FEE_RATE = 0.0005

# 硬止损比例。当前价跌破持仓成本的该比例时，立即市价止损。
# 例如 0.015 代表下跌 1.5% 后触发止损。
STOP_LOSS_PCT = 0.015

# 如果检测到已有反向空头持仓，是否自动平空后再恢复策略。
# True：自动平空；False：阻塞策略并等待人工处理。
AUTO_FLATTEN_OPPOSITE_POSITION = True
