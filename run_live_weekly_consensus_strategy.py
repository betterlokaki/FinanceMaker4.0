"""Run the weekly double-consensus strategy in live mode."""
import asyncio
import logging
import sys

from common.di_container import container

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("websockets").setLevel(logging.WARNING)

logger: logging.Logger = logging.getLogger(__name__)


async def main() -> None:
    """Start the live weekly consensus strategy and keep it running."""
    logger.info("🚀 Starting weekly double-consensus live strategy...")

    broker = container.ibkr_broker()
    strategy = container.weekly_double_consensus_strategy()
    realtime_provider = container.yahoo_realtime_provider()
    http_client = container.http_client()

    try:
        await broker.connect()
        await strategy.initialize()

        if not strategy.tickers:
            logger.warning("No tradable tickers after consensus. Exiting.")
            return

        logger.info("Strategy initialized with %d tickers: %s", len(strategy.tickers), strategy.tickers)
        logger.info("Listening for real-time ticks and placing orders on first tick per ticker.")

        while True:
            await asyncio.sleep(60)
    except KeyboardInterrupt:
        logger.info("Stopping weekly consensus strategy...")
    finally:
        try:
            await strategy.shutdown()
        except Exception as exc:
            logger.warning("Strategy shutdown error: %s", exc)
        try:
            await realtime_provider.disconnect()
        except Exception as exc:
            logger.warning("Realtime provider disconnect error: %s", exc)
        try:
            await broker.disconnect()
        except Exception as exc:
            logger.warning("Broker disconnect error: %s", exc)
        await http_client.aclose()
        logger.info("✅ Shutdown complete")


if __name__ == "__main__":
    asyncio.run(main())
