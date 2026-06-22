#!/usr/bin/env python3
"""Purged monthly walk-forward audit for Mag7 5-minute pooled ML fixed-RR strategy."""
from __future__ import annotations

from pathlib import Path
import sys
from typing import Any

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ROOT_PATH = str(PROJECT_ROOT)
if ROOT_PATH not in sys.path:
    sys.path.insert(0, ROOT_PATH)

from backtests.backtesting_py.mag7_adaptive_long_short_strategy import MAG7_TICKERS
from backtests.backtesting_py.mag7_intraday_ml_rr_strategy import MlRrParams
from backtests.run_mag7_intraday_ml_rr_backtest import (
    _candidate_params,
    _feature_frame,
    _fetch_alpaca_5min_cached,
    _result_summary,
    _run_portfolio,
    _score_summary,
    _train_models,
    _write_json_summary,
    parse_date_range_utc,
)
from backtests.run_mag7_intraday_orb_backtest import _parse_args, _print_summary


DEFAULT_OUTPUT_PATH = PROJECT_ROOT / "backtests" / "results" / "mag7_intraday_ml_rr_walk_forward.json"


def main() -> int:
    args = _parse_args()
    if Path(str(args.output_json)).name == "mag7_intraday_orb_backtest.json":
        args.output_json = str(DEFAULT_OUTPUT_PATH)

    tickers = list(MAG7_TICKERS)
    benchmark = str(args.benchmark_ticker).strip().upper() or "SPY"
    fetch_tickers = tickers + ([] if benchmark in tickers else [benchmark])
    cache_dir = Path(args.cache_dir).expanduser().resolve()
    target = float(args.target_monthly_return_pct)
    initial_capital = float(args.initial_capital)
    round_trip_commission = float(args.round_trip_commission)

    data_start, data_end_exclusive = parse_date_range_utc(
        start_date=str(args.dev_start_date),
        end_date=str(args.holdout_end_date),
    )
    dev_start, dev_end_exclusive = parse_date_range_utc(
        start_date=str(args.dev_start_date),
        end_date=str(args.dev_end_date),
    )
    holdout_start, holdout_end_exclusive = parse_date_range_utc(
        start_date=str(args.holdout_start_date),
        end_date=str(args.holdout_end_date),
    )
    dev_data = _fetch_alpaca_5min_cached(
        tickers=fetch_tickers,
        start_time=dev_start,
        end_time=dev_end_exclusive,
        cache_dir=cache_dir,
        label="dev",
        feed=str(args.alpaca_feed),
        refresh=bool(args.refresh_cache),
    )
    holdout_data = _fetch_alpaca_5min_cached(
        tickers=fetch_tickers,
        start_time=holdout_start,
        end_time=holdout_end_exclusive,
        cache_dir=cache_dir,
        label="holdout",
        feed=str(args.alpaca_feed),
        refresh=bool(args.refresh_cache),
    )

    frames = {}
    features = {}
    for ticker_idx, ticker in enumerate(tickers):
        raw = pd.concat([dev_data[ticker], holdout_data[ticker]]).sort_index()
        frame, feature = _feature_frame(raw, ticker_code=ticker_idx)
        frames[ticker] = frame
        features[ticker] = feature

    first_month = pd.Timestamp("2024-01-01")
    final_month = pd.Timestamp(holdout_end_exclusive).tz_convert(None).replace(day=1)
    candidates = _candidate_params(max_candidates=int(args.max_candidates))
    thresholds = (0.45, 0.5, 0.55, 0.6, 0.65, 0.7, 0.75, 0.8, 0.85, 0.9)
    month_rows: list[dict[str, Any]] = []
    portfolio_curves = []
    sleeve_curves: dict[str, list[pd.Series]] = {ticker: [] for ticker in tickers}

    print("=" * 112, flush=True)
    print("MAG7 ML FIXED-RR PURGED MONTHLY WALK-FORWARD AUDIT", flush=True)
    print("=" * 112, flush=True)
    print(f"Data: {data_start.date()} to {(data_end_exclusive - pd.Timedelta(days=1)).date()}", flush=True)
    print(f"Scored months: {first_month.date()} to {final_month.date()}", flush=True)
    print("=" * 112, flush=True)

    month = first_month
    while month <= final_month:
        test_start = month
        test_end = min(month + pd.offsets.MonthEnd(0), pd.Timestamp(holdout_end_exclusive).tz_convert(None) - pd.Timedelta(seconds=1))
        validation_end = test_start - pd.Timedelta(seconds=1)
        validation_start = test_start - pd.DateOffset(months=3)
        train_end = validation_start - pd.Timedelta(seconds=1)
        if train_end < pd.Timestamp(args.dev_start_date):
            month = month + pd.DateOffset(months=1)
            continue

        selected = _select_month_candidate(
            frames=frames,
            features=features,
            tickers=tickers,
            candidates=candidates,
            thresholds=thresholds,
            train_end=train_end,
            validation_start=validation_start,
            validation_end=validation_end,
            initial_capital=initial_capital,
            round_trip_commission=round_trip_commission,
            target=target,
        )
        if selected is None:
            print(f"{test_start.date()} | no eligible candidate; staying cash", flush=True)
            month_rows.append(
                {
                    "month": test_start.strftime("%Y-%m"),
                    "status": "cash_no_candidate",
                    "return_pct": 0.0,
                    "eligible_tickers": [],
                }
            )
            month = month + pd.DateOffset(months=1)
            continue

        params = selected["params"]
        threshold = float(selected["threshold"])
        models = _train_models(
            frames=frames,
            features=features,
            tickers=tickers,
            params=params,
            train_end=pd.Timestamp(train_end),
        )
        test_result = _run_portfolio(
            frames=frames,
            features=features,
            tickers=tickers,
            params=params,
            models=models,
            threshold=threshold,
            start_time=test_start,
            end_time=test_end,
            initial_capital=initial_capital,
            round_trip_commission=round_trip_commission,
            target_monthly_return_pct=target,
        )
        test_summary = _result_summary(result=test_result, target=target)
        if not test_result.equity_curve.empty:
            portfolio_curves.append(test_result.equity_curve)
        for ticker, equity in test_result.sleeve_equity_curves.items():
            if not equity.empty:
                sleeve_curves[ticker].append(equity)

        row = {
            "month": test_start.strftime("%Y-%m"),
            "status": "traded",
            "train_end": train_end.isoformat(),
            "validation_start": validation_start.isoformat(),
            "validation_end": validation_end.isoformat(),
            "test_start": test_start.isoformat(),
            "test_end": test_end.isoformat(),
            "params": params.to_dict() | {"probability_threshold": threshold},
            "validation": selected["validation"],
            "return_pct": test_summary["return_pct"],
            "mean_monthly_return_pct": test_summary["mean_monthly_return_pct"],
            "max_drawdown_pct": test_summary["max_drawdown_pct"],
            "trades": test_summary["trades"],
            "win_rate_pct": test_summary["win_rate_pct"],
            "profit_factor": test_summary["profit_factor"],
            "average_win_to_average_loss": test_summary["average_win_to_average_loss"],
            "isolated": test_summary["isolated"],
        }
        month_rows.append(row)
        print(
            f"{test_start.date()} | ret={test_summary['return_pct']:+.2f}% | "
            f"val={selected['validation']['mean_monthly_return_pct']:+.2f}% | "
            f"min_iso={test_summary['isolated_min_mean_monthly_return_pct']:+.2f}% | "
            f"H={params.horizon_bars} stop={params.stop_pct:.4f} th={threshold:.2f}",
            flush=True,
        )
        month = month + pd.DateOffset(months=1)

    stitched = _stitch_curves(portfolio_curves)
    stitched_monthly = stitched.resample("ME").last().pct_change().dropna() * 100.0 if not stitched.empty else pd.Series(dtype=float)
    stitched_summary = _stitched_summary(stitched=stitched, monthly=stitched_monthly, month_rows=month_rows, target=target)
    isolated_walk_forward = {
        ticker: _stitched_sleeve_summary(_stitch_curves(curves), target=target)
        for ticker, curves in sleeve_curves.items()
    }

    print("\nWALK-FORWARD SUMMARY", flush=True)
    print("-" * 112, flush=True)
    for key, value in stitched_summary.items():
        if key != "monthly_returns_pct":
            print(f"{key}: {value}", flush=True)
    print("isolated:", {k: round(v["mean_monthly_return_pct"], 2) for k, v in isolated_walk_forward.items()}, flush=True)

    payload = {
        "strategy": "mag7_intraday_ml_rr_walk_forward",
        "tickers": tickers,
        "initial_capital": initial_capital,
        "target_monthly_return_pct": target,
        "round_trip_commission": round_trip_commission,
        "walk_forward": stitched_summary,
        "isolated_walk_forward": isolated_walk_forward,
        "months": month_rows,
    }
    _write_json_summary(path=str(args.output_json), payload=payload)
    return 0


