#!/usr/bin/env python3
"""Run sparse Grok swing backtest in project style (strategy vs basket B&H vs SPY B&H)."""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys

import httpx
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ROOT_PATH = str(PROJECT_ROOT)
if ROOT_PATH not in sys.path:
    sys.path.insert(0, ROOT_PATH)

from backtests.backtesting_py.data_adapter import fetch_ohlcv_from_yahoo_provider
from backtests.backtesting_py.grok_sparse_swing_strategy import (
    SparseBacktestResult,
    run_sparse_grok_swing_backtest,
)
from common.models.period import Period
from common.settings import settings
from gpt.grok.grok_base import GrokClient
from pullers.market.yahoo.yahoo_market_provider import YahooMarketProvider


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Sparse Grok swing backtest (one startup call + one call after each completed exit)."
    )
    parser.add_argument(
        "--tickers",
        nargs="+",
        default=None,
        help="Ticker symbols (comma or space separated).",
    )
    parser.add_argument(
        "--ticker",
        type=str,
        default="",
        help="Single ticker (backward-compatible alias).",
    )
    parser.add_argument("--benchmark-ticker", type=str, default="SPY", help="Benchmark ticker.")
    parser.add_argument(
        "--start-date",
        type=str,
        default="",
        help="Backtest start date (YYYY-MM-DD).",
    )
    parser.add_argument(
        "--end-date",
        type=str,
        default="",
        help="Backtest end date (YYYY-MM-DD, inclusive).",
    )
    parser.add_argument(
        "--lookback-days",
        type=int,
        default=1200,
        help="Used only if start/end dates are not fully provided.",
    )
    parser.add_argument("--capital", type=float, default=10_000.0, help="Initial portfolio capital.")
    parser.add_argument("--pair-commission", type=float, default=2.5, help="Fixed commission per completed trade pair.")
    parser.add_argument(
        "--entry-policy",
        type=str,
        choices=["touch", "next-bar-open"],
        default="touch",
        help="Entry fill policy.",
    )
    parser.add_argument(
        "--skip-wait-days",
        type=int,
        default=20,
        help="If Grok action is skip, wait N days before asking Grok again.",
    )
    parser.add_argument(
        "--min-confidence",
        type=int,
        default=70,
        help="Enter only when Grok confidence is strictly greater than this value.",
    )
    parser.add_argument("--warmup-bars", type=int, default=200, help="Warmup bars.")
    parser.add_argument("--table-candles", type=int, default=150, help="Candles sent to Grok prompt.")
    parser.add_argument("--pause-seconds", type=float, default=1.0, help="Sleep after each Grok call.")
    parser.add_argument(
        "--print-grok-responses",
        action="store_true",
        default=True,
        help="Print raw Grok responses for each setup call.",
    )
    parser.add_argument(
        "--no-print-grok-responses",
        action="store_false",
        dest="print_grok_responses",
        help="Disable raw Grok response printing.",
    )
    parser.add_argument("--no-plot", action="store_true", help="Disable matplotlib output.")
    return parser.parse_args()


def _resolve_tickers(args: argparse.Namespace) -> list[str]:
    raw_values: list[str] = []
    if args.tickers:
        raw_values.extend(args.tickers)
    if args.ticker:
        raw_values.append(args.ticker)

    if not raw_values:
        return ["AAPL"]

    out: list[str] = []
    seen: set[str] = set()
    for raw in raw_values:
        for part in str(raw).split(","):
            ticker = part.strip().upper()
            if ticker and ticker not in seen:
                out.append(ticker)
                seen.add(ticker)
    return out or ["AAPL"]


def _parse_date_utc(date_str: str) -> datetime:
    ts = pd.to_datetime(date_str, utc=True, errors="raise")
    if pd.isna(ts):
        raise ValueError(f"Invalid date: {date_str}")
    return ts.to_pydatetime()


