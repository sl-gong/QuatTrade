import asyncio
import sys
import signal

from core.logger import logger
from core.strategy import RobustMakerBot

async def main():
    bot = RobustMakerBot()
    
    def signal_handler(sig, frame):
        logger.info("\nCaught interrupt signal! Shutting down gracefully...")
        bot.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)

    try:
        await bot.run()
    except Exception as e:
        logger.error(f"Critical error in main loop: {e}")
        bot.stop()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot exited.")
