# FinanceMaker 4.0 - AI Agent Instructions

## Project Overview

FinanceMaker 4.0 is an **automated stock trading system** that uses AI consensus (Grok + Gemini) to identify earnings stocks and execute trades via Interactive Brokers. The system runs during market hours with NYSE calendar awareness.

## Architecture Overview

This is a Python async application for stock analysis using AI consensus from multiple providers (Grok + Gemini). The core flow:

1. **Scanners** scrape stock data (Finviz) → 2. **AI Clients** analyze tickers → 3. **Consensus** returns only stocks recommended by both AIs → 4. **Strategy** builds candles from real-time ticks → 5. **Broker** executes orders

### Key Components

| Layer | Location | Purpose |
|-------|----------|---------|
| DI Container | `common/di_container.py` | Singleton management (dependency-injector) |
| Settings | `common/settings.py` | Pydantic config (YAML + .env merge) |
| Scanners | `pullers/scanners/` | Abstract `ScannerBase` → `FinvizScanner` → `EarningTomorrowAI` |
| AI Clients | `gpt/` | Abstract `GPTBase` → `GrokClient`, `GeminiClient` |
| Realtime | `pullers/realtime/` | WebSocket tick data from Yahoo Finance |
| Broker | `publishers/` | Order execution via Interactive Brokers |
| Strategy | `strategy/` | Trading logic with candle building |
| Scheduler | `scheduler/` | NYSE calendar-aware execution loop |

## Pipeline Flow

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           TRADING PIPELINE                                   │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐                   │
│  │   Finviz     │───▶│   Grok AI    │───▶│              │                   │
│  │   Scanner    │    │   Analysis   │    │   Consensus  │                   │
│  │              │    ├──────────────┤    │   (Both AIs  │                   │
│  │ (Earnings    │    │  Gemini AI   │───▶│   must agree)│                   │
│  │  Tomorrow)   │    │   Analysis   │    │              │                   │
│  └──────────────┘    └──────────────┘    └──────┬───────┘                   │
│                                                  │                           │
│  ┌──────────────┐    ┌──────────────┐    ┌──────▼───────┐                   │
│  │   Yahoo      │◀───│   Strategy   │◀───│   Trading    │                   │
│  │   Realtime   │    │   (Candles)  │    │   Scheduler  │                   │
│  │   WebSocket  │    │              │    │   (NYSE Cal) │                   │
│  └──────┬───────┘    └──────────────┘    └──────────────┘                   │
│         │                                                                    │
│         │            ┌──────────────┐    ┌──────────────┐                   │
│         └───────────▶│   5-min      │───▶│   IBKR       │                   │
│                      │   Candle     │    │   Broker     │                   │
│                      │   (on_tick)  │    │   (Orders)   │                   │
│                      └──────────────┘    └──────────────┘                   │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

## Directory Structure & File Descriptions

### Root Files
| File | Description |
|------|-------------|
| `main.py` | Application entry point - runs AI consensus scanner |
| `config.yaml` | Non-secret configuration (timeouts, models, URLs) |
| `.env` | Secret keys (API keys, IBKR credentials) - gitignored |

### `common/` - Shared Infrastructure
| File | Description |
|------|-------------|
| `di_container.py` | Dependency injection container - all singletons registered here |
| `settings.py` | Pydantic configuration classes - merges YAML + .env |
| `user_agent.py` | User-agent rotation for web scraping |

#### `common/models/` - Data Models
| File | Description |
|------|-------------|
| `candlestick.py` | OHLCV candle data with bullish/bearish helpers |
| `pricing_data.py` | Real-time tick data from WebSocket |
| `order_request.py` | Order request with limit, stop loss, take profit |
| `order_response.py` | Broker response with order ID and status |
| `order.py` | Enums: OrderSide, OrderType, OrderStatus, TimeInForce |
| `portfolio.py` | Portfolio with positions and account summary |
| `position.py` | Individual stock position |
| `scanner_params.py` | Parameters for scanner operations |
| `period.py` | Time period enum (MINUTE, HOUR, DAY) |
| `market_hours.py` | Market session enum (PRE, REGULAR, POST) |

