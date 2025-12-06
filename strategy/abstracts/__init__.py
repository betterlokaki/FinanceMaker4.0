"""Strategy abstracts module."""
from strategy.abstracts.i_trading_strategy import ITradingStrategy
from strategy.abstracts.realtime_trading_base import RealTimeTradingBase

__all__: list[str] = [
    "ITradingStrategy",
    "RealTimeTradingBase",
]
