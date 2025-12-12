"""Abstract base class for ticker cache implementations."""
from abc import ABC, abstractmethod
from datetime import date

from common.cache.abstracts.i_ticker_cache import ITickerCache


class TickerCacheBase(ABC, ITickerCache):
    """Abstract base class for ticker cache implementations.
    
    Implements ITickerCache protocol and provides shared validation logic.
    All concrete ticker cache implementations should inherit from this class.
    """

    def save_tickers(self, tickers: list[str], cache_date: date) -> None:
        """Save tickers to cache for a specific date.
        
        Args:
            tickers: List of stock ticker symbols to cache.
            cache_date: Date for which the tickers are valid.
            
        Raises:
            ValueError: If tickers list is empty.
        """
        if not tickers:
            return  # Nothing to save
        
        self._save_tickers_impl(tickers, cache_date)

    def load_tickers(self, cache_date: date) -> list[str] | None:
        """Load cached tickers for a specific date.
        
        Args:
            cache_date: Date for which to load cached tickers.
            
        Returns:
            List of cached ticker symbols, or None if no cache exists.
        """
        return self._load_tickers_impl(cache_date)

    def clear_old_cache(self) -> None:
        """Clear all cache files older than today."""
        self._clear_old_cache_impl()

    @abstractmethod
    def _save_tickers_impl(self, tickers: list[str], cache_date: date) -> None:
        """Implementation of ticker saving logic.
        
        Args:
            tickers: List of stock ticker symbols to cache.
            cache_date: Date for which the tickers are valid.
        """
        ...

    @abstractmethod
    def _load_tickers_impl(self, cache_date: date) -> list[str] | None:
        """Implementation of ticker loading logic.
        
        Args:
            cache_date: Date for which to load cached tickers.
            
        Returns:
            List of cached ticker symbols, or None if no cache exists.
        """
        ...

    @abstractmethod
    def _clear_old_cache_impl(self) -> None:
        """Implementation of cache clearing logic."""
        ...