def _resolve_date_range(args: argparse.Namespace) -> tuple[datetime, datetime]:
    now_utc = datetime.now(timezone.utc)

    start_raw = args.start_date.strip() if args.start_date else ""
    end_raw = args.end_date.strip() if args.end_date else ""

    if start_raw:
        start_time = _parse_date_utc(start_raw)
    else:
        end_anchor = _parse_date_utc(end_raw) if end_raw else now_utc
        start_time = end_anchor - timedelta(days=max(1, int(args.lookback_days)))

    if end_raw:
        # Inclusive end-date behavior.
        end_time = _parse_date_utc(end_raw) + timedelta(days=1)
    else:
        end_time = now_utc

    if start_time >= end_time:
        raise ValueError("Resolved start date must be earlier than end date.")

    return start_time, end_time


def _build_buy_hold_equity(df: pd.DataFrame, index: pd.DatetimeIndex, initial_capital: float) -> pd.Series:
    close = df["Close"].reindex(index).ffill()
    valid = close.dropna()
    if valid.empty:
        return pd.Series(initial_capital, index=index, dtype=float)

    first = float(valid.iloc[0])
    if first <= 0:
        return pd.Series(initial_capital, index=index, dtype=float)

    out = initial_capital * (close / first)
    return out.ffill().fillna(initial_capital).astype(float)


def _build_drawdown(equity: pd.Series) -> pd.Series:
    peak = equity.cummax()
    dd = (equity / peak) - 1.0
    return dd.fillna(0.0)


def _sum_profit_factor(trades: list[pd.Series | dict]) -> float:
    gross_profit = 0.0
    gross_loss = 0.0
    for trade in trades:
        pnl = float(trade["pnl_dollars"])
        if pnl >= 0:
            gross_profit += pnl
        else:
            gross_loss += -pnl

    if gross_loss > 0:
        return gross_profit / gross_loss
    if gross_profit > 0:
        return float("inf")
    return 0.0


def _print_setup_log(ticker: str, result: SparseBacktestResult) -> None:
    print("\n" + "=" * 120)
    print(f"GROK RESPONSES - {ticker}")
    print("=" * 120)
    if not result.setup_log:
        print("No setup calls were made.")
        print("=" * 120)
        return

    for idx, setup in enumerate(result.setup_log, start=1):
        generated_on = pd.Timestamp(setup["generated_on"]).strftime("%Y-%m-%d")
        print(
            f"#{idx:03d} | date={generated_on} | action={setup['action']} | "
            f"entry={setup['entry_price']} | sl={setup['stop_loss']} | "
            f"tp={setup['target_price']} | conf={setup['confidence']}"
        )
        print(f"reasoning: {setup['reasoning']}")
        raw = str(setup.get("raw_response", "")).strip()
        print("raw_response:")
        print(raw if raw else "<EMPTY>")
        print("-" * 120)
    print("=" * 120)


def _print_per_ticker_summary(results: dict[str, SparseBacktestResult]) -> None:
    print("\n" + "=" * 120)
    print("PER-TICKER SUMMARY")
    print("=" * 120)
    print(
        f"{'Ticker':<8} {'Trades':>6} {'Entries':>8} {'Setups(B/S)':>14} {'LowConf':>8} "
        f"{'Return%':>10} {'FinalEq($)':>14} {'Calls':>8} {'Comm($)':>10}"
    )
    print("-" * 120)
    for ticker, result in results.items():
        print(
            f"{ticker:<8} {result.num_trades:>6} {result.entries_triggered:>8} "
            f"{result.setup_buy_count}/{result.setup_skip_count:>9} "
            f"{result.low_confidence_rejections:>8} "
            f"{result.total_return_pct:>10.2f} {result.final_equity:>14.2f} {result.grok_api_calls:>8} "
            f"{result.total_commission_dollars:>10.2f}"
        )
    print("=" * 120)


def _print_no_trade_diagnostics(results: dict[str, SparseBacktestResult]) -> None:
    print("\n" + "=" * 120)
    print("NO-TRADE DIAGNOSTICS")
    print("=" * 120)
    any_zero_trade = False
    for ticker, result in results.items():
        if result.num_trades > 0:
            continue
        any_zero_trade = True
        print(f"[{ticker}] No completed trades.")
        if result.low_confidence_rejections > 0 and result.entries_triggered == 0:
            print(
                "  Cause: BUY setups existed but were filtered out by confidence threshold "
                "(confidence must be > 70)."
            )
        elif result.setup_buy_count == 0:
            print("  Cause: Grok never returned a valid BUY setup (all SKIP/invalid).")
        elif result.entries_triggered == 0:
            print("  Cause: BUY setup existed, but entry price was never touched by OHLC range.")
        else:
            print("  Cause: Entry happened but no completed exit occurred before end-of-data.")

        if result.pending_untriggered_setup is not None:
            s = result.pending_untriggered_setup
            print(
                "  Pending setup -> "
                f"entry={s.entry_price}, stop={s.stop_loss}, target={s.target_price}, "
                f"reason={s.reasoning[:180]}"
            )
    if not any_zero_trade:
        print("All tickers produced at least one completed trade.")
    print("=" * 120)


