"""Supply and demand zone backtesting strategy."""
import pandas as pd

from backtesting.abstracts.backtest_strategy_base import BacktestStrategyBase
from backtesting.models.backtest_params import BacktestParams
from backtesting.models.zone import Zone, ZoneType


class SupplyDemandStrategy(BacktestStrategyBase):
    """Supply and demand zone trading strategy.
    
    Entry Rules:
        - Enter when price is in a demand zone (mid-range entry)
        - Skip if supply zone exists within take_profit % above entry
        
    Exit Rules:
        - Take profit: 8% above entry price
        - Stop loss: 1% below demand zone bottom
    """
    
    @property
    def name(self) -> str:
        """Return the strategy name."""
        return "Supply & Demand Zone Strategy"
    
    def generate_signals(
        self,
        df: pd.DataFrame,
        params: BacktestParams,
        zone_df: pd.DataFrame | None = None,
    ) -> pd.DataFrame:
        """Generate trading signals based on supply/demand zones.
        
        Args:
            df: DataFrame with OHLCV columns (backtest period).
            params: Backtest parameters with take profit and stop loss.
            zone_df: Optional extended DataFrame with 5 years of historical data
                before backtest start for zone detection.
            
        Returns:
            DataFrame with signal columns added.
        """
        # Import here to avoid circular dependency
        from common.helpers.zone_detection import get_supply_demand_zones
        
        self._validate_dataframe(df)
        df = self._initialize_signal_columns(df)
        
        # Use extended data for zone detection if provided, otherwise use backtest data
        zone_detection_df = zone_df if zone_df is not None else df
        zones = get_supply_demand_zones(zone_detection_df)
        if not zones:
            return df
        
        df = self._generate_zone_signals(df, zones, params)
        return df
    
    def _generate_zone_signals(
        self,
        df: pd.DataFrame,
        zones: list[Zone],
        params: BacktestParams,
    ) -> pd.DataFrame:
        """Generate signals for each bar based on zone positions.
        
        Uses base class OHLC sequence checking for each bar.
        """
        for i in range(len(df)):
            self._process_bar_with_intrabar_sequence(df, i, zones, params)
        
        return df
    
    def _check_price_triggers_entry(
        self,
        price: float,
        zones: list[Zone],
        params: BacktestParams,
    ) -> tuple[float, float, float] | None:
        """Check if price triggers entry signal at a demand zone.
        
        Args:
            price: Current price to check.
            zones: All supply/demand zones.
            params: Backtest parameters.
            
        Returns:
            Tuple of (entry_price, take_profit, stop_loss) if triggered, else None.
        """
        # Import here to avoid circular dependency
        from common.helpers.zone_detection import (
            find_demand_zones_at_price,
            has_blocking_supply_zone,
        )
        
        demand_zones = find_demand_zones_at_price(zones, price)
        if not demand_zones:
            return None
        
        # Select worst (oldest) zone for conservative entry
        worst_zone = self._select_worst_demand_zone(demand_zones)
        entry_price = worst_zone.mid_price
        
        # Check for blocking supply zone
        if has_blocking_supply_zone(zones, entry_price, params.take_profit_pct):
            return None
        
        # Calculate exit levels
        take_profit = entry_price * (1 + params.take_profit_pct)
        stop_loss = worst_zone.bottom * (1 - params.stop_loss_pct)
        
        return (entry_price, take_profit, stop_loss)
    
    def _select_worst_demand_zone(self, zones: list[Zone]) -> Zone:
        """Select the worst demand zone from candidates.
        
        Prefers oldest zone (lowest bar_index) for conservative entry.
        """
        return min(zones, key=lambda z: z.bar_index)
