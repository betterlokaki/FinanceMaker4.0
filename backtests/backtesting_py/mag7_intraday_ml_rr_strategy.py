"""Mag7 5-minute pooled ML probability strategy shell."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from backtesting import Strategy


@dataclass(frozen=True)
class MlRrParams:
    """Frozen parameters for ML-driven fixed RR simulations."""

    horizon_bars: int = 6
    stop_pct: float = 0.004
    risk_reward_ratio: float = 2.0
    probability_threshold: float = 0.6
    leverage: float = 3.0
    max_leaf_nodes: int = 31
    max_iter: int = 120
    learning_rate: float = 0.04
    l2_regularization: float = 0.08
    random_state: int = 7

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class Mag7IntradayMlRrStrategy(Strategy):
    """Backtesting.py shell; runner trains models and simulates sleeves."""

    def init(self) -> None:
        return None

    def next(self) -> None:
        return None
