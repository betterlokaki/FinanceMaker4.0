"""Backtesting.py integration package."""

from backtests.backtesting_py.config import BacktestRunConfig, PortfolioConfig, TradeDirection
from backtests.backtesting_py.isolated_backtest_engine import (
    build_equity_series_from_stats,
    build_single_buy_and_hold_equity,
    fetch_ohlcv_for_tickers,
    fetch_ohlcv_for_tickers_sync,
    filter_regular_session,
    parse_date_range_utc,
    plot_isolated_ticker_candlestick_trade_markers,
    plot_isolated_ticker_equity_curves,
    print_symbol_stats,
    resolve_tickers,
    run_isolated_backtests_from_data,
)
from backtests.backtesting_py.portfolio_orchestrator import (
    SharedPortfolioResult,
    run_shared_capital_portfolio,
)
from backtests.backtesting_py.plotting import (
    CandlestickTradeMarker,
    plot_candlestick_trade_markers,
    trade_markers_from_backtesting_trades,
    trade_markers_from_shared_executed_trades,
    trade_markers_from_stats_by_ticker,
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

try:
    from backtests.backtesting_py.rsi_extreme_rr_strategy import (
        RsiExtremeRRStrategy,
    )
except Exception:  # pragma: no cover - optional when dependency is not installed
    RsiExtremeRRStrategy = None  # type: ignore[assignment]

try:
    from backtests.backtesting_py.forecast_model_rr_strategy import (
        ForecastModelRRStrategy,
    )
except Exception:  # pragma: no cover - optional when dependency is not installed
    ForecastModelRRStrategy = None  # type: ignore[assignment]

try:
    from backtests.backtesting_py.grok_sparse_swing_strategy import (
        SparseBacktestResult,
        TradeRecord,
        SwingSetup,
        run_sparse_grok_swing_backtest,
    )
except Exception:  # pragma: no cover - optional when dependency is not installed
    SparseBacktestResult = None  # type: ignore[assignment]
    TradeRecord = None  # type: ignore[assignment]
    SwingSetup = None  # type: ignore[assignment]
    run_sparse_grok_swing_backtest = None  # type: ignore[assignment]

__all__ = [
    "BacktestRunConfig",
    "PortfolioConfig",
    "TradeDirection",
    "SharedPortfolioResult",
    "run_shared_capital_portfolio",
    "CandlestickTradeMarker",
    "plot_candlestick_trade_markers",
    "trade_markers_from_backtesting_trades",
    "trade_markers_from_shared_executed_trades",
    "trade_markers_from_stats_by_ticker",
    "resolve_tickers",
    "parse_date_range_utc",
    "fetch_ohlcv_for_tickers",
    "fetch_ohlcv_for_tickers_sync",
    "filter_regular_session",
    "build_equity_series_from_stats",
    "build_single_buy_and_hold_equity",
    "print_symbol_stats",
    "run_isolated_backtests_from_data",
    "plot_isolated_ticker_candlestick_trade_markers",
    "plot_isolated_ticker_equity_curves",
    "TDFIAdxRangeCtrConfluenceStrategy",
    "Mag7EmaSlopeRegimeStrategy",
    "RsiExtremeRRStrategy",
    "ForecastModelRRStrategy",
    "SparseBacktestResult",
    "TradeRecord",
    "SwingSetup",
    "run_sparse_grok_swing_backtest",
]
