"""Dependency injection configuration container."""
import httpx
from dependency_injector import containers, providers

from common.settings import settings
from common.user_agent import UserAgentManager
from gpt.abstracts import IGPTClient
from gpt.gemini import GeminiClient
from gpt.grok import GrokClient
from pullers.scanners.abstracts import IScanner
from pullers.scanners.ai_scanners import EarningTomorrowAI
from pullers.scanners.finviz.earning_tommrow import EarningTommrow


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


# Create global container instance
container: Container = Container()