#### `common/helpers/` - Utility Functions
| File | Description |
|------|-------------|
| `market_calendar.py` | NYSE calendar with pre-market/after-hours times |
| `ai_consensus_helpers.py` | Get AI suggestions and find consensus |
| `prompt_helpers.py` | Build prompts for AI analysis |
| `ticker_helpers.py` | Extract tickers from AI responses (JSON/regex) |
| `html_helpers.py` | Parse HTML from Finviz scraper |
| `dh_prime_helper.py` | Extract DH prime for IBKR OAuth |

#### `common/converters/ibkr/` - IBKR Data Conversion
| File | Description |
|------|-------------|
| `order_request_converter.py` | Convert OrderRequest → IBKR format |
| `order_response_converter.py` | Convert IBKR response → OrderResponse |
| `portfolio_converter.py` | Convert IBKR portfolio → Portfolio model |

### `gpt/` - AI Clients
| File | Description |
|------|-------------|
| `abstracts/i_gpt_client.py` | Protocol interface for AI clients |
| `abstracts/gpt_base.py` | Abstract base class implementing IGPTClient |
| `grok/grok_base.py` | Grok client using xAI SDK with web_search tools |
| `gemini/gemini_base.py` | Gemini client with ThinkingConfig + GoogleSearch |

### `pullers/` - Data Ingestion

#### `pullers/scanners/` - Stock Scanners
| File | Description |
|------|-------------|
| `abstracts/i_scanner.py` | Protocol interface for scanners |
| `abstracts/scanner.py` | Abstract base class implementing IScanner |
| `finviz/finviz_base.py` | Finviz scraper base with pagination |
| `finviz/earning_tommrow.py` | Stocks with earnings today + high volume |
| `ai_scanners/earning_tommrow_ai.py` | AI consensus: Grok + Gemini must agree |

#### `pullers/realtime/` - Real-time Data
| File | Description |
|------|-------------|
| `abstracts/i_realtime_provider.py` | Protocol for real-time providers |
| `abstracts/realtime_provider_base.py` | Base with fan-out subscription pattern |
| `yahoo/yahoo_realtime_provider.py` | Yahoo Finance WebSocket with auto-reconnect |
| `yahoo/pricing_data_decoder.py` | Decode protobuf messages from Yahoo |

#### `pullers/market/` - Historical Data
| File | Description |
|------|-------------|
| `abstracts/i_market_provider.py` | Protocol for market data providers |
| `abstracts/market_provider_base.py` | Abstract base for historical data |
| `yahoo/yahoo_market_provider.py` | Yahoo Finance historical OHLCV data |

### `publishers/` - Order Execution
| File | Description |
|------|-------------|
| `abstracts/i_broker.py` | Protocol interface for brokers |
| `abstracts/broker_base.py` | Abstract base implementing IBroker |
| `interactive_brokers/interactive_webapi_broker.py` | IBKR Web API with OAuth |

### `strategy/` - Trading Strategies
| File | Description |
|------|-------------|
| `abstracts/i_trading_strategy.py` | Protocol for trading strategies |
| `abstracts/realtime_trading_base.py` | Base with tick-to-candle building |
| `earning_strategy/earning_strategy.py` | Earnings strategy with AI consensus |

### `scheduler/` - Execution Scheduling
| File | Description |
|------|-------------|
| `abstracts/i_scheduler.py` | Protocol for schedulers |
| `trading_scheduler/trading_scheduler.py` | NYSE hours: 4AM-8PM EST loop |
| `strategy_runner/strategy_runner.py` | Strategy lifecycle with retry logic |

### `secrets/` - Credentials (gitignored)
| File | Description |
|------|-------------|
| `interactive/paper/*.pem` | IBKR paper trading OAuth keys |

## Architecture Patterns

### 1. Dependency Injection
All services are **singletons** via `dependency-injector`. Never instantiate directly:
```python
# ✅ Correct - get from container
from common.di_container import container
strategy = container.earning_strategy()
broker = container.ibkr_broker()

# ❌ Wrong - don't instantiate
strategy = EarningStrategy()
```

### 2. Interface → Abstract → Concrete
Every module follows this pattern:
```
IXxx (Protocol) → XxxBase (ABC, implements IXxx) → ConcreteXxx (implements XxxBase)
```
Example:
```
IScanner → ScannerBase → FinvizScanner → EarningTommrow
                       → EarningTomorrowAI
```

