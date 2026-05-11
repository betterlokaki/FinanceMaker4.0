"""Interactive Alpaca live menu for MAG7 and earnings strategies."""
from __future__ import annotations

import asyncio
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import Enum
import logging
import signal
import sys

from common.cache.abstracts import ITickerCache
from common.settings import (
    AIScannerConfig,
    OrderParamsConfig,
    PortfolioAllocationConfig,
    settings,
)
from publishers.abstracts import IBroker
from pullers.market.abstracts import IMarketProvider
from pullers.realtime.abstracts import IRealtimeProvider
from pullers.scanners.ai_scanners.earning_tommrow_ai import EarningTomorrowAI
from strategy.abstracts import ITradingStrategy
from strategy.earning_strategy import EarningStrategy
from strategy.mag7_ema_slope_regime_strategy import Mag7EmaSlopeRegimeLiveStrategy

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("websockets").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)


class LiveStrategySelection(str, Enum):
    """Strategies supported by the shared Alpaca live menu."""

    MAG7 = "mag7"
    EARNINGS = "earnings"
    BOTH = "both"


@dataclass(frozen=True)
class LiveStrategyContext:
    """Shared dependencies available to live strategy factories."""

    broker: IBroker
    realtime_provider: IRealtimeProvider
    market_provider: IMarketProvider
    earnings_scanner: EarningTomorrowAI
    ticker_cache: ITickerCache
    ai_scanner_config: AIScannerConfig
    portfolio_allocation_config: PortfolioAllocationConfig
    order_params_config: OrderParamsConfig


StrategyFactory = Callable[[LiveStrategyContext], ITradingStrategy]


@dataclass(frozen=True)
class LiveStrategySpec:
    """Menu and factory metadata for one live strategy."""

    key: LiveStrategySelection
    menu_choice: str
    label: str
    aliases: tuple[str, ...]
    factory: StrategyFactory


def _create_mag7_strategy(context: LiveStrategyContext) -> ITradingStrategy:
    return Mag7EmaSlopeRegimeLiveStrategy(
        realtime_provider=context.realtime_provider,
        market_provider=context.market_provider,
        broker=context.broker,
        notional_per_trade=settings.alpaca.notional_per_trade,
        stop_loss_pct=settings.alpaca.stop_loss_pct,
        take_profit_pct=settings.alpaca.take_profit_pct,
    )


def _create_earnings_strategy(context: LiveStrategyContext) -> ITradingStrategy:
    return EarningStrategy(
        realtime_provider=context.realtime_provider,
        earnings_scanner=context.earnings_scanner,
        broker=context.broker,
        ai_scanner_config=context.ai_scanner_config,
        ticker_cache=context.ticker_cache,
        portfolio_allocation_config=context.portfolio_allocation_config,
        order_params_config=context.order_params_config,
        notional_per_trade=settings.alpaca.notional_per_trade,
    )


STRATEGY_SPECS: tuple[LiveStrategySpec, ...] = (
    LiveStrategySpec(
        key=LiveStrategySelection.MAG7,
        menu_choice="1",
        label="MAG7 EMA Slope Regime",
        aliases=("mag7",),
        factory=_create_mag7_strategy,
    ),
    LiveStrategySpec(
        key=LiveStrategySelection.EARNINGS,
        menu_choice="2",
        label="Earnings Strategy",
        aliases=("earnings", "earning"),
        factory=_create_earnings_strategy,
    ),
)
STRATEGY_SPECS_BY_KEY: dict[LiveStrategySelection, LiveStrategySpec] = {
    spec.key: spec for spec in STRATEGY_SPECS
}
BOTH_MENU_CHOICE = "3"
EXIT_MENU_CHOICE = "4"


def _selected_strategy_specs(selection: LiveStrategySelection) -> list[LiveStrategySpec]:
    if selection == LiveStrategySelection.BOTH:
        return list(STRATEGY_SPECS)
    spec = STRATEGY_SPECS_BY_KEY.get(selection)
    return [] if spec is None else [spec]


def create_live_strategies(
    selection: LiveStrategySelection,
    *,
    broker: IBroker,
    realtime_provider: IRealtimeProvider,
    market_provider: IMarketProvider,
    earnings_scanner: EarningTomorrowAI,
    ticker_cache: ITickerCache,
    ai_scanner_config: AIScannerConfig,
    portfolio_allocation_config: PortfolioAllocationConfig,
    order_params_config: OrderParamsConfig,
) -> list[ITradingStrategy]:
    """Create selected strategies with one shared broker and realtime provider."""
    context = LiveStrategyContext(
        broker=broker,
        realtime_provider=realtime_provider,
        market_provider=market_provider,
        earnings_scanner=earnings_scanner,
        ticker_cache=ticker_cache,
        ai_scanner_config=ai_scanner_config,
        portfolio_allocation_config=portfolio_allocation_config,
        order_params_config=order_params_config,
    )
    return [spec.factory(context) for spec in _selected_strategy_specs(selection)]


