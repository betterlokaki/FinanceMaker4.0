#!/usr/bin/env python3
"""Nested per-ticker validation for Mag7 5-minute ORB/VWAP strategies."""
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
from backtests.backtesting_py.mag7_intraday_orb_strategy import (
    IntradayOrbParams,
    prepare_intraday_frame,
    run_intraday_orb_per_ticker_portfolio,
    run_intraday_orb_portfolio,
)
from backtests.run_mag7_intraday_orb_backtest import (
    _anchored_monthly_returns,
    _candidate_params,
    _fetch_alpaca_5min_cached,
    _parse_args,
    _passes_summary,
    _print_summary,
    _result_summary,
    _write_json_summary,
    parse_date_range_utc,
)


DEFAULT_OUTPUT_PATH = (
    PROJECT_ROOT / "backtests" / "results" / "mag7_intraday_orb_per_ticker_nested_backtest.json"
)


def main() -> int:
    args = _parse_args()
    if Path(str(args.output_json)).name == "mag7_intraday_orb_backtest.json":
        args.output_json = str(DEFAULT_OUTPUT_PATH)

    subtrain_start, subtrain_end_exclusive = parse_date_range_utc(
        start_date=str(args.dev_start_date),
        end_date="2025-12-31",
    )
    validation_start, validation_end_exclusive = parse_date_range_utc(
        start_date="2026-01-01",
        end_date=str(args.dev_end_date),
    )
    holdout_start, holdout_end_exclusive = parse_date_range_utc(
        start_date=str(args.holdout_start_date),
        end_date=str(args.holdout_end_date),
    )
    dev_start, dev_end_exclusive = parse_date_range_utc(
        start_date=str(args.dev_start_date),
        end_date=str(args.dev_end_date),
    )

    tickers = list(MAG7_TICKERS)
    benchmark = str(args.benchmark_ticker).strip().upper() or "SPY"
    fetch_tickers = tickers + ([] if benchmark in tickers else [benchmark])
    target = float(args.target_monthly_return_pct)
    initial_capital = float(args.initial_capital)
    sleeve_capital = initial_capital / len(tickers)
    round_trip_commission = float(args.round_trip_commission)
    short_borrow_fee_apr = float(args.short_borrow_fee_apr)
    cache_dir = Path(args.cache_dir).expanduser().resolve()

    print("=" * 112, flush=True)
    print("MAG7 5-MINUTE ORB/VWAP PER-TICKER NESTED VALIDATION", flush=True)
    print("=" * 112, flush=True)
    print(f"Subtrain: {args.dev_start_date} to 2025-12-31", flush=True)
    print(f"Validation: 2026-01-01 to {args.dev_end_date}", flush=True)
    print(f"Final holdout: {args.holdout_start_date} to {args.holdout_end_date}", flush=True)
    print("Candidate filter: configured risk_reward_ratio >= 2.0", flush=True)
    print("=" * 112, flush=True)

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
    dev_strategy_data = {ticker: prepare_intraday_frame(dev_data[ticker]) for ticker in tickers}
    holdout_strategy_data = {ticker: prepare_intraday_frame(holdout_data[ticker]) for ticker in tickers}

    subtrain_start_ts = pd.Timestamp(subtrain_start).tz_convert(None)
    subtrain_end_ts = pd.Timestamp(subtrain_end_exclusive - pd.Timedelta(seconds=1)).tz_convert(None)
    validation_start_ts = pd.Timestamp(validation_start).tz_convert(None)
    validation_end_ts = pd.Timestamp(validation_end_exclusive - pd.Timedelta(seconds=1)).tz_convert(None)
    holdout_start_ts = pd.Timestamp(holdout_start).tz_convert(None)

    candidates = [
        params
        for params in _candidate_params(max_candidates=max(int(args.max_candidates) * 3, int(args.max_candidates)))
        if float(params.risk_reward_ratio) >= 2.0
    ][: int(args.max_candidates)]
    print(f"Scoring {len(candidates)} RR>=2 candidates independently for each ticker...", flush=True)

    params_by_ticker: dict[str, IntradayOrbParams] = {}
    ticker_summaries: dict[str, dict[str, Any]] = {}
    ticker_top_candidates: dict[str, list[dict[str, Any]]] = {}

    for ticker in tickers:
        subtrain_frame = _slice_frame(dev_strategy_data[ticker], subtrain_start_ts, subtrain_end_ts)
        validation_frame = _slice_frame(dev_strategy_data[ticker], validation_start_ts, validation_end_ts)
        scored: list[dict[str, Any]] = []
        for params in candidates:
            subtrain = run_intraday_orb_portfolio(
                data_by_ticker={ticker: subtrain_frame},
                tickers=[ticker],
                params=params,
                initial_capital=sleeve_capital,
                round_trip_commission=round_trip_commission,
                target_monthly_return_pct=target,
                short_borrow_fee_apr=short_borrow_fee_apr,
            )
            validation = run_intraday_orb_portfolio(
                data_by_ticker={ticker: validation_frame},
                tickers=[ticker],
                params=params,
                initial_capital=sleeve_capital,
                round_trip_commission=round_trip_commission,
                target_monthly_return_pct=target,
                short_borrow_fee_apr=short_borrow_fee_apr,
            )
            sub_summary = _result_summary(result=subtrain, target=target)
            val_summary = _result_summary(result=validation, target=target)
            scored.append(
                {
                    "score": _nested_single_ticker_score(sub_summary=sub_summary, val_summary=val_summary),
                    "params": params.to_dict(),
                    "subtrain": sub_summary,
                    "validation": val_summary,
                }
            )
        scored.sort(key=lambda item: float(item["score"]), reverse=True)
        best = scored[0]
        params_by_ticker[ticker] = IntradayOrbParams(**best["params"])
        ticker_summaries[ticker] = {"subtrain": best["subtrain"], "validation": best["validation"]}
        ticker_top_candidates[ticker] = scored[:10]
        print(
            f"{ticker:>5} | sub={best['subtrain']['mean_monthly_return_pct']:+.2f}% | "
            f"val={best['validation']['mean_monthly_return_pct']:+.2f}% | "
            f"val rr={best['validation']['average_win_to_average_loss']:.2f}:1 | "
            f"style={best['params']['signal_style']} | rr={best['params']['risk_reward_ratio']} | "
            f"lev={best['params']['leverage']}",
            flush=True,
        )

    full_dev_result = run_intraday_orb_per_ticker_portfolio(
        data_by_ticker=dev_strategy_data,
        params_by_ticker=params_by_ticker,
        tickers=tickers,
        initial_capital=initial_capital,
        round_trip_commission=round_trip_commission,
        target_monthly_return_pct=target,
        short_borrow_fee_apr=short_borrow_fee_apr,
    )
    holdout_result = run_intraday_orb_per_ticker_portfolio(
        data_by_ticker=holdout_strategy_data,
        params_by_ticker=params_by_ticker,
        tickers=tickers,
        initial_capital=initial_capital,
        round_trip_commission=round_trip_commission,
        target_monthly_return_pct=target,
        short_borrow_fee_apr=short_borrow_fee_apr,
    )
    full_dev_summary = _result_summary(result=full_dev_result, target=target)
    holdout_summary = _result_summary(result=holdout_result, target=target)
    holdout_anchored = _anchored_monthly_returns(
        equity=holdout_result.equity_curve,
        start_time=holdout_start_ts,
    )

    print("\nNESTED PER-TICKER FULL PRE-HOLDOUT PORTFOLIO", flush=True)
    print("-" * 112, flush=True)
    _print_summary(full_dev_summary)
    print("\nNESTED PER-TICKER STRICT 5-MINUTE HOLDOUT", flush=True)
    print("-" * 112, flush=True)
    _print_summary(holdout_summary)
    print(
        "Anchored holdout month returns:",
        {key: round(value, 4) for key, value in holdout_anchored.items()},
        flush=True,
    )

    payload = {
        "strategy": "mag7_intraday_orb_vwap_per_ticker_nested",
        "tickers": tickers,
        "subtrain_window": {"start": str(args.dev_start_date), "end": "2025-12-31"},
        "validation_window": {"start": "2026-01-01", "end": str(args.dev_end_date)},
        "holdout_window": {
            "start": str(args.holdout_start_date),
            "end": str(args.holdout_end_date),
            "anchored_monthly_returns_pct": holdout_anchored,
        },
        "initial_capital": initial_capital,
        "target_monthly_return_pct": target,
        "round_trip_commission": round_trip_commission,
        "short_borrow_fee_apr": short_borrow_fee_apr,
        "params_by_ticker": {ticker: params.to_dict() for ticker, params in params_by_ticker.items()},
        "ticker_nested": ticker_summaries,
        "development": full_dev_summary,
        "holdout": holdout_summary,
        "ticker_top_candidates": ticker_top_candidates,
    }
    _write_json_summary(path=str(args.output_json), payload=payload)

    dev_pass = _passes_summary(full_dev_summary, target=target) and float(
        full_dev_summary["average_win_to_average_loss"]
    ) >= 1.6
    holdout_pass = (
        _passes_summary(holdout_summary, target=target)
        and float(holdout_summary["average_win_to_average_loss"]) >= 1.6
        and all(value >= target for value in holdout_anchored.values())
    )
    print("\nGATE", flush=True)
    print("-" * 112, flush=True)
    print(f"Development gate: {'PASS' if dev_pass else 'MISS'}", flush=True)
    print(f"Holdout gate: {'PASS' if holdout_pass else 'MISS'}", flush=True)
    print(f"Live conversion allowed: {'YES' if dev_pass and holdout_pass else 'NO'}", flush=True)
    print("=" * 112, flush=True)
    return 0


def _slice_frame(frame: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    return frame[(frame.index >= start) & (frame.index <= end)].copy()


def _nested_single_ticker_score(*, sub_summary: dict[str, Any], val_summary: dict[str, Any]) -> float:
    val_mean = float(val_summary["mean_monthly_return_pct"])
    sub_mean = float(sub_summary["mean_monthly_return_pct"])
    dd = abs(float(val_summary["max_drawdown_pct"]))
    pf = min(5.0, float(val_summary["profit_factor"]))
    realized_rr = min(4.0, float(val_summary.get("average_win_to_average_loss", 0.0)))
    trades = int(val_summary["trades"])
    months = max(1, int(val_summary["months"]))
    hit_ratio = float(val_summary["months_at_or_above_target"]) / months
    robust = min(val_mean, sub_mean)
    rr_penalty = max(0.0, 1.6 - realized_rr) * 4.0
    return robust * 3.0 + val_mean + pf + realized_rr + hit_ratio * 4.0 + min(2.0, trades / 250.0) - dd / 3.0 - rr_penalty


if __name__ == "__main__":
    raise SystemExit(main())
