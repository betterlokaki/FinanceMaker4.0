"""Application configuration using pydantic-settings.

Best practice Python configuration pattern:
- Pydantic for validation and type safety
- YAML for settings (readable, hierarchical)
- .env for secrets (not committed to git)
- Runtime merge of both sources
"""
from pathlib import Path
from typing import List, Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


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
    prompt_template: str = Field(
        default="From following tickers: {TICKERS}\n\nWhich ones do you suggest for trading today? "
                "Please provide only the ticker symbols, one per line."
    )
    extraction_method: str = Field(
        default="line_based",
        description="Method to extract tickers from AI responses (line_based, json, csv)"
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
    )

    # Service configurations
    finviz: FinvizConfig = Field(default_factory=FinvizConfig)
    grok: GrokConfig = Field(default_factory=GrokConfig)
    gemini: GeminiConfig = Field(default_factory=GeminiConfig)
    http: HttpConfig = Field(default_factory=HttpConfig)
    user_agent: UserAgentConfig = Field(default_factory=UserAgentConfig)
    ai_scanner: AIScannerConfig = Field(default_factory=AIScannerConfig)

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


# Create settings instance with YAML support
def create_settings() -> Settings:
    """Create and validate application settings.
    
    Loads configuration from:
    1. config.yaml (project root)
    2. .env file
    3. Environment variables
    
    Returns:
        Validated Settings instance.
    """
    # Load YAML config
    config_path = Path(__file__).parent.parent / "config.yaml"
    yaml_config = load_yaml_config(config_path)
    
    # Load .env file explicitly to ensure it's loaded
    env_path = Path(__file__).parent.parent / ".env"
    if env_path.exists():
        from dotenv import load_dotenv
        load_dotenv(env_path)
    
    # Create settings with YAML data merged
    settings = Settings(**yaml_config)
    
    return settings


# Global settings instance
settings = create_settings()
