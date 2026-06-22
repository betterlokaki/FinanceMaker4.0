#!/usr/bin/env python3
"""Search Mag7 5-minute opening-range/VWAP long-short strategy."""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import random
import sys
from typing import Any

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ROOT_PATH = str(PROJECT_ROOT)
if ROOT_PATH not in sys.path:
    sys.path.insert(0, ROOT_PATH)

from backtests.backtesting_py.isolated_backtest_engine import filter_regular_session, parse_date_range_utc
from backtests.backtesting_py.mag7_adaptive_long_short_strategy import MAG7_TICKERS, SleevePortfolioResult
from backtests.backtesting_py.mag7_intraday_orb_strategy import (
    IntradayOrbParams,
    prepare_intraday_frame,
    run_intraday_orb_portfolio,
)


DEFAULT_OUTPUT_PATH = PROJECT_ROOT / "backtests" / "results" / "mag7_intraday_orb_backtest.json"
DEFAULT_CACHE_DIR = PROJECT_ROOT / "backtests" / "results" / "cache" / "alpaca_5min"


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Mag7 5-minute ORB/VWAP strategy search.")
    parser.add_argument("--dev-start-date", default="2021-06-21")
    parser.add_argument("--dev-end-date", default="2026-04-20")
    parser.add_argument("--holdout-start-date", default="2026-04-21")
    parser.add_argument("--holdout-end-date", default="2026-06-20")
    parser.add_argument("--initial-capital", type=float, default=100_000.0)
    parser.add_argument("--round-trip-commission", type=float, default=1.0)
    parser.add_argument("--short-borrow-fee-apr", type=float, default=0.03)
    parser.add_argument("--target-monthly-return-pct", type=float, default=6.0)
    parser.add_argument("--benchmark-ticker", default="SPY")
    parser.add_argument("--alpaca-feed", default="")
    parser.add_argument("--max-candidates", type=int, default=300)
    parser.add_argument("--cache-dir", default=str(DEFAULT_CACHE_DIR))
    parser.add_argument("--refresh-cache", action="store_true")
    parser.add_argument("--output-json", default=str(DEFAULT_OUTPUT_PATH))
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
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
    round_trip_commission = float(args.round_trip_commission)
    short_borrow_fee_apr = float(args.short_borrow_fee_apr)
    cache_dir = Path(args.cache_dir).expanduser().resolve()

    print("=" * 110, flush=True)
    print("MAG7 5-MINUTE OPENING-RANGE/VWAP LONG-SHORT SEARCH", flush=True)
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

    dev_strategy_data = {
        ticker: prepare_intraday_frame(dev_data[ticker])
        for ticker in tickers
        if ticker in dev_data
    }
    holdout_strategy_data = {
        ticker: prepare_intraday_frame(holdout_data[ticker])
        for ticker in tickers
        if ticker in holdout_data
    }
    if len(dev_strategy_data) != len(tickers):
        raise SystemExit(f"Missing development 5Min data for: {sorted(set(tickers) - set(dev_strategy_data))}")
    if len(holdout_strategy_data) != len(tickers):
        raise SystemExit(f"Missing holdout 5Min data for: {sorted(set(tickers) - set(holdout_strategy_data))}")

    candidates = _candidate_params(max_candidates=int(args.max_candidates))
    print(f"Scoring {len(candidates)} ORB/VWAP candidates on development window only...", flush=True)
    scored: list[dict[str, Any]] = []
    progress_interval = max(1, min(25, max(5, len(candidates) // 4)))
    for idx, params in enumerate(candidates, start=1):
        result = run_intraday_orb_portfolio(
            data_by_ticker=dev_strategy_data,
            tickers=tickers,
            params=params,
            initial_capital=initial_capital,
            round_trip_commission=round_trip_commission,
            target_monthly_return_pct=target,
            short_borrow_fee_apr=short_borrow_fee_apr,
        )
        summary = _result_summary(result=result, target=target)
        scored.append({"score": _score_candidate(summary), "params": params.to_dict(), "summary": summary})
        if idx % progress_interval == 0 or idx == len(candidates):
            leader = max(scored, key=lambda item: float(item["score"]))
            leader_summary = leader["summary"]
            print(
                f"  tested {idx:>4}/{len(candidates)} | best mean_monthly="
                f"{leader_summary['mean_monthly_return_pct']:+.2f}% | "
                f"dd={leader_summary['max_drawdown_pct']:+.2f}% | "
                f"min_iso={leader_summary['isolated_min_mean_monthly_return_pct']:+.2f}%",
                flush=True,
            )

    scored.sort(key=lambda item: float(item["score"]), reverse=True)
    best = scored[0]
    best_params = IntradayOrbParams(**best["params"])
    dev_result = run_intraday_orb_portfolio(
        data_by_ticker=dev_strategy_data,
        tickers=tickers,
        params=best_params,
        initial_capital=initial_capital,
        round_trip_commission=round_trip_commission,
        target_monthly_return_pct=target,
        short_borrow_fee_apr=short_borrow_fee_apr,
    )
    dev_summary = _result_summary(result=dev_result, target=target)

    holdout_result = run_intraday_orb_portfolio(
        data_by_ticker=holdout_strategy_data,
        tickers=tickers,
        params=best_params,
        initial_capital=initial_capital,
        round_trip_commission=round_trip_commission,
        target_monthly_return_pct=target,
        short_borrow_fee_apr=short_borrow_fee_apr,
    )
    holdout_summary = _result_summary(result=holdout_result, target=target)
    holdout_anchored = _anchored_monthly_returns(
        equity=holdout_result.equity_curve,
        start_time=pd.Timestamp(holdout_start).tz_convert(None),
    )

    print("\nBEST DEVELOPMENT RESULT", flush=True)
    print("-" * 110, flush=True)
    _print_summary(dev_summary)
    print("Params:", json.dumps(best_params.to_dict(), sort_keys=True), flush=True)

    print("\nSTRICT 5-MINUTE HOLDOUT RESULT", flush=True)
    print("-" * 110, flush=True)
    _print_summary(holdout_summary)
    print(
        "Anchored holdout month returns:",
        {key: round(value, 4) for key, value in holdout_anchored.items()},
        flush=True,
    )

    payload = {
        "strategy": "mag7_intraday_orb_vwap",
        "tickers": tickers,
        "benchmark_ticker": benchmark,
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
        "best_params": best_params.to_dict(),
        "development": dev_summary,
        "holdout": holdout_summary,
        "top_candidates": scored[:25],
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


def _candidate_params(*, max_candidates: int) -> list[IntradayOrbParams]:
    rng = random.Random(20260621)
    candidates: list[IntradayOrbParams] = []
    seen: set[tuple[Any, ...]] = set()
    values = {
        "signal_style": ("breakout", "fade", "vwap_fade", "vwap_trend"),
        "opening_range_bars": (3, 6, 9, 12),
        "breakout_buffer_pct": (0.0, 0.0005, 0.001, 0.002),
        "vwap_buffer_pct": (0.0, 0.0005),
        "min_opening_range_pct": (0.0, 0.002, 0.004),
        "max_opening_range_pct": (0.025, 0.04, 0.08),
        "stop_range_fraction": (0.25, 0.35, 0.5, 0.75, 1.0),
        "min_stop_pct": (0.0025, 0.004, 0.006, 0.01),
        "max_stop_pct": (0.012, 0.02, 0.035),
        "risk_reward_ratio": (1.0, 1.2, 1.5, 2.0, 2.5, 3.0),
        "leverage": (1.0, 1.5, 2.0, 2.5, 3.0),
        "max_trades_per_day": (1, 2, 3),
        "allow_failed_breakout_flip": (False, True),
        "require_relative_volume": (False, True),
    }
    max_attempts = max_candidates * 100
    attempts = 0
    while len(candidates) < max_candidates and attempts < max_attempts:
        attempts += 1
        choice = {key: rng.choice(option_values) for key, option_values in values.items()}
        if float(choice["min_opening_range_pct"]) >= float(choice["max_opening_range_pct"]):
            continue
        if float(choice["min_stop_pct"]) >= float(choice["max_stop_pct"]):
            continue
        key = tuple(choice.items())
        if key in seen:
            continue
        seen.add(key)
        candidates.append(
            IntradayOrbParams(
                **choice,
                relative_volume_min=1.15,
            )
        )
    return candidates


def _fetch_alpaca_5min_cached(
    *,
    tickers: list[str],
    start_time: datetime,
    end_time: datetime,
    cache_dir: Path,
    label: str,
    feed: str,
    refresh: bool,
) -> dict[str, pd.DataFrame]:
    cache_dir.mkdir(parents=True, exist_ok=True)
    start_key = pd.Timestamp(start_time).strftime("%Y%m%d")
    end_key = (pd.Timestamp(end_time) - pd.Timedelta(seconds=1)).strftime("%Y%m%d")
    output: dict[str, pd.DataFrame] = {}
    missing: list[str] = []
    for ticker in tickers:
        path = cache_dir / f"{label}_{ticker}_{start_key}_{end_key}.csv"
        if path.exists() and not refresh:
            frame = pd.read_csv(path, parse_dates=["timestamp"]).set_index("timestamp")
            output[ticker] = frame
        else:
            missing.append(ticker)

    if missing:
        for ticker in missing:
            fetched = _fetch_alpaca_5min(
                tickers=[ticker],
                start_time=start_time,
                end_time=end_time,
                feed=feed,
            )
            frame = fetched.get(ticker)
            if frame is None or frame.empty:
                continue
            path = cache_dir / f"{label}_{ticker}_{start_key}_{end_key}.csv"
            frame.reset_index(names="timestamp").to_csv(path, index=False)
            output[ticker] = frame

    counts = {ticker: len(frame) for ticker, frame in output.items()}
    print(f"{label} 5Min RTH data counts: {counts}", flush=True)
    return output


def _fetch_alpaca_5min(
    *,
    tickers: list[str],
    start_time: datetime,
    end_time: datetime,
    feed: str,
) -> dict[str, pd.DataFrame]:
    _load_dotenv()
    api_key = os.getenv("ALPACA_API_KEY") or os.getenv("APCA_API_KEY_ID")
    secret_key = os.getenv("ALPACA_SECRET_KEY") or os.getenv("APCA_API_SECRET_KEY")
    if not api_key or not secret_key:
        raise SystemExit("Missing ALPACA_API_KEY and ALPACA_SECRET_KEY in environment or .env.")

    from alpaca.data.enums import DataFeed
    from alpaca.data.historical import StockHistoricalDataClient
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

    feed_arg = None
    feed_key = str(feed or "").strip().upper()
    if feed_key:
        feed_arg = getattr(DataFeed, feed_key)
    request_kwargs: dict[str, Any] = {
        "symbol_or_symbols": tickers,
        "timeframe": TimeFrame(5, TimeFrameUnit.Minute),
        "start": _ensure_utc(start_time),
        "end": _ensure_utc(end_time),
    }
    if feed_arg is not None:
        request_kwargs["feed"] = feed_arg

    print(f"Fetching Alpaca 5Min bars for {', '.join(tickers)}...", flush=True)
    client = StockHistoricalDataClient(api_key, secret_key)
    bars = client.get_stock_bars(StockBarsRequest(**request_kwargs))
    df = bars.df
    output: dict[str, pd.DataFrame] = {}
    if df is None or df.empty:
        return output

    for ticker in tickers:
        try:
            frame = df.xs(ticker, level="symbol").copy()
        except KeyError:
            continue
        frame.index = pd.DatetimeIndex(frame.index).tz_convert("UTC").tz_localize(None)
        frame = frame.rename(
            columns={
                "open": "Open",
                "high": "High",
                "low": "Low",
                "close": "Close",
                "volume": "Volume",
            }
        )
        frame = frame.loc[:, ["Open", "High", "Low", "Close", "Volume"]]
        output[ticker] = filter_regular_session(frame)
    return output


def _result_summary(*, result: SleevePortfolioResult, target: float) -> dict[str, Any]:
    isolated = {}
    isolated_means = []
    isolated_drawdowns = []
    all_trades = [trade for trades in result.trades_by_ticker.values() for trade in trades]
    winning_pnls = [float(trade.net_pnl) for trade in all_trades if float(trade.net_pnl) > 0.0]
    losing_pnls = [abs(float(trade.net_pnl)) for trade in all_trades if float(trade.net_pnl) <= 0.0]
    average_win = float(np.mean(winning_pnls)) if winning_pnls else 0.0
    average_loss = float(np.mean(losing_pnls)) if losing_pnls else 0.0
    for ticker, equity in result.sleeve_equity_curves.items():
        monthly = equity.resample("ME").last().pct_change().dropna() * 100.0
        mean_monthly = float(monthly.mean()) if not monthly.empty else 0.0
        drawdown = _max_drawdown_pct(equity)
        isolated[ticker] = {
            "final_equity": float(equity.iloc[-1]) if not equity.empty else 0.0,
            "mean_monthly_return_pct": mean_monthly,
            "max_drawdown_pct": drawdown,
            "months": int(len(monthly)),
            "months_at_or_above_target": int((monthly >= float(target)).sum()),
            "trades": int(len(result.trades_by_ticker.get(ticker, ()))),
        }
        isolated_means.append(mean_monthly)
        isolated_drawdowns.append(drawdown)
    return {
        "final_equity": result.final_equity,
        "return_pct": result.return_pct,
        "mean_monthly_return_pct": result.mean_monthly_return_pct,
        "max_drawdown_pct": result.max_drawdown_pct,
        "trades": result.trades,
        "winning_trades": result.winning_trades,
        "losing_trades": result.losing_trades,
        "win_rate_pct": result.win_rate_pct,
        "average_winning_trade_pnl": average_win,
        "average_losing_trade_pnl": average_loss,
        "average_win_to_average_loss": (average_win / average_loss) if average_loss > 0.0 else 0.0,
        "profit_factor": result.profit_factor,
        "months": result.months,
        "months_at_or_above_target": result.months_at_or_above_target,
        "monthly_returns_pct": {
            pd.Timestamp(index).strftime("%Y-%m-%d"): float(value)
            for index, value in result.monthly_returns_pct.items()
        },
        "all_months_at_or_above_target": bool(
            not result.monthly_returns_pct.empty
            and (result.monthly_returns_pct >= float(target)).all()
        ),
        "isolated_mean_monthly_return_pct": float(np.mean(isolated_means)) if isolated_means else 0.0,
        "isolated_min_mean_monthly_return_pct": float(np.min(isolated_means)) if isolated_means else 0.0,
        "isolated_worst_drawdown_pct": float(np.min(isolated_drawdowns)) if isolated_drawdowns else 0.0,
        "isolated": isolated,
    }


def _score_candidate(summary: dict[str, Any]) -> float:
    mean_monthly = float(summary["mean_monthly_return_pct"])
    min_iso = float(summary["isolated_min_mean_monthly_return_pct"])
    drawdown = abs(float(summary["max_drawdown_pct"]))
    profit_factor = min(5.0, float(summary["profit_factor"]))
    trades = int(summary["trades"])
    months = max(1, int(summary["months"]))
    hit_ratio = float(summary["months_at_or_above_target"]) / months
    robust_monthly = min(mean_monthly, min_iso)
    return (
        robust_monthly * 3.0
        + mean_monthly
        + min_iso * 1.5
        + profit_factor
        + hit_ratio * 4.0
        + min(2.0, trades / 1500.0)
        - drawdown / 3.5
    )


def _passes_summary(summary: dict[str, Any], *, target: float) -> bool:
    return (
        float(summary["mean_monthly_return_pct"]) >= float(target)
        and float(summary["isolated_min_mean_monthly_return_pct"]) >= float(target)
        and int(summary["trades"]) >= 100
        and float(summary["max_drawdown_pct"]) > -25.0
    )


def _anchored_monthly_returns(*, equity: pd.Series, start_time: pd.Timestamp) -> dict[str, float]:
    if equity.empty:
        return {}
    windows: dict[str, float] = {}
    starts = [pd.Timestamp(start_time), pd.Timestamp(start_time) + pd.DateOffset(months=1)]
    for idx, start in enumerate(starts, start=1):
        end = start + pd.DateOffset(months=1)
        period = equity[(equity.index >= start) & (equity.index < end)]
        if len(period) < 2:
            continue
        windows[f"month_{idx}_{start.date()}_{(end - pd.Timedelta(days=1)).date()}"] = (
            (float(period.iloc[-1]) / float(period.iloc[0])) - 1.0
        ) * 100.0
    return windows


def _print_summary(summary: dict[str, Any]) -> None:
    print(f"Final equity: ${float(summary['final_equity']):,.2f}", flush=True)
    print(f"Return: {float(summary['return_pct']):+.2f}%", flush=True)
    print(f"Average monthly return: {float(summary['mean_monthly_return_pct']):+.2f}%", flush=True)
    print(f"Max drawdown: {float(summary['max_drawdown_pct']):+.2f}%", flush=True)
    print(
        f"Trades: {int(summary['trades'])} | W:L {int(summary['winning_trades'])}:"
        f"{int(summary['losing_trades'])} | Win rate: {float(summary['win_rate_pct']):.2f}% | "
        f"Profit factor: {float(summary['profit_factor']):.2f}",
        flush=True,
    )
    print(
        f"Avg win/loss: ${float(summary['average_winning_trade_pnl']):,.2f}/"
        f"${float(summary['average_losing_trade_pnl']):,.2f} | "
        f"Avg win:loss {float(summary['average_win_to_average_loss']):.2f}:1",
        flush=True,
    )
    print(
        f"Monthly target months: {int(summary['months_at_or_above_target'])}/"
        f"{int(summary['months'])} | all months >= target: {summary['all_months_at_or_above_target']}",
        flush=True,
    )
    print(
        f"Isolated ticker avg monthly: mean={float(summary['isolated_mean_monthly_return_pct']):+.2f}% | "
        f"min={float(summary['isolated_min_mean_monthly_return_pct']):+.2f}%",
        flush=True,
    )


def _write_json_summary(*, path: str, payload: dict[str, Any]) -> None:
    output_path = Path(path).expanduser().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    print(f"JSON saved: {output_path}", flush=True)


def _validate_args(args: argparse.Namespace) -> None:
    if float(args.initial_capital) <= 0.0:
        raise SystemExit("Invalid --initial-capital. Value must be > 0.")
    if float(args.round_trip_commission) < 0.0:
        raise SystemExit("Invalid --round-trip-commission. Value must be >= 0.")
    if float(args.short_borrow_fee_apr) < 0.0:
        raise SystemExit("Invalid --short-borrow-fee-apr. Value must be >= 0.")
    if float(args.target_monthly_return_pct) <= 0.0:
        raise SystemExit("Invalid --target-monthly-return-pct. Value must be > 0.")
    if int(args.max_candidates) < 1:
        raise SystemExit("Invalid --max-candidates. Value must be >= 1.")


def _load_dotenv() -> None:
    env_path = PROJECT_ROOT / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _max_drawdown_pct(equity: pd.Series) -> float:
    if equity.empty:
        return 0.0
    drawdown = (equity / equity.cummax()) - 1.0
    return float(drawdown.min()) * 100.0


if __name__ == "__main__":
    raise SystemExit(main())
