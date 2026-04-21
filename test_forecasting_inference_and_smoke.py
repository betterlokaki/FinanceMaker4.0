"""Inference purity and end-to-end synthetic smoke tests for forecasting pipeline."""
from __future__ import annotations

import tempfile
import unittest

import numpy as np
import pandas as pd

from backtests.forecasting.config import ForecastConfig, ModelBundle, TradeLogicConfig
from backtests.forecasting.data import build_supervised_panel
from backtests.forecasting.evaluate import run_forecast_inference_backtest
from backtests.forecasting.features import compute_feature_frame
from backtests.forecasting.io import load_model_bundle, save_model_bundle
from backtests.forecasting.models import train_forecast_models
from backtests.forecasting.targets import target_columns_for_horizon


class _NoFitModel:
    def fit(self, X, y):  # pragma: no cover - should never be called
        raise AssertionError("fit should not be called in load-only inference")

    def predict(self, X):
        return np.zeros((len(X), 12), dtype=float)


class ForecastInferenceAndSmokeTests(unittest.TestCase):
    def _make_bars(self, rows: int = 240) -> pd.DataFrame:
        index = pd.date_range("2025-10-01", periods=rows, freq="h")
        trend = np.linspace(100.0, 130.0, rows)
        wiggle = np.sin(np.arange(rows) / 8.0)
        close = trend + wiggle
        open_ = close + np.sin(np.arange(rows) / 16.0) * 0.4
        high = np.maximum(open_, close) + 0.6
        low = np.minimum(open_, close) - 0.6
        volume = 1_000_000 + (np.arange(rows) % 24) * 10_000
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

    def test_load_only_inference_does_not_fit(self) -> None:
        bars = self._make_bars(220)
        features = compute_feature_frame(bars).dropna()
        feature_cols = sorted(features.columns)
        target_cols = target_columns_for_horizon(3)

        bundle = ModelBundle(
            run_id="dummy",
            feature_columns=feature_cols,
            target_columns=target_cols,
            base_model=_NoFitModel(),
            adapter_models={},
            calibration={"global_sigma": {"target_c3": 0.01}, "ticker_sigma": {}},
            metadata={"run_id": "dummy", "horizon": 3, "tickers": ["AAPL"]},
        )

        result = run_forecast_inference_backtest(
            bundle=bundle,
            data_by_ticker={"AAPL": bars},
            forecast_cfg=ForecastConfig(horizon=3, tickers=["AAPL"]),
            trade_cfg=TradeLogicConfig(min_edge=-1.0, min_tp_prob=0.0),
            test_start=pd.Timestamp("2026-01-01", tz="UTC"),
            test_end=pd.Timestamp("2026-01-15", tz="UTC"),
        )
        self.assertIsNotNone(result.predictions)

    def test_synthetic_end_to_end_train_save_load_infer(self) -> None:
        bars_a = self._make_bars(260)
        bars_b = self._make_bars(260) * 1.01
        bars_b.index = bars_a.index
        data = {"AAPL": bars_a, "MSFT": bars_b}

        train_start = pd.Timestamp("2025-10-03", tz="UTC")
        train_end = pd.Timestamp("2025-10-08", tz="UTC")
        panel, feature_cols, target_cols = build_supervised_panel(
            data_by_ticker=data,
            horizon=3,
            start=train_start,
            end=train_end,
        )
        self.assertFalse(panel.empty)

        cfg = ForecastConfig(
            horizon=3,
            tickers=["AAPL", "MSFT"],
            xgb_n_estimators=8,
            xgb_max_depth=3,
            xgb_learning_rate=0.1,
            cv_splits=3,
        )
        bundle = train_forecast_models(
            X=panel[feature_cols],
            y=panel[target_cols],
            tickers=panel["ticker"],
            timestamps=panel["time"],
            feature_columns=feature_cols,
            target_columns=target_cols,
            run_id="smoke",
            cfg=cfg,
        )

        with tempfile.TemporaryDirectory() as tmp:
            model_dir = save_model_bundle(bundle, tmp)
            loaded = load_model_bundle(model_dir)
            self.assertEqual(loaded.run_id, "smoke")

            result = run_forecast_inference_backtest(
                bundle=loaded,
                data_by_ticker=data,
                forecast_cfg=ForecastConfig(horizon=3, tickers=["AAPL", "MSFT"]),
                trade_cfg=TradeLogicConfig(min_edge=-1.0, min_tp_prob=0.0),
                test_start=pd.Timestamp("2025-10-09", tz="UTC"),
                test_end=pd.Timestamp("2025-10-11", tz="UTC"),
            )
            self.assertIn("trade_count", result.summary_jan_feb)
            self.assertIn("win_rate", result.summary_jan_feb)


if __name__ == "__main__":
    unittest.main()
