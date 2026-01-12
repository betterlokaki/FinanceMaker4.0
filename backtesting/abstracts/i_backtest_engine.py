"""Interface for backtest engine."""
from typing import Protocol

from backtesting.models.backtest_params import BacktestParams
from backtesting.models.backtest_result import BacktestResult


class IBacktestEngine(Protocol):
    """Protocol defining the interface for backtest engines.
    
    Implementations must execute backtests across multiple tickers
    and return aggregated results.
    """
    
    def run(
        self,
        tickers: list[str],
        params: BacktestParams,
    ) -> BacktestResult:
        """Run backtest on a list of tickers.
        
        Args:
            tickers: List of stock ticker symbols to backtest.
            params: Backtest parameters (capital, commission, etc.).
            
        Returns:
            BacktestResult with aggregated performance metrics.
        """
        ...
    
    def run_single(
        self,
        ticker: str,
        params: BacktestParams,
    ) -> list:
        """Run backtest on a single ticker.
        
        Args:
            ticker: Stock ticker symbol to backtest.
            params: Backtest parameters.
            
        Returns:
            List of TradeRecord objects for this ticker.
        """
        ...
