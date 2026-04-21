"""Zone-filtered Finviz scanner - filters stocks by 5-year demand zone presence.

Uses Supply & Demand zone detection on 5-year daily data to filter Finviz results.
Only returns stocks where the latest close price is within an active/tested demand zone.
"""
import asyncio
import logging
from datetime import datetime, timedelta

import httpx
import pandas as pd
import yfinance as yf

from common.models.scanner_params import ScannerParams
from pullers.scanners.finviz.finviz_base import FinvizScanner
from common.helpers.yfinance_cache_manager import init_yfinance_cache

init_yfinance_cache()

logger = logging.getLogger(__name__)


class ZoneFilteredScanner(FinvizScanner):
    """Finviz scanner that filters results by 5-year demand zone presence.
    
    Inherits from FinvizScanner and adds zone-based filtering:
    1. Fetches tickers from Finviz using provided custom URL
    2. For each ticker, downloads 5 years of daily OHLCV data
    3. Calculates supply/demand zones using default parameters
    4. Keeps ticker only if latest close is in an active/tested demand zone
    5. Returns filtered list of qualifying tickers
    
    Args:
        http_client: AsyncClient for HTTP requests.
        url: Custom Finviz screener URL to use instead of default.
    """

    def __init__(self, http_client: httpx.AsyncClient, url: str) -> None:
        """Initialize zone-filtered scanner with custom URL.
        
        Args:
            http_client: AsyncClient for HTTP requests.
            url: Custom Finviz screener URL.
            
        Raises:
            ValueError: If http_client is None or url is empty.
        """
        super().__init__(http_client)
        if not url:
            raise ValueError("url is required")
        self.BASE_URL = url
        logger.info(f"ZoneFilteredScanner initialized with URL: {url}")

    async def scan(self, params: ScannerParams) -> list[str]:
        """Scan Finviz with custom URL and filter by 5-year demand zones.
        
        Fetches tickers from Finviz, then filters by checking if the latest
        close price falls within an active/tested demand zone on 5-year daily data.
        
        Args:
            params: Scanner parameters.
            
        Returns:
            List of ticker symbols where latest close is in a demand zone.
            
        Raises:
            RuntimeError: If all tickers in a batch fail to fetch from yfinance.
        """
        # Get base tickers from Finviz using custom URL
        logger.info("Starting Finviz scan with custom URL...")
        base_tickers = await super().scan(params)
        logger.info(f"Found {len(base_tickers)} tickers from Finviz")

        if not base_tickers:
            logger.warning("No tickers found from Finviz scan")
            return []

        # Process tickers in batches of 10 for parallel yfinance fetching
        batch_size = 10
        filtered_tickers: list[str] = []

        for i in range(0, len(base_tickers), batch_size):
            batch = base_tickers[i : i + batch_size]
            logger.info(
                f"Processing batch {i // batch_size + 1}: "
                f"{len(batch)} tickers (indices {i}-{i + len(batch) - 1})"
            )

            try:
                batch_filtered = await self._filter_batch_by_zones(batch)
                filtered_tickers.extend(batch_filtered)
                logger.info(
                    f"Batch {i // batch_size + 1}: "
                    f"{len(batch_filtered)} of {len(batch)} passed zone filter"
                )
            except RuntimeError as e:
                logger.error(f"Batch {i // batch_size + 1} failed: {e}")
                raise

        logger.info(
            f"Scan complete: {len(filtered_tickers)} of {len(base_tickers)} "
            f"tickers have latest close in demand zone"
        )
        return filtered_tickers

    async def _filter_batch_by_zones(self, tickers: list[str]) -> list[str]:
        """Filter batch of tickers by 5-year demand zone presence.
        
        Fetches 5-year daily data for all tickers in parallel, calculates zones,
        and filters to keep only tickers with latest close in demand zone.
        
        Args:
            tickers: List of ticker symbols to filter.
            
        Returns:
            Filtered list of tickers in demand zones.
            
        Raises:
            RuntimeError: If all tickers in batch fail to fetch from yfinance.
        """
        # Import here to avoid circular dependency with common.models.zone
        from common.helpers.zone_detection import (
            find_demand_zones_at_price,
            get_supply_demand_zones,
        )
        
        # Fetch 5-year data for all tickers in parallel
        end_date = datetime.now()
        start_date = end_date - timedelta(days=365 * 5)

        logger.debug(
            f"Fetching 5-year daily data ({start_date.date()} to {end_date.date()}) "
            f"for {len(tickers)} tickers..."
        )
        if "N" in tickers:
            tickers.remove("N")
        # Download data for all tickers (yfinance handles parallel internally)
        try:
            data = yf.download(
                tickers,
                start=start_date,
                end=end_date,
                interval="1d",
                progress=False,
            )
        except Exception as e:
            logger.error(f"yfinance download failed for batch: {e}")
            raise RuntimeError(f"Yahoo Finance error: {e}")

        if data is None or data.empty:
            logger.error(f"No data returned from yfinance for tickers: {tickers}")
            raise RuntimeError("Yahoo is bitch - all tickers failed to fetch")

        # Restructure single-ticker response to multi-ticker format
        if len(tickers) == 1:
            # Single ticker: yfinance returns DataFrame with Close, Open, etc.
            data = {tickers[0]: data}
        else:
            # Multiple tickers: yfinance returns MultiIndex DataFrame
            # Convert to dict-like structure: ticker -> DataFrame
            data_dict = {}
            for ticker in tickers:
                try:
                    if isinstance(data.columns, pd.MultiIndex):
                        # MultiIndex columns: select by (column, ticker)
                        ticker_data = data.xs(ticker, level=1, axis=1)
                    else:
                        # Single-level columns (single ticker edge case)
                        ticker_data = data
                    data_dict[ticker] = ticker_data
                except (KeyError, IndexError):
                    logger.warning(f"No data for ticker {ticker}")
                    continue
            data = data_dict

        # Filter tickers by demand zones
        filtered: list[str] = []
        for ticker in tickers:
            try:
                if ticker not in data:
                    logger.warning(f"Skipping {ticker}: no data available")
                    continue

                df = data[ticker]
                if df is None or df.empty or len(df) < 200:
                    logger.warning(
                        f"Skipping {ticker}: insufficient data "
                        f"(got {len(df) if df is not None else 0} rows, need 200+)"
                    )
                    continue

                # Ensure required columns exist and are numeric
                required_cols = ["Open", "High", "Low", "Close", "Volume"]
                if not all(col in df.columns for col in required_cols):
                    logger.warning(
                        f"Skipping {ticker}: missing required columns. "
                        f"Has: {list(df.columns)}"
                    )
                    continue

                # Convert to numeric and drop NaN rows
                for col in required_cols:
                    df[col] = pd.to_numeric(df[col], errors="coerce")
                df = df.dropna(subset=required_cols)

                if df.empty:
                    logger.warning(f"Skipping {ticker}: all rows are NaN")
                    continue

                latest_close = float(df["Close"].iloc[-1])

                # Calculate zones using existing function with default parameters
                zones = get_supply_demand_zones(df)

                # Check if latest close is in any demand zone
                demand_zones = find_demand_zones_at_price(zones, latest_close)

                if demand_zones:
                    filtered.append(ticker)
                    logger.debug(
                        f"{ticker}: MATCHED - close ${latest_close:.2f} in "
                        f"{len(demand_zones)} demand zone(s)"
                    )
                else:
                    logger.debug(f"{ticker}: no demand zones containing close")

            except Exception as e:
                logger.warning(f"Error processing {ticker}: {e}")
                continue

        return filtered
