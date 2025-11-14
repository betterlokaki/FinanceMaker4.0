"""
CONFIGURATION MANAGEMENT - BEST PRACTICES
==========================================

## Overview

Implemented professional Python configuration management following best practices:
- **Pydantic Settings**: Type-safe configuration with validation
- **YAML Configuration**: Human-readable settings for all services
- **.env Secrets**: Environment-based secrets (API keys) not in git
- **Runtime Merge**: YAML + .env merged at startup
- **Type Safety**: Full type hints and validation

## Configuration Structure

### Files Created:

1. **config.yaml** (Project root)
   - All non-secret configuration settings
   - Service configurations (Finviz, Grok, Gemini)
   - HTTP settings (timeout, connections)
   - User-agent settings
   - Committed to git (no secrets)

2. **.env.example** (Project root)
   - Template for environment variables
   - Shows required API keys and their sources
   - Committed to git as documentation

3. **.env** (Project root - NOT committed)
   - Actual API keys and sensitive values
   - Created by copying .env.example and filling in values
   - Added to .gitignore automatically

4. **common/settings.py**
   - Pydantic configuration classes
   - Settings validation and type hints
   - Loads and merges YAML + .env

## Configuration Classes (Pydantic)

### Service-Specific Configs:

```python
class FinvizConfig(BaseSettings):
    base_url: str = "https://finviz.com/screener.ashx?v=111"
    timeout: float = 30.0
    max_pages: int = 30
    results_per_page: int = 20

class GrokConfig(BaseSettings):
    base_url: str = "https://api.x.ai/v1"
    api_key: str  # From .env
    model: str = "grok-beta"
    timeout: float = 30.0

class GeminiConfig(BaseSettings):
    base_url: str = "https://generativelanguage.googleapis.com/v1beta/openai/"
    api_key: str  # From .env
    model: str = "gemini-2.0-flash"
    timeout: float = 30.0
```

### Main Settings Class:

```python
class Settings(BaseSettings):
    """Loads from:
    1. .env file (secrets)
    2. config.yaml (settings)
    3. Environment variables (overrides)
    """
    finviz: FinvizConfig
    grok: GrokConfig
    gemini: GeminiConfig
    http: HttpConfig
    debug: bool
    log_level: str
```

## Usage Pattern

### Loading Configuration:

```python
from common.settings import settings

# Access any configuration
finviz_timeout = settings.finviz.timeout
grok_api_key = settings.grok.api_key
```

### In Services:

```python
class FinvizScanner(Scanner):
    def __init__(self, http_client=None):
        self._config = settings.finviz
        self.base_url = self._config.base_url
        self.timeout = self._config.timeout
```

### In DI Container:

```python
class Container(containers.DeclarativeContainer):
    config = providers.Singleton(lambda: settings)
    
    http_client = providers.Singleton(
        httpx.AsyncClient,
        timeout=settings.http.timeout,  # From config
    )
```

## Setup Instructions for Users

### 1. Initial Setup:

```bash
# Copy .env template to actual .env
cp .env.example .env

# Edit .env and add your API keys
nano .env  # or vim/code
```

### 2. .env File Format:

```
# .env file (do NOT commit to git)
GROK_API_KEY=your_actual_grok_key_here
GEMINI_API_KEY=your_actual_gemini_key_here
DEBUG=false
LOG_LEVEL=INFO
```

### 3. config.yaml (Always Committed):

```yaml
# config.yaml - committed to git
http:
  timeout: 30.0
  max_connections: 5

finviz:
  base_url: "https://finviz.com/screener.ashx?v=111"
  max_pages: 30

grok:
  model: "grok-beta"
  # api_key: comes from .env

gemini:
  model: "gemini-2.0-flash"
  # api_key: comes from .env
```

## Magic Strings Extracted to Config

### FinvizScanner:
- ✅ Base URL: `https://finviz.com/screener.ashx?v=111`
- ✅ Max pages: `30`
- ✅ Results per page: `20`
- ✅ Request timeout: `30.0` seconds
- ✅ Max connections: `5`

### GrokClient:
- ✅ API Base URL: `https://api.x.ai/v1`
- ✅ Model name: `grok-beta`
- ✅ Max tokens: `1000`
- ✅ Timeout: `30.0` seconds
- ✅ API Key: From `.env` (GROK_API_KEY)

### GeminiClient:
- ✅ API Base URL: `https://generativelanguage.googleapis.com/v1beta/openai/`
- ✅ Model name: `gemini-2.0-flash`
- ✅ Max tokens: `1000`
- ✅ Timeout: `30.0` seconds
- ✅ API Key: From `.env` (GEMINI_API_KEY)

## Configuration Priority (Highest to Lowest)

1. **Environment Variables** (e.g., `export FINVIZ__TIMEOUT=60`)
2. **.env File** (API keys, sensitive values)
3. **config.yaml** (Default settings)
4. **Class Defaults** (Pydantic Field defaults)

This means you can override any setting via environment variables without changing files.

## Environment Variable Format

For nested configurations, use `__` as separator:

```bash
# Sets settings.http.timeout = 60.0
export HTTP__TIMEOUT=60.0

# Sets settings.finviz.max_pages = 50
export FINVIZ__MAX_PAGES=50
```

## Getting API Keys

### Grok API:
1. Go to https://console.x.ai/
2. Create account / login
3. Create API key
4. Add to .env: `GROK_API_KEY=your_key`

### Gemini API:
1. Go to https://ai.google.dev/
2. Create account / login
3. Create API key
4. Add to .env: `GEMINI_API_KEY=your_key`

## Best Practices Implemented

✅ **Separation of Concerns**
- Config file: settings
- Secrets: .env
- Code: uses both without hardcoding

✅ **Type Safety**
- Pydantic validation
- Full type hints
- Catch config errors at startup

✅ **Security**
- .env in .gitignore
- Never commit API keys
- .env.example shows structure

✅ **Flexibility**
- YAML readable format
- Environment variable overrides
- Easy to change without code changes

✅ **Maintainability**
- Centralized configuration
- Clear service configs
- Documented sources

✅ **Scalability**
- Easy to add new services
- Template for new configs
- Consistent pattern

## Future Enhancements

1. Environment-specific configs (dev.yaml, prod.yaml)
2. Config validation schema
3. Configuration hot-reload
4. Encrypted secrets support
5. Database configuration
6. Logging configuration
7. Feature flags
"""
