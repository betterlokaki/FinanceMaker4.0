"""Market provider interface."""
from datetime import datetime
from typing import Protocol

import pandas as pd

from common.models.period import Period


class IMarketProvider(Protocol):
    """Protocol for market data providers.
    
    Defines the contract for fetching historical price data.
    """

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
        ...
