"""Breakout swing trading strategy for large cap stocks.

Strategy Rules:
    Entry:
        - Stock must have tested a resistance level at least 3 times.
        - Stock must be in an uptrend (higher lows, above moving average).
        - Enter slightly below resistance zone.
        - Wait for breakout above resistance.
        
    Exit:
        - Take profit: 10% above entry.
        - Stop loss: 5% below entry.
        - Risk:Reward = 5:10 (1:2 ratio).
"""
import pandas as pd

from backtesting.abstracts.backtest_strategy_base import BacktestStrategyBase
from backtesting.models.backtest_params import BacktestParams
from backtesting.models.zone import Zone, ZoneState
from common.helpers.resistance_helpers import (
    check_breakout,
    detect_resistance_zones,
    get_closest_untested_resistance,
    is_in_uptrend,
)


class BreakoutSwingStrategy(BacktestStrategyBase):
    """Swing trading strategy based on resistance breakouts.
    
    Targets large cap stocks with established resistance levels.
    Enters on confirmation of uptrend, exits on breakout with 1:2 R:R.
    """
    
    def __init__(self) -> None:
        """Initialize strategy."""
        self._resistance_zones: list[Zone] = []
    
    @property
    def name(self) -> str:
        """Return the strategy name."""
        return "Breakout Swing Strategy"
    
    def generate_signals(
        self,
        df: pd.DataFrame,
        params: BacktestParams,
        zone_df: pd.DataFrame | None = None,
    ) -> pd.DataFrame:
        """Generate trading signals based on resistance breakout.
        
        Args:
            df: DataFrame with OHLCV columns (daily data).
            params: Backtest parameters.
            zone_df: Optional extended DataFrame for zone detection.
            
        Returns:
            DataFrame with signal columns added.
        """
        self._validate_dataframe(df)
        df = self._initialize_signal_columns(df)
        
        if len(df) < 50:  # Need sufficient history
            return df
        
        # Use extended dataframe for zone detection if available
        detection_df = zone_df if zone_df is not None else df
        
        # Detect resistance zones (must be tested at least 3 times)
        self._resistance_zones = detect_resistance_zones(
            detection_df,
            zone_tolerance_pct=0.02,  # 2% tolerance
            min_tests=3,
        )
        
        if not self._resistance_zones:
            return df
        
        # Track if we've entered a trade already
        entered_trade = False
        
        # Process each bar
        for i in range(20, len(df)):  # Start after window for uptrend detection
            if entered_trade:
                break  # Only one trade per backtest run
            
            current_price = df["Close"].iloc[i]
            
            # Check if in uptrend using recent data
            recent_df = df.iloc[:i+1]
            if not is_in_uptrend(recent_df, window=20, min_higher_lows=2):
                continue
            
            # Get closest resistance above current price
            resistance = get_closest_untested_resistance(
                df.iloc[:i+1],
                self._resistance_zones,
                current_price,
            )
            
            if resistance is None:
                continue
            
            # Check if price is approaching resistance (within 2% below)
            distance_to_resistance = (resistance.bottom - current_price) / current_price
            if not (0 < distance_to_resistance <= 0.02):
                continue
            
            # Set entry signal slightly below resistance
            entry_price = resistance.bottom * 0.995  # 0.5% below resistance
            take_profit = entry_price * 1.10  # 10% profit
            stop_loss = entry_price * 0.95  # 5% stop loss
            
            df.at[df.index[i], "entry_signal"] = True
            df.at[df.index[i], "entry_price"] = entry_price
            df.at[df.index[i], "take_profit"] = take_profit
            df.at[df.index[i], "stop_loss"] = stop_loss
            
            entered_trade = True
        
        return df
    
    def _check_price_triggers_entry(
        self,
        price: float,
        zones: list[Zone],
        params: BacktestParams,
    ) -> tuple[float, float, float] | None:
        """Check if price triggers entry signal.
        
        Not used by this strategy (uses generate_signals instead).
        
        Args:
            price: Current price to check.
            zones: List of resistance zones.
            params: Backtest parameters.
            
        Returns:
            None (strategy doesn't use this method).
        """
        return None
