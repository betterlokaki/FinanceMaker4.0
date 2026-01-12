"""Zone model for supply and demand zone representation."""
from dataclasses import dataclass
from enum import Enum


class ZoneType(str, Enum):
    """Type of price zone."""
    
    SUPPLY = "supply"
    DEMAND = "demand"


class ZoneState(str, Enum):
    """Current state of a zone."""
    
    ACTIVE = "active"
    TESTED = "tested"
    BROKEN = "broken"


@dataclass(frozen=True)
class Zone:
    """Immutable supply or demand zone.
    
    Represents a price zone where significant buying (demand)
    or selling (supply) pressure has been identified.
    
    Attributes:
        zone_type: Whether this is a supply or demand zone.
        top: Upper price boundary of the zone.
        bottom: Lower price boundary of the zone.
        bar_index: Index of the bar where zone was created.
        state: Current state of the zone (active, tested, broken).
        delta: Volume delta when zone was formed.
    """
    
    zone_type: ZoneType
    top: float
    bottom: float
    bar_index: int
    state: ZoneState
    delta: float = 0.0
    
    @property
    def mid_price(self) -> float:
        """Calculate the middle price of the zone."""
        return (self.top + self.bottom) / 2
    
    @property
    def height(self) -> float:
        """Calculate the zone height (top - bottom)."""
        return self.top - self.bottom
    
    def contains_price(self, price: float) -> bool:
        """Check if a price is within this zone."""
        return self.bottom <= price <= self.top
    
    def is_active_or_tested(self) -> bool:
        """Check if zone is still valid for trading."""
        return self.state in (ZoneState.ACTIVE, ZoneState.TESTED)
