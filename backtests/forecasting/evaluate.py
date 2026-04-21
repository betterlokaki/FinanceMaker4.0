"""Inference-time evaluation pipeline for model loading and RR backtest logic."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from backtests.forecasting.config import ForecastConfig, ModelBundle, TradeLogicConfig
from backtests.forecasting.features import compute_feature_frame
from backtests.forecasting.models import predict_with_bundle
from backtests.forecasting.targets import target_columns_for_horizon
from backtests.forecasting.trade_logic import run_trade_simulation_for_ticker


@dataclass(slots=True)
class ForecastBacktestResult:
    predictions: pd.DataFrame
    trades: pd.DataFrame
    summary_jan_feb: dict[str, Any]
    summary_jan_only: dict[str, Any]


def _as_utc_timestamp(value: pd.Timestamp) -> pd.Timestamp:
    ts = pd.Timestamp(value)
    if ts.tzinfo is None:
        return ts.tz_localize("UTC")
    return ts.tz_convert("UTC")


def _predicted_returns_to_prices(
    *,
    base_close: pd.Series,
    pred_returns: pd.DataFrame,
    target_cols: list[str],
) -> pd.DataFrame:
    out = pred_returns.copy()
    for col in target_cols:
        out[f"pred_price_{col}"] = base_close.reindex(out.index) * (1.0 + out[col])
    return out


def _summary_from_trades(
    *,
    trades: pd.DataFrame,
    start: pd.Timestamp,
    end: pd.Timestamp,
    initial_capital_total: float,
) -> dict[str, Any]:
    if trades.empty:
        return {
            "start": str(start),
            "end": str(end),
            "trade_count": 0,
            "win_rate": 0.0,
            "net_pnl": 0.0,
            "net_return_pct": 0.0,
            "monthlyized_return_pct": 0.0,
            "max_drawdown_pct": 0.0,
        }

    ordered = trades.sort_values("exit_time").copy()
    ordered["cum_pnl"] = pd.to_numeric(ordered["net_pnl"], errors="coerce").fillna(0.0).cumsum()
    equity = float(initial_capital_total) + ordered["cum_pnl"]
    roll_max = equity.cummax().replace(0.0, np.nan)
    drawdown = (equity / roll_max) - 1.0

    wins = (pd.to_numeric(ordered["net_pnl"], errors="coerce") > 0.0).sum()
    trade_count = int(len(ordered))
    win_rate = float(wins / trade_count) if trade_count else 0.0

    net_pnl = float(pd.to_numeric(ordered["net_pnl"], errors="coerce").sum())
    net_ret = net_pnl / max(1e-9, float(initial_capital_total))

    window_days = max(1, int((pd.Timestamp(end) - pd.Timestamp(start)).days + 1))
    if net_ret <= -0.999999:
        monthlyized = -1.0
    else:
        monthlyized = (1.0 + net_ret) ** (30.0 / float(window_days)) - 1.0

    return {
        "start": str(pd.Timestamp(start)),
        "end": str(pd.Timestamp(end)),
        "trade_count": trade_count,
        "win_rate": float(win_rate),
        "net_pnl": float(net_pnl),
        "net_return_pct": float(net_ret * 100.0),
        "monthlyized_return_pct": float(monthlyized * 100.0),
        "max_drawdown_pct": float(drawdown.min() * 100.0 if len(drawdown) else 0.0),
    }


def _ticker_sigma_for_close_horizon(bundle: ModelBundle, ticker: str, horizon: int) -> float:
    key = f"target_c{int(horizon)}"
    ticker_key = str(ticker).upper()
    ticker_sigma = bundle.calibration.get("ticker_sigma", {}).get(ticker_key, {})
    if key in ticker_sigma:
        return float(ticker_sigma[key])
    global_sigma = bundle.calibration.get("global_sigma", {})
    return float(global_sigma.get(key, 0.01))


def run_forecast_inference_backtest(
    *,
    bundle: ModelBundle,
    data_by_ticker: dict[str, pd.DataFrame],
    forecast_cfg: ForecastConfig,
    trade_cfg: TradeLogicConfig,
    test_start: pd.Timestamp,
    test_end: pd.Timestamp,
) -> ForecastBacktestResult:
    """Load-only inference flow: predict test set, then execute deterministic RR logic."""
    target_cols = target_columns_for_horizon(int(forecast_cfg.horizon))
    pred_rows: list[pd.DataFrame] = []
    signal_rows: list[pd.DataFrame] = []
    trade_rows: list[pd.DataFrame] = []

    test_start_naive = _as_utc_timestamp(test_start).tz_localize(None)
    test_end_naive = _as_utc_timestamp(test_end).tz_localize(None)

    for ticker in sorted(data_by_ticker):
        bars = data_by_ticker[ticker]
        if bars.empty:
            continue

        features = compute_feature_frame(bars)
        features = features.reindex(columns=bundle.feature_columns)
        features = features.replace([np.inf, -np.inf], np.nan)
        features = features.ffill().fillna(0.0)

        features = features.loc[(features.index >= test_start_naive) & (features.index <= test_end_naive)]
        if features.empty:
            continue

        pred_values = predict_with_bundle(bundle, X=features, ticker=ticker)
        pred_df = pd.DataFrame(pred_values, index=features.index, columns=target_cols)
        pred_df = pred_df.rename(columns={col: f"pred_{col}" for col in target_cols})
        pred_df["ticker"] = ticker

        # Add translated forecast prices for convenience.
        ret_cols = [f"pred_{col}" for col in target_cols]
        pred_with_prices = _predicted_returns_to_prices(
            base_close=bars["Close"],
            pred_returns=pred_df[ret_cols],
            target_cols=ret_cols,
        )
        pred_with_prices["ticker"] = ticker

        sigma_c3 = _ticker_sigma_for_close_horizon(bundle=bundle, ticker=ticker, horizon=forecast_cfg.horizon)
        signals_df, trades_df = run_trade_simulation_for_ticker(
            ticker=ticker,
            bars=bars.loc[bars.index <= test_end_naive].copy(),
            predictions=pd.concat([pred_df, features[["atr_14"]]], axis=1),
            trade_cfg=trade_cfg,
            sigma_c3=sigma_c3,
            initial_capital=float(forecast_cfg.initial_capital_per_ticker),
        )

        pred_rows.append(pred_with_prices.reset_index().rename(columns={"index": "time"}))
        if not signals_df.empty:
            signal_rows.append(signals_df)
        if not trades_df.empty:
            trade_rows.append(trades_df)

    predictions = pd.concat(pred_rows, axis=0, ignore_index=True) if pred_rows else pd.DataFrame()
    signals = pd.concat(signal_rows, axis=0, ignore_index=True) if signal_rows else pd.DataFrame()
    trades = pd.concat(trade_rows, axis=0, ignore_index=True) if trade_rows else pd.DataFrame()

    if not predictions.empty:
        predictions["time"] = pd.to_datetime(predictions["time"], errors="coerce")
    if not signals.empty:
        signals["time"] = pd.to_datetime(signals["time"], errors="coerce")
        predictions = predictions.merge(
            signals,
            how="left",
            on=["ticker", "time"],
            suffixes=("", "_signal"),
        )
    if not trades.empty:
        trades["entry_time"] = pd.to_datetime(trades["entry_time"], errors="coerce")
        trades["exit_time"] = pd.to_datetime(trades["exit_time"], errors="coerce")

    initial_total = float(forecast_cfg.initial_capital_per_ticker) * float(len(forecast_cfg.tickers))
    summary_full = _summary_from_trades(
        trades=trades,
        start=test_start_naive,
        end=test_end_naive,
        initial_capital_total=initial_total,
    )

    jan_start = pd.Timestamp("2026-01-01 00:00:00")
    jan_end = pd.Timestamp("2026-01-31 23:59:59")
    jan_trades = trades.loc[(trades["entry_time"] >= jan_start) & (trades["entry_time"] <= jan_end)].copy() if not trades.empty else pd.DataFrame()
    summary_jan = _summary_from_trades(
        trades=jan_trades,
        start=jan_start,
        end=jan_end,
        initial_capital_total=initial_total,
    )

    return ForecastBacktestResult(
        predictions=predictions,
        trades=trades,
        summary_jan_feb=summary_full,
        summary_jan_only=summary_jan,
    )
