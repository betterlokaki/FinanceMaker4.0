#!/usr/bin/env python3
"""Nested validation for Mag7 5-minute compression breakout/flip strategy."""
from __future__ import annotations

from pathlib import Path
import random
import sys
from typing import Any

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ROOT_PATH = str(PROJECT_ROOT)
if ROOT_PATH not in sys.path:
    sys.path.insert(0, ROOT_PATH)

from backtests.backtesting_py.mag7_adaptive_long_short_strategy import MAG7_TICKERS
from backtests.backtesting_py.mag7_intraday_compression_flip_strategy import (
    CompressionFlipParams,
    run_compression_flip_portfolio,
)
from backtests.run_mag7_intraday_orb_backtest import (
    _anchored_monthly_returns,
    _fetch_alpaca_5min_cached,
    _parse_args,
    _passes_summary,
    _print_summary,
    _result_summary,
    _write_json_summary,
    parse_date_range_utc,
)


DEFAULT_OUTPUT_PATH = (
    PROJECT_ROOT / "backtests" / "results" / "mag7_intraday_compression_flip_backtest.json"
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
    round_trip_commission = float(args.round_trip_commission)
    short_borrow_fee_apr = float(args.short_borrow_fee_apr)
    cache_dir = Path(args.cache_dir).expanduser().resolve()

    print("=" * 112, flush=True)
    print("MAG7 5-MINUTE COMPRESSION BREAKOUT/FLIP NESTED VALIDATION", flush=True)
    print("=" * 112, flush=True)
    print(f"Subtrain: {args.dev_start_date} to 2025-12-31", flush=True)
    print(f"Validation: 2026-01-01 to {args.dev_end_date}", flush=True)
    print(f"Final holdout: {args.holdout_start_date} to {args.holdout_end_date}", flush=True)
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
    strategy_dev_data = {ticker: dev_data[ticker] for ticker in tickers}
    strategy_holdout_data = {ticker: holdout_data[ticker] for ticker in tickers}

    subtrain_start_ts = pd.Timestamp(subtrain_start).tz_convert(None)
    subtrain_end_ts = pd.Timestamp(subtrain_end_exclusive - pd.Timedelta(seconds=1)).tz_convert(None)
    validation_start_ts = pd.Timestamp(validation_start).tz_convert(None)
    validation_end_ts = pd.Timestamp(validation_end_exclusive - pd.Timedelta(seconds=1)).tz_convert(None)
    holdout_start_ts = pd.Timestamp(holdout_start).tz_convert(None)
    holdout_end_ts = pd.Timestamp(holdout_end_exclusive - pd.Timedelta(seconds=1)).tz_convert(None)

    candidates = _candidate_params(max_candidates=int(args.max_candidates))
    print(f"Scoring {len(candidates)} compression/flip candidates...", flush=True)
    scored: list[dict[str, Any]] = []
    progress_interval = max(1, min(25, max(5, len(candidates) // 4)))

    for idx, params in enumerate(candidates, start=1):
        subtrain = run_compression_flip_portfolio(
            data_by_ticker=strategy_dev_data,
            tickers=tickers,
            params=params,
            initial_capital=initial_capital,
            round_trip_commission=round_trip_commission,
            target_monthly_return_pct=target,
            short_borrow_fee_apr=short_borrow_fee_apr,
            start_time=subtrain_start_ts,
            end_time=subtrain_end_ts,
        )
        validation = run_compression_flip_portfolio(
            data_by_ticker=strategy_dev_data,
            tickers=tickers,
            params=params,
            initial_capital=initial_capital,
            round_trip_commission=round_trip_commission,
            target_monthly_return_pct=target,
            short_borrow_fee_apr=short_borrow_fee_apr,
            start_time=validation_start_ts,
            end_time=validation_end_ts,
        )
        sub_summary = _result_summary(result=subtrain, target=target)
        val_summary = _result_summary(result=validation, target=target)
        scored.append(
            {
                "score": _nested_score(sub_summary=sub_summary, val_summary=val_summary),
                "params": params.to_dict(),
                "subtrain": sub_summary,
                "validation": val_summary,
            }
        )
        if idx % progress_interval == 0 or idx == len(candidates):
            leader = max(scored, key=lambda row: float(row["score"]))
            print(
                f"  tested {idx:>4}/{len(candidates)} | val mean="
                f"{leader['validation']['mean_monthly_return_pct']:+.2f}% | "
                f"val rr={leader['validation']['average_win_to_average_loss']:.2f}:1 | "
                f"val min_iso={leader['validation']['isolated_min_mean_monthly_return_pct']:+.2f}%",
                flush=True,
            )

    scored.sort(key=lambda row: float(row["score"]), reverse=True)
    best_params = CompressionFlipParams(**scored[0]["params"])
    full_dev = run_compression_flip_portfolio(
        data_by_ticker=strategy_dev_data,
        tickers=tickers,
        params=best_params,
        initial_capital=initial_capital,
        round_trip_commission=round_trip_commission,
        target_monthly_return_pct=target,
        short_borrow_fee_apr=short_borrow_fee_apr,
        start_time=subtrain_start_ts,
        end_time=validation_end_ts,
    )
    holdout = run_compression_flip_portfolio(
        data_by_ticker=strategy_holdout_data,
        tickers=tickers,
        params=best_params,
        initial_capital=initial_capital,
        round_trip_commission=round_trip_commission,
        target_monthly_return_pct=target,
        short_borrow_fee_apr=short_borrow_fee_apr,
        start_time=holdout_start_ts,
        end_time=holdout_end_ts,
    )
    full_dev_summary = _result_summary(result=full_dev, target=target)
    holdout_summary = _result_summary(result=holdout, target=target)
    holdout_anchored = _anchored_monthly_returns(
        equity=holdout.equity_curve,
        start_time=holdout_start_ts,
    )

    print("\nBEST SUBTRAIN RESULT", flush=True)
    print("-" * 112, flush=True)
    _print_summary(scored[0]["subtrain"])
    print("\nBEST VALIDATION RESULT", flush=True)
    print("-" * 112, flush=True)
    _print_summary(scored[0]["validation"])
    print("Params:", best_params.to_dict(), flush=True)
    print("\nFULL PRE-HOLDOUT RESULT", flush=True)
    print("-" * 112, flush=True)
    _print_summary(full_dev_summary)
    print("\nSTRICT 5-MINUTE HOLDOUT RESULT", flush=True)
    print("-" * 112, flush=True)
    _print_summary(holdout_summary)
    print(
        "Anchored holdout month returns:",
        {key: round(value, 4) for key, value in holdout_anchored.items()},
        flush=True,
    )

    payload = {
        "strategy": "mag7_intraday_compression_flip",
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
        "best_params": best_params.to_dict(),
        "best_subtrain": scored[0]["subtrain"],
        "best_validation": scored[0]["validation"],
        "development": full_dev_summary,
        "holdout": holdout_summary,
        "top_candidates": scored[:25],
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


def _candidate_params(*, max_candidates: int) -> list[CompressionFlipParams]:
    rng = random.Random(20260622)
    values = {
        "compression_bars": (12, 24, 36),
        "width_window_bars": (780, 1560, 3120),
        "compression_quantile": (0.15, 0.2, 0.25, 0.3),
        "atr_bars": (14, 20, 28),
        "breakout_atr_fraction": (0.0, 0.1, 0.2, 0.3),
        "use_volume_filter": (False, True),
        "volume_lookback": (20, 39),
        "volume_multiple": (1.0, 1.2, 1.5),
        "stop_mode": ("range", "atr"),
        "stop_atr_mult": (0.75, 1.0, 1.25),
        "min_stop_pct": (0.0025, 0.004, 0.006),
        "max_stop_pct": (0.012, 0.02, 0.035),
        "risk_reward_ratio": (2.0, 2.5, 3.0),
        "leverage": (1.0, 1.5, 2.0, 2.5, 3.0),
        "max_holding_bars": (6, 12, 18, 24),
        "flip_timeout_bars": (3, 6, 9),
        "allow_failure_flip": (False, True),
        "max_trades_per_day": (1, 2, 3),
        "entry_start_bar": (6, 9, 12),
        "entry_end_bar": (48, 60, 66),
    }
    out: list[CompressionFlipParams] = []
    seen: set[tuple[Any, ...]] = set()
    attempts = 0
    while len(out) < max_candidates and attempts < max_candidates * 100:
        attempts += 1
        choice = {key: rng.choice(options) for key, options in values.items()}
        if float(choice["min_stop_pct"]) >= float(choice["max_stop_pct"]):
            continue
        if int(choice["entry_start_bar"]) >= int(choice["entry_end_bar"]):
            continue
        if int(choice["flip_timeout_bars"]) > int(choice["max_holding_bars"]):
            continue
        key = tuple(choice.items())
        if key in seen:
            continue
        seen.add(key)
        out.append(CompressionFlipParams(**choice))
    return out


def _nested_score(*, sub_summary: dict[str, Any], val_summary: dict[str, Any]) -> float:
    val_mean = float(val_summary["mean_monthly_return_pct"])
    val_min_iso = float(val_summary["isolated_min_mean_monthly_return_pct"])
    sub_mean = float(sub_summary["mean_monthly_return_pct"])
    dd = abs(float(val_summary["max_drawdown_pct"]))
    pf = min(5.0, float(val_summary["profit_factor"]))
    realized_rr = min(4.0, float(val_summary.get("average_win_to_average_loss", 0.0)))
    trades = int(val_summary["trades"])
    months = max(1, int(val_summary["months"]))
    hit_ratio = float(val_summary["months_at_or_above_target"]) / months
    robust = min(val_mean, val_min_iso, sub_mean)
    rr_penalty = max(0.0, 1.6 - realized_rr) * 4.0
    trade_bonus = min(2.0, trades / 400.0)
    return robust * 3.0 + val_mean + val_min_iso + pf + realized_rr + hit_ratio * 4.0 + trade_bonus - dd / 3.0 - rr_penalty


if __name__ == "__main__":
    raise SystemExit(main())
