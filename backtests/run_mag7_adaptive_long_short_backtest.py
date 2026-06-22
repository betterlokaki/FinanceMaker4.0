#!/usr/bin/env python3
"""Search and validate the Mag7 adaptive long/short sleeve strategy."""
from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sys
from typing import Any

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ROOT_PATH = str(PROJECT_ROOT)
if ROOT_PATH not in sys.path:
    sys.path.insert(0, ROOT_PATH)

from backtests.backtesting_py.isolated_backtest_engine import (
    fetch_ohlcv_for_tickers_sync,
    filter_regular_session,
    parse_date_range_utc,
)
from backtests.backtesting_py.mag7_adaptive_long_short_strategy import (
    MAG7_TICKERS,
    AdaptiveLongShortParams,
    SleevePortfolioResult,
    compute_mag7_adaptive_features,
    run_equal_weight_sleeve_portfolio,
)
from common.models.period import Period


DEFAULT_OUTPUT_PATH = (
    PROJECT_ROOT / "backtests" / "results" / "mag7_adaptive_long_short_backtest.json"
)
DEFAULT_CHART_PATH = (
    PROJECT_ROOT / "backtests" / "results" / "mag7_adaptive_long_short_trade_chart.png"
)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Search Mag7 adaptive long/short strategy without using final holdout."
    )
    parser.add_argument("--dev-start-date", default="2021-06-21")
    parser.add_argument("--dev-end-date", default="2026-04-20")
    parser.add_argument("--holdout-start-date", default="2026-04-21")
    parser.add_argument("--holdout-end-date", default="2026-06-20")
    parser.add_argument("--warmup-days", type=int, default=420)
    parser.add_argument("--initial-capital", type=float, default=100_000.0)
    parser.add_argument("--round-trip-commission", type=float, default=1.0)
    parser.add_argument("--short-borrow-fee-apr", type=float, default=0.03)
    parser.add_argument("--target-monthly-return-pct", type=float, default=6.0)
    parser.add_argument("--benchmark-ticker", default="SPY")
    parser.add_argument(
        "--holdout-provider",
        choices=["alpaca", "yahoo", "skip"],
        default="alpaca",
        help="Provider for the last-two-month 5-minute holdout.",
    )
    parser.add_argument(
        "--alpaca-feed",
        default="",
        help="Optional Alpaca data feed: iex, sip, delayed_sip. Empty uses account default.",
    )
    parser.add_argument(
        "--max-candidates",
        type=int,
        default=900,
        help="Deterministic cap on parameter candidates scored on the development window.",
    )
    parser.add_argument(
        "--output-json",
        default=str(DEFAULT_OUTPUT_PATH),
        help="Path for machine-readable result summary.",
    )
    parser.add_argument(
        "--chart-path",
        default=str(DEFAULT_CHART_PATH),
        help="Path for trade-overlay chart. Use empty value to skip chart.",
    )
    parser.add_argument("--no-chart", action="store_true")
    return parser.parse_args(argv)


