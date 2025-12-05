"""CandleStick model representing OHLCV price data."""
from dataclasses import dataclass
from datetime import datetime

from common.models.period import Period


@dataclass
class CandleStick:
    """A candlestick representing OHLCV price data for a time period.
    
    Attributes:
        open: Opening price for the period.
        high: Highest price during the period.
        low: Lowest price during the period.
        close: Closing price for the period.
        volume: Trading volume during the period.
        time: Timestamp for the candlestick (UTC).
        period: Time granularity of the candlestick.
    """
    open: float
    high: float
    low: float
    close: float
    volume: int
    time: datetime
    period: Period
    
    @property
    def is_bullish(self) -> bool:
        """Check if this is a bullish (green) candle."""
        return self.close > self.open
    
    @property
    def is_bearish(self) -> bool:
        """Check if this is a bearish (red) candle."""
        return self.close < self.open
    
    @property
    def body_size(self) -> float:
        """Calculate the absolute size of the candle body."""
        return abs(self.close - self.open)
    
    @property
    def range_size(self) -> float:
        """Calculate the total range (high - low)."""
        return self.high - self.low
