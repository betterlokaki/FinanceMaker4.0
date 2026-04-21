#!/usr/bin/env python3
"""Run forecast-model RR strategy as a regular backtesting.py Strategy with plotting."""
from __future__ import annotations

import argparse
from datetime import timedelta
from pathlib import Path
import sys
from typing import Any

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ROOT_PATH = str(PROJECT_ROOT)
if ROOT_PATH not in sys.path:
    sys.path.insert(0, ROOT_PATH)

from backtests.backtesting_py.data_adapter import infer_tick_size
from backtests.backtesting_py.isolated_backtest_engine import (
    build_equity_series_from_stats,
    build_single_buy_and_hold_equity,
    fetch_ohlcv_for_tickers_sync,
    filter_regular_session,
    parse_date_range_utc,
    plot_isolated_ticker_candlestick_trade_markers,
    plot_isolated_ticker_equity_curves,
    print_symbol_stats,
    resolve_tickers,
)
from backtests.backtesting_py.forecast_model_rr_strategy import ForecastModelRRStrategy
from backtests.backtesting_py.cost_model import make_commission_callable
from common.models.period import Period


try:
    from backtesting import Backtest
except Exception as exc:  # pragma: no cover - runtime dependency gate
    raise RuntimeError("Missing dependency `backtesting`.") from exc


DEFAULT_TICKERS: list[str] = [
    "AAPL",
    "MSFT",
    "NVDA",
    "AMZN",
    "META",
    "TSLA",
    "GOOGL",
]


def _parse_float_csv(raw: str) -> list[float]:
    out: list[float] = []
    for token in str(raw).split(","):
        text = token.strip()
        if not text:
            continue
        out.append(float(text))
    if not out:
        raise ValueError("Expected at least one numeric value.")
    return out


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Forecast model RR strategy backtest (inherits backtesting.Strategy).",
    )
    parser.add_argument("--model-dir", type=str, required=True)
    parser.add_argument("--tickers", nargs="+", default=None)
    parser.add_argument("--start-date", type=str, required=True)
    parser.add_argument("--end-date", type=str, required=True)
    parser.add_argument("--warmup-days", type=int, default=120)

    parser.add_argument("--initial-capital", type=float, default=10_000.0)
    parser.add_argument("--leverage", type=float, default=1.0)
    parser.add_argument("--round-trip-commission", type=float, default=0.0)
    parser.add_argument("--benchmark-ticker", type=str, default="SPY")

    parser.add_argument("--trade-direction", choices=["Both", "Long Only", "Short Only"], default="Both")
    parser.add_argument("--lookback-bars", type=int, default=70)
    parser.add_argument("--prediction-target-pct", type=float, default=0.03)
    parser.add_argument("--max-adverse-pct", type=float, default=0.01)
    parser.add_argument("--stop-loss-pct", type=float, default=0.01)
    parser.add_argument("--risk-reward-ratio", type=float, default=3.0)
    parser.add_argument("--max-hold-bars", type=int, default=3)

    parser.add_argument("--tune-thresholds", action="store_true")
    parser.add_argument("--tune-target-move-values", type=str, default="0.02,0.025,0.03,0.035,0.04")
    parser.add_argument("--tune-max-adverse-values", type=str, default="0.005,0.0075,0.01,0.0125,0.015")
    parser.add_argument("--tune-min-trades", type=int, default=1)

    parser.add_argument("--regular-session-only", action="store_true")
    parser.add_argument("--no-plot", action="store_true")
    return parser.parse_args(argv)


def _run_isolated(
    *,
    data_by_ticker: dict[str, pd.DataFrame],
    strategy_common_kwargs: dict[str, Any],
    initial_capital: float,
    leverage: float,
    commission_per_side: float,
) -> tuple[dict[str, pd.Series], dict[str, pd.Series]]:
    stats_by_ticker: dict[str, pd.Series] = {}
    equity_by_ticker: dict[str, pd.Series] = {}

    for ticker, df in data_by_ticker.items():
        if df.empty or len(df) < 100:
            continue
        tick_size = infer_tick_size(df, fallback=0.01)
        bt = Backtest(
            data=df,
            strategy=ForecastModelRRStrategy,
            cash=float(initial_capital),
            commission=make_commission_callable(
                commission_rate=0.0,
                tick_size=tick_size,
                slippage_ticks=0.0,
                fixed_commission_per_side=max(0.0, float(commission_per_side)),
            ),
            margin=1.0 / float(leverage),
            trade_on_close=False,
            hedging=False,
            exclusive_orders=False,
            finalize_trades=True,
        )

        kwargs = dict(strategy_common_kwargs)
        kwargs["ticker"] = str(ticker).upper()
        stats = bt.run(**kwargs)
        stats_by_ticker[ticker] = stats
        equity_by_ticker[ticker] = build_equity_series_from_stats(
            stats=stats,
            fallback_index=pd.DatetimeIndex(df.index).sort_values(),
            initial_capital=float(initial_capital),
        )

    return stats_by_ticker, equity_by_ticker


