"""Dynamic stop loss module — monitors positions and fires LIMIT SELL ORH."""
from dynamic_stop_loss.dynamic_stop_loss_manager import DynamicStopLossManager
from dynamic_stop_loss.interfaces.i_dynamic_stop_loss_manager import (
    IDynamicStopLossManager,
)
from dynamic_stop_loss.interfaces.i_dynamic_stop_loss_policy import (
    IDynamicStopLossPolicy,
)
from dynamic_stop_loss.trailing_stop_loss_policy import TrailingStopLossPolicy

__all__: list[str] = [
    "DynamicStopLossManager",
    "IDynamicStopLossManager",
    "IDynamicStopLossPolicy",
    "TrailingStopLossPolicy",
]
