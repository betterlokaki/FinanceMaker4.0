"""Dependency injection configuration container."""
import httpx
from dependency_injector import containers, providers

from common.helpers.market_calendar import MarketCalendar
from common.settings import settings
from common.user_agent import UserAgentManager
from gpt.abstracts import IGPTClient
from gpt.gemini import GeminiClient
from gpt.grok import GrokClient
from publishers.abstracts import IBroker
from publishers.interactive_brokers import InteractiveWebapiBroker
from pullers.market.abstracts import IMarketProvider
from pullers.market.yahoo import YahooMarketProvider
from pullers.realtime.abstracts import IRealtimeProvider
from pullers.realtime.yahoo import YahooRealtimeProvider
from pullers.scanners.abstracts import IScanner
from pullers.scanners.ai_scanners import EarningTomorrowAI
from pullers.scanners.finviz.earning_tommrow import EarningTommrow
from scheduler.abstracts import IScheduler
from scheduler.strategy_runner import StrategyRunner
from scheduler.trading_scheduler import TradingScheduler
from strategy.abstracts import ITradingStrategy
from strategy.earning_strategy import EarningStrategy


class Container(containers.DeclarativeContainer):
    """Application dependency injection container.
    
    Manages all application services and their dependencies following
    the same pattern as C# dependency injection containers.
    
    All services are registered as singletons - instances are created once
    and reused throughout the application lifecycle.
    """

    # Configuration (injected from settings)
    config = providers.Singleton(lambda: settings)

    # Third-party services
    http_client = providers.Singleton(
        httpx.AsyncClient,
        timeout=settings.http.timeout,
        follow_redirects=settings.http.follow_redirects,
        limits=httpx.Limits(
            max_connections=settings.http.max_connections,
            max_keepalive_connections=settings.http.max_keepalive_connections
        ),
    )

    # Domain services
    user_agent_manager = providers.Singleton(UserAgentManager)

    # AI Clients (must be defined before scanners that depend on them)
    grok_client: providers.Provider[IGPTClient] = providers.Singleton(
        GrokClient,
        http_client=http_client,
    )

    gemini_client: providers.Provider[IGPTClient] = providers.Singleton(
        GeminiClient,
        http_client=http_client,
    )

    # Scanners (all as singletons - created once, reused)
    finviz_scanner: providers.Provider[IScanner] = providers.Singleton(
        EarningTommrow,
        http_client=http_client,
    )

    # AI-powered scanner (uses EarningTomorrow + AI consensus)
    earning_tomorrow_ai_scanner: providers.Provider[IScanner] = providers.Singleton(
        EarningTomorrowAI,
        http_client=http_client,
        earnings_scanner=finviz_scanner,
        grok_client=grok_client,
        gemini_client=gemini_client,
    )

    # Brokers
    ibkr_broker: providers.Provider[IBroker] = providers.Singleton(
        InteractiveWebapiBroker,
        config=providers.Object(settings.ibkr),
    )

    # Market Providers
    yahoo_market_provider: providers.Provider[IMarketProvider] = providers.Singleton(
        YahooMarketProvider,
        http_client=http_client,
    )

    # Realtime Providers
    yahoo_realtime_provider: providers.Provider[IRealtimeProvider] = providers.Singleton(
        YahooRealtimeProvider,
        base_url=settings.realtime.base_url,
        reconnect_delay=settings.realtime.reconnect_delay,
        max_reconnect_attempts=settings.realtime.max_reconnect_attempts,
    )

    # Trading Strategies
    earning_strategy: providers.Provider[ITradingStrategy] = providers.Singleton(
        EarningStrategy,
        realtime_provider=yahoo_realtime_provider,
        earnings_scanner=earning_tomorrow_ai_scanner,
        broker=ibkr_broker,
        ai_scanner_config=providers.Object(settings.ai_scanner),
    )

    # Strategy list for scheduler
    strategies: providers.Provider[list[ITradingStrategy]] = providers.List(
        earning_strategy,
    )

    # Market Calendar
    market_calendar = providers.Singleton(
        MarketCalendar,
        exchange=settings.scheduler.exchange,
        timezone=settings.scheduler.timezone,
    )

    # Strategy Runner
    strategy_runner = providers.Singleton(
        StrategyRunner,
        strategies=strategies,
        max_retries=settings.scheduler.strategy_max_retries,
        retry_delay=settings.scheduler.strategy_retry_delay,
    )

    # Scheduler
    trading_scheduler: providers.Provider[IScheduler] = providers.Singleton(
        TradingScheduler,
        strategy_runner=strategy_runner,
        market_calendar=market_calendar,
    )


# Create global container instance
container: Container = Container()

