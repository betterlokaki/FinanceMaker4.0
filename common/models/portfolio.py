"""Portfolio model containing account summary and positions."""
from dataclasses import dataclass, field
from typing import Optional

from common.models.position import Position


@dataclass
class Portfolio:
    """Portfolio summary containing account information and positions.
    
    Attributes:
        positions: List of current positions.
        cash_balance: Available cash in the account.
        total_market_value: Total value of all positions.
        total_equity: Cash + market value of positions.
        buying_power: Available buying power for trading.
        unrealized_pnl: Total unrealized profit/loss.
        realized_pnl: Total realized profit/loss.
    """
    positions: list[Position] = field(default_factory=list)
    cash_balance: float = 0.0
    total_market_value: float = 0.0
    total_equity: float = 0.0
    buying_power: float = 0.0
    unrealized_pnl: float = 0.0
    realized_pnl: float = 0.0
    
    def get_position(self, ticker: str) -> Optional[Position]:
        """Get position for a specific ticker.
        
        Args:
            ticker: Stock ticker symbol.
            
        Returns:
            Position if found, None otherwise.
        """
        for position in self.positions:
            if position.ticker.upper() == ticker.upper():
                return position
        return None
    
    def has_position(self, ticker: str) -> bool:
        """Check if portfolio has a position in the given ticker.
        
        Args:
            ticker: Stock ticker symbol.
            
        Returns:
            True if position exists, False otherwise.
        """
        return self.get_position(ticker) is not None
    
    @property
    def position_count(self) -> int:
        """Get number of positions in portfolio."""
        return len(self.positions)
