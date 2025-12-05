"""PricingData model for real-time market tick data."""
from dataclasses import dataclass
from datetime import datetime

from common.models.market_hours import MarketHours


@dataclass(slots=True)
class PricingData:
    """Real-time pricing data from WebSocket feed.
    
    Represents a single tick/quote update for a security.
    Uses __slots__ for memory efficiency with high-frequency data.
    
    Attributes:
        id: Ticker symbol (e.g., "AAPL").
        price: Current/last trade price.
        time: Timestamp of the tick (UTC).
        currency: Currency code (e.g., "USD").
        exchange: Exchange code (e.g., "NMS").
        market_hours: Market session indicator.
        change: Absolute price change from previous close.
        change_percent: Percentage change from previous close.
        day_volume: Total volume for the trading day.
        day_high: Highest price of the day.
        day_low: Lowest price of the day.
        open_price: Opening price for the day.
        previous_close: Previous day's closing price.
        bid: Current bid price.
        bid_size: Current bid size.
        ask: Current ask price.
        ask_size: Current ask size.
        last_size: Size of the last trade.
        short_name: Company short name.
    """
    id: str
    price: float
    time: datetime
    currency: str = ""
    exchange: str = ""
    market_hours: MarketHours = MarketHours.REGULAR_MARKET
    change: float = 0.0
    change_percent: float = 0.0
    day_volume: int = 0
    day_high: float = 0.0
    day_low: float = 0.0
    open_price: float = 0.0
    previous_close: float = 0.0
    bid: float = 0.0
    bid_size: int = 0
    ask: float = 0.0
    ask_size: int = 0
    last_size: int = 0
    short_name: str = ""

    @property
    def spread(self) -> float:
        """Calculate bid-ask spread."""
        return self.ask - self.bid if self.ask and self.bid else 0.0

    @property
    def mid_price(self) -> float:
        """Calculate mid-point between bid and ask."""
        if self.ask and self.bid:
            return (self.ask + self.bid) / 2.0
        return self.price
