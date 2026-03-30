"""Unit tests for RSI extreme strategy behavior."""
from __future__ import annotations

import unittest

import pandas as pd
from backtesting import Backtest

from backtests.backtesting_py.rsi_extreme_rr_strategy import RsiExtremeRRStrategy


def _build_ohlcv_from_close(close_values: list[float]) -> pd.DataFrame:
    index = pd.date_range("2025-01-01", periods=len(close_values), freq="h")
    close = pd.Series(close_values, index=index, dtype=float)
    open_ = close.shift(1).fillna(close.iloc[0])
    high = pd.concat([open_, close], axis=1).max(axis=1) * 1.002
    low = pd.concat([open_, close], axis=1).min(axis=1) * 0.998
    volume = pd.Series(1000.0, index=index, dtype=float)
    return pd.DataFrame(
        {
            "Open": open_.values,
            "High": high.values,
            "Low": low.values,
            "Close": close.values,
            "Volume": volume.values,
        },
        index=index,
    )


class RsiExtremeRRStrategyTests(unittest.TestCase):
    def test_compute_exit_prices_matches_3_to_1_risk_reward(self) -> None:
        long_sl, long_tp = RsiExtremeRRStrategy.compute_exit_prices(
            entry_price=100.0,
            is_long=True,
            stop_loss_pct=0.02,
            risk_reward_ratio=3.0,
        )
        short_sl, short_tp = RsiExtremeRRStrategy.compute_exit_prices(
            entry_price=100.0,
            is_long=False,
            stop_loss_pct=0.02,
            risk_reward_ratio=3.0,
        )

        self.assertAlmostEqual(long_sl, 98.0, places=8)
        self.assertAlmostEqual(long_tp, 106.0, places=8)
        self.assertAlmostEqual(short_sl, 102.0, places=8)
        self.assertAlmostEqual(short_tp, 94.0, places=8)

    def test_long_only_mode_produces_only_long_trades(self) -> None:
        close_values = [
            100, 99, 98, 97, 96, 95, 94, 93, 92, 91,
            90, 89, 88, 87, 86, 85, 84, 83, 82, 81,
            80, 82, 84, 86, 88, 90, 92, 94,
        ]
        df = _build_ohlcv_from_close(close_values)
        bt = Backtest(
            data=df,
            strategy=RsiExtremeRRStrategy,
            cash=10_000.0,
            commission=0.0,
            margin=1.0,
            trade_on_close=False,
            hedging=False,
            exclusive_orders=False,
            finalize_trades=True,
        )
        stats = bt.run(
            trade_direction="Long Only",
            rsi_period=5,
            rsi_oversold=35.0,
            rsi_overbought=65.0,
            stop_loss_pct=0.02,
            risk_reward_ratio=3.0,
            use_full_equity_sizing=True,
            full_equity_fraction=0.999999,
            use_limit_entry=False,
        )
        trades = stats.get("_trades", pd.DataFrame())
        self.assertFalse(trades.empty)
        self.assertTrue((trades["Size"] > 0).all())

    def test_short_only_mode_produces_only_short_trades(self) -> None:
        close_values = [
            100, 101, 102, 103, 104, 105, 106, 107, 108, 109,
            110, 111, 112, 113, 114, 115, 116, 117, 118, 119,
            120, 118, 116, 114, 112, 110, 108, 106,
        ]
        df = _build_ohlcv_from_close(close_values)
        bt = Backtest(
            data=df,
            strategy=RsiExtremeRRStrategy,
            cash=10_000.0,
            commission=0.0,
            margin=1.0,
            trade_on_close=False,
            hedging=False,
            exclusive_orders=False,
            finalize_trades=True,
        )
        stats = bt.run(
            trade_direction="Short Only",
            rsi_period=5,
            rsi_oversold=35.0,
            rsi_overbought=65.0,
            stop_loss_pct=0.02,
            risk_reward_ratio=3.0,
            use_full_equity_sizing=True,
            full_equity_fraction=0.999999,
            use_limit_entry=False,
        )
        trades = stats.get("_trades", pd.DataFrame())
        self.assertFalse(trades.empty)
        self.assertTrue((trades["Size"] < 0).all())

    def test_activation_time_blocks_pre_start_entries(self) -> None:
        close_values = [
            100, 99, 98, 97, 96, 95, 94, 93, 92, 91,
            90, 89, 88, 87, 86, 85, 84, 83, 82, 81,
            80, 82, 84, 86, 88, 90, 92, 94,
        ]
        df = _build_ohlcv_from_close(close_values)
        activation_time = df.index[12]
        bt = Backtest(
            data=df,
            strategy=RsiExtremeRRStrategy,
            cash=10_000.0,
            commission=0.0,
            margin=1.0,
            trade_on_close=False,
            hedging=False,
            exclusive_orders=False,
            finalize_trades=True,
        )
        stats = bt.run(
            trade_direction="Long Only",
            rsi_period=5,
            rsi_oversold=35.0,
            rsi_overbought=65.0,
            stop_loss_pct=0.02,
            risk_reward_ratio=3.0,
            use_full_equity_sizing=True,
            full_equity_fraction=0.999999,
            use_limit_entry=False,
            activation_time_utc=activation_time.isoformat(),
        )
        trades = stats.get("_trades", pd.DataFrame())
        self.assertFalse(trades.empty)
        self.assertTrue((pd.to_datetime(trades["EntryTime"]) >= activation_time).all())

    def test_oracle_mode_is_disabled(self) -> None:
        close_values = [
            100, 101, 102, 103, 104, 103, 102, 101, 100, 99,
            98, 99, 100, 101, 102, 101, 100, 99, 98, 97,
            98, 99, 100, 101, 102, 103, 104, 105,
        ]
        df = _build_ohlcv_from_close(close_values)
        bt = Backtest(
            data=df,
            strategy=RsiExtremeRRStrategy,
            cash=10_000.0,
            commission=0.0,
            margin=1.0,
            trade_on_close=False,
            hedging=False,
            exclusive_orders=False,
            finalize_trades=True,
        )

        with self.assertRaises(ValueError) as ctx:
            bt.run(
                trade_direction="Both",
                oracle_mode_enabled=True,
            )
        self.assertIn("disabled", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
