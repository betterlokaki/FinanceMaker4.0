"""Backtesting module for strategy evaluation.

Public interface exports for backtesting functionality.
"""
from backtesting.abstracts import (
    BacktestStrategyBase,
    IBacktestEngine,
    IBacktestStrategy,
)
from backtesting.engines import VectorBTEngine
from backtesting.exceptions import BacktestError, InsufficientDataError
from backtesting.models import BacktestParams, BacktestResult, TradeRecord, Zone
from backtesting.strategies import SupplyDemandStrategy

__all__ = [
    # Interfaces
    "IBacktestEngine",
    "IBacktestStrategy",
    # Base classes
    "BacktestStrategyBase",
    # Engines
    "VectorBTEngine",
    # Strategies
    "SupplyDemandStrategy",
    # Models
    "BacktestParams",
    "BacktestResult",
    "TradeRecord",
    "Zone",
    # Exceptions
    "BacktestError",
    "InsufficientDataError",
]
