"""Opening drop trading strategy."""
from datetime import timedelta

import numpy as np
import pandas as pd

from backtesting.abstracts.backtest_strategy_base import BacktestStrategyBase
from backtesting.models.backtest_params import BacktestParams
from backtesting.models.zone import Zone


class OpeningDropStrategy(BacktestStrategyBase):
    """Strategy that buys when price drops 1% below the opening candle's low.
    
    Entry Rules:
        - Identify the first 5-minute candle of the trading day.
        - Calculate entry price = Opening Candle Low * 0.99.
        - Enter if price drops to or below this level during the same day.
        
    Exit Rules:
        - Take profit: 8% above entry price.
        - Stop loss: 4% below entry price.
    """
    
    def __init__(self) -> None:
        """Initialize strategy with day tracking."""
        self._current_day_open_low: float | None = None
    
    @property
    def name(self) -> str:
        """Return the strategy name."""
        return "Opening Drop Strategy"
    
    def generate_signals(
        self,
        df: pd.DataFrame,
        params: BacktestParams,
        zone_df: pd.DataFrame | None = None,
    ) -> pd.DataFrame:
        """Generate trading signals based on opening range drop.
        
        Args:
            df: DataFrame with OHLCV columns (must be intraday, e.g. 5m).
            params: Backtest parameters.
            zone_df: Optional extended DataFrame (unused by this strategy).
            
        Returns:
            DataFrame with signal columns added.
        """
        self._validate_dataframe(df)
        df = self._initialize_signal_columns(df)
        
        # Ensure we have datetime index
        if not isinstance(df.index, pd.DatetimeIndex):
            return df
        
        # Build day-to-opening-low mapping
        df['date'] = df.index.date
        df['is_open_candle'] = df['date'] != df['date'].shift(1)
        df['day_open_low'] = np.where(df['is_open_candle'], df['Low'], np.nan)
        df['day_open_low'] = df['day_open_low'].ffill()
        
        # Process each bar using base class helper
        for i in range(len(df)):
            # Skip opening candle (can't trigger on itself)
            if df['is_open_candle'].iloc[i]:
                continue
            
            day_open_low = df['day_open_low'].iloc[i]
            if pd.isna(day_open_low):
                continue
            
            # Store context for _check_price_triggers_entry
            self._current_day_open_low = day_open_low
            
            # Use base class method to process bar with OHLC sequence
            self._process_bar_with_intrabar_sequence(df, i, [], params)
        
        # Clean up temporary columns
        df = df.drop(columns=['date', 'is_open_candle', 'day_open_low'])
        
        return df
    
    def _check_price_triggers_entry(
        self,
        price: float,
        zones: list[Zone],
        params: BacktestParams,
    ) -> tuple[float, float, float] | None:
        """Check if price triggers entry at 1% below opening low.
        
        Args:
            price: Current price to check.
            zones: Not used (required by abstract interface).
            params: Backtest parameters.
            
        Returns:
            Tuple of (entry_price, take_profit, stop_loss) if triggered, else None.
        """
        if self._current_day_open_low is None:
            return None
        
        # Calculate target entry: 1% below opening candle's low
        target_entry = self._current_day_open_low * 0.99
        
        # Check if current price touches or goes below target
        if price <= target_entry:
            entry_price = target_entry
            take_profit = entry_price * 1.08  # 8% above entry
            stop_loss = entry_price * 0.96    # 4% below entry
            return (entry_price, take_profit, stop_loss)
        
        return None
