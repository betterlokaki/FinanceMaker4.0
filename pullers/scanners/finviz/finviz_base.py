"""Finviz stock scanner implementation."""
import logging

import httpx

from common.helpers.html_helpers import parse_finviz_tickers
from common.models.scanner_params import ScannerParams
from common.user_agent import UserAgentManager
from common.settings import settings
from pullers.scanners.abstracts.scanner import ScannerBase

logger: logging.Logger = logging.getLogger(__name__)


class FinvizScanner(ScannerBase):
    """Finviz-based stock scanner.
    
    Scrapes the Finviz screener to find stocks matching specified criteria.
    Uses XPath expressions for efficient HTML parsing with lxml.
    
    Configuration is loaded from config.yaml and can be overridden via environment variables.
    """

    def __init__(self, http_client: httpx.AsyncClient):
        """Initialize the FinvizScanner.
        
        Args:
            http_client: httpx AsyncClient instance for HTTP requests.
            
        Raises:
            ValueError: If http_client is None.
        """
        if http_client is None:
            raise ValueError("http_client is required")
            
        self._http_client: httpx.AsyncClient = http_client
        self._config = settings.finviz
        self.BASE_URL: str = self._config.base_url

    async def scan(self, params: ScannerParams) -> list[str]:
        """Scan Finviz for stocks matching the given parameters.
        
        Args:
            params: ScannerParams object containing scan configuration.
            
        Returns:
            List of stock ticker symbols matching the scan criteria.
        """
        tickers: list[str] = await self._get_tickers(params)
        return tickers

    async def _get_tickers(self, params: ScannerParams) -> list[str]:
        """Fetch tickers from Finviz screener with pagination.
        
        Args:
            params: Scan parameters.
            
        Returns:
            List of all tickers found across all pages.
        """
        tickers: list[str] = []
        
        for page in range(self._config.max_pages):
            page_tickers: list[str] = await self._fetch_page(params, page)
            if not page_tickers:
                break
            tickers.extend(page_tickers)
        
        return tickers

    async def _fetch_page(self, params: ScannerParams, page: int) -> list[str]:
        """Fetch and parse a single page of results.
        
        Args:
            params: Scan parameters.
            page: Page number (0-indexed).
            
        Returns:
            List of tickers from this page.
        """
        url: str = self._build_url(params, page)
        
        headers: dict[str, str] = {
            "User-Agent": UserAgentManager.get_random_user_agent(),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
            "Referer": "https://finviz.com/",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
        }
        
        response: httpx.Response = await self._http_client.get(url, headers=headers)
        if response.status_code != 200:
            logger.error(f"Failed to fetch Finviz page {page + 1}: {response.status_code}")
            return []
        
        return parse_finviz_tickers(response.text)

    def _build_url(self, params: ScannerParams, page: int) -> str:
        """Build Finviz screener URL with filters and pagination.
        
        Args:
            params: ScannerParams containing filter criteria.
            page: Page number (0-indexed).
            
        Returns:
            Complete URL with filters and pagination parameter.
        """
        url: str = self.BASE_URL
        pagination_param: str = f"&r={page * self._config.results_per_page + 1}"
        return url + pagination_param