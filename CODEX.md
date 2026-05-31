# Codex Notes: Adding A New Live Strategy

Use `run_live_strategies_menu.py` as the shared live strategy factory. Both the menu and `run_scheduler.py` consume `create_live_strategies()`, so do not create a new one-off runner unless the strategy needs a genuinely different broker or realtime lifecycle.

## Current Runner Contract

- The runner uses one shared `container.alpaca_broker()` instance.
- The runner uses one shared `container.yahoo_realtime_provider()` instance.
- Every selected strategy receives the same realtime provider. The provider already supports fan-out, so if two strategies subscribe to the same ticker, both strategies receive each tick.
- Strategy initialization is independent. If one strategy fails in `initialize()`, the runner logs it, tries to shut down that failed strategy, and continues with the strategies that initialized successfully.
- If no selected strategy initializes successfully, the runner exits cleanly.
- Live trade size is controlled by `ALPACA_NOTIONAL_PER_TRADE`, defaulting to `14000`.

## Add A Strategy To The Shared Live Factory

1. Implement the strategy as an `ITradingStrategy`.
   - It must expose `initialize()`, `on_tick()`, `shutdown()`, and `is_initialized`.
   - Prefer inheriting from `RealTimeTradingBase` when it uses realtime Yahoo ticks.
   - Use the shared `IBroker` passed in by the runner. Do not instantiate broker clients inside the strategy.

2. Import the strategy in `run_live_strategies_menu.py`.

3. If the strategy needs a dependency that is not already in `LiveStrategyContext`, add it there and wire it once in `run_live_strategies_menu.py` and the `common.di_container.Container.strategies` provider.

4. Add a factory function next to the existing factories:

```python
def _create_new_strategy(context: LiveStrategyContext) -> ITradingStrategy:
    return NewStrategy(
        realtime_provider=context.realtime_provider,
        broker=context.broker,
        notional_per_trade=settings.alpaca.notional_per_trade,
    )
```

5. Add the strategy enum value and one `LiveStrategySpec` in `run_live_strategies_menu.py`:

```python
class LiveStrategySelection(str, Enum):
    MAG7 = "mag7"
    EARNINGS = "earnings"
    NEW = "new"
    BOTH = "both"


STRATEGY_SPECS: tuple[LiveStrategySpec, ...] = (
    # existing specs...
    LiveStrategySpec(
        key=LiveStrategySelection.NEW,
        menu_choice="3",
        label="New Strategy",
        aliases=("new",),
        factory=_create_new_strategy,
    ),
)
```

6. Bump `BOTH_MENU_CHOICE` and `EXIT_MENU_CHOICE` after adding a new numbered option.

7. Add or update tests in `test_run_live_strategies_menu.py`, `test_scheduler_live_strategy_wiring.py`, and the relevant integration test.
   - Verify the new selection creates the strategy.
   - Verify it receives the shared broker and realtime provider.
   - Keep `test_initialize_live_strategies_continues_after_one_failure()` passing.

## Strategy Rules For This Runner

- Use Alpaca for live orders.
- Use simple `OrderRequest` objects through the shared broker.
- Use `settings.alpaca.notional_per_trade` for per-trade sizing unless the user explicitly asks for strategy-specific sizing.
- Check buying power before entering a trade. If one share cannot be bought, skip the trade.
- Keep strategy-specific failures local. Raise during `initialize()` if the strategy cannot start; the runner will keep other strategies alive.
- Do not modify MAG7 production behavior unless the user explicitly asks.

## Validation

Run at least:

```bash
./.venv/bin/python -m pytest test_run_live_strategies_menu.py
./.venv/bin/python -m compileall run_live_strategies_menu.py
```

For broker/order changes, also run:

```bash
./.venv/bin/python -m pytest test_alpaca_order_request_converter.py test_alpaca_broker.py
```
