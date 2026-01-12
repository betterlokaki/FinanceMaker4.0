"""Earning call backtest engine with earnings date handling."""
from datetime import datetime, timedelta

import pandas as pd
import yfinance_cache as yf

from backtesting.engines.vectorbt_engine import VectorBTEngine
from backtesting.exceptions.backtest_error import InsufficientDataError
from backtesting.models.backtest_params import BacktestParams
from backtesting.models.trade_record import TradeRecord
from common.helpers.yfinance_cache_manager import init_yfinance_cache

init_yfinance_cache()


def _get_historical_earnings_dates(
    ticker: str,
    start_date,
    end_date,
) -> list[pd.Timestamp]:
    """Get all historical earnings dates within a date range.
    
    Internal function to avoid circular imports.
    """
    try:
        stock = yf.Ticker(ticker)
        earnings_dates = getattr(stock, "earnings_dates", None)
        
        if earnings_dates is None or earnings_dates.empty:
            return []
        
        start_ts = pd.Timestamp(start_date).tz_localize(None)
        end_ts = pd.Timestamp(end_date).tz_localize(None)
        
        result = []
        for dt in earnings_dates.index:
            earnings_ts = pd.Timestamp(dt)
            if earnings_ts.tzinfo is not None:
                earnings_ts = earnings_ts.tz_localize(None)
            
            if start_ts <= earnings_ts <= end_ts:
                result.append(earnings_ts)
        
        return sorted(result)
        
    except Exception:
        return []


class EarningCallEngine(VectorBTEngine):
    """Backtest engine specialized for earning call strategy.
    
    Handles fetching earnings dates and generating signals for each
    earnings event within the backtest period.
    """
    
    def __init__(self, strategy) -> None:
        """Initialize engine with earning call strategy.
        
        Args:
            strategy: EarningCallStrategy instance.
        """
        super().__init__(strategy)
        self._earning_strategy = strategy
    
    def run_single(
        self,
        ticker: str,
        params: BacktestParams,
        position_value: float | None = None,
    ) -> list[TradeRecord]:
        """Run backtest on a single ticker with earnings date handling.
        
        Args:
            ticker: Stock ticker symbol.
            params: Backtest parameters.
            position_value: Fixed position value per trade.
            
        Returns:
            List of TradeRecord objects.
        """
        if position_value is None:
            position_value = params.calculate_position_value(params.initial_capital)
        
        # Get earnings dates within the backtest period
        earnings_dates = _get_historical_earnings_dates(
            ticker, params.start_date, params.end_date
        )
        
        if not earnings_dates:
            return []
        
        # Fetch data
        zone_df, df = self._fetch_data(ticker, params)
        if df.empty:
            raise InsufficientDataError(ticker, 200, 0)
        
        all_trades: list[TradeRecord] = []
        
        # Process each earnings date
        for earnings_date in earnings_dates:
            # Set the earnings date on the strategy
            self._earning_strategy.set_earnings_date(earnings_date)
            
            # Generate signals for this earnings event
            signals_df = self._earning_strategy.generate_signals(
                df.copy(), params, zone_df=zone_df
            )
            
            # Simulate trades for this earnings event
            trades = self._simulate_trades(
                ticker, signals_df, params, position_value
            )
            
            all_trades.extend(trades)
            
            # Reset for next earnings date
            self._earning_strategy.set_earnings_date(None)
        
        return all_trades
