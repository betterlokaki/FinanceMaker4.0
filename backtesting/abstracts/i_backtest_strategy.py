"""Interface for backtest strategy."""
from typing import Protocol

import pandas as pd

from backtesting.models.backtest_params import BacktestParams


class IBacktestStrategy(Protocol):
    """Protocol defining the interface for backtest strategies.
    
    Implementations must generate entry/exit signals from price data.
    """
    
    def generate_signals(
        self,
        df: pd.DataFrame,
        params: BacktestParams,
        zone_df: pd.DataFrame | None = None,
    ) -> pd.DataFrame:
        """Generate trading signals from price data.
        
        Args:
            df: DataFrame with OHLCV columns (Open, High, Low, Close, Volume).
            params: Backtest parameters including take profit and stop loss.
            zone_df: Optional extended DataFrame for zone detection with additional
                historical data before the backtest start date.
            
        Returns:
            DataFrame with additional signal columns:
                - 'entry_signal': Boolean, True when entry condition met.
                - 'entry_price': Float, price to enter position.
                - 'take_profit': Float, take profit price level.
                - 'stop_loss': Float, stop loss price level.
                - 'skip_trade': Boolean, True if trade should be skipped.
        """
        ...
    
    @property
    def name(self) -> str:
        """Return the strategy name."""
        ...
