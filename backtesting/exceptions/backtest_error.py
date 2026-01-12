"""Custom exceptions for backtesting operations."""


class BacktestError(Exception):
    """Base exception for backtesting errors.
    
    All backtesting-specific exceptions should inherit from this class.
    """
    
    def __init__(self, message: str, ticker: str | None = None) -> None:
        """Initialize BacktestError.
        
        Args:
            message: Error description.
            ticker: Optional ticker symbol associated with the error.
        """
        self.ticker = ticker
        super().__init__(message)


class InsufficientDataError(BacktestError):
    """Raised when there is not enough data to run backtest.
    
    This occurs when the historical data period is too short
    for calculating required indicators (e.g., ATR period).
    """
    
    def __init__(
        self,
        ticker: str,
        required_bars: int,
        available_bars: int,
    ) -> None:
        """Initialize InsufficientDataError.
        
        Args:
            ticker: Ticker symbol with insufficient data.
            required_bars: Minimum number of bars required.
            available_bars: Number of bars actually available.
        """
        self.required_bars = required_bars
        self.available_bars = available_bars
        message = (
            f"Insufficient data for {ticker}: "
            f"required {required_bars} bars, got {available_bars}"
        )
        super().__init__(message, ticker)
