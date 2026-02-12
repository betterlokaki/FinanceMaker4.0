"""Application configuration using pydantic-settings.

Best practice Python configuration pattern:
- Pydantic for validation and type safety
- YAML for settings (readable, hierarchical)
- .env for secrets (not committed to git)
- GCP Secret Manager for Cloud Run deployments
- Runtime merge of both sources
"""
import logging
import os
from pathlib import Path
from typing import List, Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from common.models.order import TimeInForce

logger: logging.Logger = logging.getLogger(__name__)


class FinvizConfig(BaseSettings):
    """Finviz screener configuration."""
    base_url: str = Field(default="https://finviz.com/screener.ashx?v=111")
    timeout: float = Field(default=30.0)
    max_pages: int = Field(default=30)
    results_per_page: int = Field(default=20)
    max_connections: int = Field(default=5)
    max_keepalive_connections: int = Field(default=2)


class GrokConfig(BaseSettings):
    """Grok AI API configuration."""
    model_config = SettingsConfigDict(
        env_prefix="GROK_",
        case_sensitive=False,
    )
    base_url: str = Field(default="https://api.x.ai/v1")
    api_key: str = Field(default="", description="Grok API key from .env")
    model: str = Field(default="grok-beta")
    timeout: float = Field(default=30.0)
    max_tokens: int = Field(default=1000)


class GeminiConfig(BaseSettings):
    """Google Gemini API configuration."""
    model_config = SettingsConfigDict(
        env_prefix="GEMINI_",
        case_sensitive=False,
    )
    base_url: str = Field(default="https://generativelanguage.googleapis.com/v1beta/openai/")
    api_key: str = Field(default="", description="Gemini API key from .env")
    model: str = Field(default="gemini-2.0-flash")
    timeout: float = Field(default=30.0)
    max_tokens: int = Field(default=1000)


class HttpConfig(BaseSettings):
    """HTTP client configuration."""
    timeout: float = Field(default=30.0)
    follow_redirects: bool = Field(default=True)
    max_connections: int = Field(default=5)
    max_keepalive_connections: int = Field(default=2)


class UserAgentConfig(BaseSettings):
    """User-agent configuration."""
    enabled: bool = Field(default=True)
    rotation_enabled: bool = Field(default=True)
    custom_agents: List[str] = Field(default_factory=list)


class AIScannerConfig(BaseSettings):
    """AI scanner configuration."""
    scan_passes: int = Field(
        default=2,
        description="Number of AI consensus scan passes to run"
    )
    prompt_template: str = Field(
        default="From following tickers: {TICKERS}\n\nWhich ones do you suggest for trading today? "
                "Please provide only the ticker symbols, one per line."
    )
    extraction_method: str = Field(
        default="line_based",
        description="Method to extract tickers from AI responses (line_based, json, csv)"
    )


class IBKRConfig(BaseSettings):
    """Interactive Brokers API configuration."""
    model_config = SettingsConfigDict(
        env_prefix="IBKR_",
        case_sensitive=False,
    )
    access_token: str = Field(default="", description="IBKR OAuth access token")
    access_token_secret: str = Field(default="", description="IBKR OAuth access token secret")
    consumer_key: str = Field(default="", description="IBKR OAuth consumer key")
    dh_param_path: str = Field(default="", description="Path to DH param file")
    encryption_key_path: str = Field(default="", description="Path to encryption key file")
    signature_key_path: str = Field(default="", description="Path to signature key file")
    listing_exchange: str = Field(default="SMART", description="Default listing exchange")
    outside_rth: bool = Field(default=True, description="Allow outside regular trading hours")


class YahooConfig(BaseSettings):
    """Yahoo Finance API configuration."""
    base_url: str = Field(default="https://query1.finance.yahoo.com")
    timeout: float = Field(default=30.0)
    max_retries: int = Field(default=3)
    intraday_chunk_days: int = Field(default=7, description="Chunk size for intraday requests")


class RealtimeConfig(BaseSettings):
    """Real-time WebSocket data feed configuration."""
    base_url: str = Field(
        default="wss://streamer.finance.yahoo.com/?version=2",
        description="WebSocket URL for real-time data"
    )
    reconnect_delay: float = Field(
        default=1.0,
        description="Initial delay between reconnection attempts (seconds)"
    )
    max_reconnect_attempts: int = Field(
        default=5,
        description="Maximum number of reconnection attempts"
    )


class SchedulerConfig(BaseSettings):
    """Trading scheduler configuration."""
    exchange: str = Field(
        default="XNYS",
        description="Exchange calendar code (XNYS = NYSE)"
    )
    timezone: str = Field(
        default="America/New_York",
        description="Market timezone"
    )
    strategy_max_retries: int = Field(
        default=3,
        description="Max retry attempts for failed strategies"
    )
    strategy_retry_delay: float = Field(
        default=5.0,
        description="Delay between strategy retry attempts (seconds)"
    )


class CacheConfig(BaseSettings):
    """Ticker cache configuration."""
    enabled: bool = Field(
        default=True,
        description="Enable/disable ticker caching"
    )
    cache_dir: str = Field(
        default="./cache/tickers",
        description="Directory path for cache files"
    )


class DemandZoneStrategyConfig(BaseSettings):
    """Demand zone strategy configuration."""
    finviz_url: str = Field(
        default="https://finviz.com/screener.ashx?v=111&f=cap_midover%2Cfa_epsqoq_pos%2Cta_perf_13wdown%2Cta_sma200_pa&ft=4",
        description="Finviz screener URL for demand zone scanning"
    )
    trade_value: float = Field(
        default=3000.0,
        description="Trade value per order in USD"
    )
    run_time: str = Field(
        default="14:30",
        description="Run time in Israel timezone (HH:MM format)"
    )
    prompt_template: str = Field(
        default="From following tickers: {TICKERS}\n\nWhich ones do you suggest for trading today? "
                "Please provide only the ticker symbols, one per line.",
        description="AI prompt template with {TICKERS} placeholder"
    )


