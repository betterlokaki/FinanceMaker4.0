#!/usr/bin/env python3
"""Load trained forecast bundle and run load-only Jan-Feb 2026 evaluation."""
from __future__ import annotations

import argparse
from dataclasses import replace
from pathlib import Path
import sys

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ROOT_PATH = str(PROJECT_ROOT)
if ROOT_PATH not in sys.path:
    sys.path.insert(0, ROOT_PATH)

from backtests.forecasting.config import ForecastConfig, TradeLogicConfig
from backtests.forecasting.data import fetch_hourly_ohlcv, slice_time_window
from backtests.forecasting.evaluate import run_forecast_inference_backtest
from backtests.forecasting.io import load_model_bundle, save_csv, save_json


def _parse_float_csv(raw: str) -> list[float]:
    values = []
    for part in str(raw).split(","):
        text = part.strip()
        if not text:
            continue
        values.append(float(text))
    if not values:
        raise ValueError("Expected at least one float value.")
    return values


def _as_utc_naive(value: pd.Timestamp) -> pd.Timestamp:
    ts = pd.Timestamp(value)
    if ts.tzinfo is None:
        ts = ts.tz_localize("UTC")
    else:
        ts = ts.tz_convert("UTC")
    return ts.tz_localize(None)


def _timeline_from_data(
    *,
    data_by_ticker: dict[str, pd.DataFrame],
    start: pd.Timestamp,
    end: pd.Timestamp,
) -> pd.DatetimeIndex:
    start_naive = _as_utc_naive(start)
    end_naive = _as_utc_naive(end)
    points: list[pd.Timestamp] = []
    for bars in data_by_ticker.values():
        if bars.empty:
            continue
        window = bars.loc[(bars.index >= start_naive) & (bars.index <= end_naive)]
        if window.empty:
            continue
        points.extend(pd.DatetimeIndex(window.index).to_list())
    if not points:
        return pd.DatetimeIndex([], dtype="datetime64[ns]")
    return pd.DatetimeIndex(sorted(set(points)))


def _equity_curve_from_trades(
    *,
    trades: pd.DataFrame,
    timeline: pd.DatetimeIndex,
    initial_capital: float,
) -> pd.Series:
    if timeline.empty:
        return pd.Series(dtype=float)
    if trades.empty:
        return pd.Series(float(initial_capital), index=timeline, dtype=float)
    frame = trades.copy()
    frame["exit_time"] = pd.to_datetime(frame["exit_time"], errors="coerce")
    frame["net_pnl"] = pd.to_numeric(frame["net_pnl"], errors="coerce").fillna(0.0)
    exit_delta = frame.groupby("exit_time", dropna=True)["net_pnl"].sum().sort_index()
    cumulative = exit_delta.cumsum()
    aligned = cumulative.reindex(timeline).ffill().fillna(0.0)
    return pd.Series(float(initial_capital) + aligned, index=timeline, dtype=float)


def _spy_buy_hold_curve(
    *,
    timeline: pd.DatetimeIndex,
    start: pd.Timestamp,
    end: pd.Timestamp,
    initial_capital: float,
) -> pd.Series:
    if timeline.empty:
        return pd.Series(dtype=float)
    spy_map = fetch_hourly_ohlcv(
        tickers=["SPY"],
        start=start,
        end=end,
        warmup_days=0,
    )
    spy = spy_map.get("SPY", pd.DataFrame())
    if spy.empty:
        return pd.Series(dtype=float)
    spy = slice_time_window(spy, start=start, end=end)
    if spy.empty:
        return pd.Series(dtype=float)
    close = pd.to_numeric(spy["Close"], errors="coerce")
    close = close.reindex(timeline).ffill()
    close = close.dropna()
    if close.empty:
        return pd.Series(dtype=float)
    base = float(close.iloc[0])
    if not np.isfinite(base) or base <= 0.0:
        return pd.Series(dtype=float)
    return pd.Series(float(initial_capital) * (close / base), index=close.index, dtype=float)


