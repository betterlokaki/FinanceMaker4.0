"""Position model representing a stock holding in the portfolio."""
from dataclasses import dataclass
from typing import Optional


@dataclass
class Position:
    """A position (holding) in the portfolio.
    
    Attributes:
        ticker: Stock ticker symbol.
        quantity: Number of shares held (negative for short positions).
        average_cost: Average cost per share.
        current_price: Current market price per share.
        market_value: Total market value of the position.
        unrealized_pnl: Unrealized profit/loss.
        realized_pnl: Realized profit/loss from closed portions.
    """
    ticker: str
    quantity: int
    average_cost: float
    current_price: Optional[float] = None
    market_value: Optional[float] = None
    unrealized_pnl: Optional[float] = None
    realized_pnl: Optional[float] = None
    
    @property
    def is_long(self) -> bool:
        """Check if this is a long position."""
        return self.quantity > 0
    
    @property
    def is_short(self) -> bool:
        """Check if this is a short position."""
        return self.quantity < 0
    
    @property
    def cost_basis(self) -> float:
        """Calculate total cost basis of the position."""
        return abs(self.quantity) * self.average_cost
    
    @property
    def unrealized_pnl_percent(self) -> Optional[float]:
        """Calculate unrealized P&L as percentage of cost basis."""
        if self.unrealized_pnl is None or self.cost_basis == 0:
            return None
        return (self.unrealized_pnl / self.cost_basis) * 100
