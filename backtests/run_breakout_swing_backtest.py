#!/usr/bin/env python3
"""Run breakout swing strategy backtest with batch visualization.

Tests resistance breakout strategy on large cap stocks in batches of 10,
showing each batch's equity curve against S&P 500, then shows final combined results.

Strategy:
    - Detect resistance zones tested at least 3 times
    - Enter when in uptrend and approaching resistance
    - Exit on 10% profit or 5% stop loss (1:2 R:R)
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

import asyncio
import random
from datetime import date, timedelta

import httpx
import matplotlib
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import yfinance_cache as yf

from common.helpers.yfinance_cache_manager import init_yfinance_cache

from backtesting.engines.breakout_swing_engine import BreakoutSwingEngine
from backtesting.models.backtest_params import BacktestParams
from backtesting.models.trade_record import TradeRecord
from backtesting.strategies.breakout_swing_strategy import BreakoutSwingStrategy
from common.models.scanner_params import ScannerParams
from pullers.scanners.finviz.custom_finviz import CustomFinviz

# Enable interactive mode
matplotlib.use('TkAgg')
plt.ion()
init_yfinance_cache()

# Large cap stock universe (S&P 100 components - stable, liquid stocks)
scanner_url = "https://finviz.com/screener.ashx?v=111&f=earningsdate_thismonth%2Csh_avgvol_o1000&ft=4"
client = httpx.AsyncClient()
scanner = CustomFinviz(http_client=client, url=scanner_url)



TICKERS = asyncio.run(scanner.scan(ScannerParams("your_param_here")))
# TICKERS = ["AAPL", "MSFT", "GOOGL", "AMZN", "TSLA", "META", "NVDA"]
# Select X random tickers from the full list
# NUM_RANDOM_TICKERS = 200 # Change this value to select a different number of tickers
# TICKERS = random.sample(TICKERS, min(NUM_RANDOM_TICKERS, len(TICKERS)))

LARGE_CAP_TICKERS = TICKERS

BATCH_SIZE = 10


def get_spy_equity_curve(
    initial_capital: float,
    start_date: date,
    end_date: date,
) -> tuple[pd.DatetimeIndex, np.ndarray]:
    """Get S&P 500 (SPY) buy-and-hold equity curve.
    
    Args:
        initial_capital: Starting capital to invest.
        start_date: Beginning of comparison window.
        end_date: End of comparison window.
        
    Returns:
        Tuple of (dates, equity values).
    """
    df = yf.download(
        "SPY",
        start=start_date,
        end=end_date + timedelta(days=1),
        progress=False,
    )
    
    if df.empty:
        return pd.DatetimeIndex([]), np.array([])
    
    initial_price = df["Close"].iloc[0]
    shares = initial_capital / initial_price
    equity = df["Close"] * shares
    
    full_range = pd.date_range(start=start_date, end=end_date, freq="D")
    equity_full = equity.reindex(full_range).ffill()
    
    return equity_full.index, equity_full.values


def build_strategy_equity_curve(
    trades: list[TradeRecord],
    initial_capital: float,
    start_date: date,
    end_date: date,
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
    
    for i, dt in enumerate(date_range):
        while trade_idx < len(sorted_trades):
            trade = sorted_trades[trade_idx]
            exit_date = trade.exit_date
            if hasattr(exit_date, "date"):
                exit_date = exit_date.date()
            if hasattr(dt, "date"):
                current_date = dt.date()
            else:
                current_date = dt
                
            if exit_date <= current_date:
                cumulative_pnl += trade.pnl
                trade_idx += 1
            else:
                break
        
        equity[i] = initial_capital + cumulative_pnl
    
    return date_range, equity


def plot_final_comparison(
    all_trades: list[TradeRecord],
    spy_dates: pd.DatetimeIndex,
    spy_equity: np.ndarray,
    initial_capital: float,
    start_date: date,
    end_date: date,
    total_tickers: int,
    batch_equity_curves: list[tuple[int, list[str], pd.DatetimeIndex, np.ndarray]]
) -> None:
    """Plot all batch equity curves plus combined vs S&P 500 on one chart."""
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
    ax.plot(
        strategy_series.index,
        strategy_series.values,
        label="Breakout Swing Strategy (All Tickers)",
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
        f"Breakout Swing Strategy - Final Results ({total_tickers} Large Cap Tickers)\n"
        f"Entry: Approaching Resistance (Tested 3+ Times) in Uptrend | Risk:Reward = 5%:10%",
        fontsize=16,
        fontweight="bold",
    )
    
    ax.legend(loc="upper left", fontsize=12)
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
        f"Strategy Final: ${strategy_final:,.2f} ({strategy_return:+.1f}%)\n"
        f"S&P 500 Final: ${spy_final:,.2f} ({spy_return:+.1f}%)\n"
        f"Outperformance: {strategy_return - spy_return:+.1f}%\n"
        f"Total Trades: {total_trades}\n"
        f"Win Rate: {win_rate:.1f}%"
    )
    
    props = dict(boxstyle="round", facecolor="white", alpha=0.9)
    ax.text(
        0.02, 0.97, textstr,
        transform=ax.transAxes,
        fontsize=12,
        verticalalignment="top",
        bbox=props,
    )
    
    plt.tight_layout()
    plt.show(block=False)
    plt.pause(5000)


def main() -> None:
    """Run breakout swing strategy backtest in batches."""
    print("=" * 60)
    print("BREAKOUT SWING STRATEGY BACKTEST")
    print("Entry: Resistance Level (Tested 3+ Times) in Uptrend")
    print("Risk:Reward = 5%:10% (1:2 Ratio)")
    print("=" * 60)
    
    # Shuffle tickers for randomization
    tickers = LARGE_CAP_TICKERS.copy()
    random.shuffle(tickers)
    
    print(f"\n1. Selected {len(tickers)} large cap tickers")
    
    # Configuration
    initial_capital = 3000.0
    start_date = date.today() - timedelta(days=1030)  # Last 2 years for swing trades
    end_date = date.today()
    
    params = BacktestParams(
        initial_capital=initial_capital,
        commission_per_trade=2.5,
        position_size_pct=1,  # 10% per position
        take_profit_pct=0.10,   # 10% profit target
        stop_loss_pct=0.05,     # 5% stop loss
        supply_skip_distance_pct=0.02,  # 2% zone tolerance
        start_date=start_date,
        end_date=end_date,
        interval="1d",  # Daily data for swing trading
    )
    
    # Get SPY data once
    print("\n2. Fetching S&P 500 data...")
    spy_dates, spy_equity = get_spy_equity_curve(
        initial_capital, start_date, end_date
    )
    
    if len(spy_dates) == 0:
        print("Error: Could not fetch S&P 500 data.")
        return
    
    # Process in batches
    all_trades: list[TradeRecord] = []
    batch_equity_curves: list[tuple[int, list[str], pd.DatetimeIndex, np.ndarray]] = []
    batch_num = 0
    
    for i in range(0, len(tickers), BATCH_SIZE):
        batch_tickers = tickers[i:i + BATCH_SIZE]
        batch_num += 1
        
        print(f"\n{'='*60}")
        print(f"Processing Batch {batch_num}: {batch_tickers}")
        print(f"{'='*60}")
        
        strategy = BreakoutSwingStrategy()
        engine = BreakoutSwingEngine(strategy=strategy)
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
        else:
            print(f"   No trades in batch {batch_num}")
    
    # Final combined plot
    print("\n" + "=" * 60)
    print("FINAL COMBINED RESULTS")
    print("=" * 60)
    
    if all_trades:
        plot_final_comparison(
            all_trades,
            spy_dates,
            spy_equity,
            initial_capital,
            start_date,
            end_date,
            len(tickers),
            batch_equity_curves,
        )
    else:
        print("No trades were executed across all batches.")


if __name__ == "__main__":
    main()
