"""File-based ticker cache implementation."""
import json
import logging
from datetime import date
from pathlib import Path

from common.cache.abstracts.ticker_cache_base import TickerCacheBase
from common.settings import CacheConfig

logger: logging.Logger = logging.getLogger(__name__)


class FileTickerCache(TickerCacheBase):
    """File-based ticker cache using JSON files in a date-organized structure.
    
    Stores tickers as JSON files in the configured cache directory with
    filenames based on the date (YYYY-MM-DD.json). Automatically creates
    the cache directory if it doesn't exist.
    
    Example structure:
        /tmp/financemaker/tickers/
            2025-12-08.json  # ["AAPL", "MSFT", "GOOGL"]
            2025-12-07.json  # ["TSLA", "NVDA"]
    """

    def __init__(self, config: CacheConfig) -> None:
        """Initialize the file ticker cache.
        
        Args:
            config: Cache configuration with enabled flag and directory path.
        """
        self._config: CacheConfig = config
        self._cache_dir: Path = Path(config.cache_dir)

    def _get_cache_file_path(self, cache_date: date) -> Path:
        """Get the file path for a specific date's cache.
        
        Args:
            cache_date: Date for which to get the cache file path.
            
        Returns:
            Path to the cache file for the given date.
        """
        return self._cache_dir / f"{cache_date.isoformat()}.json"

    def _ensure_cache_dir_exists(self) -> None:
        """Create the cache directory if it doesn't exist."""
        self._cache_dir.mkdir(parents=True, exist_ok=True)

    def _save_tickers_impl(self, tickers: list[str], cache_date: date) -> None:
        """Save tickers to a JSON file for the given date.
        
        Args:
            tickers: List of stock ticker symbols to cache.
            cache_date: Date for which the tickers are valid.
        """
        if not self._config.enabled:
            logger.debug("Cache disabled, skipping save")
            return
        
        self._ensure_cache_dir_exists()
        cache_file: Path = self._get_cache_file_path(cache_date)
        
        with open(cache_file, "w", encoding="utf-8") as f:
            json.dump(tickers, f, indent=2)
        
        logger.info("💾 Cached %d tickers to %s", len(tickers), cache_file)

    def _load_tickers_impl(self, cache_date: date) -> list[str] | None:
        """Load tickers from a JSON file for the given date.
        
        Args:
            cache_date: Date for which to load cached tickers.
            
        Returns:
            List of cached ticker symbols, or None if no cache exists.
        """
        if not self._config.enabled:
            logger.debug("Cache disabled, skipping load")
            return None
        
        cache_file: Path = self._get_cache_file_path(cache_date)
        
        if not cache_file.exists():
            logger.debug("No cache file found for %s", cache_date)
            return None
        
        with open(cache_file, "r", encoding="utf-8") as f:
            tickers: list[str] = json.load(f)
        
        logger.info("📂 Loaded %d tickers from cache: %s", len(tickers), tickers)
        return tickers

    def _clear_old_cache_impl(self) -> None:
        """Clear all cache files older than today."""
        if not self._cache_dir.exists():
            return
        
        today: date = date.today()
        deleted_count: int = 0
        
        for cache_file in self._cache_dir.glob("*.json"):
            try:
                file_date_str: str = cache_file.stem
                file_date: date = date.fromisoformat(file_date_str)
                
                if file_date < today:
                    cache_file.unlink()
                    deleted_count += 1
                    logger.debug("Deleted old cache file: %s", cache_file)
            except ValueError:
                logger.warning("Skipping invalid cache file: %s", cache_file)
                continue
        
        if deleted_count > 0:
            logger.info("🗑️ Cleared %d old cache files", deleted_count)