async def initialize_live_strategies(
    strategies: Sequence[ITradingStrategy],
) -> list[ITradingStrategy]:
    """Initialize strategies independently and keep the ones that succeed."""
    started: list[ITradingStrategy] = []
    for strategy in strategies:
        strategy_name = type(strategy).__name__
        try:
            await strategy.initialize()
            started.append(strategy)
            logger.info("Strategy initialized: %s", strategy_name)
        except Exception as exc:
            logger.error(
                "Strategy %s failed to initialize; continuing with remaining strategies: %s",
                strategy_name,
                exc,
                exc_info=True,
            )
            try:
                await strategy.shutdown()
            except Exception as shutdown_exc:
                logger.warning(
                    "Strategy shutdown after failed init also failed for %s: %s",
                    strategy_name,
                    shutdown_exc,
                )
    return started


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


async def run_selected_strategies(selection: LiveStrategySelection) -> None:
    """Run selected live strategies until Ctrl+C or process termination."""
    from common.di_container import container

    broker = container.alpaca_broker()
    realtime_provider = container.yahoo_realtime_provider()
    market_provider = container.yahoo_market_provider()
    earnings_scanner = container.earning_tomorrow_ai_scanner()
    ticker_cache = container.ticker_cache()
    http_client = container.http_client()

    strategies = create_live_strategies(
        selection,
        broker=broker,
        realtime_provider=realtime_provider,
        market_provider=market_provider,
        earnings_scanner=earnings_scanner,
        ticker_cache=ticker_cache,
        ai_scanner_config=settings.ai_scanner,
        portfolio_allocation_config=settings.portfolio_allocation,
        order_params_config=settings.order_params,
    )
    if not strategies:
        logger.warning("No strategies selected")
        return

    shutdown_event = asyncio.Event()
    install_shutdown_signal_handlers(shutdown_event)
    started_strategies: list[ITradingStrategy] = []

    try:
        await broker.connect()
        logger.info(
            "Connected to Alpaca. Starting %d strategy instance(s): %s",
            len(strategies),
            ", ".join(type(strategy).__name__ for strategy in strategies),
        )

        started_strategies = await initialize_live_strategies(strategies)
        if not started_strategies:
            logger.error("No selected strategies initialized successfully. Exiting.")
            return

        subscribed_tickers = _collect_strategy_tickers(started_strategies)
        logger.info(
            "Shared Yahoo realtime provider is subscribed to %d unique ticker(s): %s",
            len(subscribed_tickers),
            subscribed_tickers,
        )
        logger.info("Live strategies are running. Press Ctrl+C to stop.")
        await shutdown_event.wait()
    finally:
        for strategy in reversed(started_strategies):
            try:
                await strategy.shutdown()
            except Exception as exc:
                logger.warning("Strategy shutdown error for %s: %s", type(strategy).__name__, exc)
        try:
            await realtime_provider.disconnect()
        except Exception as exc:
            logger.warning("Realtime provider disconnect error: %s", exc)
        try:
            await broker.disconnect()
        except Exception as exc:
            logger.warning("Broker disconnect error: %s", exc)
        await http_client.aclose()
        logger.info("Shared Alpaca live runner shutdown complete")


def _collect_strategy_tickers(strategies: Sequence[ITradingStrategy]) -> list[str]:
    tickers: set[str] = set()
    for strategy in strategies:
        raw_tickers = getattr(strategy, "tickers", [])
        tickers.update(str(ticker).upper() for ticker in raw_tickers)
    return sorted(tickers)


def parse_menu_choice(raw_choice: str) -> LiveStrategySelection | None:
    """Parse a menu choice; returns None for exit."""
    choice = raw_choice.strip().lower()
    numeric_choices: dict[str, LiveStrategySelection | None] = {
        spec.menu_choice: spec.key for spec in STRATEGY_SPECS
    }
    numeric_choices[BOTH_MENU_CHOICE] = LiveStrategySelection.BOTH
    numeric_choices[EXIT_MENU_CHOICE] = None
    if choice in numeric_choices:
        return numeric_choices[choice]

    aliases: dict[str, LiveStrategySelection | None] = {
        "both": LiveStrategySelection.BOTH,
        "all": LiveStrategySelection.BOTH,
        "exit": None,
        "quit": None,
        "q": None,
    }
    for spec in STRATEGY_SPECS:
        for alias in spec.aliases:
            aliases[alias] = spec.key

    if choice in aliases:
        return aliases[choice]
    raise ValueError(f"Unknown menu choice: {raw_choice}")


def print_menu() -> None:
    """Print the shared live strategy menu."""
    print("\n" + "=" * 70)
    print("ALPACA LIVE STRATEGY MENU")
    print("=" * 70)
    for spec in STRATEGY_SPECS:
        print(f"{spec.menu_choice}. Run {spec.label}")
    print(f"{BOTH_MENU_CHOICE}. Run All Strategies")
    print(f"{EXIT_MENU_CHOICE}. Exit")
    print("=" * 70)


async def main() -> None:
    """Interactive menu entrypoint."""
    while True:
        # print_menu()
        try:
            selection = LiveStrategySelection.BOTH
        except KeyboardInterrupt:
            print("\nGoodbye.\n")
            return
        except ValueError as exc:
            print(f"\n{exc}\n")
            continue

        if selection is None:
            print("\nGoodbye.\n")
            return

        try:
            await run_selected_strategies(selection)
        except KeyboardInterrupt:
            logger.info("Keyboard interrupt received. Exiting menu.")
        return


if __name__ == "__main__":
    asyncio.run(main())
