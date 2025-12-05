"""Yahoo Finance market data provider."""
import logging
from datetime import datetime, timedelta, timezone

import httpx
import pandas as pd

from common.models.candlestick import CandleStick
from common.models.period import Period
from common.settings import settings
from common.user_agent import UserAgentManager
from pullers.market.abstracts.market_provider_base import MarketProviderBase

logger: logging.Logger = logging.getLogger(__name__)


class YahooMarketProvider(MarketProviderBase):
    """Yahoo Finance-based market data provider.
    
    Fetches historical price data from Yahoo Finance API.
    Supports multiple time granularities from seconds to daily.
    """

    # Yahoo Finance period mappings
    _PERIOD_MAPPING: dict[Period, str] = {
        Period.SECOND: "1s",
        Period.MINUTE: "1m",
        Period.HOUR: "1h",
        Period.DAILY: "1d",
    }
    
    # Intraday periods require chunked requests (7-day limit)
    _INTRADAY_PERIODS: set[Period] = {Period.SECOND, Period.MINUTE, Period.HOUR}
    
    # Chunk size for intraday data requests
    _INTRADAY_CHUNK_DAYS: int = 7

    def __init__(self, http_client: httpx.AsyncClient):
        """Initialize the Yahoo market provider.
        
        Args:
            http_client: httpx AsyncClient instance for HTTP requests.
        """
        super().__init__(http_client)
        self._config = settings.yahoo
        self._base_url: str = self._config.base_url

    async def get_prices(
        self,
        ticker: str,
        start_time: datetime,
        end_time: datetime,
        period: Period
    ) -> pd.DataFrame:
        """Fetch historical price data from Yahoo Finance.
        
        Args:
            ticker: Stock ticker symbol.
            start_time: Start of the time range (UTC).
            end_time: End of the time range (UTC).
            period: Time granularity for the data.
            
        Returns:
            DataFrame with columns: open, high, low, close, volume, period.
            Index is the timestamp.
            
        Raises:
            NotImplementedError: If the period is not supported.
        """
        if period not in self._PERIOD_MAPPING:
            raise NotImplementedError(f"Period {period} not supported")

        yahoo_period = self._PERIOD_MAPPING[period]

        if period in self._INTRADAY_PERIODS:
            candles = await self._fetch_intraday_chunked(
                ticker, start_time, end_time, yahoo_period, period
            )
        else:
            data = await self._pull_data_from_yahoo(
                start_time, end_time, ticker, yahoo_period
            )
            candles = self._create_candles_from_response(data, period)

        return self._candles_to_dataframe(candles)

    async def _fetch_intraday_chunked(
        self,
        ticker: str,
        start_time: datetime,
        end_time: datetime,
        yahoo_period: str,
        period: Period
    ) -> list[CandleStick]:
        """Fetch intraday data in chunks (Yahoo limits intraday to 7 days).
        
        Args:
            ticker: Stock ticker symbol.
            start_time: Start of the time range.
            end_time: End of the time range.
            yahoo_period: Yahoo Finance interval string.
            period: Period enum value.
            
        Returns:
            List of CandleStick objects.
        """
        candles: list[CandleStick] = []
        chunk_start = start_time
        
        while chunk_start < end_time:
            chunk_end = min(
                chunk_start + timedelta(days=self._INTRADAY_CHUNK_DAYS),
                end_time
            )
            data = await self._pull_data_from_yahoo(
                chunk_start, chunk_end, ticker, yahoo_period
            )
            candles.extend(self._create_candles_from_response(data, period))
            chunk_start = chunk_end
            
        return candles

    async def _pull_data_from_yahoo(
        self,
        start: datetime,
        end: datetime,
        ticker: str,
        yahoo_period: str
    ) -> dict:
        """Fetch raw data from Yahoo Finance API.
        
        Args:
            start: Start timestamp.
            end: End timestamp.
            ticker: Stock ticker symbol.
            yahoo_period: Yahoo Finance interval string.
            
        Returns:
            JSON response as dictionary, or empty dict on failure.
        """
        url = self._build_url(ticker, start, end, yahoo_period)
        headers = {
            "User-Agent": UserAgentManager.get_random_user_agent(),
            "Accept": "application/json",
            "Accept-Language": "en-US,en;q=0.9",
        }
        
        max_attempts = 3
        for attempt in range(max_attempts):
            try:
                response = await self._http_client.get(url, headers=headers)
                if response.status_code == 200:
                    return response.json()
                logger.warning(
                    "Yahoo API returned %d for %s (attempt %d/%d)",
                    response.status_code, ticker, attempt + 1, max_attempts
                )
                # Add delay on rate limit before retry
                if response.status_code == 429 and attempt < max_attempts - 1:
                    import asyncio
                    await asyncio.sleep(1.0 * (attempt + 1))
            except httpx.HTTPError as e:
                logger.warning(
                    "HTTP error fetching %s: %s (attempt %d/%d)",
                    ticker, str(e), attempt + 1, max_attempts
                )
                
        return {}

    def _build_url(
        self,
        ticker: str,
        start: datetime,
        end: datetime,
        interval: str
    ) -> str:
        """Build Yahoo Finance API URL.
        
        Args:
            ticker: Stock ticker symbol.
            start: Start timestamp.
            end: End timestamp.
            interval: Yahoo Finance interval string.
            
        Returns:
            Formatted URL string.
        """
        return (
            f"{self._base_url}/v8/finance/chart/{ticker}"
            f"?period1={self._to_unix(start)}"
            f"&period2={self._to_unix(end)}"
            f"&interval={interval}"
            f"&includePrePost=true"
            f"&events=div%2Csplit"
            f"&corsDomain=finance.yahoo.com"
        )

    @staticmethod
    def _to_unix(dt: datetime) -> int:
        """Convert datetime to Unix timestamp."""
        return int(dt.timestamp())

    @staticmethod
    def _create_candles_from_response(
        yahoo_response: dict,
        period: Period
    ) -> list[CandleStick]:
        """Parse Yahoo Finance response into CandleStick objects.
        
        Args:
            yahoo_response: Raw JSON response from Yahoo Finance.
            period: Period enum value.
            
        Returns:
            List of CandleStick objects.
        """
        chart = yahoo_response.get("chart", {})
        result = chart.get("result")
        if not result:
            return []

        result = result[0]
        timestamps = result.get("timestamp")
        indicators = result.get("indicators", {})
        quote = indicators.get("quote", [{}])[0]

        if not timestamps or not quote:
            return []

        candles: list[CandleStick] = []
        num_points = len(timestamps)
        
        opens = quote.get("open", [None] * num_points)
        highs = quote.get("high", [None] * num_points)
        lows = quote.get("low", [None] * num_points)
        closes = quote.get("close", [None] * num_points)
        volumes = quote.get("volume", [None] * num_points)

        for i in range(num_points):
            open_ = opens[i]
            high = highs[i]
            low = lows[i]
            close = closes[i]
            volume = volumes[i]

            # Skip incomplete data points
            if None in (open_, high, low, close, volume):
                continue

            dt = datetime.fromtimestamp(timestamps[i], tz=timezone.utc)
            candle = CandleStick(
                open=open_,
                high=high,
                low=low,
                close=close,
                volume=volume,
                time=dt,
                period=period
            )
            candles.append(candle)

        return candles

    @staticmethod
    def _candles_to_dataframe(candles: list[CandleStick]) -> pd.DataFrame:
        """Convert CandleStick list to pandas DataFrame.
        
        Args:
            candles: List of CandleStick objects.
            
        Returns:
            DataFrame with time as index and OHLCV columns.
        """
        if not candles:
            return pd.DataFrame(
                columns=["open", "high", "low", "close", "volume", "period"]
            ).set_index(pd.Index([], name="time"))
            
        data = {
            "open": [c.open for c in candles],
            "high": [c.high for c in candles],
            "low": [c.low for c in candles],
            "close": [c.close for c in candles],
            "volume": [c.volume for c in candles],
            "period": [c.period for c in candles],
        }
        df = pd.DataFrame(data, index=[c.time for c in candles])
        df.index.name = "time"
        return df
