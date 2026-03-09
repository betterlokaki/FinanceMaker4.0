#!/usr/bin/env python3
"""Run TDFI+ADX+RangeFilter+CTR strategy with backtesting.py."""
from __future__ import annotations

import asyncio
import argparse
from pathlib import Path
import sys

import httpx
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ROOT_PATH = str(PROJECT_ROOT)
if ROOT_PATH not in sys.path:
    sys.path.insert(0, ROOT_PATH)

from backtests.backtesting_py.config import BacktestRunConfig, PortfolioConfig
from backtests.backtesting_py.cost_model import make_commission_callable
from backtests.backtesting_py.data_adapter import (
    fetch_ohlcv_from_yahoo_provider,
    infer_tick_size,
)
from backtests.backtesting_py.portfolio_orchestrator import (
    run_shared_capital_portfolio,
)
from backtests.backtesting_py.tdfi_adx_range_ctr_strategy import (
    TDFIAdxRangeCtrConfluenceStrategy,
)
from common.settings import settings
from pullers.market.yahoo.yahoo_market_provider import YahooMarketProvider

try:
    from backtesting import Backtest
except ImportError as exc:  # pragma: no cover - runtime dependency gate
    raise SystemExit(
        "Missing dependency `backtesting`. Install requirements first."
    ) from exc


# Hardcoded default ticker list, as requested.
DEFAULT_TICKERS: list[str] = [
    "AAPL",
    "MSFT",
    "NVDA",
    "AMZN",
    "META",
    "TSLA",
    "GOOGL",
    "NFLX",
    "AMD",
    "PLTR",
]


RUN_CONFIG = BacktestRunConfig()
PORTFOLIO_CONFIG = PortfolioConfig(
    initial_capital=RUN_CONFIG.initial_capital,
    max_leverage=RUN_CONFIG.leverage,
    commission_rate=RUN_CONFIG.commission_rate,
    slippage_ticks=RUN_CONFIG.slippage_ticks,
    default_tick_size=RUN_CONFIG.default_tick_size,
)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run backtesting.py TDFI+ADX+RangeFilter+CTR strategy."
    )
    parser.add_argument(
        "--tickers",
        nargs="+",
        default=None,
        help="Ticker symbols to run. Supports comma-separated or space-separated input.",
    )
    return parser.parse_args()


def _resolve_tickers(arg_values: list[str] | None) -> list[str]:
    if not arg_values:
        return DEFAULT_TICKERS

    resolved: list[str] = []
    seen: set[str] = set()
    for raw in arg_values:
        for item in raw.split(","):
            ticker = item.strip().upper()
            if ticker and ticker not in seen:
                resolved.append(ticker)
                seen.add(ticker)

    return resolved or DEFAULT_TICKERS


async def _fetch_all_data(tickers: list[str]) -> dict[str, pd.DataFrame]:
    """Fetch OHLCV data for all hardcoded tickers using YahooMarketProvider."""
    client = httpx.AsyncClient(
        timeout=settings.http.timeout,
        follow_redirects=settings.http.follow_redirects,
        limits=httpx.Limits(
            max_connections=settings.http.max_connections,
            max_keepalive_connections=settings.http.max_keepalive_connections,
        ),
    )
    provider = YahooMarketProvider(http_client=client)
    data: dict[str, pd.DataFrame] = {}
    try:
        for ticker in tickers:
            df = await fetch_ohlcv_from_yahoo_provider(
                provider=provider,
                ticker=ticker,
                start_time=RUN_CONFIG.start_time,
                end_time=RUN_CONFIG.end_time,
                period=RUN_CONFIG.period,
            )
            if not df.empty:
                data[ticker] = df
    finally:
        await client.aclose()
    return data


def _print_symbol_stats(ticker: str, stats: pd.Series) -> None:
    trades = int(stats.get("# Trades", 0))
    ret = float(stats.get("Return [%]", 0.0))
    win = float(stats.get("Win Rate [%]", 0.0))
    equity = float(stats.get("Equity Final [$]", 0.0))
    print(
        f"{ticker:>6} | trades={trades:>3} | return={ret:>8.2f}% | "
        f"win={win:>6.2f}% | equity=${equity:>10.2f}"
    )


