"""Backtest parameters model."""
from dataclasses import dataclass, field
from datetime import date, timedelta


def _default_start_date() -> date:
    """Default to 5 years ago."""
    return date.today() - timedelta(days=5 * 365)


def _default_end_date() -> date:
    """Default to today."""
    return date.today()


@dataclass(frozen=True)
class BacktestParams:
    """Immutable parameters for backtest configuration.
    
    Attributes:
        initial_capital: Starting capital for the backtest.
        commission_per_trade: Commission cost per transaction (buy or sell).
        position_size_pct: Percentage of capital to use per trade (0.0-1.0).
        take_profit_pct: Take profit percentage above entry (e.g., 0.08 = 8%).
        stop_loss_pct: Stop loss percentage below zone bottom (e.g., 0.01 = 1%).
        supply_skip_distance_pct: Skip trade if supply zone within this % above entry.
        start_date: Start date for backtest data (defaults to 5 years ago).
        end_date: End date for backtest data (defaults to today).
        min_capital_threshold: Minimum capital to continue trading ($100).
    """
    
    MIN_CAPITAL_THRESHOLD: float = 100.0
    
    initial_capital: float = 3000.0
    commission_per_trade: float = 2.5
    position_size_pct: float = 0.5
    take_profit_pct: float = 0.08
    stop_loss_pct: float = 0.01
    supply_skip_distance_pct: float = 0.08
    interval: str = "1d"
    zone_lookback_years: int = 5
    start_date: date = field(default_factory=_default_start_date)
    end_date: date = field(default_factory=_default_end_date)
    
    @property
    def round_trip_commission(self) -> float:
        """Calculate total commission for a complete trade (buy + sell)."""
        return self.commission_per_trade * 2
    
    def calculate_position_value(self, capital: float) -> float:
        """Calculate position value based on available capital."""
        return capital * self.position_size_pct