def _validate_args(args: argparse.Namespace) -> None:
    if float(args.initial_capital) <= 0.0:
        raise SystemExit("Invalid --initial-capital. Value must be > 0.")
    if int(args.warmup_days) < 0:
        raise SystemExit("Invalid --warmup-days. Value must be >= 0.")
    if float(args.round_trip_commission) < 0.0:
        raise SystemExit("Invalid --round-trip-commission. Value must be >= 0.")
    if float(args.short_borrow_fee_apr) < 0.0:
        raise SystemExit("Invalid --short-borrow-fee-apr. Value must be >= 0.")
    if float(args.target_monthly_return_pct) <= 0.0:
        raise SystemExit("Invalid --target-monthly-return-pct. Value must be > 0.")
    if int(args.max_candidates) < 1:
        raise SystemExit("Invalid --max-candidates. Value must be >= 1.")


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    _validate_args(args)

    dev_start_dt, dev_end_exclusive = parse_date_range_utc(
        start_date=str(args.dev_start_date),
        end_date=str(args.dev_end_date),
    )
    holdout_start_dt, holdout_end_exclusive = parse_date_range_utc(
        start_date=str(args.holdout_start_date),
        end_date=str(args.holdout_end_date),
    )
    if dev_end_exclusive > holdout_start_dt:
        raise SystemExit("Development window overlaps holdout. Move --dev-end-date earlier.")

    tickers = list(MAG7_TICKERS)
    benchmark_ticker = str(args.benchmark_ticker).strip().upper() or "SPY"
    target = float(args.target_monthly_return_pct)
    initial_capital = float(args.initial_capital)
    round_trip_commission = float(args.round_trip_commission)
    short_borrow_fee_apr = float(args.short_borrow_fee_apr)

    dev_start_time = pd.Timestamp(dev_start_dt).tz_convert(None)
    dev_end_time = pd.Timestamp(dev_end_exclusive - timedelta(days=1)).tz_convert(None)
    fetch_start_dt = dev_start_dt - timedelta(days=int(args.warmup_days))
    fetch_tickers = tickers + ([] if benchmark_ticker in tickers else [benchmark_ticker])

    print("=" * 110)
    print("MAG7 ADAPTIVE LONG/SHORT BACKTEST V2")
    print("=" * 110)
    print(f"Development window: {args.dev_start_date} to {args.dev_end_date} (holdout excluded)")
    print(f"Final holdout window: {args.holdout_start_date} to {args.holdout_end_date} | 5-minute bars")
    print(f"Tickers: {', '.join(tickers)} | Portfolio: ${initial_capital:,.2f}, split 1/7 per ticker")
    print(f"Target: {target:.2f}% average monthly | Commission: ${round_trip_commission:.2f}/round-trip")
    print("=" * 110)

    fetched = fetch_ohlcv_for_tickers_sync(
        tickers=fetch_tickers,
        start_time=fetch_start_dt,
        end_time=dev_end_exclusive,
        period=Period.DAILY,
    )
    data_by_ticker = {ticker: fetched[ticker] for ticker in tickers if ticker in fetched}
    if len(data_by_ticker) != len(tickers):
        missing = sorted(set(tickers) - set(data_by_ticker))
        raise SystemExit(f"Missing development data for: {', '.join(missing)}")
    benchmark = fetched.get(benchmark_ticker)

    candidates = _candidate_params(max_candidates=int(args.max_candidates))
    print(f"Scoring {len(candidates)} frozen parameter candidates on development data only...")
    scored: list[dict[str, Any]] = []
    best_result: SleevePortfolioResult | None = None
    best_params: AdaptiveLongShortParams | None = None

    for idx, params in enumerate(candidates, start=1):
        featured = compute_mag7_adaptive_features(
            data_by_ticker=data_by_ticker,
            params=params,
            benchmark=benchmark,
        )
        result = run_equal_weight_sleeve_portfolio(
            data_by_ticker=featured,
            tickers=tickers,
            params=params,
            initial_capital=initial_capital,
            round_trip_commission=round_trip_commission,
            target_monthly_return_pct=target,
            start_time=dev_start_time,
            end_time=dev_end_time,
            short_borrow_fee_apr=short_borrow_fee_apr,
        )
        summary = _result_summary(result=result, target=target)
        score = _score_candidate(summary)
        scored.append({"score": score, "params": params.to_dict(), "summary": summary})
        if best_result is None or score > float(scored[-2]["score"] if len(scored) > 1 and best_result is result else -np.inf):
            current_best = max(scored, key=lambda item: float(item["score"]))
            if current_best is scored[-1]:
                best_result = result
                best_params = params
        if idx % 100 == 0:
            leader = max(scored, key=lambda item: float(item["score"]))
            leader_summary = leader["summary"]
            print(
                f"  tested {idx:>4}/{len(candidates)} | best mean_monthly="
                f"{leader_summary['mean_monthly_return_pct']:+.2f}% | "
                f"dd={leader_summary['max_drawdown_pct']:+.2f}% | "
                f"min_iso={leader_summary['isolated_min_mean_monthly_return_pct']:+.2f}%"
            )

    scored.sort(key=lambda item: float(item["score"]), reverse=True)
    best = scored[0]
    best_params = AdaptiveLongShortParams(**best["params"])
    featured = compute_mag7_adaptive_features(
        data_by_ticker=data_by_ticker,
        params=best_params,
        benchmark=benchmark,
    )
    best_result = run_equal_weight_sleeve_portfolio(
        data_by_ticker=featured,
        tickers=tickers,
        params=best_params,
        initial_capital=initial_capital,
        round_trip_commission=round_trip_commission,
        target_monthly_return_pct=target,
        start_time=dev_start_time,
        end_time=dev_end_time,
        short_borrow_fee_apr=short_borrow_fee_apr,
    )
    dev_summary = _result_summary(result=best_result, target=target)
    print("\nBEST DEVELOPMENT RESULT")
    print("-" * 110)
    _print_summary(dev_summary)
    print("Params:", json.dumps(best_params.to_dict(), sort_keys=True))

    holdout_summary: dict[str, Any] | None = None
    holdout_data_note = "skipped"
    holdout_featured: dict[str, pd.DataFrame] = {}
    holdout_result: SleevePortfolioResult | None = None
    if str(args.holdout_provider) != "skip":
        holdout_data, holdout_data_note = _fetch_holdout_5min(
            provider=str(args.holdout_provider),
            tickers=tickers,
            benchmark_ticker=benchmark_ticker,
            start_time=holdout_start_dt,
            end_time=holdout_end_exclusive,
            alpaca_feed=str(args.alpaca_feed),
        )
        holdout_strategy_data = {ticker: holdout_data[ticker] for ticker in tickers if ticker in holdout_data}
        holdout_benchmark = holdout_data.get(benchmark_ticker)
        if len(holdout_strategy_data) == len(tickers):
            holdout_featured = compute_mag7_adaptive_features(
                data_by_ticker=holdout_strategy_data,
                params=best_params,
                benchmark=holdout_benchmark,
            )
            holdout_result = run_equal_weight_sleeve_portfolio(
                data_by_ticker=holdout_featured,
                tickers=tickers,
                params=best_params,
                initial_capital=initial_capital,
                round_trip_commission=round_trip_commission,
                target_monthly_return_pct=target,
                start_time=pd.Timestamp(holdout_start_dt).tz_convert(None),
                end_time=pd.Timestamp(holdout_end_exclusive - timedelta(seconds=1)).tz_convert(None),
                short_borrow_fee_apr=short_borrow_fee_apr,
            )
            holdout_summary = _result_summary(result=holdout_result, target=target)
            print("\nLAST-TWO-MONTH 5-MINUTE HOLDOUT RESULT")
            print("-" * 110)
            print(holdout_data_note)
            _print_summary(holdout_summary)
        else:
            missing = sorted(set(tickers) - set(holdout_strategy_data))
            holdout_data_note = f"{holdout_data_note}; missing holdout data for: {', '.join(missing)}"
            print("\nHoldout skipped:", holdout_data_note)

    chart_path = str(args.chart_path or "").strip()
    if chart_path and not bool(args.no_chart):
        chart_source_result = holdout_result if holdout_result is not None else best_result
        chart_source_data = holdout_featured if holdout_featured else featured
        chart_title = (
            "Mag7 Adaptive Long/Short 5-Minute Holdout Trades"
            if holdout_result is not None
            else "Mag7 Adaptive Long/Short Development Trades"
        )
        _plot_trade_chart(
            data_by_ticker=chart_source_data,
            result=chart_source_result,
            path=Path(chart_path).expanduser().resolve(),
            title=chart_title,
        )

    payload = {
        "strategy": "mag7_adaptive_long_short",
        "tickers": tickers,
        "benchmark_ticker": benchmark_ticker,
        "development_window": {
            "start": str(args.dev_start_date),
            "end": str(args.dev_end_date),
            "warmup_days": int(args.warmup_days),
            "period": "daily",
        },
        "holdout_window": {
            "start": str(args.holdout_start_date),
            "end": str(args.holdout_end_date),
            "period": "5Min",
            "provider": str(args.holdout_provider),
            "data_note": holdout_data_note,
        },
        "initial_capital": initial_capital,
        "target_monthly_return_pct": target,
        "round_trip_commission": round_trip_commission,
        "short_borrow_fee_apr": short_borrow_fee_apr,
        "best_params": best_params.to_dict(),
        "development": dev_summary,
        "holdout": holdout_summary,
        "top_candidates": scored[:25],
        "chart_path": chart_path if chart_path and not bool(args.no_chart) else "",
    }
    _write_json_summary(path=str(args.output_json), payload=payload)

    dev_pass = _passes_summary(dev_summary, target=target)
    holdout_pass = holdout_summary is not None and _passes_summary(holdout_summary, target=target)
    print("\nGATE")
    print("-" * 110)
    print(f"Development gate: {'PASS' if dev_pass else 'MISS'}")
    print(f"Holdout gate: {'PASS' if holdout_pass else 'MISS'}")
    print(f"Live conversion allowed: {'YES' if dev_pass and holdout_pass else 'NO'}")
    print("=" * 110)
    return 0


