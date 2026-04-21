"""Tests for target construction utilities."""
from __future__ import annotations

import unittest

import pandas as pd

from backtests.forecasting.targets import make_ohlc_return_targets, target_columns_for_horizon


class ForecastTargetsTests(unittest.TestCase):
    def test_target_alignment_for_next_three_candles(self) -> None:
        index = pd.date_range("2025-01-01", periods=6, freq="h")
        df = pd.DataFrame(
            {
                "Open": [100, 101, 102, 103, 104, 105],
                "High": [101, 102, 103, 104, 105, 106],
                "Low": [99, 100, 101, 102, 103, 104],
                "Close": [100, 101, 102, 103, 104, 105],
                "Volume": [1000, 1000, 1000, 1000, 1000, 1000],
            },
            index=index,
        )

        out = make_ohlc_return_targets(df, horizon=3)

        expected_o1 = (df["Open"].iloc[1] / df["Close"].iloc[0]) - 1.0
        expected_h2 = (df["High"].iloc[2] / df["Close"].iloc[0]) - 1.0
        expected_c3 = (df["Close"].iloc[3] / df["Close"].iloc[0]) - 1.0

        self.assertAlmostEqual(float(out.iloc[0]["target_o1"]), float(expected_o1), places=8)
        self.assertAlmostEqual(float(out.iloc[0]["target_h2"]), float(expected_h2), places=8)
        self.assertAlmostEqual(float(out.iloc[0]["target_c3"]), float(expected_c3), places=8)

    def test_target_column_order_is_stable(self) -> None:
        cols = target_columns_for_horizon(3)
        self.assertEqual(
            cols,
            [
                "target_o1",
                "target_h1",
                "target_l1",
                "target_c1",
                "target_o2",
                "target_h2",
                "target_l2",
                "target_c2",
                "target_o3",
                "target_h3",
                "target_l3",
                "target_c3",
            ],
        )


if __name__ == "__main__":
    unittest.main()
