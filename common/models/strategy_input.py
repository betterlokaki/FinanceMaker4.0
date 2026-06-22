"""Shared strategy sizing and risk input model."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class StrategyInputModel:
    """Shared live strategy trade sizing and risk/reward settings."""

    portfolio_pct_per_trade: float
    risk_pct: float
    reward_pct: float
    max_notional_per_trade: float | None = None

    def __post_init__(self) -> None:
        if not 0 < self.portfolio_pct_per_trade <= 1:
            raise ValueError("portfolio_pct_per_trade must be > 0 and <= 1")
        if self.risk_pct < 0:
            raise ValueError("risk_pct must be >= 0")
        if self.reward_pct < 0:
            raise ValueError("reward_pct must be >= 0")
        if self.max_notional_per_trade is not None and self.max_notional_per_trade <= 0:
            raise ValueError("max_notional_per_trade must be positive when set")


DEFAULT_STRATEGY_INPUT = StrategyInputModel(
    portfolio_pct_per_trade=0.25,
    risk_pct=0.03,
    reward_pct=0.05,
)
