#!/usr/bin/env python3
"""Run backtest with equity curve visualization.

Compares Supply & Demand Zone strategy against S&P 500 buy-and-hold.
"""
from pathlib import Path
import sys
import warnings
# Suppress multiprocessing resource_tracker warnings (known macOS issue with yfinance-cache)
warnings.filterwarnings("ignore", message="resource_tracker:")

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ROOT_PATH = str(PROJECT_ROOT)
if ROOT_PATH not in sys.path:
    sys.path.insert(0, ROOT_PATH)

import httpx
import matplotlib.pyplot as plt
import matplotlib
import numpy as np
import pandas as pd
import yfinance_cache as yf
from datetime import date, datetime, timedelta
import random
from backtesting.engines.vectorbt_engine import VectorBTEngine
from backtesting.models.backtest_params import BacktestParams
from backtesting.strategies.supply_demand_strategy import SupplyDemandStrategy
from backtesting.strategies.opening_drop_strategy import OpeningDropStrategy
from common.helpers.yfinance_cache_manager import init_yfinance_cache
from common.models.scanner_params import ScannerParams
from pullers.scanners.finviz.custom_finviz import CustomFinviz
import asyncio
# ...existing code...
# Enable interactive mode
matplotlib.use('TkAgg')
plt.ion()


scanner_url = "https://finviz.com/screener.ashx?v=111&f=sh_avgvol_o2000&ft=4"
client = httpx.AsyncClient()
scanner = CustomFinviz(http_client=client, url=scanner_url)

init_yfinance_cache()


TICKERS = asyncio.run(scanner.scan(ScannerParams("your_param_here")))
# TICKERS = ["NIO"]
# Select X random tickers from the full list
NUM_RANDOM_TICKERS = 200 # Change this value to select a different number of tickers
TICKERS = random.sample(TICKERS, min(NUM_RANDOM_TICKERS, len(TICKERS)))

# Batch configuration
BATCH_SIZE = 10
random.shuffle(TICKERS)  # Shuffle for randomization across batches


def get_spy_equity_curve(
    initial_capital: float,
    start_date: datetime,
    end_date: datetime,
) -> tuple[pd.DatetimeIndex, np.ndarray]:
    """Get S&P 500 (SPY) buy-and-hold equity curve aligned to date range.
    
    Args:
        initial_capital: Starting capital to invest.
        start_date: Beginning of comparison window.
        end_date: End of comparison window.
        
    Returns:
        Tuple of (dates, equity values) reindexed to full date range.
    """
    # Fetch SPY with explicit date window to avoid empty frames
    df = yf.download(
        "SPY",
        start=start_date,
        end=end_date + timedelta(days=1),  # include end date
        progress=True,
    )
    
    if df.empty:
        return pd.DatetimeIndex([]), np.array([])
    
    initial_price = df["Close"].iloc[0]
    shares = initial_capital / initial_price
    equity = df["Close"] * shares
    
    # Reindex to full daily range for consistent plotting
    full_range = pd.date_range(start=start_date, end=end_date, freq="D")
    equity_full = equity.reindex(full_range).ffill()
    
    return equity_full.index, equity_full.values


def build_strategy_equity_curve(
    trades: list,
    initial_capital: float,
    start_date: datetime,
    end_date: datetime,
) -> tuple[pd.DatetimeIndex, np.ndarray]:
    """Build equity curve from trade records.
    
    Args:
        trades: List of TradeRecord objects.
        initial_capital: Starting capital.
        start_date: Backtest start date.
        end_date: Backtest end date.
        
    Returns:
        Tuple of (dates, equity values).
    """
    date_range = pd.date_range(start=start_date, end=end_date, freq="D")
    equity = np.full(len(date_range), initial_capital, dtype=float)
    
    cumulative_pnl = 0.0
    trade_idx = 0
    sorted_trades = sorted(trades, key=lambda t: t.exit_date)
    
    for i, date in enumerate(date_range):
        while trade_idx < len(sorted_trades):
            trade = sorted_trades[trade_idx]
            exit_date = trade.exit_date
            if hasattr(exit_date, "date"):
                exit_date = exit_date.date()
            if hasattr(date, "date"):
                current_date = date.date()
            else:
                current_date = date
                
            if exit_date <= current_date:
                cumulative_pnl += trade.pnl
                trade_idx += 1
            else:
                break
        
        equity[i] = initial_capital + cumulative_pnl
    
    return date_range, equity