def _plot_equity_curves(
    *,
    total_strategy_equity: pd.Series,
    per_ticker_equity: dict[str, pd.Series],
    spy_equity: pd.Series,
    out_path: Path,
) -> Path:
    if total_strategy_equity.empty:
        raise ValueError("Cannot plot empty strategy equity.")
    import matplotlib.pyplot as plt

    out_path.parent.mkdir(parents=True, exist_ok=True)

    plt.figure(figsize=(15, 8))
    for ticker in sorted(per_ticker_equity):
        eq = per_ticker_equity[ticker]
        if eq.empty:
            continue
        plt.plot(eq.index, eq.values, linewidth=1.0, alpha=0.35, label=f"{ticker} Strategy")

    plt.plot(
        total_strategy_equity.index,
        total_strategy_equity.values,
        linewidth=2.8,
        color="#0b5fff",
        label="MAG7 Forecast Strategy (Total)",
    )
    if not spy_equity.empty:
        plt.plot(
            spy_equity.index,
            spy_equity.values,
            linewidth=2.2,
            color="black",
            linestyle="--",
            label="SPY Buy & Hold",
        )

    plt.title("MAG7 Forecast Strategy vs SPY Buy & Hold")
    plt.xlabel("Time")
    plt.ylabel("Equity ($)")
    plt.grid(True, alpha=0.25)
    plt.legend(ncols=2)
    plt.tight_layout()
    plt.savefig(out_path, dpi=150)
    plt.close()
    return out_path


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Load-only forecast inference + RR test engine (Jan-Feb + Jan slice reports).",
    )
    parser.add_argument("--model-dir", type=str, required=True, help="Path to trained model bundle directory.")
    parser.add_argument("--test-start", type=str, default="2026-01-01")
    parser.add_argument("--test-end", type=str, default="2026-02-28")
    parser.add_argument("--warmup-days", type=int, default=120)
    parser.add_argument("--report-root", type=str, default="backtests/results/forecasting/reports")

    parser.add_argument("--atr-multiplier", type=float, default=1.0)
    parser.add_argument("--rr-ratio", type=float, default=4.0)
    parser.add_argument("--min-edge", type=float, default=0.0003)
    parser.add_argument("--min-tp-prob", type=float, default=0.50)
    parser.add_argument("--max-hold-candles", type=int, default=3)
    parser.add_argument("--long-round-trip-fee", type=float, default=2.5)
    parser.add_argument("--short-round-trip-fee", type=float, default=5.0)
    parser.add_argument("--slippage-ticks", type=float, default=0.0)
    parser.add_argument("--tick-size", type=float, default=0.01)
    parser.add_argument(
        "--tune-thresholds",
        action="store_true",
        help="Grid-search min-edge and min-tp-prob on this evaluation window.",
    )
    parser.add_argument(
        "--tune-min-edge-values",
        type=str,
        default="-0.02,-0.01,-0.005,-0.002,-0.001,0.0,0.0003",
        help="CSV list of min-edge values used when --tune-thresholds is enabled.",
    )
    parser.add_argument(
        "--tune-min-tp-prob-values",
        type=str,
        default="0.0,0.1,0.2,0.3,0.4,0.5",
        help="CSV list of min-tp-prob values used when --tune-thresholds is enabled.",
    )
    parser.add_argument(
        "--tune-min-trades",
        type=int,
        default=1,
        help="Prefer threshold sets with at least this many trades.",
    )
    parser.add_argument(
        "--plot-equity",
        action="store_true",
        help="Save equity curve plot (strategy + per ticker + SPY buy and hold).",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    model_dir = Path(args.model_dir).resolve()
    bundle = load_model_bundle(model_dir)
    tickers = [str(t).upper() for t in bundle.metadata.get("tickers", [])]
    if not tickers:
        raise SystemExit("Model metadata contains no tickers.")

    test_start = pd.Timestamp(args.test_start, tz="UTC")
    test_end = pd.Timestamp(args.test_end, tz="UTC")
    if test_end < test_start:
        raise SystemExit("--test-end must be >= --test-start")

    forecast_cfg = ForecastConfig(
        horizon=int(bundle.metadata.get("horizon", 3)),
        tickers=tickers,
        test_start=str(test_start.date()),
        test_end=str(test_end.date()),
        warmup_days=max(0, int(args.warmup_days)),
    )
    trade_cfg = TradeLogicConfig(
        atr_multiplier=max(1e-6, float(args.atr_multiplier)),
        rr_ratio=max(1e-6, float(args.rr_ratio)),
        min_edge=float(args.min_edge),
        min_tp_prob=float(args.min_tp_prob),
        max_hold_candles=max(1, int(args.max_hold_candles)),
        long_round_trip_fee=max(0.0, float(args.long_round_trip_fee)),
        short_round_trip_fee=max(0.0, float(args.short_round_trip_fee)),
        slippage_ticks=max(0.0, float(args.slippage_ticks)),
        tick_size=max(1e-9, float(args.tick_size)),
    )

    print("=" * 96)
    print("MAG7 FORECAST LOAD-ONLY TEST")
    print("=" * 96)
    print(f"Model dir: {model_dir}")
    print(f"Tickers: {', '.join(tickers)}")
    print(f"Test: {test_start.date()} -> {test_end.date()} | Horizon={forecast_cfg.horizon}")
    print(f"Warmup days: {forecast_cfg.warmup_days}")

    data_by_ticker = fetch_hourly_ohlcv(
        tickers=tickers,
        start=test_start,
        end=test_end,
        warmup_days=forecast_cfg.warmup_days,
    )
    if not data_by_ticker:
        raise SystemExit("No test OHLCV data fetched.")

    run_id = str(bundle.run_id)
    report_dir = Path(args.report_root).resolve() / run_id

    tuning_df = pd.DataFrame()
    if bool(args.tune_thresholds):
        edge_values = _parse_float_csv(args.tune_min_edge_values)
        tp_values = _parse_float_csv(args.tune_min_tp_prob_values)
        min_trades = max(0, int(args.tune_min_trades))
        print(
            f"Tuning thresholds over {len(edge_values) * len(tp_values)} combinations "
            f"(min_trades preference={min_trades})..."
        )
        best_result = None
        best_cfg = trade_cfg
        best_key = None
        tuning_rows: list[dict[str, float]] = []
        for edge in edge_values:
            for prob in tp_values:
                candidate_cfg = replace(trade_cfg, min_edge=float(edge), min_tp_prob=float(prob))
                candidate_result = run_forecast_inference_backtest(
                    bundle=bundle,
                    data_by_ticker=data_by_ticker,
                    forecast_cfg=forecast_cfg,
                    trade_cfg=candidate_cfg,
                    test_start=test_start,
                    test_end=test_end,
                )
                summary = candidate_result.summary_jan_feb
                trade_count = int(summary.get("trade_count", 0) or 0)
                net_return = float(summary.get("net_return_pct", 0.0) or 0.0)
                win_rate = float(summary.get("win_rate", 0.0) or 0.0)
                tuning_rows.append(
                    {
                        "min_edge": float(edge),
                        "min_tp_prob": float(prob),
                        "trade_count": trade_count,
                        "net_return_pct": net_return,
                        "win_rate": win_rate,
                    }
                )

                meets = 1 if trade_count >= min_trades else 0
                key = (meets, net_return, trade_count, win_rate)
                if best_key is None or key > best_key:
                    best_key = key
                    best_result = candidate_result
                    best_cfg = candidate_cfg

        tuning_df = pd.DataFrame(tuning_rows).sort_values(
            ["trade_count", "net_return_pct", "win_rate", "min_edge", "min_tp_prob"],
            ascending=[False, False, False, True, True],
        )
        if best_result is None:
            raise SystemExit("Threshold tuning failed to produce any candidate.")
        result = best_result
        trade_cfg = best_cfg
        save_csv(tuning_df, report_dir / "tuning_thresholds.csv")
        print(
            "Selected thresholds: "
            f"min_edge={trade_cfg.min_edge:.6f}, min_tp_prob={trade_cfg.min_tp_prob:.3f}"
        )
    else:
        result = run_forecast_inference_backtest(
            bundle=bundle,
            data_by_ticker=data_by_ticker,
            forecast_cfg=forecast_cfg,
            trade_cfg=trade_cfg,
            test_start=test_start,
            test_end=test_end,
        )

    pred_path = save_csv(result.predictions, report_dir / "predictions_test.csv")
    trades_path = save_csv(result.trades, report_dir / "trades_test.csv")
    full_summary = dict(result.summary_jan_feb)
    full_summary["selected_min_edge"] = float(trade_cfg.min_edge)
    full_summary["selected_min_tp_prob"] = float(trade_cfg.min_tp_prob)
    full_summary["selected_rr_ratio"] = float(trade_cfg.rr_ratio)
    full_summary["selected_atr_multiplier"] = float(trade_cfg.atr_multiplier)

    equity_plot_path: Path | None = None
    if bool(args.plot_equity):
        timeline = _timeline_from_data(data_by_ticker=data_by_ticker, start=test_start, end=test_end)
        initial_total = float(forecast_cfg.initial_capital_per_ticker) * float(len(tickers))
        total_equity = _equity_curve_from_trades(
            trades=result.trades,
            timeline=timeline,
            initial_capital=initial_total,
        )
        per_ticker_equity: dict[str, pd.Series] = {}
        for ticker in tickers:
            ticker_trades = result.trades.loc[result.trades["ticker"] == ticker].copy() if not result.trades.empty else pd.DataFrame()
            per_ticker_equity[ticker] = _equity_curve_from_trades(
                trades=ticker_trades,
                timeline=timeline,
                initial_capital=float(forecast_cfg.initial_capital_per_ticker),
            )
        spy_equity = _spy_buy_hold_curve(
            timeline=timeline,
            start=test_start,
            end=test_end,
            initial_capital=initial_total,
        )
        if not spy_equity.empty:
            spy_ret = ((float(spy_equity.iloc[-1]) / float(initial_total)) - 1.0) * 100.0
            full_summary["spy_buy_hold_return_pct"] = float(spy_ret)
            full_summary["outperformance_vs_spy_pct"] = float(
                float(full_summary.get("net_return_pct", 0.0)) - float(spy_ret)
            )
        equity_plot_path = _plot_equity_curves(
            total_strategy_equity=total_equity,
            per_ticker_equity=per_ticker_equity,
            spy_equity=spy_equity,
            out_path=report_dir / "equity_vs_spy.png",
        )
        full_summary["equity_plot_path"] = str(equity_plot_path)

    full_path = save_json(full_summary, report_dir / "summary_jan_feb.json")
    jan_path = save_json(result.summary_jan_only, report_dir / "summary_jan_only.json")

    print(
        "Jan-Feb summary: "
        f"trades={result.summary_jan_feb.get('trade_count', 0)} | "
        f"net_return={result.summary_jan_feb.get('net_return_pct', 0.0):.2f}% | "
        f"win_rate={result.summary_jan_feb.get('win_rate', 0.0) * 100.0:.2f}%"
    )
    print(f"Saved predictions: {pred_path}")
    print(f"Saved trades: {trades_path}")
    print(f"Saved summary (Jan-Feb): {full_path}")
    print(f"Saved summary (Jan only): {jan_path}")
    if bool(args.tune_thresholds):
        print(f"Saved tuning table: {report_dir / 'tuning_thresholds.csv'}")
    if equity_plot_path is not None:
        print(f"Saved equity plot: {equity_plot_path}")
    print("=" * 96)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