def _select_month_candidate(
    *,
    frames: dict[str, pd.DataFrame],
    features: dict[str, pd.DataFrame],
    tickers: list[str],
    candidates: list[MlRrParams],
    thresholds: tuple[float, ...],
    train_end: pd.Timestamp,
    validation_start: pd.Timestamp,
    validation_end: pd.Timestamp,
    initial_capital: float,
    round_trip_commission: float,
    target: float,
) -> dict[str, Any] | None:
    best: dict[str, Any] | None = None
    for params in candidates:
        models = _train_models(
            frames=frames,
            features=features,
            tickers=tickers,
            params=params,
            train_end=pd.Timestamp(train_end),
        )
        for threshold in thresholds:
            result = _run_portfolio(
                frames=frames,
                features=features,
                tickers=tickers,
                params=params,
                models=models,
                threshold=float(threshold),
                start_time=pd.Timestamp(validation_start),
                end_time=pd.Timestamp(validation_end),
                initial_capital=initial_capital,
                round_trip_commission=round_trip_commission,
                target_monthly_return_pct=target,
            )
            summary = _result_summary(result=result, target=target)
            if not _passes_validation_floor(summary):
                continue
            score = _walk_forward_score(summary)
            if best is None or score > float(best["score"]):
                best = {
                    "score": score,
                    "params": params,
                    "threshold": float(threshold),
                    "validation": summary,
                }
    return best