def plot_final_comparison(
    all_trades: list,
    spy_dates: pd.DatetimeIndex,
    spy_equity: np.ndarray,
    initial_capital: float,
    start_date: date,
    end_date: date,
    total_tickers: int,
    batch_equity_curves: list[tuple[int, list[str], pd.DatetimeIndex, np.ndarray]],
    position_size_pct: float,
    big7_trades: list = None,
) -> None:
    """Plot all batch equity curves plus Big 7 plus combined vs S&P 500 on one chart."""
    strategy_dates, strategy_equity = build_strategy_equity_curve(
        all_trades, initial_capital, start_date, end_date
    )
    
    strategy_values = np.asarray(strategy_equity, dtype=float).reshape(-1)
    spy_values = np.asarray(spy_equity, dtype=float).reshape(-1)

    strategy_series = pd.Series(
        strategy_values,
        index=pd.DatetimeIndex(strategy_dates),
        name="strategy",
    )
    spy_series = pd.Series(
        spy_values,
        index=pd.DatetimeIndex(spy_dates),
        name="spy",
    )

    aligned = pd.concat([strategy_series, spy_series], axis=1).ffill().dropna()
    if aligned.empty:
        print("Warning: Unable to align data for final comparison.")
        return

    strategy_series = aligned["strategy"]
    spy_series = aligned["spy"]
    
    # Build Big 7 equity curve if trades provided
    big7_series = None
    if big7_trades:
        big7_dates, big7_equity = build_strategy_equity_curve(
            big7_trades, initial_capital, start_date, end_date
        )
        big7_values = np.asarray(big7_equity, dtype=float).reshape(-1)
        big7_series = pd.Series(
            big7_values,
            index=pd.DatetimeIndex(big7_dates),
            name="big7",
        )
        aligned_with_big7 = pd.concat([strategy_series, spy_series, big7_series], axis=1).ffill().dropna()
        if not aligned_with_big7.empty:
            strategy_series = aligned_with_big7["strategy"]
            spy_series = aligned_with_big7["spy"]
            big7_series = aligned_with_big7["big7"]
    
    plt.style.use("seaborn-v0_8-darkgrid")
    fig, ax = plt.subplots(figsize=(16, 10))

    # Plot SPY baseline
    ax.plot(
        spy_series.index,
        spy_series.values,
        label="S&P 500 Buy & Hold",
        color="#3498db",
        linewidth=2.5,
    )

    # Plot combined strategy curve
    # ax.plot(
    #     strategy_series.index,
    #     strategy_series.values,
    #     label="Supply & Demand Strategy (All Batches)",
    #     color="#2ecc71",
    #     linewidth=3,
    # )
    
    # Plot Big 7 curve if available
    if big7_series is not None:
        ax.plot(
            big7_series.index,
            big7_series.values,
            label="Big 7 Stocks (TSLA, NVDA, AMZN, MSFT, GOOG, META, AAPL)",
            color="#e74c3c",
            linewidth=3,
        )

    # Plot each batch curve
    batch_cmap = plt.cm.get_cmap("tab20", max(len(batch_equity_curves), 1))
    for idx, (batch_num, batch_tickers, batch_dates, batch_equity) in enumerate(batch_equity_curves):
        batch_values = np.asarray(batch_equity, dtype=float).reshape(-1)
        batch_series = pd.Series(
            batch_values,
            index=pd.DatetimeIndex(batch_dates),
            name=f"batch_{batch_num}",
        )
        ax.plot(
            batch_series.index,
            batch_series.values,
            label=f"Batch {batch_num} ({len(batch_tickers)} tickers)",
            color=batch_cmap(idx % batch_cmap.N),
            linewidth=1.5,
            alpha=0.8,
        )
    
    ax.axhline(
        y=initial_capital,
        color="#95a5a6",
        linestyle="--",
        linewidth=1.5,
        label=f"Initial Capital (${initial_capital:,.0f})",
    )
    
    ax.set_xlabel("Date", fontsize=14)
    ax.set_ylabel("Portfolio Value ($)", fontsize=14)
    ax.set_title(
        f"Supply & Demand Zone Strategy - Complete Analysis ({total_tickers} Tickers + Big 7)\n"
        f"Position Size: {position_size_pct*100:.0f}% | Risk:Reward = 4%:12%",
        fontsize=16,
        fontweight="bold",
    )
    
    ax.legend(loc="upper right", fontsize=9)
    ax.yaxis.set_major_formatter(
        plt.FuncFormatter(lambda x, p: f"${x:,.0f}")
    )
    
    strategy_final = float(strategy_series.values[-1])
    spy_final = float(spy_series.values[-1])
    
    strategy_return = ((strategy_final - initial_capital) / initial_capital) * 100
    spy_return = ((spy_final - initial_capital) / initial_capital) * 100
    
    total_trades = len(all_trades)
    winners = len([t for t in all_trades if t.is_winner])
    win_rate = (winners / total_trades * 100) if total_trades > 0 else 0
    
    textstr = (
        f"All Batches Final: ${strategy_final:,.2f} ({strategy_return:+.1f}%)\n"
        f"S&P 500 Final: ${spy_final:,.2f} ({spy_return:+.1f}%)\n"
        f"Outperformance: {strategy_return - spy_return:+.1f}%\n"
        f"Total Trades: {total_trades}\n"
        f"Win Rate: {win_rate:.1f}%"
    )
    
    # Add Big 7 stats if available
    if big7_series is not None and big7_trades:
        big7_final = float(big7_series.values[-1])
        big7_return = ((big7_final - initial_capital) / initial_capital) * 100
        big7_winners = len([t for t in big7_trades if t.is_winner])
        big7_win_rate = (big7_winners / len(big7_trades) * 100) if len(big7_trades) > 0 else 0
        textstr += (
            f"\n\nBig 7 Final: ${big7_final:,.2f} ({big7_return:+.1f}%)\n"
            f"Big 7 Trades: {len(big7_trades)}\n"
            f"Big 7 Win Rate: {big7_win_rate:.1f}%"
        )
    
    props = dict(boxstyle="round", facecolor="white", alpha=0.9)
    ax.text(
        0.02, 0.97, textstr,
        transform=ax.transAxes,
        fontsize=11,
        verticalalignment="top",
        bbox=props,
    )
    
    plt.tight_layout()
    plt.show(block=False)
    plt.pause(5000)
    print("\nChart displayed in interactive window. Close the window to continue.")


