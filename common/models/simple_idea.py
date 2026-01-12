"""Simple trade idea model."""
from dataclasses import dataclass


@dataclass(frozen=True)
class SimpleIdea:
    """Represents a static trade idea.

    Attributes:
        ticker: Stock ticker symbol (e.g., "AAPL").
        entry_price: Planned entry price.
        take_profit: Target price to take profit.
        stop_loss: Price to cut losses.
    """
    ticker: str
    entry_price: float
    take_profit: float
    stop_loss: float

    def __post_init__(self) -> None:
        """Validate the trade idea parameters."""
        if not self.ticker:
            raise ValueError("ticker is required")
        if self.entry_price <= 0:
            raise ValueError("entry_price must be positive")
        if self.take_profit <= 0:
            raise ValueError("take_profit must be positive")
        if self.stop_loss <= 0:
            raise ValueError("stop_loss must be positive")
