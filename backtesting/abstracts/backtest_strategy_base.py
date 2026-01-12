"""Abstract base class for backtest strategies."""
from abc import ABC, abstractmethod

import pandas as pd

from backtesting.abstracts.i_backtest_strategy import IBacktestStrategy
from backtesting.models.backtest_params import BacktestParams
from backtesting.models.zone import Zone


class BacktestStrategyBase(ABC, IBacktestStrategy):
    """Abstract base class for backtest strategies.
    
    Provides common functionality for signal generation strategies.
    Subclasses must implement generate_signals() and name property.
    """
    
    @abstractmethod
    def generate_signals(
        self,
        df: pd.DataFrame,
        params: BacktestParams,
        zone_df: pd.DataFrame | None = None,
    ) -> pd.DataFrame:
        """Generate trading signals from price data.
        
        Args:
            df: DataFrame with OHLCV columns.
            params: Backtest parameters.
            zone_df: Optional extended DataFrame for zone detection with additional
                historical data before the backtest start date.
            
        Returns:
            DataFrame with signal columns added.
        """
        ...
    
    @abstractmethod
    def _check_price_triggers_entry(
        self,
        price: float,
        zones: list[Zone],
        params: BacktestParams,
    ) -> tuple[float, float, float] | None:
        """Check if price triggers entry signal.
        
        Args:
            price: Current price to check.
            zones: List of supply/demand zones.
            params: Backtest parameters.
            
        Returns:
            Tuple of (entry_price, take_profit, stop_loss) if triggered, else None.
        """
        ...
    
    @property
    @abstractmethod
    def name(self) -> str:
        """Return the strategy name."""
        ...
    
    def _validate_dataframe(self, df: pd.DataFrame) -> bool:
        """Validate that DataFrame has required columns.
        
        Args:
            df: DataFrame to validate.
            
        Returns:
            True if valid, raises ValueError otherwise.
        """
        required_cols = {"Open", "High", "Low", "Close", "Volume"}
        missing = required_cols - set(df.columns)
        if missing:
            raise ValueError(f"DataFrame missing required columns: {missing}")
        return True
    
    def _initialize_signal_columns(self, df: pd.DataFrame) -> pd.DataFrame:
        """Initialize signal columns with default values.
        
        Args:
            df: DataFrame to add columns to.
            
        Returns:
            DataFrame with initialized signal columns.
        """
        df = df.copy()
        df["entry_signal"] = False
        df["entry_price"] = 0.0
        df["take_profit"] = 0.0
        df["stop_loss"] = 0.0
        df["skip_trade"] = False
        return df
    
    def _get_intrabar_price_sequence(
        self,
        open_price: float,
        high: float,
        low: float,
        close: float,
    ) -> list[float]:
        """Get intra-bar price sequence simulating realistic price movement.
        
        Simulates the order in which prices are hit during a bar:
        - Bearish candles (close < open): Open → High → Low → Close
        - Bullish candles (close >= open): Open → Low → High → Close
        
        Args:
            open_price: Opening price of the bar.
            high: Highest price of the bar.
            low: Lowest price of the bar.
            close: Closing price of the bar.
            
        Returns:
            List of prices in the order they would be hit during the bar.
        """
        if close < open_price:
            # Bearish candle: price went up first, then down
            return [open_price, high, low, close]
        else:
            # Bullish candle: price went down first, then up
            return [open_price, low, high, close]
    
    def _process_bar_with_intrabar_sequence(
        self,
        df: pd.DataFrame,
        bar_index: int,
        zones: list[Zone],
        params: BacktestParams,
    ) -> None:
        """Process a single bar using intra-bar price sequence.
        
        Extracts OHLC, gets realistic price sequence, checks each price
        for entry trigger, and sets signal on first match.
        
        Args:
            df: DataFrame to modify (modified in place).
            bar_index: Index of the bar to process.
            zones: List of supply/demand zones.
            params: Backtest parameters.
        """
        # Extract OHLC for current bar
        open_price = df["Open"].iloc[bar_index]
        high = df["High"].iloc[bar_index]
        low = df["Low"].iloc[bar_index]
        close = df["Close"].iloc[bar_index]
        
        # Get realistic intra-bar price sequence
        price_sequence = self._get_intrabar_price_sequence(open_price, high, low, close)
        
        # Check each price in sequence for entry trigger
        for price in price_sequence:
            result = self._check_price_triggers_entry(price, zones, params)
            if result is not None:
                entry_price, take_profit, stop_loss = result
                df.loc[df.index[bar_index], "entry_signal"] = True
                df.loc[df.index[bar_index], "entry_price"] = entry_price
                df.loc[df.index[bar_index], "take_profit"] = take_profit
                df.loc[df.index[bar_index], "stop_loss"] = stop_loss
                break  # First touch wins