def _candidate_params(*, max_candidates: int) -> list[AdaptiveLongShortParams]:
    """Build deterministic parameter candidates from the implement-first slate."""
    candidates: list[AdaptiveLongShortParams] = []
    momentum_sets = ((5, 21, 63), (10, 42, 126), (21, 63, 126), (3, 10, 42))
    trend_sets = ((20, 5), (30, 10), (50, 20))
    stop_sets = ((2.0, 0.02, 0.08), (3.0, 0.025, 0.12), (4.0, 0.04, 0.16))
    rr_values = (1.5, 2.0, 2.5, 3.0)
    leverage_values = (1.0, 1.5, 2.0, 2.5)
    hold_values = (10, 21, 42, 63)
    trend_strength_values = (0.0, 0.003, 0.006, 0.01)
    score_bands = ((0.0, 0.0), (0.01, -0.01), (-0.01, 0.01))

    for fast, mid, slow in momentum_sets:
        for trend_ema, slope_bars in trend_sets:
            for atr_mult, min_stop, max_stop in stop_sets:
                for rr in rr_values:
                    for leverage in leverage_values:
                        for max_hold in hold_values:
                            for trend_strength in trend_strength_values:
                                for min_long_score, max_short_score in score_bands:
                                    candidates.append(
                                        AdaptiveLongShortParams(
                                            fast_momentum_bars=fast,
                                            mid_momentum_bars=mid,
                                            slow_momentum_bars=slow,
                                            trend_ema_period=trend_ema,
                                            trend_slope_bars=slope_bars,
                                            atr_stop_multiplier=atr_mult,
                                            min_stop_pct=min_stop,
                                            max_stop_pct=max_stop,
                                            risk_reward_ratio=rr,
                                            max_holding_bars=max_hold,
                                            min_trend_strength=trend_strength,
                                            min_long_score=min_long_score,
                                            max_short_score=max_short_score,
                                            leverage=leverage,
                                            exposure_fraction=1.0,
                                            allow_market_regime_shorts=True,
                                            close_on_neutral=True,
                                        )
                                    )
                                    if len(candidates) >= max_candidates:
                                        return candidates
    return candidates


