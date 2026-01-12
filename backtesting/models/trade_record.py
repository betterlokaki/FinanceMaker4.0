"""Trade record model for individual trade tracking."""
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class TradeRecord:
    """Immutable record of a single trade.
    
    Attributes:
        ticker: Stock symbol traded.
        entry_date: Date when position was opened.
        entry_price: Price at which position was opened.
        exit_date: Date when position was closed.
        exit_price: Price at which position was closed.
        shares: Number of shares traded.
        pnl: Profit/loss in dollars (after commissions).
        pnl_pct: Profit/loss as percentage.
        exit_reason: Reason for exit (take_profit, stop_loss, end_of_data).
        is_unrealized: True if position was still open at end of data.
    """
    
    ticker: str
    entry_date: datetime
    entry_price: float
    exit_date: datetime
    exit_price: float
    shares: float
    pnl: float
    pnl_pct: float
    exit_reason: str
    is_unrealized: bool = False
    
    @property
    def is_winner(self) -> bool:
        """Check if this trade was profitable."""
        return self.pnl > 0
    
    @property
    def hold_days(self) -> int:
        """Calculate number of days position was held."""
        return (self.exit_date - self.entry_date).days
