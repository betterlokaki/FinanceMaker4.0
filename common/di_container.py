"""Dependency injection configuration container."""
import httpx
from dependency_injector import containers, providers

from backtesting.abstracts import IBacktestEngine, IBacktestStrategy
from backtesting.engines import VectorBTEngine
from backtesting.strategies import SupplyDemandStrategy
from common.cache.abstracts import ITickerCache
from common.cache.file_ticker_cache import FileTickerCache
from common.helpers.ai_ticker_analyzer import AITickerAnalyzer
from common.helpers.market_calendar import MarketCalendar
from common.helpers.risk_reward_calculator import RiskRewardCalculator
from common.settings import settings
from common.user_agent import UserAgentManager
from gpt.abstracts import IGPTClient
from gpt.gemini import GeminiClient
from gpt.grok import GrokClient
from publishers.abstracts import IBroker
from publishers.interactive_brokers import InteractiveWebapiBroker
from pullers.ideas.abstracts import IIdeaPuller
from pullers.ideas.trading_view_idea_puller import TradingViewIdeaPuller
from pullers.market.abstracts import IMarketProvider
from pullers.market.yahoo import YahooMarketProvider
from pullers.realtime.abstracts import IRealtimeProvider
from pullers.realtime.yahoo import YahooRealtimeProvider
from pullers.scanners.abstracts import IScanner
from pullers.scanners.ai_scanners import EarningTomorrowAI
from pullers.scanners.finviz.earning_tommrow import EarningTommrow
from pullers.scanners.finviz.zone_filtered_scanner import ZoneFilteredScanner
from scheduler.abstracts import IScheduler
from scheduler.demand_zone_scheduler import DemandZoneScheduler
from scheduler.strategy_runner import StrategyRunner
from scheduler.trading_scheduler import TradingScheduler
from strategy.abstracts import ITradingStrategy
from strategy.demand_zone_strategy import DemandZoneStrategy
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

    # Ticker Cache
    ticker_cache: providers.Provider[ITickerCache] = providers.Singleton(
        FileTickerCache,
        config=providers.Object(settings.cache),
    )

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

    # Zone-filtered scanner (requires custom URL at instantiation)
    # Note: Use container.zone_filtered_scanner() with custom URL parameter
    zone_filtered_scanner: providers.Provider[IScanner] = providers.Factory(
        ZoneFilteredScanner,
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

    # Idea Pullers
    tradingview_idea_puller: providers.Provider[IIdeaPuller] = providers.Singleton(
        TradingViewIdeaPuller,
        http_client=http_client,
    )

    # Helpers
    ai_ticker_analyzer: providers.Provider[AITickerAnalyzer] = providers.Singleton(
        AITickerAnalyzer,
    )

    risk_reward_calculator: providers.Provider[RiskRewardCalculator] = providers.Singleton(
        RiskRewardCalculator,
    )

    # Demand zone scanner (factory with URL)
    demand_zone_scanner: providers.Provider[IScanner] = providers.Factory(
        ZoneFilteredScanner,
        http_client=http_client,
        url=providers.Object(settings.demand_zone_strategy.finviz_url),
    )

    # Trading Strategies
    earning_strategy: providers.Provider[ITradingStrategy] = providers.Singleton(
        EarningStrategy,
        realtime_provider=yahoo_realtime_provider,
        earnings_scanner=earning_tomorrow_ai_scanner,
        broker=ibkr_broker,
        ai_scanner_config=providers.Object(settings.ai_scanner),
        ticker_cache=ticker_cache,
        portfolio_allocation_config=providers.Object(settings.portfolio_allocation),
        order_params_config=providers.Object(settings.order_params),
    )

    demand_zone_strategy: providers.Provider[ITradingStrategy] = providers.Singleton(
        DemandZoneStrategy,
        http_client=http_client,
        zone_scanner=demand_zone_scanner,
        ai_analyzer=ai_ticker_analyzer,
        grok_client=grok_client,
        gemini_client=gemini_client,
        realtime_provider=yahoo_realtime_provider,
        broker=ibkr_broker,
        risk_calculator=risk_reward_calculator,
        ticker_cache=ticker_cache,
        prompt_template=providers.Object(settings.demand_zone_strategy.prompt_template),
        finviz_url=providers.Object(settings.demand_zone_strategy.finviz_url),
        portfolio_allocation_config=providers.Object(settings.portfolio_allocation),
        order_params_config=providers.Object(settings.order_params),
    )

    # Strategy list for scheduler
    strategies: providers.Provider[list[ITradingStrategy]] = providers.List(
        earning_strategy,
        demand_zone_strategy,
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
        ticker_cache=ticker_cache,
        broker=ibkr_broker,
    )

    demand_zone_scheduler: providers.Provider[IScheduler] = providers.Singleton(
        DemandZoneScheduler,
        strategy=demand_zone_strategy,
        market_calendar=market_calendar,
    )

    # Backtesting
    supply_demand_strategy: providers.Provider[IBacktestStrategy] = providers.Singleton(
        SupplyDemandStrategy,
    )

    backtest_engine: providers.Provider[IBacktestEngine] = providers.Singleton(
        VectorBTEngine,
        strategy=supply_demand_strategy,
    )


# Create global container instance
container: Container = Container()