def main() -> None:
    args = _parse_args()
    tickers = _resolve_tickers(args.tickers)

    print("=" * 80)
    print("BACKTESTING.PY - TDFI + ADX + RANGE FILTER + CTR (MULTI-SYMBOL)")
    print("=" * 80)
    print(f"Tickers: {', '.join(tickers)}")
    print(
        f"Date range: {RUN_CONFIG.start_time.date()} to {RUN_CONFIG.end_time.date()} | "
        f"Interval: {RUN_CONFIG.period.value}"
    )
    print(
        f"Initial capital=${RUN_CONFIG.initial_capital:,.2f} | "
        f"Target notional/trade=${RUN_CONFIG.notional_per_trade:,.2f} | "
        f"Leverage={RUN_CONFIG.leverage:.1f}x"
    )
    print(
        f"Commission={RUN_CONFIG.commission_rate * 100:.3f}%/side | "
        f"Slippage={RUN_CONFIG.slippage_ticks:.1f} ticks/side"
    )
    print("=" * 80)

    data_by_ticker = asyncio.run(_fetch_all_data(tickers))
    if not data_by_ticker:
        print("No data fetched for any ticker.")
        return

    trades_by_ticker: dict[str, pd.DataFrame] = {}
    tick_size_by_ticker: dict[str, float] = {}

    print("\nPer-symbol results:")
    for ticker, df in data_by_ticker.items():
        if len(df) < 100:
            print(f"{ticker:>6} | skipped (insufficient bars: {len(df)})")
            continue

        tick_size = infer_tick_size(df, fallback=RUN_CONFIG.default_tick_size)
        tick_size_by_ticker[ticker] = tick_size
        commission_callable = make_commission_callable(
            commission_rate=RUN_CONFIG.commission_rate,
            tick_size=tick_size,
            slippage_ticks=RUN_CONFIG.slippage_ticks,
        )

        bt = Backtest(
            data=df,
            strategy=TDFIAdxRangeCtrConfluenceStrategy,
            cash=RUN_CONFIG.initial_capital,
            commission=commission_callable,
            margin=1.0 / RUN_CONFIG.leverage,
            trade_on_close=RUN_CONFIG.trade_on_close,
            hedging=False,
            exclusive_orders=False,
            finalize_trades=RUN_CONFIG.finalize_trades,
        )
        stats = bt.run(
            trade_direction=RUN_CONFIG.trade_direction.value,
            notional_per_trade=RUN_CONFIG.notional_per_trade,
            atr_sl_multiplier=RUN_CONFIG.atr_sl_multiplier,
            atr_tp_multiplier=RUN_CONFIG.atr_tp_multiplier,
            atr_period=RUN_CONFIG.atr_period,
            adx_len=RUN_CONFIG.adx_len,
            adx_di_len=RUN_CONFIG.adx_di_len,
            adx_ema_len=RUN_CONFIG.adx_ema_len,
            tdfi_lookback=RUN_CONFIG.tdfi_lookback,
            tdfi_filter_high=RUN_CONFIG.tdfi_filter_high,
            tdfi_filter_low=RUN_CONFIG.tdfi_filter_low,
            rf_movement_source=RUN_CONFIG.rf_movement_source,
            rf_range_size=RUN_CONFIG.rf_range_size,
            rf_range_scale=RUN_CONFIG.rf_range_scale,
            rf_range_period=RUN_CONFIG.rf_range_period,
            rf_smooth_range=RUN_CONFIG.rf_smooth_range,
            rf_smooth_period=RUN_CONFIG.rf_smooth_period,
            ctr_len=RUN_CONFIG.ctr_len,
            ctr_tlen=RUN_CONFIG.ctr_tlen,
            ctr_upper=RUN_CONFIG.ctr_upper,
            ctr_lower=RUN_CONFIG.ctr_lower,
        )

        trades = stats.get("_trades", pd.DataFrame())
        if isinstance(trades, pd.DataFrame):
            trades_by_ticker[ticker] = trades
        _print_symbol_stats(ticker, stats)

        if RUN_CONFIG.open_browser_plots:
            try:
                bt.plot(results=stats, open_browser=True)
            except Exception as exc:
                print(f"{ticker:>6} | plot failed: {exc}")

    shared = run_shared_capital_portfolio(
        trades_by_ticker=trades_by_ticker,
        portfolio_config=PORTFOLIO_CONFIG,
        tick_size_by_ticker=tick_size_by_ticker,
    )

    print("\n" + "=" * 80)
    print("SHARED PORTFOLIO RESULT")
    print("=" * 80)
    print(f"Initial Capital: ${shared.initial_capital:,.2f}")
    print(f"Final Equity:    ${shared.final_equity:,.2f}")
    print(f"Return:          {shared.total_return_pct:+.2f}%")
    print(f"Max Drawdown:    {shared.max_drawdown_pct:.2f}%")
    print(f"Trades:          {shared.total_trades}")
    print(f"Wins / Losses:   {shared.winning_trades} / {shared.losing_trades}")
    print(f"Skipped Entries: {len(shared.skipped_trades)}")
    print("=" * 80)


if __name__ == "__main__":
    main()
