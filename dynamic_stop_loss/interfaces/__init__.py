"""Dynamic stop loss interfaces."""
from dynamic_stop_loss.interfaces.i_dynamic_stop_loss_manager import (
    IDynamicStopLossManager,
)
from dynamic_stop_loss.interfaces.i_dynamic_stop_loss_policy import (
    IDynamicStopLossPolicy,
)

__all__: list[str] = [
    "IDynamicStopLossManager",
    "IDynamicStopLossPolicy",
]
