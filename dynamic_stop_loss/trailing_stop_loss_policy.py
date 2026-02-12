"""Trailing percentage stop loss policy."""


class TrailingStopLossPolicy:
    """Trails a fixed percentage below the high watermark.

    Example (trailing_pct=4.0):
        entry=$100 → stop=$96 → price→$110 → stop=$105.60 → TRIGGERED
    """

    def calculate_stop_level(
        self,
        high_watermark: float,
        trailing_pct: float,
    ) -> float:
        """Calculate trailing stop: high_watermark * (1 - trail%)."""
        return round(high_watermark * (1 - trailing_pct / 100), 2)
