"""Tests for model training helpers and adapter weighting."""
from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from backtests.forecasting.config import ForecastConfig
from backtests.forecasting.models import build_adapter_sample_weights, train_forecast_models
from backtests.forecasting.targets import target_columns_for_horizon


class ForecastModelsTests(unittest.TestCase):
    def test_adapter_weights_distinguish_target_ticker(self) -> None:
        tickers = ["AAPL", "MSFT", "AAPL", "NVDA"]
        weights = build_adapter_sample_weights(
            tickers,
            target_ticker="AAPL",
            target_weight=4.0,
            other_weight=1.0,
        )
        self.assertEqual(weights.tolist(), [4.0, 1.0, 4.0, 1.0])

    def test_train_forecast_models_builds_base_and_adapters(self) -> None:
        rows = 80
        feature_cols = ["f1", "f2", "f3", "f4"]
        target_cols = target_columns_for_horizon(3)

        rng = np.random.default_rng(7)
        X = pd.DataFrame(rng.normal(size=(rows, len(feature_cols))), columns=feature_cols)
        y = pd.DataFrame(rng.normal(scale=0.01, size=(rows, len(target_cols))), columns=target_cols)
        tickers = pd.Series(["AAPL" if i % 2 == 0 else "MSFT" for i in range(rows)])
        timestamps = pd.Series(pd.date_range("2025-01-01", periods=rows, freq="h"))

        cfg = ForecastConfig(
            tickers=["AAPL", "MSFT"],
            xgb_n_estimators=8,
            xgb_max_depth=3,
            xgb_learning_rate=0.1,
            cv_splits=3,
        )

        bundle = train_forecast_models(
            X=X,
            y=y,
            tickers=tickers,
            timestamps=timestamps,
            feature_columns=feature_cols,
            target_columns=target_cols,
            run_id="unit-test",
            cfg=cfg,
        )

        self.assertEqual(bundle.run_id, "unit-test")
        self.assertEqual(sorted(bundle.adapter_models.keys()), ["AAPL", "MSFT"])
        self.assertIn("global_sigma", bundle.calibration)
        self.assertIn("target_c3", bundle.calibration["global_sigma"])


if __name__ == "__main__":
    unittest.main()
