"""Run the MAG7 EMA+slope regime strategy in live mode."""
import asyncio
import logging
import sys

from common.di_container import container
from strategy.mag7_ema_slope_regime_strategy import Mag7EmaSlopeRegimeLiveStrategy

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
    """Start MAG7 live strategy and keep it running."""
    logger.info("Starting MAG7 EMA+slope regime live strategy...")

    broker = container.ibkr_broker()
    realtime_provider = container.yahoo_realtime_provider()
    market_provider = container.yahoo_market_provider()
    http_client = container.http_client()

    strategy = Mag7EmaSlopeRegimeLiveStrategy(
        realtime_provider=realtime_provider,
        market_provider=market_provider,
        broker=broker,
    )

    try:
        await broker.connect()
        await strategy.initialize()

        if not strategy.tickers:
            logger.warning("No tickers loaded. Exiting.")
            return

        logger.info(
            "MAG7 strategy initialized with %d tickers: %s",
            len(strategy.tickers),
            strategy.tickers,
        )
        logger.info("Listening for real-time ticks...")

        while True:
            await asyncio.sleep(60)
    except KeyboardInterrupt:
        logger.info("Stopping MAG7 strategy...")
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
        logger.info("Shutdown complete")


if __name__ == "__main__":
    asyncio.run(main())
