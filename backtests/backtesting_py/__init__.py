"""Backtesting.py integration package."""

from backtests.backtesting_py.config import BacktestRunConfig, PortfolioConfig, TradeDirection
from backtests.backtesting_py.portfolio_orchestrator import (
    SharedPortfolioResult,
    run_shared_capital_portfolio,
)

try:
    from backtests.backtesting_py.tdfi_adx_range_ctr_strategy import (
        TDFIAdxRangeCtrConfluenceStrategy,
    )
except Exception:  # pragma: no cover - optional when dependency is not installed
    TDFIAdxRangeCtrConfluenceStrategy = None  # type: ignore[assignment]

try:
    from backtests.backtesting_py.mag7_ema_slope_regime_strategy import (
        Mag7EmaSlopeRegimeStrategy,
    )
except Exception:  # pragma: no cover - optional when dependency is not installed
    Mag7EmaSlopeRegimeStrategy = None  # type: ignore[assignment]

__all__ = [
    "BacktestRunConfig",
    "PortfolioConfig",
    "TradeDirection",
    "SharedPortfolioResult",
    "run_shared_capital_portfolio",
    "TDFIAdxRangeCtrConfluenceStrategy",
    "Mag7EmaSlopeRegimeStrategy",
]
