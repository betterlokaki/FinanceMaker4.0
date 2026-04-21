"""Configuration models for MAG7 forecasting and RR trade evaluation."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


MAG7_TICKERS: list[str] = [
    "AAPL",
    "MSFT",
    "NVDA",
    "AMZN",
    "META",
    "TSLA",
    "GOOGL",
]


@dataclass(slots=True)
class ForecastConfig:
    """Training and inference config for next-N OHLC forecasting."""

    horizon: int = 3
    tickers: list[str] = field(default_factory=lambda: list(MAG7_TICKERS))
    train_start: str = "2025-01-01"
    train_end: str = "2025-12-31"
    test_start: str = "2026-01-01"
    test_end: str = "2026-02-28"
    warmup_days: int = 120
    xgb_n_estimators: int = 350
    xgb_max_depth: int = 6
    xgb_learning_rate: float = 0.05
    xgb_subsample: float = 0.9
    xgb_colsample_bytree: float = 0.9
    xgb_reg_alpha: float = 0.0
    xgb_reg_lambda: float = 1.0
    xgb_random_state: int = 42
    adapter_weight_target: float = 4.0
    adapter_weight_other: float = 1.0
    cv_splits: int = 3
    initial_capital_per_ticker: float = 10_000.0


@dataclass(slots=True)
class TradeLogicConfig:
    """Config for forecast-driven RR trade execution simulation."""

    atr_multiplier: float = 1.0
    rr_ratio: float = 4.0
    min_edge: float = 0.0003
    min_tp_prob: float = 0.50
    max_hold_candles: int = 3
    long_round_trip_fee: float = 2.5
    short_round_trip_fee: float = 5.0
    slippage_ticks: float = 0.0
    tick_size: float = 0.01
    tie_break_rule: str = "SL_FIRST"


@dataclass(slots=True)
class ModelBundle:
    """In-memory model bundle used by train/test scripts."""

    run_id: str
    feature_columns: list[str]
    target_columns: list[str]
    base_model: Any
    adapter_models: dict[str, Any]
    calibration: dict[str, Any]
    metadata: dict[str, Any]


def default_run_id() -> str:
    """Build a UTC timestamp run id for deterministic artifact folders."""
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
