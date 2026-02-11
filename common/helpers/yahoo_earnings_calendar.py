"""Yahoo Finance earnings calendar scraper.

Uses Yahoo Finance's screener API to retrieve ticker symbols that have
earnings announcements on specific dates. Handles consent cookies and
crumb authentication required by the Yahoo Finance API.
"""
import logging
import re
import time
from datetime import date, timedelta

import httpx

from common.helpers.abstracts.i_earnings_calendar import IEarningsCalendar
from common.user_agent import UserAgentManager

logger: logging.Logger = logging.getLogger(__name__)

_YAHOO_HOME_URL: str = "https://finance.yahoo.com/"
_CONSENT_URL: str = "https://consent.yahoo.com/v2/collectConsent"
_CRUMB_URL: str = "https://query2.finance.yahoo.com/v1/test/getcrumb"
_SCREENER_URL: str = "https://query2.finance.yahoo.com/v1/finance/screener"
_PAGE_SIZE: int = 100
_REQUEST_DELAY_SECONDS: float = 2.0
_MAX_RETRIES: int = 5
_RETRY_BACKOFF_SECONDS: float = 10.0


class YahooEarningsCalendarScraper(IEarningsCalendar):
    """Fetches earnings tickers by date via Yahoo Finance screener API.
    
    Authenticates with Yahoo Finance (handling EU cookie consent),
    obtains a crumb token, then queries the screener API for tickers
    with earnings on the requested date(s).
    
    The screener API supports historical dates, so this works for
    backtesting as well as real-time use.
    """
    
    def __init__(self, delay: float = _REQUEST_DELAY_SECONDS) -> None:
        """Initialise the scraper.
        
        Args:
            delay: Seconds to wait between API requests (rate limiting).
        """
        self._delay: float = delay
        self._client: httpx.Client | None = None
        self._crumb: str | None = None
    
    def get_earnings_on_date(self, target_date: date) -> list[str]:
        """Get all US ticker symbols with earnings on a specific date.
        
        Args:
            target_date: The date to query for earnings announcements.
            
        Returns:
            List of unique US ticker symbols with earnings on the date.
        """
        self._ensure_authenticated()
        
        all_tickers: list[str] = []
        offset: int = 0
        
        while True:
            tickers = self._fetch_earnings_page(target_date, offset)
            
            if not tickers:
                break
            
            all_tickers.extend(tickers)
            
            # If we got fewer than PAGE_SIZE, no more pages
            if len(tickers) < _PAGE_SIZE:
                break
            
            offset += _PAGE_SIZE
        
        # Deduplicate preserving order
        seen: set[str] = set()
        unique: list[str] = []
        for t in all_tickers:
            if t not in seen:
                seen.add(t)
                unique.append(t)
        
        logger.info(f"Found {len(unique)} US earnings tickers for {target_date}")
        return unique
    
    def get_earnings_between(
        self,
        start_date: date,
        end_date: date,
    ) -> dict[date, list[str]]:
        """Get all US ticker symbols with earnings in a date range.
        
        Iterates through each weekday in the range and queries the
        screener API for each date.
        
        Args:
            start_date: Start of the date range (inclusive).
            end_date: End of the date range (inclusive).
            
        Returns:
            Dictionary mapping each date to its list of earnings tickers.
            Only dates with earnings are included.
            
        Raises:
            ValueError: If start_date is after end_date.
        """
        if start_date > end_date:
            raise ValueError("start_date must not be after end_date")
        
        self._ensure_authenticated()
        
        earnings_map: dict[date, list[str]] = {}
        current: date = start_date
        total_days: int = (end_date - start_date).days + 1
        day_count: int = 0
        
        while current <= end_date:
            day_count += 1
            
            # Skip weekends
            if current.weekday() >= 5:
                current += timedelta(days=1)
                continue
            
            logger.info(
                f"[{day_count}/{total_days}] Fetching earnings for {current}..."
            )
            
            tickers = self.get_earnings_on_date(current)
            
            if tickers:
                earnings_map[current] = tickers
                logger.info(f"  {current}: {len(tickers)} tickers")
            
            current += timedelta(days=1)
        
        total_tickers = sum(len(t) for t in earnings_map.values())
        logger.info(
            f"Earnings calendar complete: {len(earnings_map)} dates, "
            f"{total_tickers} total ticker-date pairs"
        )
        return earnings_map
    
    def _ensure_authenticated(self) -> None:
        """Ensure we have a valid httpx client with cookies and crumb.
        
        Creates a new client session, handles Yahoo's cookie consent
        page if needed, and obtains a crumb token for API access.
        """
        if self._client is not None and self._crumb is not None:
            return
        
        logger.info("Authenticating with Yahoo Finance...")
        
        self._client = httpx.Client(timeout=30.0, follow_redirects=True)
        
        ua: str = UserAgentManager.get_random_user_agent()
        html_headers: dict[str, str] = {
            "User-Agent": ua,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
            "sec-fetch-dest": "document",
        }
        
        # Hit Yahoo Finance home to get cookies
        resp = self._client.get(_YAHOO_HOME_URL, headers=html_headers)
        
        # Handle EU cookie consent if redirected
        if "consent.yahoo.com" in str(resp.url):
            self._accept_consent(resp.text, html_headers)
        
        # Obtain crumb with retry logic
        for attempt in range(_MAX_RETRIES):
            crumb_resp = self._client.get(
                _CRUMB_URL, headers={"User-Agent": ua}
            )
            
            if crumb_resp.status_code == 200:
                self._crumb = crumb_resp.text.strip()
                logger.info("Yahoo Finance authentication successful")
                return
            
            if crumb_resp.status_code == 429:
                backoff = _RETRY_BACKOFF_SECONDS * (2 ** attempt)
                logger.debug(
                    f"Rate limited getting crumb, retrying in {backoff:.0f}s "
                    f"(attempt {attempt + 1}/{_MAX_RETRIES})"
                )
                time.sleep(backoff)
                continue
            
            raise RuntimeError(
                f"Failed to get Yahoo crumb: {crumb_resp.status_code} - "
                f"{crumb_resp.text[:200]}"
            )
        
        raise RuntimeError(
            "Exhausted retries getting Yahoo crumb (rate limited)"
        )
    
    def _accept_consent(
        self, consent_html: str, headers: dict[str, str]
    ) -> None:
        """Accept Yahoo's cookie consent form.
        
        Args:
            consent_html: HTML content of the consent page.
            headers: HTTP headers to use for the POST request.
        """
        csrf_match = re.search(
            r'name="csrfToken"\s+value="([^"]*)"', consent_html
        )
        session_match = re.search(
            r'name="sessionId"\s+value="([^"]*)"', consent_html
        )
        
        if not csrf_match or not session_match:
            logger.warning("Could not find consent form fields")
            return
        
        self._client.post(
            _CONSENT_URL,
            data={
                "csrfToken": csrf_match.group(1),
                "sessionId": session_match.group(1),
                "originalDoneUrl": _YAHOO_HOME_URL,
                "namespace": "yahoo",
                "agree": "agree",
            },
            headers={
                **headers,
                "Content-Type": "application/x-www-form-urlencoded",
            },
        )
        logger.debug("Yahoo consent accepted")
    
    def _fetch_earnings_page(
        self, target_date: date, offset: int
    ) -> list[str]:
        """Fetch one page of earnings tickers from the screener API.
        
        Includes retry logic with exponential backoff for rate-limited
        (429) responses.
        
        Args:
            target_date: Date to query.
            offset: Pagination offset (0, 100, 200, ...).
            
        Returns:
            List of ticker symbols from this page.
        """
        date_str: str = target_date.strftime("%Y-%m-%d")
        next_date_str: str = (target_date + timedelta(days=1)).strftime(
            "%Y-%m-%d"
        )
        
        body: dict = {
            "offset": offset,
            "size": _PAGE_SIZE,
            "sortField": "startdatetime",
            "sortType": "ASC",
            "entityIdType": "earnings",
            "query": {
                "operator": "AND",
                "operands": [
                    {
                        "operator": "BTWN",
                        "operands": [
                            "startdatetime",
                            f"{date_str}T00:00:00.000-05:00",
                            f"{next_date_str}T00:00:00.000-05:00",
                        ],
                    },
                    {
                        "operator": "EQ",
                        "operands": ["region", "us"],
                    },
                ],
            },
        }
        
        for attempt in range(_MAX_RETRIES):
            time.sleep(self._delay)
            ua: str = UserAgentManager.get_random_user_agent()
            
            try:
                resp = self._client.post(
                    f"{_SCREENER_URL}?crumb={self._crumb}"
                    f"&formatted=true&lang=en-US&region=US",
                    json=body,
                    headers={
                        "User-Agent": ua,
                        "Content-Type": "application/json",
                    },
                )
                
                if resp.status_code == 429:
                    backoff = _RETRY_BACKOFF_SECONDS * (2 ** attempt)
                    logger.debug(
                        f"Rate limited for {date_str}, "
                        f"retrying in {backoff:.0f}s (attempt {attempt + 1}/{_MAX_RETRIES})"
                    )
                    time.sleep(backoff)
                    continue
                
                if resp.status_code != 200:
                    logger.warning(
                        f"Screener API returned {resp.status_code} for {date_str}"
                    )
                    return []
                
                data = resp.json()
                finance = data.get("finance", {})
                
                error = finance.get("error")
                if error:
                    logger.warning(
                        f"Screener API error for {date_str}: {error}"
                    )
                    return []
                
                result = finance.get("result", [])
                if not result:
                    return []
                
                quotes = result[0].get("quotes", [])
                tickers: list[str] = []
                
                for quote in quotes:
                    symbol = quote.get("symbol")
                    if isinstance(symbol, str) and symbol:
                        tickers.append(symbol)
                
                return tickers
            
            except httpx.TimeoutException:
                logger.warning(f"Timeout fetching earnings for {date_str}")
                return []
            except Exception as e:
                logger.warning(
                    f"Error fetching earnings for {date_str}: {e}"
                )
                return []
        
        logger.warning(
            f"Exhausted retries for {date_str} (offset={offset})"
        )
        return []
    
    def close(self) -> None:
        """Close the underlying HTTP client."""
        if self._client is not None:
            self._client.close()
            self._client = None
            self._crumb = None
    
    def __del__(self) -> None:
        """Clean up resources on garbage collection."""
        self.close()
