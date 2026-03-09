"""Resistance zone detection helpers for breakout swing strategy.

Detects resistance levels tested at least 3 times.
"""
from typing import Final

import pandas as pd

from common.models.zone import Zone, ZoneState, ZoneType


# Resistance detection constants
DEFAULT_ZONE_TOLERANCE_PCT: Final[float] = 0.02  # 2% tolerance for zone grouping
MIN_TESTS_REQUIRED: Final[int] = 3  # Minimum tests to consider a valid resistance


def detect_resistance_zones(
    df: pd.DataFrame,
    zone_tolerance_pct: float = DEFAULT_ZONE_TOLERANCE_PCT,
    min_tests: int = MIN_TESTS_REQUIRED,
) -> list[Zone]:
    """Detect resistance zones tested at least min_tests times.
    
    A resistance zone is identified when price tests a similar high level
    multiple times without breaking through significantly.
    
    Args:
        df: DataFrame with OHLCV columns (daily candles).
        zone_tolerance_pct: Percentage tolerance for grouping highs into zones.
        min_tests: Minimum number of tests required for valid resistance.
        
    Returns:
        List of Zone objects representing resistance levels.
    """
    if len(df) < min_tests:
        return []
    
    df = df.copy().reset_index(drop=True)
    
    # Find local highs (peaks) where High[i] > High[i-1] and High[i] > High[i+1]
    local_highs: list[tuple[int, float]] = []
    
    for i in range(1, len(df) - 1):
        if df["High"].iloc[i] > df["High"].iloc[i - 1] and df["High"].iloc[i] > df["High"].iloc[i + 1]:
            local_highs.append((i, df["High"].iloc[i]))
    
    if len(local_highs) < min_tests:
        return []
    
    # Group local highs into zones based on tolerance
    zones: list[Zone] = []
    used_indices: set[int] = set()
    
    for i, (idx, high_price) in enumerate(local_highs):
        if idx in used_indices:
            continue
        
        # Find all local highs within tolerance of this high
        tolerance = high_price * zone_tolerance_pct
        zone_high = high_price
        zone_low = high_price - tolerance
        zone_highs: list[tuple[int, float]] = [(idx, high_price)]
        used_indices.add(idx)
        
        for j, (other_idx, other_high) in enumerate(local_highs):
            if i == j or other_idx in used_indices:
                continue
            
            # Check if this high is within tolerance
            if zone_low <= other_high <= zone_high + tolerance:
                zone_highs.append((other_idx, other_high))
                used_indices.add(other_idx)
                # Expand zone boundaries
                zone_high = max(zone_high, other_high + tolerance)
                zone_low = min(zone_low, other_high - tolerance)
        
        # Only create zone if tested at least min_tests times
        if len(zone_highs) >= min_tests:
            # Get average high as zone center
            avg_high = sum(h for _, h in zone_highs) / len(zone_highs)
            
            # Get earliest test index
            earliest_idx = min(idx for idx, _ in zone_highs)
            
            # Create resistance zone
            zone = Zone(
                zone_type=ZoneType.SUPPLY,  # Resistance is supply zone
                top=avg_high + (avg_high * zone_tolerance_pct / 2),
                bottom=avg_high - (avg_high * zone_tolerance_pct / 2),
                bar_index=earliest_idx,
                state=ZoneState.TESTED,  # Already tested multiple times
                delta=len(zone_highs),  # Store test count in delta
            )
            zones.append(zone)
    
    # Sort by bar index (chronological order)
    zones.sort(key=lambda z: z.bar_index)
    
    return zones


def is_in_uptrend(
    df: pd.DataFrame,
    window: int = 20,
    min_higher_lows: int = 2,
) -> bool:
    """Check if the stock is currently in an uptrend.
    
    An uptrend is identified when:
    - Recent closes are above the moving average.
    - We have at least min_higher_lows higher lows in the window.
    
    Args:
        df: DataFrame with OHLCV columns.
        window: Number of recent bars to analyze.
        min_higher_lows: Minimum higher lows required.
        
    Returns:
        True if in uptrend, False otherwise.
    """
    if len(df) < window:
        return False
    
    recent_df = df.tail(window).copy().reset_index(drop=True)
    
    # Calculate simple moving average
    sma = recent_df["Close"].mean()
    
    # Check if recent closes are above SMA
    recent_closes_above_sma = recent_df["Close"].tail(5).mean() > sma
    
    if not recent_closes_above_sma:
        return False
    
    # Count higher lows
    higher_lows_count = 0
    for i in range(1, len(recent_df)):
        if recent_df["Low"].iloc[i] > recent_df["Low"].iloc[i - 1]:
            higher_lows_count += 1
    
    return higher_lows_count >= min_higher_lows


def get_closest_untested_resistance(
    df: pd.DataFrame,
    resistance_zones: list[Zone],
    current_price: float,
) -> Zone | None:
    """Get the closest resistance zone above current price that hasn't been broken.
    
    Args:
        df: DataFrame with OHLCV columns.
        resistance_zones: List of resistance zones.
        current_price: Current stock price.
        
    Returns:
        Closest resistance Zone above current price, or None if no valid zone.
    """
    if not resistance_zones:
        return None
    
    # Filter zones above current price
    zones_above = [
        z for z in resistance_zones
        if z.bottom > current_price and z.state != ZoneState.BROKEN
    ]
    
    if not zones_above:
        return None
    
    # Sort by distance from current price
    zones_above.sort(key=lambda z: z.bottom - current_price)
    
    return zones_above[0]


def check_breakout(
    current_high: float,
    current_close: float,
    resistance_zone: Zone,
) -> bool:
    """Check if price has broken out above resistance zone.
    
    Breakout confirmed when both:
    - High touches or exceeds zone bottom
    - Close is above zone bottom (confirmation)
    
    Args:
        current_high: Current candle high.
        current_close: Current candle close.
        resistance_zone: Resistance zone to check.
        
    Returns:
        True if breakout confirmed, False otherwise.
    """
    return current_high >= resistance_zone.bottom and current_close > resistance_zone.bottom
