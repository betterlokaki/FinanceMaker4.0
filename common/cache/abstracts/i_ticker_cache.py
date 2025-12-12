"""Ticker cache interface protocol."""
from datetime import date
from typing import Protocol


class ITickerCache(Protocol):
    """Ticker cache interface - defines contract for all ticker cache implementations.
    
    This protocol defines the interface that all ticker cache implementations
    must follow. Use this for type hints instead of concrete classes.
    """

    def save_tickers(self, tickers: list[str], cache_date: date) -> None:
        """Save tickers to cache for a specific date.
        
        Args:
            tickers: List of stock ticker symbols to cache.
            cache_date: Date for which the tickers are valid.
        """
        ...

    def load_tickers(self, cache_date: date) -> list[str] | None:
        """Load cached tickers for a specific date.
        
        Args:
            cache_date: Date for which to load cached tickers.
            
        Returns:
            List of cached ticker symbols, or None if no cache exists.
        """
        ...

    def clear_old_cache(self) -> None:
        """Clear all cache files older than today.
        
        This should be called at the end of each trading day to remove
        stale ticker data.
        """
        ...
