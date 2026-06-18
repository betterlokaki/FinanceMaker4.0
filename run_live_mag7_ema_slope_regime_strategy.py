"""Run the MAG7 EMA+slope regime strategy in live Alpaca mode."""
from __future__ import annotations

import asyncio
import logging
import signal
import sys
from datetime import datetime, time
from zoneinfo import ZoneInfo

from common.settings import settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("websockets").setLevel(logging.WARNING)

logger: logging.Logger = logging.getLogger(__name__)
ISRAEL_TZ: ZoneInfo = ZoneInfo("Asia/Jerusalem")
ALLOWED_BROKER_PROVIDER: str = "alpaca"
STOP_TIME_ISRAEL: time = time(hour=23, minute=0)


def seconds_until_israel_stop(now: datetime | None = None) -> float:
    """Return seconds until today's 23:00 Israel stop time."""
    now_israel = now or datetime.now(ISRAEL_TZ)
    if now_israel.tzinfo is None:
        now_israel = now_israel.replace(tzinfo=ISRAEL_TZ)
    else:
        now_israel = now_israel.astimezone(ISRAEL_TZ)

    stop_at = now_israel.replace(
        hour=STOP_TIME_ISRAEL.hour,
        minute=STOP_TIME_ISRAEL.minute,
        second=0,
        microsecond=0,
    )
    return max(0.0, (stop_at - now_israel).total_seconds())


def validate_alpaca_only() -> None:
    """Fail fast if this dedicated runner is not configured for Alpaca."""
    broker_provider = settings.broker_provider.lower()
    if broker_provider != ALLOWED_BROKER_PROVIDER:
        raise RuntimeError(
            "MAG7 Cloud Run runner supports only Alpaca. "
            f"Set BROKER_PROVIDER={ALLOWED_BROKER_PROVIDER}; got {broker_provider!r}."
        )


def install_shutdown_signal_handlers(shutdown_event: asyncio.Event) -> None:
    """Request graceful shutdown on SIGTERM/SIGINT."""
    loop = asyncio.get_running_loop()

    def request_shutdown(signal_name: str) -> None:
        logger.info("Received %s. Requesting graceful shutdown...", signal_name)
        shutdown_event.set()

    for stop_signal in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(
                stop_signal,
                request_shutdown,
                stop_signal.name,
            )
        except NotImplementedError:
            signal.signal(
                stop_signal,
                lambda _signum, _frame, name=stop_signal.name: request_shutdown(name),
            )


async def main() -> None:
    """Start MAG7 live strategy and keep it running."""
    validate_alpaca_only()

    runtime_seconds = seconds_until_israel_stop()
    if runtime_seconds <= 0:
        logger.info("Current Israel time is at or after 23:00. Exiting.")
        return

    logger.info("Starting MAG7 EMA+slope regime live strategy...")
    logger.info("Runner will stop in %.0f seconds at 23:00 Israel time", runtime_seconds)

    from common.di_container import container
    from strategy.mag7_ema_slope_regime_strategy import Mag7EmaSlopeRegimeLiveStrategy

    shutdown_event = asyncio.Event()
    install_shutdown_signal_handlers(shutdown_event)
    broker = container.live_broker()
    realtime_provider = container.yahoo_realtime_provider()
    market_provider = container.yahoo_market_provider()
    http_client = container.http_client()

    strategy = Mag7EmaSlopeRegimeLiveStrategy(
        realtime_provider=realtime_provider,
        market_provider=market_provider,
        broker=broker,
        notional_per_trade=settings.alpaca.notional_per_trade,
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

        stop_task = asyncio.create_task(asyncio.sleep(runtime_seconds))
        shutdown_task = asyncio.create_task(shutdown_event.wait())
        done, pending = await asyncio.wait(
            {stop_task, shutdown_task},
            return_when=asyncio.FIRST_COMPLETED,
        )

        if stop_task in done:
            logger.info("Reached 23:00 Israel time. Stopping MAG7 strategy...")
        if shutdown_task in done:
            logger.info("Shutdown requested. Stopping MAG7 strategy...")

        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
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
