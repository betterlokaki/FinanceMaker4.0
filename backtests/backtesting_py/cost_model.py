"""Cost model helpers for backtesting.py runs."""
from __future__ import annotations

from collections.abc import Callable


CommissionCallable = Callable[[float, float], float]


def calculate_side_cost(
    order_size: float,
    price: float,
    commission_rate: float = 0.0005,
    tick_size: float = 0.01,
    slippage_ticks: float = 2.0,
    fixed_commission_per_side: float = 0.0,
) -> float:
    """Calculate one-side execution cost.

    Formula:
    - Fixed commission: fixed_commission_per_side
    - Percentage commission: commission_rate * notional
    - Slippage approximation: slippage_ticks * tick_size * abs(shares)
    """
    qty = abs(float(order_size))
    px = abs(float(price))
    notional = qty * px
    fixed_commission = max(0.0, float(fixed_commission_per_side))
    commission = notional * commission_rate
    slippage = qty * tick_size * slippage_ticks
    return fixed_commission + commission + slippage


def make_commission_callable(
    commission_rate: float = 0.0005,
    tick_size: float = 0.01,
    slippage_ticks: float = 2.0,
    fixed_commission_per_side: float = 0.0,
) -> CommissionCallable:
    """Create backtesting.py commission callable."""

    def _commission(order_size: float, price: float) -> float:
        return calculate_side_cost(
            order_size=order_size,
            price=price,
            commission_rate=commission_rate,
            tick_size=tick_size,
            slippage_ticks=slippage_ticks,
            fixed_commission_per_side=fixed_commission_per_side,
        )

    return _commission
