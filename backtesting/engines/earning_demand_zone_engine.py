"""Earning demand zone backtest engine with earnings calendar integration.

Discovers tickers by date via an earnings calendar provider, then runs
the demand zone + scoring strategy for each ticker-earnings pair.
"""
import logging
from collections import defaultdict
from datetime import timedelta

import pandas as pd

from backtesting.engines.vectorbt_engine import VectorBTEngine
from backtesting.exceptions.backtest_error import InsufficientDataError
from backtesting.models.backtest_params import BacktestParams
from backtesting.models.backtest_result import BacktestResult
from backtesting.models.trade_record import TradeRecord
from backtesting.strategies.earning_demand_zone_strategy import (
    EarningDemandZoneStrategy,
)
from common.helpers.abstracts.i_earnings_calendar import IEarningsCalendar

logger: logging.Logger = logging.getLogger(__name__)


class EarningDemandZoneEngine(VectorBTEngine):
    """Backtest engine specialised for the earning demand zone strategy.
    
    Unlike the base engine which takes a pre-defined ticker list, this
    engine discovers tickers dynamically from an earnings calendar
    provider. For each date in the backtest range it:
    
    1. Queries the calendar for tickers reporting earnings on that date.
    2. Groups results by ticker to minimise data fetching.
    3. For each ticker + earnings date, delegates to the strategy for
       demand zone checking, scoring, and signal generation.
    4. Processes all resulting trades chronologically via the inherited
       capital-tracking logic.
    """
    
    def __init__(
        self,
        strategy: EarningDemandZoneStrategy,
        calendar: IEarningsCalendar,
    ) -> None:
        """Initialise with strategy and calendar provider.
        
        Args:
            strategy: EarningDemandZoneStrategy instance.
            calendar: Earnings calendar provider for date-based lookups.
        """
        super().__init__(strategy)
        self._earning_strategy: EarningDemandZoneStrategy = strategy
        self._calendar: IEarningsCalendar = calendar
    
    def run(
        self,
        tickers: list[str] | None = None,
        params: BacktestParams | None = None,
        store_price_data: bool = False,
    ) -> BacktestResult:
        """Run the earning demand zone backtest.
        
        Overrides the base ``run`` to discover tickers from the earnings
        calendar instead of requiring a pre-built list.
        
        Args:
            tickers: Ignored. Tickers are discovered from the calendar.
            params: Backtest parameters (start_date, end_date, etc.).
            store_price_data: If True, store OHLCV data for visualisation.
            
        Returns:
            Aggregated BacktestResult.
        """
        if params is None:
            params = BacktestParams()
        
        # --- Phase 1: Discover tickers from earnings calendar ---
        print(
            f"\n[PHASE 1] Fetching earnings calendar "
            f"({params.start_date} to {params.end_date})..."
        )
        
        date_to_tickers = self._calendar.get_earnings_between(
            params.start_date, params.end_date
        )
        
        if not date_to_tickers:
            print("[WARNING] No earnings found in the date range.")
            return self._empty_result(params.initial_capital, 0)
        
        # Invert: ticker -> list[earnings_dates]  (minimises yfinance fetches)
        ticker_to_dates: dict[str, list[pd.Timestamp]] = defaultdict(list)
        for earnings_date, ticker_list in date_to_tickers.items():
            for ticker in ticker_list:
                ticker_to_dates[ticker].append(
                    pd.Timestamp(earnings_date)
                )
        
        # Sort each ticker's earnings dates chronologically
        for ticker in ticker_to_dates:
            ticker_to_dates[ticker].sort()
        
        total_pairs = sum(len(dates) for dates in ticker_to_dates.values())
        print(
            f"[PHASE 1 COMPLETE] {len(ticker_to_dates)} unique tickers, "
            f"{total_pairs} ticker-earnings pairs across "
            f"{len(date_to_tickers)} dates"
        )
        
        # --- Phase 2: Collect signals from each ticker ---
        print(
            f"\n[PHASE 2] Generating signals for "
            f"{len(ticker_to_dates)} tickers..."
        )
        
        all_signals: list[tuple[str, pd.DataFrame]] = []
        price_data_dict: dict[str, pd.DataFrame] = {}
        skipped: int = 0
        processed: int = 0
        
        for ticker, earnings_dates in ticker_to_dates.items():
            try:
                signals = self._process_ticker(
                    ticker, earnings_dates, params
                )
                
                if signals:
                    all_signals.extend(signals)
                    processed += 1
                    
                    if store_price_data and signals:
                        price_data_dict[ticker] = signals[0][1].copy()
                else:
                    skipped += 1
            
            except (InsufficientDataError, Exception) as e:
                logger.debug(f"Skipping {ticker}: {e}")
                skipped += 1
        
        if not all_signals:
            print("[WARNING] No valid signals collected from any ticker.")
            return self._empty_result(params.initial_capital, skipped)
        
        print(
            f"[PHASE 2 COMPLETE] Collected signals from {processed} tickers, "
            f"skipped {skipped}"
        )
        
        # --- Phase 3: Process trades chronologically ---
        print(
            "\n[PHASE 3] Processing trades chronologically "
            "with capital tracking..."
        )
        
        trades = self._process_chronological_trades(all_signals, params)
        
        if not trades:
            print("[WARNING] No trades executed despite signals being present.")
            return self._empty_result(
                params.initial_capital, skipped, False, price_data_dict
            )
        
        print(f"[PHASE 3 COMPLETE] Executed {len(trades)} trades")
        
        # --- Phase 4: Aggregate results ---
        final_capital = params.initial_capital + sum(t.pnl for t in trades)
        traded_tickers = len(set(t.ticker for t in trades))
        capital_depleted = final_capital < params.MIN_CAPITAL_THRESHOLD
        
        return self._aggregate_results(
            trades,
            params.initial_capital,
            final_capital,
            traded_tickers,
            skipped,
            capital_depleted,
            price_data_dict,
        )
    
    def _process_ticker(
        self,
        ticker: str,
        earnings_dates: list[pd.Timestamp],
        params: BacktestParams,
    ) -> list[tuple[str, pd.DataFrame]]:
        """Process a single ticker across all its earnings dates.
        
        Fetches data once, then iterates through each earnings date
        to generate signals via the strategy.
        
        Args:
            ticker: Stock ticker symbol.
            earnings_dates: Sorted list of earnings dates for this ticker.
            params: Backtest parameters.
            
        Returns:
            List of (ticker, signals_df) tuples for each earnings event
            that produced signals.
        """
        # Fetch data once for the entire backtest period
        zone_df, df = self._fetch_data(ticker, params)
        
        if df.empty:
            return []
        
        results: list[tuple[str, pd.DataFrame]] = []
        
        for earnings_date in earnings_dates:
            # Configure strategy for this earnings event
            self._earning_strategy.set_ticker(ticker)
            self._earning_strategy.set_earnings_date(earnings_date)
            
            try:
                signals_df = self._earning_strategy.generate_signals(
                    df.copy(), params, zone_df=zone_df
                )
                
                # Only include if there's at least one entry signal
                if signals_df["entry_signal"].any():
                    results.append((ticker, signals_df))
            except Exception as e:
                logger.debug(
                    f"Error generating signals for {ticker} "
                    f"(earnings {earnings_date}): {e}"
                )
            finally:
                # Reset strategy state
                self._earning_strategy.set_earnings_date(None)
                self._earning_strategy.set_ticker(None)
        
        return results
