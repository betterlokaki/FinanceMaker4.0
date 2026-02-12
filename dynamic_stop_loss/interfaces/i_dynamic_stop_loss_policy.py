"""Dynamic stop loss policy interface."""
from typing import Protocol


class IDynamicStopLossPolicy(Protocol):
    """Calculates the trailing stop level from a high watermark."""

    def calculate_stop_level(
        self,
        high_watermark: float,
        trailing_pct: float,
    ) -> float:
        """Calculate the stop price.

        Returns:
            Stop level — if price drops to/below this, LIMIT SELL fires.
        """
        ...