def _result_summary(*, result: SleevePortfolioResult, target: float) -> dict[str, Any]:
    isolated = {}
    isolated_means = []
    isolated_drawdowns = []
    for ticker, equity in result.sleeve_equity_curves.items():
        monthly = equity.resample("ME").last().pct_change().dropna() * 100.0
        mean_monthly = float(monthly.mean()) if not monthly.empty else 0.0
        drawdown = _max_drawdown_pct(equity)
        isolated[ticker] = {
            "final_equity": float(equity.iloc[-1]) if not equity.empty else 0.0,
            "return_pct": ((float(equity.iloc[-1]) / float(equity.iloc[0])) - 1.0) * 100.0
            if len(equity) > 1 and float(equity.iloc[0]) > 0.0
            else 0.0,
            "mean_monthly_return_pct": mean_monthly,
            "max_drawdown_pct": drawdown,
            "months": int(len(monthly)),
            "months_at_or_above_target": int((monthly >= float(target)).sum()),
            "trades": int(len(result.trades_by_ticker.get(ticker, ()))),
        }
        isolated_means.append(mean_monthly)
        isolated_drawdowns.append(drawdown)

    monthly_returns = result.monthly_returns_pct
    return {
        "final_equity": result.final_equity,
        "return_pct": result.return_pct,
        "mean_monthly_return_pct": result.mean_monthly_return_pct,
        "max_drawdown_pct": result.max_drawdown_pct,
        "trades": result.trades,
        "winning_trades": result.winning_trades,
        "losing_trades": result.losing_trades,
        "win_rate_pct": result.win_rate_pct,
        "profit_factor": result.profit_factor,
        "months": result.months,
        "months_at_or_above_target": result.months_at_or_above_target,
        "monthly_returns_pct": {
            pd.Timestamp(index).strftime("%Y-%m-%d"): float(value)
            for index, value in monthly_returns.items()
        },
        "all_months_at_or_above_target": bool(
            not monthly_returns.empty and (monthly_returns >= float(target)).all()
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
    trade_count = int(summary["trades"])
    profit_factor = float(summary["profit_factor"])
    months = max(1, int(summary["months"]))
    hit_ratio = float(summary["months_at_or_above_target"]) / months
    trade_bonus = min(2.0, trade_count / 250.0)
    return (
        mean_monthly * 2.0
        + min_iso
        + hit_ratio * 4.0
        + min(5.0, profit_factor)
        + trade_bonus
        - drawdown / 4.0
    )


def _passes_summary(summary: dict[str, Any], *, target: float) -> bool:
    return (
        float(summary["mean_monthly_return_pct"]) >= float(target)
        and float(summary["isolated_min_mean_monthly_return_pct"]) >= float(target)
        and int(summary["trades"]) >= 30
        and float(summary["max_drawdown_pct"]) > -35.0
    )


def _print_summary(summary: dict[str, Any]) -> None:
    print(f"Final equity: ${float(summary['final_equity']):,.2f}")
    print(f"Return: {float(summary['return_pct']):+.2f}%")
    print(f"Average monthly return: {float(summary['mean_monthly_return_pct']):+.2f}%")
    print(f"Max drawdown: {float(summary['max_drawdown_pct']):+.2f}%")
    print(
        f"Trades: {int(summary['trades'])} | W:L "
        f"{int(summary['winning_trades'])}:{int(summary['losing_trades'])} | "
        f"Win rate: {float(summary['win_rate_pct']):.2f}% | "
        f"Profit factor: {float(summary['profit_factor']):.2f}"
    )
    print(
        f"Monthly target months: {int(summary['months_at_or_above_target'])}/"
        f"{int(summary['months'])} | all months >= target: {summary['all_months_at_or_above_target']}"
    )
    print(
        f"Isolated ticker average monthly: mean={float(summary['isolated_mean_monthly_return_pct']):+.2f}% | "
        f"min={float(summary['isolated_min_mean_monthly_return_pct']):+.2f}%"
    )


def _fetch_holdout_5min(
    *,
    provider: str,
    tickers: list[str],
    benchmark_ticker: str,
    start_time: datetime,
    end_time: datetime,
    alpaca_feed: str,
) -> tuple[dict[str, pd.DataFrame], str]:
    fetch_tickers = tickers + ([] if benchmark_ticker in tickers else [benchmark_ticker])
    if provider == "yahoo":
        raw = fetch_ohlcv_for_tickers_sync(
            tickers=fetch_tickers,
            start_time=start_time,
            end_time=end_time,
            period=Period.MINUTE,
        )
        data = {ticker: _resample_to_5min(filter_regular_session(frame)) for ticker, frame in raw.items()}
        counts = {ticker: len(frame) for ticker, frame in data.items()}
        return data, f"Yahoo minute->5Min smoke data counts: {counts}"
    if provider == "alpaca":
        return _fetch_alpaca_5min(
            tickers=fetch_tickers,
            start_time=start_time,
            end_time=end_time,
            feed=alpaca_feed,
        )
    return {}, "holdout provider skipped"


def _fetch_alpaca_5min(
    *,
    tickers: list[str],
    start_time: datetime,
    end_time: datetime,
    feed: str,
) -> tuple[dict[str, pd.DataFrame], str]:
    _load_dotenv()
    api_key = os.getenv("ALPACA_API_KEY") or os.getenv("APCA_API_KEY_ID")
    secret_key = os.getenv("ALPACA_SECRET_KEY") or os.getenv("APCA_API_SECRET_KEY")
    if not api_key or not secret_key:
        raise SystemExit(
            "Missing Alpaca data credentials. Set ALPACA_API_KEY and ALPACA_SECRET_KEY in .env."
        )

    try:
        from alpaca.data.enums import DataFeed
        from alpaca.data.historical import StockHistoricalDataClient
        from alpaca.data.requests import StockBarsRequest
        from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
    except ImportError as exc:  # pragma: no cover - dependency gate
        raise SystemExit("Missing alpaca-py dependency. Install requirements.txt.") from exc

    feed_arg = None
    feed_key = str(feed or "").strip().upper()
    if feed_key:
        feed_arg = getattr(DataFeed, feed_key)

    client = StockHistoricalDataClient(api_key, secret_key)
    request_kwargs: dict[str, Any] = {
        "symbol_or_symbols": tickers,
        "timeframe": TimeFrame(5, TimeFrameUnit.Minute),
        "start": _ensure_utc(start_time),
        "end": _ensure_utc(end_time),
    }
    if feed_arg is not None:
        request_kwargs["feed"] = feed_arg
    bars = client.get_stock_bars(StockBarsRequest(**request_kwargs))
    df = bars.df
    data: dict[str, pd.DataFrame] = {}
    if df is None or df.empty:
        return data, "Alpaca returned no 5Min holdout bars."

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
        frame = filter_regular_session(frame)
        data[ticker] = frame
    counts = {ticker: len(frame) for ticker, frame in data.items()}
    note = f"Alpaca 5Min RTH data counts: {counts}"
    return data, note


def _resample_to_5min(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return frame
    return (
        frame.resample("5min", label="left", closed="left")
        .agg({"Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"})
        .dropna(subset=["Open", "High", "Low", "Close"])
    )


def _plot_trade_chart(
    *,
    data_by_ticker: dict[str, pd.DataFrame],
    result: SleevePortfolioResult,
    path: Path,
    title: str,
) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.dates as mdates
    import matplotlib.pyplot as plt
    from matplotlib.lines import Line2D

    tickers = [ticker for ticker in MAG7_TICKERS if ticker in data_by_ticker]
    if not tickers:
        return
    path.parent.mkdir(parents=True, exist_ok=True)

    fig = plt.figure(figsize=(18, 23), constrained_layout=True)
    gs = fig.add_gridspec(nrows=8, ncols=1, height_ratios=[2.2, 1, 1, 1, 1, 1, 1, 1])
    ax_eq = fig.add_subplot(gs[0, 0])
    ax_eq.plot(result.equity_curve.index, result.equity_curve.values, color="#111827", linewidth=2.1)
    ax_eq.set_title(
        f"{title} | Return {result.return_pct:+.2f}% | "
        f"Avg Month {result.mean_monthly_return_pct:+.2f}% | DD {result.max_drawdown_pct:+.2f}%"
    )
    ax_eq.set_ylabel("Portfolio equity")
    ax_eq.yaxis.set_major_formatter(lambda value, _: f"${value:,.0f}")
    ax_eq.grid(True, alpha=0.35)

    long_color = "#16a34a"
    short_color = "#7e22ce"
    cover_color = "#2563eb"
    sell_color = "#dc2626"
    price_color = "#374151"
    axes = []

    for idx, ticker in enumerate(tickers, start=1):
        ax = fig.add_subplot(gs[idx, 0], sharex=ax_eq)
        axes.append(ax)
        frame = data_by_ticker[ticker]
        ax.plot(frame.index, frame["Close"], color=price_color, linewidth=0.85, alpha=0.85)
        trades = result.trades_by_ticker.get(ticker, ())
        long_entries = [trade for trade in trades if trade.direction == "Long"]
        short_entries = [trade for trade in trades if trade.direction == "Short"]
        if long_entries:
            ax.scatter(
                [pd.Timestamp(trade.entry_time) for trade in long_entries],
                [trade.entry_price for trade in long_entries],
                marker="^",
                s=50,
                c=long_color,
                edgecolors="white",
                linewidths=0.6,
                zorder=4,
            )
            ax.scatter(
                [pd.Timestamp(trade.exit_time) for trade in long_entries],
                [trade.exit_price for trade in long_entries],
                marker="v",
                s=50,
                c=sell_color,
                edgecolors="white",
                linewidths=0.6,
                zorder=5,
            )
        if short_entries:
            ax.scatter(
                [pd.Timestamp(trade.entry_time) for trade in short_entries],
                [trade.entry_price for trade in short_entries],
                marker="v",
                s=50,
                c=short_color,
                edgecolors="white",
                linewidths=0.6,
                zorder=4,
            )
            ax.scatter(
                [pd.Timestamp(trade.exit_time) for trade in short_entries],
                [trade.exit_price for trade in short_entries],
                marker="^",
                s=50,
                c=cover_color,
                edgecolors="white",
                linewidths=0.6,
                zorder=5,
            )
        ax.set_ylabel(ticker)
        ax.grid(True, alpha=0.3)
        ax.text(
            0.995,
            0.88,
            f"{ticker} trades: {len(trades)}",
            transform=ax.transAxes,
            ha="right",
            va="top",
            fontsize=8.5,
            bbox={"boxstyle": "round,pad=0.22", "facecolor": "white", "edgecolor": "#e5e7eb"},
        )

    legend_handles = [
        Line2D([0], [0], color=price_color, linewidth=1.2, label="Close"),
        Line2D([0], [0], marker="^", color="none", markerfacecolor=long_color, markersize=9, label="Long"),
        Line2D([0], [0], marker="v", color="none", markerfacecolor=short_color, markersize=9, label="Short"),
        Line2D([0], [0], marker="^", color="none", markerfacecolor=cover_color, markersize=9, label="Cover"),
        Line2D([0], [0], marker="v", color="none", markerfacecolor=sell_color, markersize=9, label="Sell"),
    ]
    axes[0].legend(handles=legend_handles, loc="upper left", ncols=5, fontsize=9)
    for axis in [ax_eq, *axes]:
        axis.xaxis.set_major_locator(mdates.AutoDateLocator(minticks=5, maxticks=10))
        axis.xaxis.set_major_formatter(mdates.ConciseDateFormatter(axis.xaxis.get_major_locator()))
    axes[-1].set_xlabel("Date")
    fig.savefig(path, dpi=170, bbox_inches="tight")
    plt.close(fig)
    print(f"Chart saved: {path}")


def _write_json_summary(*, path: str, payload: dict[str, Any]) -> None:
    output_path = Path(path).expanduser().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    print(f"JSON saved: {output_path}")


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
