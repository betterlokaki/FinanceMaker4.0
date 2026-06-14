# Repo Instructions

## Strategy Creation Routing

When the user asks to create a new strategy, first classify the request:

- Treat `real`, `live`, `realtime`, or `real-time` as a request for a live real-time strategy only.
- Treat `backtest`, `backtesting`, or `backtesting.py` as a request for a backtesting strategy only.
- If the request says only `strategy` without a live/backtesting qualifier, ask which target they want before editing files.

For a live real-time strategy:

- Work only in the live strategy surface unless the user explicitly asks for a backtest too.
- Follow the existing `RealTimeTradingBase` abstraction and event-driven callback style used by:
  - `strategy/mag7_ema_slope_regime_strategy/mag7_ema_slope_regime_live_strategy.py`
  - `strategy/earning_strategy/earning_strategy.py`
  - `strategy/pullback_trading_strategy/pullback_trading_live_strategy.py`
- Put the strategy under `strategy/<name>_strategy/` with package files matching the existing strategy folders.
- Add or update the root live runner and matching root Dockerfile when the new live strategy needs deployment, following `run_live_*` and `Dockerfile.*-alpaca` patterns.
- Do not create, modify, or "keep in sync" files under `backtests/` for a live-only request.

For a backtesting strategy:

- Work only in the backtesting surface unless the user explicitly asks for a live real-time strategy too.
- Follow the `backtesting.Strategy` pattern used by `backtests/backtesting_py/mag7_ema_slope_regime_strategy.py`.
- Put the strategy implementation under `backtests/backtesting_py/` and add/update backtest runner/config/result files only as needed.
- Do not create, modify, or "keep in sync" files under `strategy/`, root `run_live_*` files, or live strategy Dockerfiles for a backtesting-only request.