def _print_portfolio_summary(
    *,
    total_capital: float,
    strategy_equity: pd.Series,
    drawdown: pd.Series,
    all_trades_df: pd.DataFrame,
    total_calls: int,
    total_commission: float,
    basket_bh_equity: pd.Series,
    benchmark_equity: pd.Series,
    benchmark_ticker: str,
) -> None:
    final_equity = float(strategy_equity.iloc[-1]) if not strategy_equity.empty else float(total_capital)
    total_return_pct = ((final_equity / float(total_capital)) - 1.0) * 100.0

    win_rate = 0.0
    if not all_trades_df.empty:
        win_rate = (float((all_trades_df["pnl_dollars"] > 0).sum()) / float(len(all_trades_df))) * 100.0

    max_drawdown_pct = abs(float(drawdown.min())) * 100.0 if not drawdown.empty else 0.0
    returns = strategy_equity.pct_change().dropna()
    if len(returns) > 1 and float(returns.std(ddof=0)) > 0.0:
        sharpe = float(np.sqrt(252.0) * returns.mean() / returns.std(ddof=0))
    else:
        sharpe = 0.0

    profit_factor = _sum_profit_factor(all_trades_df.to_dict("records")) if not all_trades_df.empty else 0.0

    strategy_pnl = final_equity - total_capital
    basket_pnl = float(basket_bh_equity.iloc[-1]) - total_capital
    benchmark_pnl = float(benchmark_equity.iloc[-1]) - total_capital

    print("\n" + "=" * 96)
    print("PORTFOLIO SUMMARY")
    print("=" * 96)
    print(f"Initial Capital: ${total_capital:,.2f}")
    print(f"Final Equity:    ${final_equity:,.2f}")
    print(f"Total Return:    {total_return_pct:+.2f}%")
    print(f"Win Rate:        {win_rate:.2f}%")
    print(f"Profit Factor:   {'inf' if np.isinf(profit_factor) else f'{profit_factor:.3f}'}")
    print(f"Max Drawdown:    {max_drawdown_pct:.2f}%")
    print(f"Sharpe:          {sharpe:.3f}")
    print(f"Trades:          {len(all_trades_df)}")
    print(f"Grok API Calls:  {total_calls}")
    print(f"Total Commission:${total_commission:,.2f}")
    print("-" * 96)
    print("FINAL P&L")
    print(f"Strategy P&L:    ${strategy_pnl:,.2f}")
    print(f"Buy&Hold Basket: ${basket_pnl:,.2f}")
    print(f"{benchmark_ticker} Buy&Hold: ${benchmark_pnl:,.2f}")
    print("=" * 96)


def _plot_portfolio(
    *,
    strategy_equity: pd.Series,
    basket_bh_equity: pd.Series,
    benchmark_equity: pd.Series,
    drawdown: pd.Series,
    total_capital: float,
    benchmark_ticker: str,
    ticker_label: str,
) -> None:
    strategy_pnl = strategy_equity - total_capital
    basket_pnl = basket_bh_equity - total_capital
    benchmark_pnl = benchmark_equity - total_capital

    plt.figure(figsize=(14, 7))
    plt.plot(
        basket_pnl.index,
        basket_pnl.values,
        color="tab:blue",
        linewidth=1.8,
        label=f"{ticker_label} Buy & Hold",
    )
    plt.plot(
        strategy_pnl.index,
        strategy_pnl.values,
        color="tab:green",
        linewidth=2.0,
        label="Strategy Portfolio P&L",
    )
    plt.plot(
        benchmark_pnl.index,
        benchmark_pnl.values,
        color="tab:orange",
        linewidth=1.8,
        label=f"{benchmark_ticker} Buy & Hold",
    )
    plt.axhline(0.0, color="black", linewidth=0.9, alpha=0.6)
    plt.title(f"P&L Comparison (Strategy vs {ticker_label} B&H vs {benchmark_ticker} B&H)")
    plt.xlabel("Date")
    plt.ylabel("P&L ($)")
    plt.grid(True, alpha=0.3)
    plt.legend()
    plt.tight_layout()

    plt.figure(figsize=(14, 5))
    plt.plot(
        drawdown.index,
        drawdown.values * 100.0,
        color="tab:red",
        linewidth=1.8,
        label="Strategy Drawdown",
    )
    plt.fill_between(
        drawdown.index,
        drawdown.values * 100.0,
        0.0,
        color="tab:red",
        alpha=0.15,
    )
    plt.axhline(0.0, color="black", linewidth=0.9, alpha=0.6)
    plt.title("Drawdown Curve")
    plt.xlabel("Date")
    plt.ylabel("Drawdown (%)")
    plt.grid(True, alpha=0.3)
    plt.legend()
    plt.tight_layout()
    plt.show()


