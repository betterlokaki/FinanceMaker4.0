#!/usr/bin/env python3
"""Train MAG7 next-3-candle OHLC forecaster (base + ticker adapters)."""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ROOT_PATH = str(PROJECT_ROOT)
if ROOT_PATH not in sys.path:
    sys.path.insert(0, ROOT_PATH)

from backtests.forecasting.config import ForecastConfig, default_run_id
from backtests.forecasting.data import build_supervised_panel, fetch_hourly_ohlcv
from backtests.forecasting.io import save_model_bundle
from backtests.forecasting.models import train_forecast_models


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Train MAG7 next-N OHLC forecast models (base + weighted adapters).",
    )
    parser.add_argument(
        "--tickers",
        nargs="+",
        default=ForecastConfig().tickers,
        help="Ticker universe (default MAG7).",
    )
    parser.add_argument("--train-start", type=str, default="2025-01-01")
    parser.add_argument("--train-end", type=str, default="2025-12-31")
    parser.add_argument("--horizon", type=int, default=3)
    parser.add_argument("--warmup-days", type=int, default=120)
    parser.add_argument("--run-id", type=str, default=None)
    parser.add_argument(
        "--model-root",
        type=str,
        default="backtests/results/forecasting/models",
        help="Root directory for model artifacts.",
    )
    parser.add_argument("--xgb-n-estimators", type=int, default=350)
    parser.add_argument("--xgb-max-depth", type=int, default=6)
    parser.add_argument("--xgb-learning-rate", type=float, default=0.05)
    parser.add_argument("--adapter-weight-target", type=float, default=4.0)
    parser.add_argument("--adapter-weight-other", type=float, default=1.0)
    parser.add_argument("--cv-splits", type=int, default=3)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    tickers = [str(t).strip().upper() for t in args.tickers if str(t).strip()]
    train_start = pd.Timestamp(args.train_start, tz="UTC")
    train_end = pd.Timestamp(args.train_end, tz="UTC")
    if train_end < train_start:
        raise SystemExit("--train-end must be >= --train-start")

    cfg = ForecastConfig(
        horizon=max(1, int(args.horizon)),
        tickers=tickers,
        train_start=str(train_start.date()),
        train_end=str(train_end.date()),
        warmup_days=max(0, int(args.warmup_days)),
        xgb_n_estimators=max(10, int(args.xgb_n_estimators)),
        xgb_max_depth=max(2, int(args.xgb_max_depth)),
        xgb_learning_rate=max(1e-4, float(args.xgb_learning_rate)),
        adapter_weight_target=max(1.0, float(args.adapter_weight_target)),
        adapter_weight_other=max(0.0, float(args.adapter_weight_other)),
        cv_splits=max(2, int(args.cv_splits)),
    )
    run_id = str(args.run_id or default_run_id())

    print("=" * 96)
    print("MAG7 FORECAST TRAINING")
    print("=" * 96)
    print(f"Tickers: {', '.join(tickers)}")
    print(f"Train: {train_start.date()} -> {train_end.date()} | Horizon={cfg.horizon}")
    print(f"Warmup days: {cfg.warmup_days}")

    data_by_ticker = fetch_hourly_ohlcv(
        tickers=tickers,
        start=train_start,
        end=train_end,
        warmup_days=cfg.warmup_days,
    )
    if not data_by_ticker:
        raise SystemExit("No OHLCV data fetched for provided tickers.")

    panel, feature_cols, target_cols = build_supervised_panel(
        data_by_ticker=data_by_ticker,
        horizon=cfg.horizon,
        start=train_start,
        end=train_end,
    )
    if panel.empty:
        raise SystemExit("No supervised rows available after feature/target alignment.")

    X = panel[feature_cols]
    y = panel[target_cols]
    ticker_series = panel["ticker"].astype(str)
    timestamps = pd.to_datetime(panel["time"], errors="coerce")

    bundle = train_forecast_models(
        X=X,
        y=y,
        tickers=ticker_series,
        timestamps=timestamps,
        feature_columns=feature_cols,
        target_columns=target_cols,
        run_id=run_id,
        cfg=cfg,
    )
    bundle.metadata.update(
        {
            "train_rows": int(len(panel)),
            "train_start": str(train_start),
            "train_end": str(train_end),
            "warmup_days": int(cfg.warmup_days),
            "indicators": [
                "EMA",
                "RSI",
                "MACD",
                "ADX",
                "CCI",
                "PPO",
                "ROC",
                "STOCH",
                "STOCHRSI",
                "WILLR",
                "ULTOSC",
                "ATR",
                "NATR",
                "BBANDS",
                "OBV",
                "ADOSC",
                "CMF",
                "PSAR",
                "SUPERTREND",
            ],
        }
    )

    model_dir = Path(args.model_root).resolve() / run_id
    saved_dir = save_model_bundle(bundle, model_dir)

    print(f"Train rows: {len(panel):,} | features={len(feature_cols)} | targets={len(target_cols)}")
    print(f"Saved model bundle: {saved_dir}")
    print("=" * 96)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
