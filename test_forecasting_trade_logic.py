"""Tests for deterministic RR trade simulator behavior."""
from __future__ import annotations

import unittest

import pandas as pd

from backtests.forecasting.config import TradeLogicConfig
from backtests.forecasting.trade_logic import run_trade_simulation_for_ticker


class ForecastTradeLogicTests(unittest.TestCase):
    def _bars_sl_tp_same_candle(self) -> pd.DataFrame:
        index = pd.date_range("2026-01-01", periods=6, freq="h")
        return pd.DataFrame(
            {
                "Open": [100, 101, 101, 101, 101, 101],
                "High": [101, 106, 102, 102, 102, 102],
                "Low": [99, 99, 100, 100, 100, 100],
                "Close": [100, 101, 101, 101, 101, 101],
                "Volume": [1000, 1000, 1000, 1000, 1000, 1000],
            },
            index=index,
        )

    def _bars_no_hit_for_max_hold(self) -> pd.DataFrame:
        index = pd.date_range("2026-01-01", periods=7, freq="h")
        return pd.DataFrame(
            {
                "Open": [100, 101, 101, 101, 101, 101, 101],
                "High": [101.2, 103.0, 103.5, 104.0, 104.0, 104.0, 104.0],
                "Low": [99.8, 100.3, 100.4, 100.5, 100.6, 100.6, 100.6],
                "Close": [100.5, 101.1, 101.2, 101.3, 101.4, 101.4, 101.4],
                "Volume": [1000] * 7,
            },
            index=index,
        )

    def test_entry_uses_next_open_and_tie_is_sl_first(self) -> None:
        bars = self._bars_sl_tp_same_candle()
        predictions = pd.DataFrame(
            {
                "pred_target_c3": [0.15],
                "atr_14": [1.0],
            },
            index=[bars.index[0]],
        )

        cfg = TradeLogicConfig(
            atr_multiplier=1.0,
            rr_ratio=4.0,
            min_edge=-1.0,
            min_tp_prob=0.0,
            max_hold_candles=3,
            slippage_ticks=0.0,
            tick_size=0.01,
            tie_break_rule="SL_FIRST",
        )

        _, trades = run_trade_simulation_for_ticker(
            ticker="AAPL",
            bars=bars,
            predictions=predictions,
            trade_cfg=cfg,
            sigma_c3=0.02,
            initial_capital=10_000.0,
        )

        self.assertEqual(len(trades), 1)
        trade = trades.iloc[0]
        self.assertEqual(pd.Timestamp(trade["entry_time"]), bars.index[1])
        self.assertAlmostEqual(float(trade["entry_price"]), 101.0, places=8)
        self.assertAlmostEqual(float(trade["stop_price"]), 100.0, places=8)
        self.assertAlmostEqual(float(trade["take_profit_price"]), 105.0, places=8)
        self.assertEqual(str(trade["exit_reason"]), "SL")

    def test_max_hold_exit_after_three_candles(self) -> None:
        bars = self._bars_no_hit_for_max_hold()
        predictions = pd.DataFrame(
            {
                "pred_target_c3": [0.10],
                "atr_14": [1.0],
            },
            index=[bars.index[0]],
        )

        cfg = TradeLogicConfig(
            atr_multiplier=1.0,
            rr_ratio=4.0,
            min_edge=-1.0,
            min_tp_prob=0.0,
            max_hold_candles=3,
            slippage_ticks=0.0,
            tick_size=0.01,
        )

        _, trades = run_trade_simulation_for_ticker(
            ticker="MSFT",
            bars=bars,
            predictions=predictions,
            trade_cfg=cfg,
            sigma_c3=0.02,
            initial_capital=10_000.0,
        )

        self.assertEqual(len(trades), 1)
        trade = trades.iloc[0]
        self.assertEqual(str(trade["exit_reason"]), "MAX_HOLD")
        self.assertEqual(int(trade["entry_idx"]), 1)
        self.assertEqual(int(trade["exit_idx"]), 3)


if __name__ == "__main__":
    unittest.main()