def main() -> None:
    """Run backtest in batches and generate comparison chart."""
    print("=" * 60)
    print("SUPPLY & DEMAND STRATEGY BACKTEST - BATCH ANALYSIS")
    print("=" * 60)
    
    initial_capital = 3000.0
    
    # Use explicit date range: last month
    start_date = date.today() - timedelta(days=365)
    end_date = date.today() - timedelta(days=0)
    
    params = BacktestParams(
        initial_capital=initial_capital,
        commission_per_trade=2.5,
        position_size_pct=1,
        take_profit_pct=0.08,
        stop_loss_pct=0.04,
        supply_skip_distance_pct=0.06,
        start_date=start_date,
        end_date=end_date,
        interval="1d",
    )
    
    print(f"\n1. Fetching tickers...")
    print(f"   Total tickers: {len(TICKERS)}")
    
    print("\n2. Fetching S&P 500 data...")
    spy_dates, spy_equity = get_spy_equity_curve(
        initial_capital, start_date, end_date
    )
    
    if len(spy_dates) == 0:
        print("Error: Could not fetch S&P 500 data.")
        return
    
    # Process in batches
    all_trades: list = []
    batch_equity_curves: list[tuple[int, list[str], pd.DatetimeIndex, np.ndarray]] = []
    batch_performance: list[tuple[int, list[str], float, float]] = []
    batch_num = 0
    
    for i in range(0, len(TICKERS), BATCH_SIZE):
        batch_tickers = TICKERS[i:i + BATCH_SIZE]
        batch_num += 1
        
        print(f"\n{'='*60}")
        print(f"Processing Batch {batch_num}: {batch_tickers}")
        print(f"{'='*60}")
        
        strategy = SupplyDemandStrategy()
        engine = VectorBTEngine(strategy=strategy)
        result = engine.run(tickers=batch_tickers, params=params)
        
        print(result.summary())
        
        if result.trades:
            all_trades.extend(result.trades)
            batch_trades = list(result.trades)
            if batch_trades:
                batch_dates, batch_equity = build_strategy_equity_curve(
                    batch_trades, initial_capital, start_date, end_date
                )
                batch_equity_curves.append(
                    (batch_num, batch_tickers, batch_dates, batch_equity)
                )
                
                # Track performance
                final_equity = float(batch_equity[-1])
                pnl = final_equity - initial_capital
                pnl_pct = (pnl / initial_capital) * 100
                batch_performance.append(
                    (batch_num, batch_tickers, final_equity, pnl_pct)
                )
        else:
            print(f"   No trades in batch {batch_num}")
            batch_performance.append(
                (batch_num, batch_tickers, initial_capital, 0.0)
            )
    
    # Final combined results
    print("\n" + "=" * 60)
    print("FINAL COMBINED RESULTS")
    print("=" * 60)
    
    # Run Big 7 analysis
    big7_tickers = ["TSLA", "NVDA", "AMZN", "MSFT", "GOOG", "META", "AAPL"]
    print("\n" + "=" * 60)
    print("BIG 7 STOCKS ANALYSIS")
    print(f"Running strategy on: {big7_tickers}")
    print("=" * 60)
    
    strategy = SupplyDemandStrategy()
    engine = VectorBTEngine(strategy=strategy)
    big7_result = engine.run(tickers=big7_tickers, params=params)
    print(big7_result.summary())
    
    big7_trades = list(big7_result.trades) if big7_result.trades else None
    
    if all_trades:
        # Print best and worst batches
        if batch_performance:
            best_batch = max(batch_performance, key=lambda x: x[3])
            worst_batch = min(batch_performance, key=lambda x: x[3])
            
            print("\n📈 BEST PERFORMING BATCH:")
            print(f"   Batch {best_batch[0]}: {best_batch[1]}")
            print(f"   Final Equity: ${best_batch[2]:,.2f}")
            print(f"   PnL: {best_batch[3]:+.2f}%")
            
            print("\n📉 WORST PERFORMING BATCH:")
            print(f"   Batch {worst_batch[0]}: {worst_batch[1]}")
            print(f"   Final Equity: ${worst_batch[2]:,.2f}")
            print(f"   PnL: {worst_batch[3]:+.2f}%")
            print()
        
        plot_final_comparison(
            all_trades,
            spy_dates,
            spy_equity,
            initial_capital,
            start_date,
            end_date,
            len(TICKERS),
            batch_equity_curves,
            params.position_size_pct,
            big7_trades,
        )
    else:
        print("No trades were executed across all batches.")


if __name__ == "__main__":
    main()
