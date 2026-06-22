#!/usr/bin/env python3
"""Tune Mag7 5-minute ORB/VWAP parameters per ticker, then validate portfolio."""
from __future__ import annotations

import json
from pathlib import Path
import sys

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
    _score_candidate,
    _validate_args,
    _write_json_summary,
    parse_date_range_utc,
)


DEFAULT_OUTPUT_PATH = (
    Path(__file__).resolve().parents[1]
    / "backtests"
    / "results"
    / "mag7_intraday_orb_per_ticker_backtest.json"
)


def main() -> int:
    args = _parse_args()
    if Path(str(args.output_json)).name == "mag7_intraday_orb_backtest.json":
        args.output_json = str(DEFAULT_OUTPUT_PATH)
    _validate_args(args)

    dev_start, dev_end_exclusive = parse_date_range_utc(
        start_date=str(args.dev_start_date),
        end_date=str(args.dev_end_date),
    )
    holdout_start, holdout_end_exclusive = parse_date_range_utc(
        start_date=str(args.holdout_start_date),
        end_date=str(args.holdout_end_date),
    )
    if dev_end_exclusive > holdout_start:
        raise SystemExit("Development window overlaps holdout.")

    tickers = list(MAG7_TICKERS)
    benchmark = str(args.benchmark_ticker).strip().upper() or "SPY"
    fetch_tickers = tickers + ([] if benchmark in tickers else [benchmark])
    target = float(args.target_monthly_return_pct)
    initial_capital = float(args.initial_capital)
    sleeve_capital = initial_capital / len(tickers)
    round_trip_commission = float(args.round_trip_commission)
    short_borrow_fee_apr = float(args.short_borrow_fee_apr)
    cache_dir = Path(args.cache_dir).expanduser().resolve()

    print("=" * 110, flush=True)
    print("MAG7 5-MINUTE ORB/VWAP PER-TICKER PARAMETER SEARCH", flush=True)
    print("=" * 110, flush=True)
    print(f"Development: {args.dev_start_date} to {args.dev_end_date} | Holdout excluded", flush=True)
    print(f"Holdout: {args.holdout_start_date} to {args.holdout_end_date} | Alpaca 5Min RTH", flush=True)
    print(f"Portfolio: ${initial_capital:,.2f}, split 1/7 per ticker | Target {target:.2f}% monthly", flush=True)
    print("=" * 110, flush=True)

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
    candidates = _candidate_params(max_candidates=int(args.max_candidates))
    print(f"Scoring {len(candidates)} candidates independently for each ticker...", flush=True)

    params_by_ticker: dict[str, IntradayOrbParams] = {}
    ticker_summaries: dict[str, dict] = {}
    ticker_top_candidates: dict[str, list[dict]] = {}

    for ticker in tickers:
        scored = []
        for params in candidates:
            result = run_intraday_orb_portfolio(
                data_by_ticker={ticker: dev_strategy_data[ticker]},
                tickers=[ticker],
                params=params,
                initial_capital=sleeve_capital,
                round_trip_commission=round_trip_commission,
                target_monthly_return_pct=target,
                short_borrow_fee_apr=short_borrow_fee_apr,
            )
            summary = _result_summary(result=result, target=target)
            scored.append(
                {
                    "score": _single_ticker_score(summary),
                    "params": params.to_dict(),
                    "summary": summary,
                }
            )
        scored.sort(key=lambda item: float(item["score"]), reverse=True)
        best = scored[0]
        params_by_ticker[ticker] = IntradayOrbParams(**best["params"])
        ticker_summaries[ticker] = best["summary"]
        ticker_top_candidates[ticker] = scored[:10]
        print(
            f"{ticker:>5} | dev mean={best['summary']['mean_monthly_return_pct']:+.2f}% | "
            f"dd={best['summary']['max_drawdown_pct']:+.2f}% | "
            f"trades={best['summary']['trades']} | style={best['params']['signal_style']} | "
            f"lev={best['params']['leverage']}",
            flush=True,
        )

    dev_result = run_intraday_orb_per_ticker_portfolio(
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
    dev_summary = _result_summary(result=dev_result, target=target)
    holdout_summary = _result_summary(result=holdout_result, target=target)
    holdout_anchored = _anchored_monthly_returns(
        equity=holdout_result.equity_curve,
        start_time=pd.Timestamp(holdout_start).tz_convert(None),
    )

    print("\nPER-TICKER FROZEN DEVELOPMENT PORTFOLIO", flush=True)
    print("-" * 110, flush=True)
    _print_summary(dev_summary)
    print("\nPER-TICKER FROZEN 5-MINUTE HOLDOUT", flush=True)
    print("-" * 110, flush=True)
    _print_summary(holdout_summary)
    print(
        "Anchored holdout month returns:",
        {key: round(value, 4) for key, value in holdout_anchored.items()},
        flush=True,
    )

    payload = {
        "strategy": "mag7_intraday_orb_vwap_per_ticker",
        "tickers": tickers,
        "development_window": {
            "start": str(args.dev_start_date),
            "end": str(args.dev_end_date),
            "period": "5Min",
            "provider": "alpaca",
        },
        "holdout_window": {
            "start": str(args.holdout_start_date),
            "end": str(args.holdout_end_date),
            "period": "5Min",
            "provider": "alpaca",
            "anchored_monthly_returns_pct": holdout_anchored,
        },
        "initial_capital": initial_capital,
        "target_monthly_return_pct": target,
        "round_trip_commission": round_trip_commission,
        "short_borrow_fee_apr": short_borrow_fee_apr,
        "params_by_ticker": {ticker: params.to_dict() for ticker, params in params_by_ticker.items()},
        "ticker_development": ticker_summaries,
        "development": dev_summary,
        "holdout": holdout_summary,
        "ticker_top_candidates": ticker_top_candidates,
    }
    _write_json_summary(path=str(args.output_json), payload=payload)

    dev_pass = _passes_summary(dev_summary, target=target)
    holdout_pass = _passes_summary(holdout_summary, target=target) and all(
        value >= target for value in holdout_anchored.values()
    )
    print("\nGATE", flush=True)
    print("-" * 110, flush=True)
    print(f"Development gate: {'PASS' if dev_pass else 'MISS'}", flush=True)
    print(f"Holdout gate: {'PASS' if holdout_pass else 'MISS'}", flush=True)
    print(f"Live conversion allowed: {'YES' if dev_pass and holdout_pass else 'NO'}", flush=True)
    print("=" * 110, flush=True)
    return 0


def _single_ticker_score(summary: dict) -> float:
    mean_monthly = float(summary["mean_monthly_return_pct"])
    drawdown = abs(float(summary["max_drawdown_pct"]))
    profit_factor = min(5.0, float(summary["profit_factor"]))
    trades = int(summary["trades"])
    months = max(1, int(summary["months"]))
    hit_ratio = float(summary["months_at_or_above_target"]) / months
    return mean_monthly * 3.0 + profit_factor + hit_ratio * 3.0 + min(2.0, trades / 500.0) - drawdown / 4.0


if __name__ == "__main__":
    raise SystemExit(main())
