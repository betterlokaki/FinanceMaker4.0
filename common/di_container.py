"""Dependency injection configuration container."""
import httpx
from dependency_injector import containers, providers

from common.settings import settings
from pullers.scanners.finviz.earning_tommrow import EarningTommrow
from pullers.scanners.ai_scanners import EarningTomorrowAI
from gpt.grok import GrokClient
from gpt.gemini import GeminiClient
from common.user_agent import UserAgentManager


class Container(containers.DeclarativeContainer):
    """Application dependency injection container.
    
    Manages all application services and their dependencies following
    the same pattern as C# dependency injection containers.
    
    All services are registered as singletons - instances are created once
    and reused throughout the application lifecycle.
    """

    # Configuration (injected from settings)
    config = providers.Singleton(
        lambda: settings
    )

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

    # Scanners (all as singletons - created once, reused)
    finviz_scanner = providers.Singleton(
        EarningTommrow,
        http_client=http_client,
    )

    # AI-powered scanner (uses EarningTomorrow + AI consensus)
    earning_tomorrow_ai_scanner = providers.Singleton(
        EarningTomorrowAI,
        http_client=http_client,
        earnings_scanner=finviz_scanner,
        grok_client=None,  # Will be injected from container at runtime
        gemini_client=None,  # Will be injected from container at runtime
    )

    # AI Clients (all as singletons - created once, reused)
    grok_client = providers.Singleton(
        GrokClient,
        http_client=http_client,
    )

    gemini_client = providers.Singleton(
        GeminiClient,
        http_client=http_client,
    )

    # Generic scanner provider (can inject any scanner implementation)
    scanner = providers.Factory(
        lambda: Container.finviz_scanner(),  # Default to finviz
        # Can be overridden by calling: container.scanner.override(some_other_scanner)
    )


# Create global container instance
container = Container()