async def _run(args: argparse.Namespace) -> None:
    load_dotenv()

    tickers = _resolve_tickers(args)
    benchmark_ticker = args.benchmark_ticker.strip().upper() or "SPY"
    test_start_time, test_end_exclusive = _resolve_date_range(args)
    warmup_fetch_days = max(30, int(args.warmup_bars) * 3)
    fetch_start_time = test_start_time - timedelta(days=warmup_fetch_days)
    fetch_end_time = test_end_exclusive
    test_end_inclusive = (test_end_exclusive - timedelta(days=1))

    total_capital = max(1.0, float(args.capital))

    print("=" * 110)
    print("SPARSE GROK SWING BACKTEST (MULTI-TICKER)")
    print("=" * 110)
    print(f"Tickers: {', '.join(tickers)}")
    print(f"Benchmark: {benchmark_ticker}")
    print(
        f"Test period: {test_start_time.date()} to {test_end_inclusive.date()} | "
        "Interval: daily"
    )
    print(f"Warmup source window starts at: {fetch_start_time.date()}")
    print(
        f"Capital=${total_capital:,.2f} | Pair Commission=${max(0.0, float(args.pair_commission)):.2f} | "
        f"Warmup={max(2, int(args.warmup_bars))} | TableCandles={max(20, int(args.table_candles))} | "
        f"EntryPolicy={args.entry_policy} | SkipWaitDays={max(1, int(args.skip_wait_days))} | "
        f"MinConfidence>{int(args.min_confidence)}"
    )
    print("=" * 110)

    fetch_tickers = list(tickers)
    if benchmark_ticker not in fetch_tickers:
        fetch_tickers.append(benchmark_ticker)

    http_client = httpx.AsyncClient(
        timeout=settings.http.timeout,
        follow_redirects=settings.http.follow_redirects,
        limits=httpx.Limits(
            max_connections=settings.http.max_connections,
            max_keepalive_connections=settings.http.max_keepalive_connections,
        ),
    )

    provider = YahooMarketProvider(http_client=http_client)
    grok_client = GrokClient(http_client=http_client)

    try:
        data_by_ticker: dict[str, pd.DataFrame] = {}
        for ticker in fetch_tickers:
            df = await fetch_ohlcv_from_yahoo_provider(
                provider=provider,
                ticker=ticker,
                start_time=fetch_start_time,
                end_time=fetch_end_time,
                period=Period.DAILY,
            )
            if not df.empty:
                data_by_ticker[ticker] = df

        valid_tickers = [ticker for ticker in tickers if ticker in data_by_ticker and not data_by_ticker[ticker].empty]
        if not valid_tickers:
            raise ValueError("No data fetched for requested strategy tickers.")

        per_ticker_capital = total_capital / float(len(valid_tickers))
        results: dict[str, SparseBacktestResult] = {}

        for ticker in valid_tickers:
            print(f"\nRunning ticker {ticker}...")
            result = await run_sparse_grok_swing_backtest(
                ticker=ticker,
                df=data_by_ticker[ticker],
                grok_client=grok_client,
                initial_capital=per_ticker_capital,
                warmup_bars=max(2, int(args.warmup_bars)),
                table_candles=max(20, int(args.table_candles)),
                pause_seconds=max(0.0, float(args.pause_seconds)),
                fixed_commission_per_pair=max(0.0, float(args.pair_commission)),
                verbose_grok=bool(args.print_grok_responses),
                entry_policy=str(args.entry_policy),
                skip_wait_bars=max(1, int(args.skip_wait_days)),
                low_conf_wait_bars=1,
                min_entry_confidence=max(0, int(args.min_confidence)),
                test_start=pd.Timestamp(test_start_time),
                test_end=pd.Timestamp(test_end_inclusive),
            )
            results[ticker] = result

        _print_per_ticker_summary(results)
        _print_no_trade_diagnostics(results)

        if bool(args.print_grok_responses):
            for ticker in valid_tickers:
                _print_setup_log(ticker, results[ticker])

        all_indexes: set[pd.Timestamp] = set()
        for ticker in valid_tickers:
            all_indexes.update(pd.DatetimeIndex(results[ticker].strategy_equity.index).to_pydatetime())

        common_index = pd.DatetimeIndex(sorted(pd.Timestamp(ts) for ts in all_indexes))
        if common_index.empty:
            raise ValueError("Combined index is empty.")

        strategy_portfolio_equity = pd.Series(0.0, index=common_index, dtype=float)
        basket_bh_equity = pd.Series(0.0, index=common_index, dtype=float)

        for ticker in valid_tickers:
            result = results[ticker]
            strategy_part = result.strategy_equity.reindex(common_index).ffill().fillna(result.initial_capital)
            strategy_portfolio_equity = strategy_portfolio_equity + strategy_part

            bh_part = _build_buy_hold_equity(
                data_by_ticker[ticker],
                index=common_index,
                initial_capital=per_ticker_capital,
            )
            basket_bh_equity = basket_bh_equity + bh_part

        benchmark_label = benchmark_ticker
        benchmark_df = data_by_ticker.get(benchmark_ticker)
        if benchmark_df is None or benchmark_df.empty:
            benchmark_equity = basket_bh_equity.copy()
            benchmark_label = f"{benchmark_ticker} (fallback=basket)"
        else:
            benchmark_equity = _build_buy_hold_equity(
                benchmark_df,
                index=common_index,
                initial_capital=total_capital,
            )

        drawdown = _build_drawdown(strategy_portfolio_equity)

        all_trade_frames = []
        total_calls = 0
        total_commission = 0.0
        for ticker in valid_tickers:
            result = results[ticker]
            total_calls += result.grok_api_calls
            total_commission += result.total_commission_dollars
            if not result.trade_log_df.empty:
                frame = result.trade_log_df.copy()
                frame["ticker"] = ticker
                all_trade_frames.append(frame)

        if all_trade_frames:
            all_trades_df = pd.concat(all_trade_frames, ignore_index=True)
        else:
            all_trades_df = pd.DataFrame(
                columns=[
                    "ticker",
                    "entry_date",
                    "entry_price",
                    "exit_date",
                    "exit_price",
                    "shares",
                    "pnl_dollars",
                    "return_pct",
                    "commission_dollars",
                    "exit_reason",
                    "grok_confidence",
                    "grok_reasoning",
                    "setup_generated_on",
                ]
            )

        _print_portfolio_summary(
            total_capital=total_capital,
            strategy_equity=strategy_portfolio_equity,
            drawdown=drawdown,
            all_trades_df=all_trades_df,
            total_calls=total_calls,
            total_commission=total_commission,
            basket_bh_equity=basket_bh_equity,
            benchmark_equity=benchmark_equity,
            benchmark_ticker=benchmark_label,
        )

        if not bool(args.no_plot):
            ticker_label = "Basket"
            if len(valid_tickers) == 1:
                ticker_label = valid_tickers[0]
            _plot_portfolio(
                strategy_equity=strategy_portfolio_equity,
                basket_bh_equity=basket_bh_equity,
                benchmark_equity=benchmark_equity,
                drawdown=drawdown,
                total_capital=total_capital,
                benchmark_ticker=benchmark_label,
                ticker_label=ticker_label,
            )
    finally:
        await http_client.aclose()


def main() -> None:
    args = _parse_args()
    asyncio.run(_run(args))


if __name__ == "__main__":
    main()
