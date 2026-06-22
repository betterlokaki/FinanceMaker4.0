"""Tests for the Mag7 relative-strength RR backtest strategy."""
from __future__ import annotations

import pandas as pd

from backtests.backtesting_py.mag7_relative_strength_rr_strategy import (
    Mag7RelativeStrengthRRStrategy,
    compute_mag7_relative_strength_features,
)


def _frame(closes: list[float]) -> pd.DataFrame:
    index = pd.date_range("2024-01-01", periods=len(closes), freq="D")
    close = pd.Series(closes, index=index, dtype=float)
    return pd.DataFrame(
        {
            "Open": close,
            "High": close + 1.0,
            "Low": close - 1.0,
            "Close": close,
            "Volume": 1_000_000,
        },
        index=index,
    )


def test_compute_exit_prices_uses_one_to_two_risk_reward() -> None:
    stop_price, take_profit_price = Mag7RelativeStrengthRRStrategy.compute_exit_prices(
        entry_price=100.0,
        atr_value=2.0,
        atr_stop_multiplier=2.0,
        min_stop_pct=0.06,
        max_stop_pct=0.12,
        risk_reward_ratio=2.0,
    )

    assert stop_price == 94.0
    assert take_profit_price == 112.0


def test_relative_strength_features_do_not_depend_on_future_rows() -> None:
    base = {
        "AAPL": _frame([100, 101, 102, 103, 104, 105, 106]),
        "MSFT": _frame([100, 100, 100, 100, 100, 100, 100]),
        "NVDA": _frame([100, 99, 98, 97, 96, 95, 94]),
    }
    changed_future = {ticker: frame.copy() for ticker, frame in base.items()}
    changed_future["NVDA"].iloc[-1, changed_future["NVDA"].columns.get_loc("Close")] = 200.0

    base_features = compute_mag7_relative_strength_features(
        base,
        fast_momentum_bars=1,
        mid_momentum_bars=2,
        slow_momentum_bars=3,
        atr_period=2,
    )
    changed_features = compute_mag7_relative_strength_features(
        changed_future,
        fast_momentum_bars=1,
        mid_momentum_bars=2,
        slow_momentum_bars=3,
        atr_period=2,
    )

    checked_index = base["AAPL"].index[-2]
    assert (
        base_features["AAPL"].at[checked_index, "Mag7Rank"]
        == changed_features["AAPL"].at[checked_index, "Mag7Rank"]
    )
    assert (
        base_features["NVDA"].at[checked_index, "Mag7Score"]
        == changed_features["NVDA"].at[checked_index, "Mag7Score"]
    )
