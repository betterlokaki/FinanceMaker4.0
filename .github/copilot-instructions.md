# FinanceMaker 4.0 - AI Agent Instructions

## Architecture Overview

This is a Python async application for stock analysis using AI consensus from multiple providers (Grok + Gemini). The core flow:

1. **Scanners** scrape stock data (Finviz) → 2. **AI Clients** analyze tickers → 3. **Consensus** returns only stocks recommended by both AIs

### Key Components

| Layer | Location | Purpose |
|-------|----------|---------|
| DI Container | `common/di_container.py` | Singleton management (dependency-injector) |
| Settings | `common/settings.py` | Pydantic config (YAML + .env merge) |
| Scanners | `pullers/scanners/` | Abstract `Scanner` → `FinvizScanner` → `EarningTomorrowAI` |
| AI Clients | `gpt/` | Abstract `GPTBase` → `GrokClient`, `GeminiClient` |

## Conventions & Patterns

### Dependency Injection
All services are **singletons** via `dependency-injector`. Never instantiate directly:
```python
# ✅ Correct - get from container
from common.di_container import container
scanner = container.earning_tomorrow_ai_scanner()

# ❌ Wrong - don't use 'new'
scanner = EarningTomorrowAI()
```

### Adding New Scanners
1. Inherit from `Scanner` abstract class (`pullers/scanners/abstracts/scanner.py`)
2. Implement `async def scan(self, params: ScannerParams) -> List[str]`
3. Register in `common/di_container.py` as a `providers.Singleton`
4. Accept `http_client` as optional param for testability

### Adding New AI Providers
1. Inherit from `GPTBase` (`gpt/abstracts/gpt_base.py`)
2. Implement `async def generate_text(self, prompt: str) -> str`
3. Add config class in `common/settings.py` with `env_prefix`
4. Register in DI container

### Configuration Pattern
```yaml
# config.yaml - non-secrets
grok:
  model: "grok-4-1"
  timeout: 30.0

# .env - secrets only
GROK_API_KEY=xxx
GEMINI_API_KEY=xxx
```
Settings merge: Environment vars > .env > config.yaml > defaults

## File Naming Convention
- Typo in naming: `earning_tommrow` (not `tomorrow`) - maintain consistency with existing files

## Error Handling
Follow "throw first, catch later" - let exceptions bubble up to the caller rather than catching locally:
```python
# ✅ Correct - let it propagate
async def scan(self, params: ScannerParams) -> List[str]:
    tickers = await self._fetch_data()  # Throws on failure
    return tickers

# ❌ Wrong - don't swallow errors silently
async def scan(self, params: ScannerParams) -> List[str]:
    try:
        return await self._fetch_data()
    except Exception:
        return []  # Silent failure
```

## Running the Application

```bash
# Activate venv
source .venv/bin/activate

# Run main scanner
python main.py
```

## Key Dependencies
- `httpx` - Async HTTP client (shared via DI)
- `pydantic-settings` - Configuration management
- `lxml` - HTML parsing for Finviz scraping
- `xai_sdk` - Grok API (uses web_search, x_search, code_execution tools)
- `google-genai` - Gemini API (uses ThinkingConfig + GoogleSearch)

## AI Consensus Pattern (`EarningTomorrowAI`)
The core innovation: Both Grok and Gemini must agree on a stock recommendation.
- `_get_ai_suggestions()` - Sends same prompt to each AI
- `_extract_tickers_from_response()` - Parses JSON or regex from AI output
- `_find_consensus()` - Returns intersection of both suggestion sets
