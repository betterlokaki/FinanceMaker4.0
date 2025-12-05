"""Abstract base class for market data providers."""
from abc import ABC, abstractmethod
from datetime import datetime

import httpx
import pandas as pd

from common.models.period import Period


class MarketProviderBase(ABC):
    """Abstract base class for market data providers.
    
    Implements the IMarketProvider protocol. Concrete implementations
    should inherit from this class and implement the abstract methods.
    """

    def __init__(self, http_client: httpx.AsyncClient):
        """Initialize the market provider.
        
        Args:
            http_client: httpx AsyncClient instance for HTTP requests.
            
        Raises:
            ValueError: If http_client is None.
        """
        if http_client is None:
            raise ValueError("http_client is required")
        
        self._http_client: httpx.AsyncClient = http_client

    @abstractmethod
    async def get_prices(
        self,
        ticker: str,
        start_time: datetime,
        end_time: datetime,
        period: Period
    ) -> pd.DataFrame:
        """Fetch historical price data for a ticker.
        
        Args:
            ticker: Stock ticker symbol.
            start_time: Start of the time range (UTC).
            end_time: End of the time range (UTC).
            period: Time granularity for the data.
            
        Returns:
            DataFrame with columns: open, high, low, close, volume, period.
            Index is the timestamp.
        """
        pass