def _passes_validation_floor(summary: dict[str, Any]) -> bool:
    if float(summary["isolated_min_mean_monthly_return_pct"]) < 0.0:
        return False
    if float(summary["max_drawdown_pct"]) <= -20.0:
        return False
    if float(summary["isolated_worst_drawdown_pct"]) <= -35.0:
        return False
    eligible = 0
    for ticker_summary in summary["isolated"].values():
        if (
            float(ticker_summary["mean_monthly_return_pct"]) >= 3.0
            and float(ticker_summary["max_drawdown_pct"]) > -30.0
            and int(ticker_summary["trades"]) >= 40
        ):
            eligible += 1
    return eligible >= 4


def _walk_forward_score(summary: dict[str, Any]) -> float:
    months = max(1, int(summary["months"]))
    hit_rate = float(summary["months_at_or_above_target"]) / float(months)
    eligible = sum(
        1
        for ticker_summary in summary["isolated"].values()
        if (
            float(ticker_summary["mean_monthly_return_pct"]) >= 3.0
            and float(ticker_summary["max_drawdown_pct"]) > -30.0
            and int(ticker_summary["trades"]) >= 40
        )
    )
    return (
        min(
            float(summary["mean_monthly_return_pct"]),
            float(summary["isolated_min_mean_monthly_return_pct"]),
        )
        * 3.0
        + hit_rate * 4.0
        + eligible * 0.5
        + min(2.0, int(summary["trades"]) / 1000.0)
        - abs(float(summary["max_drawdown_pct"])) / 3.0
        - abs(float(summary["isolated_worst_drawdown_pct"])) / 5.0
    )


def _stitch_curves(curves: list[pd.Series]) -> pd.Series:
    if not curves:
        return pd.Series(dtype=float)
    pieces = []
    offset = 0.0
    last_value: float | None = None
    for curve in curves:
        if curve.empty:
            continue
        normalized = curve.copy().sort_index()
        if last_value is not None:
            offset = last_value - float(normalized.iloc[0])
            normalized = normalized + offset
        pieces.append(normalized)
        last_value = float(normalized.iloc[-1])
    if not pieces:
        return pd.Series(dtype=float)
    return pd.concat(pieces).sort_index()


def _stitched_summary(
    *,
    stitched: pd.Series,
    monthly: pd.Series,
    month_rows: list[dict[str, Any]],
    target: float,
) -> dict[str, Any]:
    if stitched.empty:
        return {
            "return_pct": 0.0,
            "mean_monthly_return_pct": 0.0,
            "max_drawdown_pct": 0.0,
            "months": 0,
            "months_at_or_above_target": 0,
            "monthly_returns_pct": {},
        }
    return {
        "return_pct": ((float(stitched.iloc[-1]) / float(stitched.iloc[0])) - 1.0) * 100.0,
        "mean_monthly_return_pct": float(monthly.mean()) if not monthly.empty else 0.0,
        "max_drawdown_pct": float(((stitched / stitched.cummax()) - 1.0).min()) * 100.0,
        "months": len(monthly),
        "scored_month_rows": len(month_rows),
        "months_at_or_above_target": int((monthly >= float(target)).sum()) if not monthly.empty else 0,
        "min_monthly_return_pct": float(monthly.min()) if not monthly.empty else 0.0,
        "monthly_returns_pct": {pd.Timestamp(idx).strftime("%Y-%m-%d"): float(value) for idx, value in monthly.items()},
    }


def _stitched_sleeve_summary(stitched: pd.Series, *, target: float) -> dict[str, Any]:
    if stitched.empty:
        return {
            "return_pct": 0.0,
            "mean_monthly_return_pct": 0.0,
            "max_drawdown_pct": 0.0,
            "months": 0,
            "months_at_or_above_target": 0,
        }
    monthly = stitched.resample("ME").last().pct_change().dropna() * 100.0
    return {
        "return_pct": ((float(stitched.iloc[-1]) / float(stitched.iloc[0])) - 1.0) * 100.0,
        "mean_monthly_return_pct": float(monthly.mean()) if not monthly.empty else 0.0,
        "max_drawdown_pct": float(((stitched / stitched.cummax()) - 1.0).min()) * 100.0,
        "months": len(monthly),
        "months_at_or_above_target": int((monthly >= float(target)).sum()) if not monthly.empty else 0,
    }


if __name__ == "__main__":
    raise SystemExit(main())
