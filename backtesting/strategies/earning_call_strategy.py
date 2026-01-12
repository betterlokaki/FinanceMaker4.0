"""Earning call trading strategy."""
import pandas as pd

from backtesting.abstracts.backtest_strategy_base import BacktestStrategyBase
from backtesting.models.backtest_params import BacktestParams
from backtesting.models.zone import Zone


class EarningCallStrategy(BacktestStrategyBase):
    """Strategy that buys at the low of the day before earnings call.
    
    Entry Rules:
        - For each stock, get the earnings call date from Yahoo Finance.
        - Calculate entry price = Low of the day before earnings.
        - Enter at the low price of the day before earnings.
        
    Exit Rules:
        - Take profit: 8% above entry price.
        - Stop loss: 4% below entry price.
    """
    
    def __init__(self) -> None:
        """Initialize strategy with earnings tracking."""
        self._current_entry_low: float | None = None
        self._earnings_date: pd.Timestamp | None = None
    
    @property
    def name(self) -> str:
        """Return the strategy name."""
        return "Earning Call Strategy"
    
    def set_earnings_date(self, earnings_date: pd.Timestamp | None) -> None:
        """Set the earnings date for the current ticker.
        
        Args:
            earnings_date: The earnings call date for the ticker.
        """
        self._earnings_date = earnings_date
    
    def generate_signals(
        self,
        df: pd.DataFrame,
        params: BacktestParams,
        zone_df: pd.DataFrame | None = None,
    ) -> pd.DataFrame:
        """Generate trading signals based on day before earnings.
        
        Args:
            df: DataFrame with OHLCV columns.
            params: Backtest parameters.
            zone_df: Optional extended DataFrame (unused by this strategy).
            
        Returns:
            DataFrame with signal columns added.
        """
        self._validate_dataframe(df)
        df = self._initialize_signal_columns(df)
        
        if not isinstance(df.index, pd.DatetimeIndex):
            return df
        
        if self._earnings_date is None:
            return df
        
        # Normalize earnings date for comparison
        earnings_date = pd.Timestamp(self._earnings_date)
        if earnings_date.tzinfo is not None:
            earnings_date = earnings_date.tz_localize(None)
        earnings_date_normalized = earnings_date.normalize()
        
        # Find the day before earnings
        day_before_earnings = earnings_date_normalized - pd.Timedelta(days=1)
        
        # Skip weekends (go back to Friday if weekend)
        while day_before_earnings.weekday() >= 5:
            day_before_earnings -= pd.Timedelta(days=1)
        
        # Get normalized dates from dataframe for comparison
        df_dates_normalized = df.index.normalize()
        if hasattr(df_dates_normalized, 'tz') and df_dates_normalized.tz is not None:
            df_dates_normalized = df_dates_normalized.tz_localize(None)
        
        # Find bars on the day before earnings
        day_before_mask = df_dates_normalized == day_before_earnings
        if not day_before_mask.any():
            return df
        
        # Get the low of the day before earnings
        day_before_data = df[day_before_mask]
        if day_before_data.empty:
            return df
        
        entry_low = day_before_data["Low"].min()
        
        # Set entry signal on the last bar of the day before earnings
        last_bar_idx = day_before_data.index[-1]
        bar_position = df.index.get_loc(last_bar_idx)
        
        # Calculate entry, TP, and SL
        entry_price = entry_low
        take_profit = entry_price * 1.08  # 8% above entry
        stop_loss = entry_price * 0.96    # 4% below entry
        
        # Set the signal directly
        df.iloc[bar_position, df.columns.get_loc("entry_signal")] = True
        df.iloc[bar_position, df.columns.get_loc("entry_price")] = entry_price
        df.iloc[bar_position, df.columns.get_loc("take_profit")] = take_profit
        df.iloc[bar_position, df.columns.get_loc("stop_loss")] = stop_loss
        
        return df
    
    def _check_price_triggers_entry(
        self,
        price: float,
        zones: list[Zone],
        params: BacktestParams,
    ) -> tuple[float, float, float] | None:
        """Check if price triggers entry at day's low before earnings.
        
        This method is not used by this strategy but required by interface.
        """
        return None
