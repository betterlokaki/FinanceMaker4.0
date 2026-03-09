"""Zone calculation helpers for supply and demand zones.

Refactored from pullers/generating_stocks_for_nextime_prompt.py
with proper type hints and SOLID compliance.
"""
import pandas as pd

from common.models.zone import Zone, ZoneState, ZoneType


def calculate_atr(df: pd.DataFrame, period: int = 200) -> pd.Series:
    """Calculate Average True Range (ATR).
    
    Args:
        df: DataFrame with High, Low, Close columns.
        period: ATR lookback period.
        
    Returns:
        Series containing ATR values.
    """
    high = df["High"]
    low = df["Low"]
    prev_close = df["Close"].shift(1)
    
    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()
    true_range = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    
    return true_range.rolling(window=period).mean()


def detect_candle_patterns(df: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    """Detect bullish and bearish candle patterns.
    
    Args:
        df: DataFrame with Open, Close columns.
        
    Returns:
        Tuple of (bull_candle, bear_candle) boolean Series.
    """
    bull_candle = df["Close"] > df["Open"]
    bear_candle = df["Close"] < df["Open"]
    return bull_candle, bear_candle


def detect_extra_volume(df: pd.DataFrame, period: int = 1000) -> pd.Series:
    """Detect bars with above-average volume.
    
    Args:
        df: DataFrame with Volume column.
        period: Volume averaging period.
        
    Returns:
        Boolean Series indicating extra volume bars.
    """
    window = min(period, len(df))
    avg_volume = df["Volume"].rolling(window=window).mean()
    return df["Volume"] > avg_volume


def _create_supply_zone(
    df: pd.DataFrame,
    idx: int,
    atr_value: float,
    delta: float,
) -> Zone:
    """Create a supply zone at the given index."""
    zone_low = df["Low"].iloc[idx]
    return Zone(
        zone_type=ZoneType.SUPPLY,
        top=zone_low + atr_value,
        bottom=zone_low,
        bar_index=idx,
        state=ZoneState.ACTIVE,
        delta=delta,
    )


def _create_demand_zone(
    df: pd.DataFrame,
    idx: int,
    atr_value: float,
    delta: float,
) -> Zone:
    """Create a demand zone at the given index."""
    zone_high = df["High"].iloc[idx]
    return Zone(
        zone_type=ZoneType.DEMAND,
        top=zone_high,
        bottom=zone_high - atr_value,
        bar_index=idx,
        state=ZoneState.ACTIVE,
        delta=delta,
    )


def _update_zone_state(
    zone: Zone,
    current_close: float,
    current_high: float,
    current_low: float,
    bar_index: int,
) -> Zone:
    """Update zone state based on current price action."""
    top = zone.top
    bot = zone.bottom
    
    if zone.zone_type == ZoneType.SUPPLY:
        if current_close > top:
            return Zone(
                zone_type=zone.zone_type,
                top=zone.top,
                bottom=zone.bottom,
                bar_index=zone.bar_index,
                state=ZoneState.BROKEN,
                delta=zone.delta,
            )
        if zone.state == ZoneState.ACTIVE and (bar_index - zone.bar_index - 15) > 20:
            if current_high > bot > current_low:
                return Zone(
                    zone_type=zone.zone_type,
                    top=zone.top,
                    bottom=zone.bottom,
                    bar_index=zone.bar_index,
                    state=ZoneState.TESTED,
                    delta=zone.delta,
                )
    else:  # DEMAND
        if current_close < bot:
            return Zone(
                zone_type=zone.zone_type,
                top=zone.top,
                bottom=zone.bottom,
                bar_index=zone.bar_index,
                state=ZoneState.BROKEN,
                delta=zone.delta,
            )
        if zone.state == ZoneState.ACTIVE and (bar_index - zone.bar_index - 15) > 20:
            if current_low < top < current_high:
                return Zone(
                    zone_type=zone.zone_type,
                    top=zone.top,
                    bottom=zone.bottom,
                    bar_index=zone.bar_index,
                    state=ZoneState.TESTED,
                    delta=zone.delta,
                )
    
    return zone


def _remove_overlapping_zones(zones: list[Zone]) -> list[Zone]:
    """Remove overlapping zones, keeping more recent ones."""
    if len(zones) <= 1:
        return zones
    
    result: list[Zone] = []
    for i, zone in enumerate(zones):
        is_overlapped = False
        for j, other in enumerate(zones):
            if i == j:
                continue
            if zone.zone_type == ZoneType.SUPPLY:
                if zone.bottom < other.top < zone.top:
                    if other.bar_index > zone.bar_index:
                        is_overlapped = True
                        break
            else:
                if zone.bottom < other.bottom < zone.top:
                    if other.bar_index > zone.bar_index:
                        is_overlapped = True
                        break
        if not is_overlapped:
            result.append(zone)
    
    return result
