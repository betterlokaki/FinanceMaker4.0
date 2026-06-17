"""Shared realtime tick logging utilities for live strategies."""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from common.models.pricing_data import PricingData


class RealtimeTickLogger:
    """Log first tick per ticker and then throttle repeated tick logs."""

    def __init__(self, *, interval_seconds: float = 30.0) -> None:
        self._interval_seconds = max(0.0, float(interval_seconds))
        self._last_logged_at: dict[str, datetime] = {}

    def reset(self) -> None:
        self._last_logged_at.clear()

    def log(
        self,
        logger: logging.Logger,
        *,
        strategy_name: str,
        data: PricingData,
        tick_time: datetime,
        state: str,
    ) -> None:
        ticker = data.id.upper()
        now = datetime.now(timezone.utc)
        last_logged_at = self._last_logged_at.get(ticker)
        if last_logged_at is not None:
            elapsed = (now - last_logged_at).total_seconds()
            if elapsed < self._interval_seconds:
                return

        self._last_logged_at[ticker] = now
        logger.info(
            "%s live tick %s price=%.4f size=%d tick_time=%s state=%s",
            strategy_name,
            ticker,
            float(data.price),
            max(0, int(data.last_size or 0)),
            tick_time.isoformat(),
            state,
        )
