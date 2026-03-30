"""Unit tests for isolated backtest engine helpers."""
from __future__ import annotations

import unittest

import pandas as pd

from backtests.backtesting_py.isolated_backtest_engine import (
    build_single_buy_and_hold_equity,
    filter_regular_session,
    resolve_tickers,
)


class IsolatedBacktestEngineTests(unittest.TestCase):
    def test_resolve_tickers_normalizes_and_dedupes(self) -> None:
        tickers = resolve_tickers(
            ["aapl, msft", "AAPL", " nvda "],
            default_tickers=["TSLA"],
        )
        self.assertEqual(tickers, ["AAPL", "MSFT", "NVDA"])

    def test_filter_regular_session_trims_outside_hours(self) -> None:
        et_index = pd.DatetimeIndex(
            [
                "2025-01-06 09:29:00",
                "2025-01-06 09:30:00",
                "2025-01-06 15:59:00",
                "2025-01-06 16:00:00",
            ],
            tz="America/New_York",
        )
        utc_naive_index = et_index.tz_convert("UTC").tz_localize(None)
        df = pd.DataFrame(
            {
                "Open": [1.0, 2.0, 3.0, 4.0],
                "High": [1.0, 2.0, 3.0, 4.0],
                "Low": [1.0, 2.0, 3.0, 4.0],
                "Close": [1.0, 2.0, 3.0, 4.0],
                "Volume": [100, 100, 100, 100],
            },
            index=utc_naive_index,
        )

        filtered = filter_regular_session(df)
        expected_index = utc_naive_index[[1, 2]]
        self.assertEqual(len(filtered), 2)
        self.assertTrue(filtered.index.equals(expected_index))

    def test_build_single_buy_and_hold_equity_matches_known_values(self) -> None:
        index = pd.date_range("2025-01-01", periods=3, freq="D")
        df = pd.DataFrame(
            {
                "Open": [100.0, 110.0, 121.0],
                "High": [100.0, 110.0, 121.0],
                "Low": [100.0, 110.0, 121.0],
                "Close": [100.0, 110.0, 121.0],
                "Volume": [100, 100, 100],
            },
            index=index,
        )
        equity = build_single_buy_and_hold_equity(
            df=df,
            index=index,
            initial_capital=10_000.0,
        )
        self.assertAlmostEqual(float(equity.iloc[0]), 10_000.0, places=8)
        self.assertAlmostEqual(float(equity.iloc[1]), 11_000.0, places=8)
        self.assertAlmostEqual(float(equity.iloc[2]), 12_100.0, places=8)


if __name__ == "__main__":
    unittest.main()
