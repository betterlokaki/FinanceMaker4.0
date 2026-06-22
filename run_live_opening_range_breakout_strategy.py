"""Run the opening range breakout strategy in live Alpaca mode."""
from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys
from datetime import datetime, time
from zoneinfo import ZoneInfo

from common.helpers.market_calendar import MarketCalendar
from common.models.strategy_input import StrategyInputModel
from common.runners import CommonStrategyRunner
from common.settings import AlpacaConfig
from publishers.alpaca import AlpacaBroker
from strategy.opening_range_breakout_strategy import OpeningRangeBreakoutLiveStrategy

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("websockets").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)
ISRAEL_TZ = ZoneInfo("Asia/Jerusalem")
NY_TZ = ZoneInfo("America/New_York")
STOP_TIME_ISRAEL = time(hour=23, minute=0)
REGULAR_OPEN_TIME_NY = time(hour=9, minute=30)
DEFAULT_PORTFOLIO_PCT_PER_TRADE = 0.5 / len(OpeningRangeBreakoutLiveStrategy.ORB_TICKERS)


def create_orb_alpaca_config_from_env() -> AlpacaConfig:
    """Create a separate Alpaca config from ORB_* env variables."""
    return AlpacaConfig(
        api_key=os.getenv("ORB_ALPACA_API_KEY", "").strip(),
        secret_key=os.getenv("ORB_ALPACA_SECRET_KEY", "").strip(),
        paper=_env_bool("ORB_ALPACA_PAPER", default=True),
        url_override=os.getenv("ORB_ALPACA_URL_OVERRIDE", "").strip(),
    )


def validate_orb_alpaca_config(config: AlpacaConfig) -> None:
    """Fail fast when dedicated ORB Alpaca credentials are missing."""
    if not config.api_key or not config.secret_key:
        raise RuntimeError(
            "Missing ORB Alpaca credentials. Set ORB_ALPACA_API_KEY "
            "and ORB_ALPACA_SECRET_KEY."
        )


def create_orb_strategy_input_from_env() -> StrategyInputModel:
    """Create ORB sizing and bracket risk settings with strict 3R default."""
    risk_pct = _env_float("ORB_STOP_LOSS_PCT", 0.015)
    reward_to_risk = _env_float("ORB_REWARD_TO_RISK", 3.0)
    return StrategyInputModel(
        portfolio_pct_per_trade=_env_float(
            "ORB_PORTFOLIO_PCT_PER_TRADE",
            DEFAULT_PORTFOLIO_PCT_PER_TRADE,
        ),
        risk_pct=risk_pct,
        reward_pct=risk_pct * reward_to_risk,
    )


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


def seconds_until_ny_regular_open(now: datetime | None = None) -> float:
    """Return seconds until today's 09:30 New York regular-session open."""
    now_ny = now or datetime.now(NY_TZ)
    if now_ny.tzinfo is None:
        now_ny = now_ny.replace(tzinfo=NY_TZ)
    else:
        now_ny = now_ny.astimezone(NY_TZ)
    open_at = now_ny.replace(
        hour=REGULAR_OPEN_TIME_NY.hour,
        minute=REGULAR_OPEN_TIME_NY.minute,
        second=0,
        microsecond=0,
    )
    return max(0.0, (open_at - now_ny).total_seconds())


def install_shutdown_signal_handlers(shutdown_event: asyncio.Event) -> None:
    """Request graceful shutdown on SIGTERM/SIGINT."""
    loop = asyncio.get_running_loop()

    def request_shutdown(signal_name: str) -> None:
        logger.info("Received %s. Requesting graceful shutdown...", signal_name)
        shutdown_event.set()

    for stop_signal in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(stop_signal, request_shutdown, stop_signal.name)
        except NotImplementedError:
            signal.signal(
                stop_signal,
                lambda _signum, _frame, name=stop_signal.name: request_shutdown(name),
            )


async def main() -> None:
    """Start the isolated opening range breakout strategy runner."""
    alpaca_config = create_orb_alpaca_config_from_env()
    validate_orb_alpaca_config(alpaca_config)

    runtime_seconds = seconds_until_israel_stop()
    if runtime_seconds <= 0:
        logger.info("Current Israel time is at or after 23:00. Exiting.")
        return

    market_calendar = MarketCalendar()
    now_ny = market_calendar.now()
    if not market_calendar.is_trading_day(now_ny):
        logger.info("Today is not a NYSE trading day. Exiting.")
        return

    shutdown_event = asyncio.Event()
    install_shutdown_signal_handlers(shutdown_event)

    wait_seconds = min(seconds_until_ny_regular_open(), runtime_seconds)
    if wait_seconds > 0:
        logger.info("Waiting %.0f seconds until 09:30 New York regular open", wait_seconds)
        wait_task = asyncio.create_task(asyncio.sleep(wait_seconds))
        shutdown_task = asyncio.create_task(shutdown_event.wait())
        done, pending = await asyncio.wait(
            {wait_task, shutdown_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        if shutdown_task in done:
            logger.info("Shutdown requested before ORB startup. Exiting.")
            return

    from common.di_container import container

    broker = AlpacaBroker(config=alpaca_config)
    realtime_provider = container.yahoo_realtime_provider()
    market_provider = container.yahoo_market_provider()
    http_client = container.http_client()
    strategy_input = create_orb_strategy_input_from_env()
    strategy = OpeningRangeBreakoutLiveStrategy(
        realtime_provider=realtime_provider,
        market_provider=market_provider,
        broker=broker,
        strategy_input=strategy_input,
        opening_range_minutes=_env_int("ORB_OPENING_RANGE_MINUTES", 15),
        confirmation_candle_minutes=_env_int("ORB_CONFIRMATION_CANDLE_MINUTES", 5),
        max_positions=_env_int("ORB_MAX_POSITIONS", 3),
    )
    runner = CommonStrategyRunner(strategies=[strategy], strategy_input=strategy_input)

    try:
        await broker.connect()
        await runner.start_all()
        logger.info("ORB strategy running with tickers: %s", strategy.tickers)
        await wait_until_stop_or_shutdown(shutdown_event)
    finally:
        try:
            await runner.stop_all()
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
        logger.info("ORB runner shutdown complete")


async def wait_until_stop_or_shutdown(shutdown_event: asyncio.Event) -> None:
    """Keep the Cloud Run job alive until the configured stop time or shutdown."""
    remaining_runtime = seconds_until_israel_stop()
    stop_task = asyncio.create_task(asyncio.sleep(remaining_runtime))
    shutdown_task = asyncio.create_task(shutdown_event.wait())
    done, pending = await asyncio.wait(
        {stop_task, shutdown_task},
        return_when=asyncio.FIRST_COMPLETED,
    )
    if stop_task in done:
        logger.info("Reached 23:00 Israel time. Stopping ORB strategy...")
    if shutdown_task in done:
        logger.info("Shutdown requested. Stopping ORB strategy...")
    for task in pending:
        task.cancel()
    await asyncio.gather(*pending, return_exceptions=True)


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None or not value.strip():
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def _env_float(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None or not value.strip():
        return default
    return float(value)


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None or not value.strip():
        return default
    return int(value)


if __name__ == "__main__":
    asyncio.run(main())
