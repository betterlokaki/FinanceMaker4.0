"""MarketHours enum for real-time market data."""
from enum import IntEnum


class MarketHours(IntEnum):
    """Market hours status from real-time data feed.
    
    Indicates when the market data was captured relative to trading hours.
    """
    PRE_MARKET = 0
    REGULAR_MARKET = 1
    POST_MARKET = 2
    EXTENDED_HOURS_MARKET = 3
    OVERNIGHT_MARKET = 4
