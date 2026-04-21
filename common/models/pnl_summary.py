"""Portfolio profit/loss summary for reporting."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class PnlSummary:
    """Normalized P/L summary payload used for end-of-day reporting."""

    as_of_date: date
    since_date: date
    currency: str
    daily_pnl: float | None
    pnl_since_date: float | None
    baseline_date: date | None
    baseline_nav: float | None
    current_nav: float | None
