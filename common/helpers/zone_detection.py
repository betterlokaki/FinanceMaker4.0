"""Zone detection functions for supply and demand zones.

Main entry point functions for zone detection.
"""
import pandas as pd

from backtesting.models.zone import Zone, ZoneState, ZoneType
from common.helpers.zone_helpers import (
    _create_demand_zone,
    _create_supply_zone,
    _remove_overlapping_zones,
    _update_zone_state,
    calculate_atr,
    detect_candle_patterns,
    detect_extra_volume,
)


def get_supply_demand_zones(
    df: pd.DataFrame,
    atr_period: int = 200,
    atr_multiplier: float = 2.0,
    volume_period: int = 1000,
    lookback_bars: int = 5,
    consecutive_candles: int = 3,
    max_zones: int = 5,
) -> list[Zone]:
    """Calculate supply and demand zones from daily candles.
    
    Based on the Pine Script "Supply and Demand Zones [BigBeluga]" indicator.
    
    Args:
        df: DataFrame with columns: Open, High, Low, Close, Volume.
        atr_period: Period for ATR calculation.
        atr_multiplier: Multiplier for ATR to set zone height.
        volume_period: Period for average volume calculation.
        lookback_bars: Max bars to look back for trigger candle.
        consecutive_candles: Required consecutive candles for pattern.
        max_zones: Maximum zones per type to keep.
        
    Returns:
        List of Zone objects with active/tested states.
    """
    if len(df) < atr_period:
        return []
    
    df = df.copy().reset_index(drop=True)
    
    atr = calculate_atr(df, atr_period) * atr_multiplier
    bull_candle, bear_candle = detect_candle_patterns(df)
    extra_vol = detect_extra_volume(df, volume_period)
    
    supply_zones: list[Zone] = []
    demand_zones: list[Zone] = []
    count_bear = 0
    count_bull = 0
    
    for i in range(consecutive_candles, len(df)):
        current_atr = atr.iloc[i]
        if pd.isna(current_atr):
            continue
        
        supply_zones, count_bear = _check_supply_pattern(
            df, i, bear_candle, bull_candle, extra_vol,
            current_atr, consecutive_candles, lookback_bars,
            supply_zones, count_bear,
        )
        
        demand_zones, count_bull = _check_demand_pattern(
            df, i, bull_candle, bear_candle, extra_vol,
            current_atr, consecutive_candles, lookback_bars,
            demand_zones, count_bull,
        )
        
        supply_zones, demand_zones = _update_all_zones(
            df, i, supply_zones, demand_zones,
        )
    
    supply_zones = [z for z in supply_zones if z.state != ZoneState.BROKEN]
    demand_zones = [z for z in demand_zones if z.state != ZoneState.BROKEN]
    
    supply_zones = _remove_overlapping_zones(supply_zones)
    demand_zones = _remove_overlapping_zones(demand_zones)
    
    supply_zones = supply_zones[-max_zones:]
    demand_zones = demand_zones[-max_zones:]
    
    return supply_zones + demand_zones


def _check_supply_pattern(
    df: pd.DataFrame,
    i: int,
    bear_candle: pd.Series,
    bull_candle: pd.Series,
    extra_vol: pd.Series,
    current_atr: float,
    consecutive: int,
    lookback: int,
    zones: list[Zone],
    count: int,
) -> tuple[list[Zone], int]:
    """Check for supply zone pattern at bar index i."""
    is_bear_pattern = all(bear_candle.iloc[i - j] for j in range(consecutive))
    has_extra_vol = extra_vol.iloc[i - 1] if i >= 1 else False
    
    if is_bear_pattern and has_extra_vol and count == 0:
        delta = 0.0
        for j in range(lookback + 1):
            idx = i - j
            if idx < 0:
                break
            if bull_candle.iloc[idx]:
                zone = _create_supply_zone(df, idx, current_atr, delta)
                zones.append(zone)
                count = 1
                break
            vol = df["Volume"].iloc[idx]
            delta += -vol if bear_candle.iloc[idx] else vol
    
    if count >= 1:
        count += 1
    if count >= 15:
        count = 0
    
    return zones, count


def _check_demand_pattern(
    df: pd.DataFrame,
    i: int,
    bull_candle: pd.Series,
    bear_candle: pd.Series,
    extra_vol: pd.Series,
    current_atr: float,
    consecutive: int,
    lookback: int,
    zones: list[Zone],
    count: int,
) -> tuple[list[Zone], int]:
    """Check for demand zone pattern at bar index i."""
    is_bull_pattern = all(bull_candle.iloc[i - j] for j in range(consecutive))
    has_extra_vol = extra_vol.iloc[i - 1] if i >= 1 else False
    
    if is_bull_pattern and has_extra_vol and count == 0:
        delta = 0.0
        for j in range(lookback + 1):
            idx = i - j
            if idx < 0:
                break
            if bear_candle.iloc[idx]:
                zone = _create_demand_zone(df, idx, current_atr, delta)
                zones.append(zone)
                count = 1
                break
            vol = df["Volume"].iloc[idx]
            delta += vol if bull_candle.iloc[idx] else -vol
    
    if count >= 1:
        count += 1
    if count >= 15:
        count = 0
    
    return zones, count


def _update_all_zones(
    df: pd.DataFrame,
    i: int,
    supply_zones: list[Zone],
    demand_zones: list[Zone],
) -> tuple[list[Zone], list[Zone]]:
    """Update all zone states based on current price."""
    current_close = df["Close"].iloc[i]
    current_high = df["High"].iloc[i]
    current_low = df["Low"].iloc[i]
    
    supply_zones = [
        _update_zone_state(z, current_close, current_high, current_low, i)
        for z in supply_zones
    ]
    demand_zones = [
        _update_zone_state(z, current_close, current_high, current_low, i)
        for z in demand_zones
    ]
    
    return supply_zones, demand_zones


def find_demand_zones_at_price(
    zones: list[Zone],
    price: float,
) -> list[Zone]:
    """Find all demand zones containing a given price.
    
    Args:
        zones: List of zones to search.
        price: Price to check.
        
    Returns:
        List of demand zones containing the price.
    """
    return [
        z for z in zones
        if z.zone_type == ZoneType.DEMAND
        and z.is_active_or_tested()
        and z.contains_price(price)
    ]


def find_supply_zones_above_price(
    zones: list[Zone],
    price: float,
    max_distance_pct: float,
) -> list[Zone]:
    """Find supply zones above a price within distance threshold.
    
    Args:
        zones: List of zones to search.
        price: Reference price.
        max_distance_pct: Maximum distance as percentage (0.08 = 8%).
        
    Returns:
        List of supply zones within range above the price.
    """
    max_price = price * (1 + max_distance_pct)
    return [
        z for z in zones
        if z.zone_type == ZoneType.SUPPLY
        and z.is_active_or_tested()
        and price < z.bottom <= max_price
    ]


def has_blocking_supply_zone(
    zones: list[Zone],
    entry_price: float,
    take_profit_pct: float,
) -> bool:
    """Check if a supply zone blocks the take profit target.
    
    Args:
        zones: List of zones to check.
        entry_price: Proposed entry price.
        take_profit_pct: Take profit percentage (0.08 = 8%).
        
    Returns:
        True if a supply zone exists between entry and take profit.
    """
    blocking_zones = find_supply_zones_above_price(
        zones, entry_price, take_profit_pct
    )
    return len(blocking_zones) > 0
