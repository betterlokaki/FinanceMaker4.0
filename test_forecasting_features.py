"""Tests for leakage-safe feature engineering."""
from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from backtests.forecasting.features import compute_feature_frame


class ForecastFeaturesTests(unittest.TestCase):
    def _make_bars(self, rows: int = 220) -> pd.DataFrame:
        index = pd.date_range("2025-01-01", periods=rows, freq="h")
        base = np.linspace(100.0, 120.0, rows)
        noise = np.sin(np.arange(rows) / 10.0) * 0.4
        close = base + noise
        open_ = close + np.sin(np.arange(rows) / 17.0) * 0.2
        high = np.maximum(open_, close) + 0.5
        low = np.minimum(open_, close) - 0.5
        volume = 1_000_000 + (np.arange(rows) * 100)
        return pd.DataFrame(
            {
                "Open": open_,
                "High": high,
                "Low": low,
                "Close": close,
                "Volume": volume,
            },
            index=index,
        )

    def test_expected_indicator_columns_exist(self) -> None:
        bars = self._make_bars()
        feat = compute_feature_frame(bars)

        expected = {
            "rsi_14",
            "macd_hist",
            "adx_14",
            "cci_20",
            "ppo",
            "roc_10",
            "stoch_k",
            "stochrsi_k",
            "willr_14",
            "ultosc",
            "atr_14",
            "natr_14",
            "bb_width",
            "obv",
            "adosc",
            "cmf_20",
            "psar_long",
            "supertrend",
        }
        self.assertTrue(expected.issubset(set(feat.columns)))

    def test_future_mutation_does_not_change_past_features(self) -> None:
        bars = self._make_bars()
        baseline = compute_feature_frame(bars)

        modified = bars.copy()
        mutated_idx = modified.index[180]
        modified.loc[mutated_idx, "Close"] = modified.loc[mutated_idx, "Close"] * 8.0
        modified.loc[mutated_idx, "High"] = modified.loc[mutated_idx, "High"] * 8.0
        modified.loc[mutated_idx, "Low"] = modified.loc[mutated_idx, "Low"] * 8.0
        changed = compute_feature_frame(modified)

        compare_idx = bars.index[120]
        before_a = baseline.loc[compare_idx]
        before_b = changed.loc[compare_idx]

        for col in baseline.columns:
            a = before_a[col]
            b = before_b[col]
            if np.isnan(a) and np.isnan(b):
                continue
            self.assertAlmostEqual(float(a), float(b), places=10, msg=col)


if __name__ == "__main__":
    unittest.main()
