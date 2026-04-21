"""Forecasting package for MAG7 next-N OHLC prediction and RR simulation."""

from backtests.forecasting.config import (
    ForecastConfig,
    MAG7_TICKERS,
    ModelBundle,
    TradeLogicConfig,
    default_run_id,
)
from backtests.forecasting.evaluate import (
    ForecastBacktestResult,
    run_forecast_inference_backtest,
)
from backtests.forecasting.io import load_model_bundle, save_model_bundle
from backtests.forecasting.models import train_forecast_models

__all__ = [
    "MAG7_TICKERS",
    "ForecastConfig",
    "TradeLogicConfig",
    "ModelBundle",
    "default_run_id",
    "train_forecast_models",
    "load_model_bundle",
    "save_model_bundle",
    "ForecastBacktestResult",
    "run_forecast_inference_backtest",
]