class PortfolioAllocationConfig(BaseSettings):
    """Portfolio allocation configuration for strategies."""
    strategy_allocation_pct: float = Field(
        default=0.5,
        description="Percentage of total buying power allocated per strategy (0.5 = 50%)"
    )
    ticker_allocation_pct: float = Field(
        default=0.33,
        description="Percentage of strategy's buying power allocated per ticker (0.33 = 33% of strategy's allocation)"
    )


class DynamicStopLossConfig(BaseSettings):
    """Dynamic stop loss manager configuration."""
    limit_sell_offset_pct: float = Field(
        default=0.5,
        description="Percentage below current price for LIMIT SELL to ensure fill ORH"
    )


class OrderParamsConfig(BaseSettings):
    """Order parameters configuration for trading strategies."""
    buy_limit_tif: TimeInForce = Field(
        default=TimeInForce.DAY,
        description="Time in force for buy limit orders (DAY = cancel at end of day)"
    )
    buy_limit_rth: bool = Field(
        default=True,
        description="Buy limit orders execute only during regular trading hours (RTH=true)"
    )
    stop_loss_tif: TimeInForce = Field(
        default=TimeInForce.GTC,
        description="Time in force for stop loss orders (GTC = good till cancelled)"
    )
    stop_loss_rth: bool = Field(
        default=False,
        description="Stop loss RTH setting (Note: Stop loss is always RTH-only in implementation, this config is for future use)"
    )
    take_profit_tif: TimeInForce = Field(
        default=TimeInForce.GTC,
        description="Time in force for take profit orders (GTC = good till cancelled)"
    )
    take_profit_rth: bool = Field(
        default=True,
        description="Take profit orders execute only during regular trading hours (RTH=true)"
    )


class Settings(BaseSettings):
    """Main application settings.
    
    Loads configuration from:
    1. .env file (secrets, API keys)
    2. config.yaml (settings, defaults)
    3. Environment variables (overrides)
    
    Priority: Environment variables > .env > config.yaml > defaults
    """
    
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        env_nested_delimiter="__",  # For nested env vars: HTTP__TIMEOUT=60
        extra="ignore",  # Ignore extra fields from config.yaml
    )

    # Service configurations
    finviz: FinvizConfig = Field(default_factory=FinvizConfig)
    grok: GrokConfig = Field(default_factory=GrokConfig)
    gemini: GeminiConfig = Field(default_factory=GeminiConfig)
    http: HttpConfig = Field(default_factory=HttpConfig)
    user_agent: UserAgentConfig = Field(default_factory=UserAgentConfig)
    ai_scanner: AIScannerConfig = Field(default_factory=AIScannerConfig)
    ibkr: IBKRConfig = Field(default_factory=IBKRConfig)
    yahoo: YahooConfig = Field(default_factory=YahooConfig)
    realtime: RealtimeConfig = Field(default_factory=RealtimeConfig)
    scheduler: SchedulerConfig = Field(default_factory=SchedulerConfig)
    cache: CacheConfig = Field(default_factory=CacheConfig)
    demand_zone_strategy: DemandZoneStrategyConfig = Field(default_factory=DemandZoneStrategyConfig)
    portfolio_allocation: PortfolioAllocationConfig = Field(default_factory=PortfolioAllocationConfig)
    order_params: OrderParamsConfig = Field(default_factory=OrderParamsConfig)
    dynamic_stop_loss: DynamicStopLossConfig = Field(default_factory=DynamicStopLossConfig)

    # Application settings
    debug: bool = Field(default=False)
    log_level: str = Field(default="INFO")


# Load settings from config.yaml manually (pydantic-settings doesn't load YAML by default)
def load_yaml_config(config_path: Path) -> dict:
    """Load YAML configuration file.
    
    Args:
        config_path: Path to config.yaml file.
        
    Returns:
        Dictionary of configuration values.
    """
    import yaml
    
    if not config_path.exists():
        return {}
    
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _load_secrets_from_gcp() -> None:
    """Load secrets from GCP Secret Manager and set as environment variables.
    
    In Cloud Run, secrets are mounted as environment variables or files by Cloud Run itself.
    This function is a no-op - Cloud Run handles secret mounting natively.
    We just need to ensure the environment variables are set correctly.
    """
    # Cloud Run mounts secrets directly as environment variables or files
    # No code needed - secrets are available via os.getenv() automatically
    # PEM files are mounted as files at paths specified in Cloud Run deployment
    logger.debug("Using Cloud Run native secret mounting - no code changes needed")


# Create settings instance with YAML support
def create_settings() -> Settings:
    """Create and validate application settings.
    
    Loads configuration from:
    1. GCP Secret Manager (if running in Cloud Run)
    2. config.yaml (project root)
    3. .env file (local development)
    4. Environment variables (overrides)
    
    Returns:
        Validated Settings instance.
    """
    # Load secrets from GCP Secret Manager first (if in Cloud Run)
    _load_secrets_from_gcp()
    
    # Load YAML config
    config_path = Path(__file__).parent.parent / "config.yaml"
    yaml_config = load_yaml_config(config_path)
    
    # Load .env file explicitly to ensure it's loaded (for local development)
    env_path = Path(__file__).parent.parent / ".env"
    if env_path.exists():
        from dotenv import load_dotenv
        load_dotenv(env_path)
    
    # Create settings with YAML data merged
    settings = Settings(**yaml_config)
    
    return settings


# Global settings instance
settings = create_settings()