### 3. Configuration Hierarchy
```
Environment vars > .env > config.yaml > defaults
```
- **Secrets** (API keys, tokens) → `.env`
- **Settings** (timeouts, URLs) → `config.yaml`

### 4. Error Handling
"Throw first, catch later" - let exceptions bubble up:
```python
# ✅ Correct - propagate errors
async def scan(self, params: ScannerParams) -> list[str]:
    return await self._fetch_data()  # Throws on failure

# ❌ Wrong - don't swallow errors
async def scan(self, params: ScannerParams) -> list[str]:
    try:
        return await self._fetch_data()
    except Exception:
        return []  # Silent failure
```

## EarningStrategy Logic

The core trading strategy:

1. **Load tickers**: Run `EarningTomorrowAI` scanner **TWICE** for consensus
2. **Wait**: Until 9:35 AM NY (5 min after market open)
3. **On FIRST 5-min candle per ticker**:
   - Entry = candle LOW - 1%
   - Stop Loss = entry - 4%
   - Take Profit = entry + 8%
   - Risk:Reward = 1:2
4. **No duplicates**: One order per ticker per day

```python
# Example calculation
candle.low = 100.00
entry_price = 99.00      # 100 - 1%
stop_loss = 95.04        # 99 - 4%
take_profit = 106.92     # 99 + 8%
```

## Adding New Components

### New Scanner
1. Create `pullers/scanners/xxx/xxx_scanner.py`
2. Inherit from `ScannerBase`
3. Implement `async def scan(self, params: ScannerParams) -> list[str]`
4. Register in `common/di_container.py`

### New AI Provider
1. Create `gpt/xxx/xxx_client.py`
2. Inherit from `GPTBase` (which implements `IGPTClient`)
3. Implement `async def generate_text(self, prompt: str) -> str`
4. Add config class in `common/settings.py` with `env_prefix`
5. Register in DI container

### New Broker
1. Create `publishers/xxx/xxx_broker.py`
2. Inherit from `BrokerBase` (which implements `IBroker`)
3. Implement all abstract methods: `connect`, `place_order`, etc.
4. Register in DI container

### New Strategy
1. Create `strategy/xxx/xxx_strategy.py`
2. Inherit from `RealTimeTradingBase` (which implements `ITradingStrategy`)
3. Implement `load_tickers()` and `on_candle()`
4. Register in DI container and add to `strategies` list

## File Naming Convention
- **Note**: Typo in naming: `earning_tommrow` (not `tomorrow`) - maintain consistency

## Key Dependencies
| Package | Purpose |
|---------|---------|
| `httpx` | Async HTTP client (shared via DI) |
| `pydantic-settings` | Configuration with validation |
| `dependency-injector` | DI container |
| `lxml` | HTML parsing for Finviz |
| `xai_sdk` | Grok API (web_search, x_search, code_execution) |
| `google-genai` | Gemini API (ThinkingConfig + GoogleSearch) |
| `ibind` | IBKR Web API with OAuth |
| `websockets` | Yahoo Finance real-time data |
| `exchange_calendars` | NYSE holiday calendar |

## AI Consensus Pattern (`EarningTomorrowAI`)
The core innovation: Both Grok and Gemini must agree on a stock recommendation.
- `get_ai_suggestions()` - Sends same prompt to each AI
- `extract_tickers_from_response()` - Parses JSON or regex from AI output
- `find_consensus()` - Returns intersection of both suggestion sets

## Configuration Pattern
```yaml
# config.yaml - non-secrets
grok:
  model: "grok-4-1"
  timeout: 30.0

# .env - secrets only
GROK_API_KEY=xxx
GEMINI_API_KEY=xxx
IBKR_ACCESS_TOKEN=xxx
IBKR_SIGNATURE_KEY_PATH=secrets/interactive/paper/private_signature_paper.pem
```
Settings merge: Environment vars > .env > config.yaml > defaults

## Running the Application

```bash
# Activate venv
source .venv/bin/activate

# Run AI consensus scanner
python main.py

# Test IBKR connection
python -c "
import asyncio
from common.di_container import container

async def test():
    broker = container.ibkr_broker()
    await broker.connect()
    print(f'Connected: {broker._account_id}')

asyncio.run(test())
"
```
