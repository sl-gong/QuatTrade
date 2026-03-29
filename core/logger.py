import logging
import os
from datetime import datetime

def setup_logger(name="TradeBot", log_file="logs/trade_bot.log"):
    log_dir = os.path.dirname(log_file)
    os.makedirs(log_dir, exist_ok=True)

    file_name, file_ext = os.path.splitext(os.path.basename(log_file))
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    timed_log_file = os.path.join(log_dir, f"{file_name}-{timestamp}{file_ext}")
    
    logger = logging.getLogger(name)
    if not logger.handlers:
        logger.setLevel(logging.INFO)
        formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')

        # Console Handler
        ch = logging.StreamHandler()
        ch.setFormatter(formatter)
        logger.addHandler(ch)

        # File Handler
        fh = logging.FileHandler(timed_log_file, encoding='utf-8')
        fh.setFormatter(formatter)
        logger.addHandler(fh)

    return logger

logger = setup_logger()
