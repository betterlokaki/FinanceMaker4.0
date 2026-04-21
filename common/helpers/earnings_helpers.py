"""Earnings date helpers using Yahoo Finance."""
from datetime import date, datetime

import pandas as pd
import yfinance as yf

from common.helpers.yfinance_cache_manager import init_yfinance_cache

init_yfinance_cache()


def get_earnings_date(
    ticker: str,
    start_date: date | None = None,
    end_date: date | None = None,
) -> pd.Timestamp | None:
    """Get the next or most recent earnings date for a ticker.
    
    Args:
        ticker: Stock ticker symbol.
        start_date: Optional start date to filter earnings within range.
        end_date: Optional end date to filter earnings within range.
        
    Returns:
        Earnings date as Timestamp if found, None otherwise.
    """
    try:
        stock = yf.Ticker(ticker)
        
        # Try to get from earnings_dates attribute (most reliable)
        earnings_dates = getattr(stock, "earnings_dates", None)
        if earnings_dates is not None and not earnings_dates.empty:
            # Get all earnings dates
            all_dates = earnings_dates.index.tolist()
            
            if start_date and end_date:
                # Filter earnings within the backtest range
                start_ts = pd.Timestamp(start_date).tz_localize(None)
                end_ts = pd.Timestamp(end_date).tz_localize(None)
                
                filtered_dates = []
                for d in all_dates:
                    d_ts = pd.Timestamp(d)
                    if d_ts.tzinfo is not None:
                        d_ts = d_ts.tz_localize(None)
                    if start_ts <= d_ts <= end_ts:
                        filtered_dates.append(d_ts)
                
                if filtered_dates:
                    return filtered_dates[0]
            else:
                # Return the most recent/next earnings
                if all_dates:
                    d_ts = pd.Timestamp(all_dates[0])
                    if d_ts.tzinfo is not None:
                        d_ts = d_ts.tz_localize(None)
                    return d_ts
        
        # Try to get earnings dates from calendar
        calendar = stock.calendar
        if calendar is not None:
            if isinstance(calendar, dict) and "Earnings Date" in calendar:
                earnings_list = calendar["Earnings Date"]
                if earnings_list:
                    return pd.Timestamp(earnings_list[0])
        
        return None
        
    except Exception:
        return None


def get_historical_earnings_dates(
    ticker: str,
    start_date: date,
    end_date: date,
) -> list[pd.Timestamp]:
    """Get all historical earnings dates within a date range.
    
    Args:
        ticker: Stock ticker symbol.
        start_date: Start of the date range.
        end_date: End of the date range.
        
    Returns:
        List of earnings dates within the range.
    """
    try:
        stock = yf.Ticker(ticker)
        earnings_dates = getattr(stock, "earnings_dates", None)
        
        if earnings_dates is None or earnings_dates.empty:
            print(f"{ticker}: No earnings dates found, symbol may be delisted")
            return []
        
        start_ts = pd.Timestamp(start_date).tz_localize(None)
        end_ts = pd.Timestamp(end_date).tz_localize(None)
        
        result = []
        for dt in earnings_dates.index:
            earnings_ts = pd.Timestamp(dt)
            # Handle timezone-aware timestamps
            if earnings_ts.tzinfo is not None:
                earnings_ts = earnings_ts.tz_localize(None)
            
            if start_ts <= earnings_ts <= end_ts:
                result.append(earnings_ts)
        
        return sorted(result)
        
    except Exception as e:
        print(f"{ticker}: Error getting earnings dates - {e}")
        return []
