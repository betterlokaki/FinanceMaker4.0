#!/usr/bin/env python3
"""Run earning demand zone strategy backtest.

Entry point script for backtesting the earnings + demand zone + scoring
strategy. Discovers tickers from Yahoo Finance's earnings calendar,
filters by demand zone proximity, scores them, and only trades those
scoring 70 or above with a 4%:8% risk-reward ratio.

Usage:
    python run_earning_demand_zone_backtest.py
"""
import logging
import sys
import warnings
from datetime import date, timedelta
from pathlib import Path

# Suppress noisy warnings
warnings.filterwarnings("ignore", message="resource_tracker:")
warnings.filterwarnings("ignore", category=DeprecationWarning)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ROOT_PATH = str(PROJECT_ROOT)
if ROOT_PATH not in sys.path:
    sys.path.insert(0, ROOT_PATH)

from backtesting.engines.earning_demand_zone_engine import EarningDemandZoneEngine
from backtesting.models.backtest_params import BacktestParams
from backtesting.strategies.earning_demand_zone_strategy import (
    EarningDemandZoneStrategy,
)
from common.helpers.yahoo_earnings_calendar import YahooEarningsCalendarScraper

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)

# Reduce noise from third-party libraries
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("yfinance").setLevel(logging.WARNING)
logging.getLogger("peewee").setLevel(logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.WARNING)


def main() -> None:
    """Run the backtest and print results."""
    # --- Configuration ---
    # Date range for the backtest.
    # Adjust these to control the period under test.
    start_date: date = date.today() - timedelta(days=365)
    end_date: date = date.today()
    
    initial_capital: float = 3000.0
    
    params = BacktestParams(
        initial_capital=initial_capital,
        commission_per_trade=2.5,
        position_size_pct=0.5,
        take_profit_pct=0.08,
        stop_loss_pct=0.04,
        supply_skip_distance_pct=0.08,
        start_date=start_date,
        end_date=end_date,
        interval="1d",
    )
    
    # --- Print header ---
    print("=" * 60)
    print("EARNING DEMAND ZONE STRATEGY BACKTEST")
    print("=" * 60)
    print()
    print("Strategy Rules:")
    print("  1. Discover tickers with earnings from Yahoo calendar")
    print("  2. Filter: close must be in a 5-year demand zone")
    print("  3. Filter: technical/fundamental score >= 70 / 100")
    print("  4. Entry:  close on day before earnings")
    print("  5. TP:     +8%  |  SL: -4%")
    print()
    print("Backtest Parameters:")
    print(f"  Initial Capital:     ${params.initial_capital:,.2f}")
    print(f"  Commission/Trade:    ${params.commission_per_trade:.2f}")
    print(f"  Position Size:       {params.position_size_pct * 100:.0f}% of capital")
    print(f"  Take Profit:         {params.take_profit_pct * 100:.0f}%")
    print(f"  Stop Loss:           {params.stop_loss_pct * 100:.0f}%")
    print(f"  Date Range:          {params.start_date} to {params.end_date}")
    print("=" * 60)
    
    # --- Build components ---
    strategy = EarningDemandZoneStrategy()
    calendar = YahooEarningsCalendarScraper(delay=1.5)
    engine = EarningDemandZoneEngine(strategy=strategy, calendar=calendar)
    
    # --- Run ---
    print(f"\nRunning backtest with {strategy.name}...")
    print("This may take a while (scraping calendar + fetching data)...\n")
    
    result = engine.run(params=params)
    
    # --- Print results ---
    print(result.summary())
    _print_trade_details(result)


def _print_trade_details(result) -> None:
    """Print detailed trade information."""
    if not result.trades:
        print("No trades executed.")
        return
    
    print("\nTOP 10 WINNING TRADES:")
    print("-" * 60)
    winners = sorted(
        [t for t in result.trades if t.is_winner],
        key=lambda x: x.pnl,
        reverse=True,
    )[:10]
    
    for t in winners:
        print(
            f"  {t.ticker:6} | Entry: ${t.entry_price:8.2f} | "
            f"Exit: ${t.exit_price:8.2f} | P&L: ${t.pnl:+8.2f} ({t.pnl_pct:+.1f}%)"
        )
    
    print("\nTOP 10 LOSING TRADES:")
    print("-" * 60)
    losers = sorted(
        [t for t in result.trades if not t.is_winner],
        key=lambda x: x.pnl,
    )[:10]
    
    for t in losers:
        print(
            f"  {t.ticker:6} | Entry: ${t.entry_price:8.2f} | "
            f"Exit: ${t.exit_price:8.2f} | P&L: ${t.pnl:+8.2f} ({t.pnl_pct:+.1f}%)"
        )


if __name__ == "__main__":
    main()
