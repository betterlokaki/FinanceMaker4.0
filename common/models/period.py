"""Period enum for time granularity in market data."""
from enum import Enum


class Period(Enum):
    """Time period granularity for candlestick data.
    
    Used to specify the interval for price data retrieval.
    """
    SECOND = "second"
    MINUTE = "minute"
    HOUR = "hour"
    DAILY = "daily"
