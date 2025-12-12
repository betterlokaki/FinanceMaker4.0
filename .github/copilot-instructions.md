
# FinanceMaker 4.0 — AI Agent Instructions

## Project Purpose
FinanceMaker 4.0 is an async Python system for **automated stock trading** using AI consensus (Grok + Gemini) to select earnings stocks and execute trades via Interactive Brokers. It is designed for NYSE market hours and robust, repeatable operation.

## Architecture Overview
- **DI Container:** All services are singletons, registered in `common/di_container.py` using `dependency-injector`. Never instantiate directly—always use the container.
- **Settings:** Config is merged from `.env` (secrets) and `config.yaml` (non-secrets) via Pydantic in `common/settings.py`.
- **Scanners:** In `pullers/scanners/`, abstract base classes define the interface. Concrete implementations include Finviz and AI consensus (`EarningTommrowAI`).
- **AI Clients:** In `gpt/`, with `GPTBase` as the abstract base. `GrokClient` and `GeminiClient` are concrete implementations.
- **Realtime Data:** `pullers/realtime/` provides Yahoo Finance WebSocket tick data.
- **Broker:** `publishers/` contains Interactive Brokers integration.
- **Strategy:** `strategy/` implements trading logic, including candle building and order rules.
- **Scheduler:** `scheduler/` manages NYSE-aware execution loops.

## Data Flow
1. **Scanners** (Finviz, AI) → 2. **AI Analysis** (Grok, Gemini) → 3. **Consensus** (intersection) → 4. **Strategy** (candle logic) → 5. **Broker** (order execution)

## Key Patterns & Conventions
- **Interface → Abstract → Concrete:**
    - Example: `IScanner` (Protocol) → `ScannerBase` (ABC) → `FinvizScanner` → `EarningTommrow`
- **All helpers/utilities** go in `common/helpers/` (never in feature folders).
- **Type hints are mandatory** everywhere. No `Any` unless documented.
- **Async consistency:** All I/O is async; never mix sync/async in the same class.
- **Error handling:** Let exceptions bubble up; do not swallow errors.
- **Naming:**
    - Protocols/interfaces: `I*` (e.g., `IScanner`)
    - Abstract base: `*Base` (e.g., `ScannerBase`)
    - Concrete: Descriptive (e.g., `FinvizScanner`)
    - Note: Typo in `earning_tommrow` is intentional for consistency.

## EarningStrategy Example
- Run `EarningTommrowAI` scanner **twice** for consensus.
- Wait until 9:35 AM NY (5 min after open).
- On first 5-min candle per ticker:
    - Entry = candle low - 1%
    - Stop = entry - 4%
    - Take profit = entry + 8%
    - One order per ticker per day.

## Adding New Components
- **Scanner:** Inherit from `ScannerBase`, implement `scan`, register in DI.
- **AI Provider:** Inherit from `GPTBase`, implement `generate_text`, add config, register in DI.
- **Broker:** Inherit from `BrokerBase`, implement all methods, register in DI.
- **Strategy:** Inherit from `RealTimeTradingBase`, implement `load_tickers` and `on_candle`, register in DI.

## AI Consensus Pattern
- Both Grok and Gemini must agree for a stock to be traded.
- Use `common/helpers/ai_consensus_helpers.py` for consensus logic.

## Developer Workflows
- **Activate venv:** `source .venv/bin/activate`
- **Run main pipeline:** `python main.py`
- **Test IBKR connection:** See code example in `copilot-instructions.md`.
- **Config:** Secrets in `.env`, settings in `config.yaml`.

## Integration Points
- **External APIs:**
    - Grok (xAI SDK)
    - Gemini (Google GenAI)
    - Interactive Brokers (ibind)
    - Yahoo Finance (websockets)
- **All API keys and credentials** are loaded via `.env` and injected via DI.

## Reference Files
- `main.py` — entry point
- `common/di_container.py` — DI setup
- `common/settings.py` — config
- `pullers/scanners/ai_scanners/earning_tommrow_ai.py` — AI consensus scanner
- `strategy/earning_strategy/earning_strategy.py` — main trading logic

---
**For more details, see the full file for examples and advanced usage.**