def _aggregate_return_pct(
    *,
    equity_by_ticker: dict[str, pd.Series],
    initial_capital: float,
) -> float:
    if not equity_by_ticker:
        return 0.0
    final_sum = 0.0
    count = 0
    for equity in equity_by_ticker.values():
        if equity is None or equity.empty:
            continue
        final_sum += float(equity.iloc[-1])
        count += 1
    if count == 0:
        return 0.0
    initial_total = float(initial_capital) * float(count)
    if initial_total <= 0:
        return 0.0
    return ((final_sum / initial_total) - 1.0) * 100.0


def _total_trade_count(stats_by_ticker: dict[str, pd.Series]) -> int:
    total = 0
    for stats in stats_by_ticker.values():
        total += int(stats.get("# Trades", 0) or 0)
    return total


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    model_dir = Path(str(args.model_dir)).expanduser().resolve()
    if not model_dir.exists():
        raise SystemExit(f"Model directory not found: {model_dir}")

    tickers = resolve_tickers(args.tickers, default_tickers=DEFAULT_TICKERS)
    benchmark_ticker = str(args.benchmark_ticker).strip().upper() or "SPY"

    start_time, end_time_exclusive = parse_date_range_utc(
        start_date=str(args.start_date),
        end_date=str(args.end_date),
    )
    warmup_days = max(0, int(args.warmup_days))
    fetch_start_time = start_time - timedelta(days=warmup_days)
    start_time_utc_naive = pd.Timestamp(start_time).tz_convert("UTC").tz_localize(None)

    initial_capital = float(args.initial_capital)
    leverage = float(args.leverage)
    commission_per_side = max(0.0, float(args.round_trip_commission) / 2.0)

    print("=" * 96)
    print("FORECAST MODEL RR BACKTEST (backtesting.Strategy)")
    print("=" * 96)
    print(f"Model dir: {model_dir}")
    print(f"Tickers: {', '.join(tickers)}")
    print(
        f"Date range: {start_time.date()} to {(end_time_exclusive - pd.Timedelta(days=1)).date()} | "
        f"Interval: hour | Regular session only: {bool(args.regular_session_only)}"
    )
    print(f"Warmup: {warmup_days} day(s) | Fetch start: {fetch_start_time.date()}")

    fetch_tickers = list(tickers)
    if benchmark_ticker not in fetch_tickers:
        fetch_tickers.append(benchmark_ticker)

    fetched_data = fetch_ohlcv_for_tickers_sync(
        tickers=fetch_tickers,
        start_time=fetch_start_time,
        end_time=end_time_exclusive,
        period=Period.HOUR,
    )
    if not fetched_data:
        print("No data fetched for any ticker.")
        return 1

    data_by_ticker: dict[str, pd.DataFrame] = {}
    for ticker in tickers:
        frame = fetched_data.get(ticker, pd.DataFrame())
        if frame is None or frame.empty:
            continue
        if bool(args.regular_session_only):
            frame = filter_regular_session(frame)
        frame = frame[frame.index >= start_time_utc_naive]
        if not frame.empty:
            data_by_ticker[ticker] = frame

    if not data_by_ticker:
        print("No strategy-ticker data after filtering.")
        return 1

    benchmark_df = fetched_data.get(benchmark_ticker, pd.DataFrame())
    if benchmark_df is not None and not benchmark_df.empty:
        if bool(args.regular_session_only):
            benchmark_df = filter_regular_session(benchmark_df)
        benchmark_df = benchmark_df[benchmark_df.index >= start_time_utc_naive]

    strategy_kwargs = {
        "model_dir": str(model_dir),
        "trade_direction": str(args.trade_direction),
        "lookback_bars": max(20, int(args.lookback_bars)),
        "prediction_target_pct": max(0.0001, float(args.prediction_target_pct)),
        "max_adverse_pct": max(0.0001, float(args.max_adverse_pct)),
        "stop_loss_pct": max(0.0001, float(args.stop_loss_pct)),
        "risk_reward_ratio": max(0.1, float(args.risk_reward_ratio)),
        "max_hold_bars": max(1, int(args.max_hold_bars)),
        "use_full_equity_sizing": True,
        "full_equity_fraction": 1.0,
    }

    tuning_rows: list[dict[str, float]] = []
    if bool(args.tune_thresholds):
        target_values = _parse_float_csv(args.tune_target_move_values)
        adverse_values = _parse_float_csv(args.tune_max_adverse_values)
        min_trades = max(0, int(args.tune_min_trades))

        print(f"Tuning thresholds on {len(target_values) * len(adverse_values)} combinations...")
        best_key = None
        best_kwargs = dict(strategy_kwargs)
        for target in target_values:
            for adverse in adverse_values:
                candidate_kwargs = dict(strategy_kwargs)
                candidate_kwargs["prediction_target_pct"] = max(0.0001, float(target))
                candidate_kwargs["max_adverse_pct"] = max(0.0001, float(adverse))

                stats_tmp, equity_tmp = _run_isolated(
                    data_by_ticker=data_by_ticker,
                    strategy_common_kwargs=candidate_kwargs,
                    initial_capital=initial_capital,
                    leverage=leverage,
                    commission_per_side=commission_per_side,
                )
                trade_count = _total_trade_count(stats_tmp)
                ret_pct = _aggregate_return_pct(equity_by_ticker=equity_tmp, initial_capital=initial_capital)
                meets = 1 if trade_count >= min_trades else 0
                key = (meets, ret_pct, trade_count)
                tuning_rows.append(
                    {
                        "prediction_target_pct": float(target),
                        "max_adverse_pct": float(adverse),
                        "trade_count": int(trade_count),
                        "agg_return_pct": float(ret_pct),
                    }
                )
                if best_key is None or key > best_key:
                    best_key = key
                    best_kwargs = candidate_kwargs

        strategy_kwargs = best_kwargs
        print(
            "Selected thresholds: "
            f"prediction_target_pct={float(strategy_kwargs['prediction_target_pct']):.4f}, "
            f"max_adverse_pct={float(strategy_kwargs['max_adverse_pct']):.4f}"
        )

    stats_by_ticker, equity_by_ticker = _run_isolated(
        data_by_ticker=data_by_ticker,
        strategy_common_kwargs=strategy_kwargs,
        initial_capital=initial_capital,
        leverage=leverage,
        commission_per_side=commission_per_side,
    )
    if not stats_by_ticker:
        print("No standalone ticker results to report.")
        return 1

    ranked = sorted(
        stats_by_ticker.items(),
        key=lambda item: float(item[1].get("Return [%]", 0.0)),
        reverse=True,
    )
    print("\n" + "=" * 96)
    print("STANDALONE TICKER SUMMARY")
    print("=" * 96)
    for ticker, stats in ranked:
        print_symbol_stats(ticker, stats)
    print("=" * 96)

    agg_return = _aggregate_return_pct(equity_by_ticker=equity_by_ticker, initial_capital=initial_capital)
    trade_count = _total_trade_count(stats_by_ticker)
    print(
        f"Aggregate strategy return (isolated sum): {agg_return:+.2f}% | Total trades={trade_count}"
    )

    master_index = pd.DatetimeIndex([])
    for equity in equity_by_ticker.values():
        if equity is not None and not equity.empty:
            master_index = master_index.union(pd.DatetimeIndex(equity.index))
    if benchmark_df is not None and not benchmark_df.empty:
        master_index = master_index.union(pd.DatetimeIndex(benchmark_df.index))
    master_index = master_index.sort_values()

    benchmark_bh_equity: pd.Series | None = None
    if benchmark_df is not None and not benchmark_df.empty and not master_index.empty:
        benchmark_bh_equity = build_single_buy_and_hold_equity(
            df=benchmark_df,
            index=master_index,
            initial_capital=initial_capital,
        )
        benchmark_return_pct = (
            ((float(benchmark_bh_equity.iloc[-1]) / initial_capital) - 1.0) * 100.0
            if initial_capital > 0.0
            else 0.0
        )
        print(f"{benchmark_ticker} Buy&Hold: {benchmark_return_pct:+.2f}%")
    else:
        print(f"{benchmark_ticker} Buy&Hold: unavailable (no benchmark data)")

    if tuning_rows:
        out_dir = PROJECT_ROOT / "backtests" / "results" / "forecasting" / "reports" / Path(model_dir).name
        out_dir.mkdir(parents=True, exist_ok=True)
        pd.DataFrame(tuning_rows).sort_values(
            ["trade_count", "agg_return_pct", "prediction_target_pct", "max_adverse_pct"],
            ascending=[False, False, True, True],
        ).to_csv(out_dir / "strategy_tuning_thresholds.csv", index=False)
        print(f"Saved tuning table: {out_dir / 'strategy_tuning_thresholds.csv'}")

    if bool(args.no_plot):
        print("Charts skipped (--no-plot).")
        return 0

    if master_index.empty:
        print("Charts skipped (empty index).")
        return 0

    plot_isolated_ticker_equity_curves(
        equity_by_ticker=equity_by_ticker,
        initial_capital=initial_capital,
        benchmark_bh_equity=benchmark_bh_equity,
        benchmark_ticker=benchmark_ticker,
    )
    chart_ok = plot_isolated_ticker_candlestick_trade_markers(
        data_by_ticker={
            ticker: frame[frame.index >= start_time_utc_naive]
            for ticker, frame in data_by_ticker.items()
        },
        stats_by_ticker=stats_by_ticker,
        title="Forecast Model RR Strategy Candlesticks with Long/Short/Sell/Cover",
    )
    if not chart_ok:
        print("Candlestick marker chart skipped (no executed trades).")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
