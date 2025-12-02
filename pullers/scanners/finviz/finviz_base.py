"""Finviz stock scanner implementation."""
import logging
from typing import List, Optional

import httpx
from lxml import html

from common.models.scanner_params import ScannerParams
from common.user_agent import UserAgentManager
from common.settings import settings
from pullers.scanners.abstracts.scanner import Scanner

logger = logging.getLogger(__name__)


class FinvizScanner(Scanner):
    """Finviz-based stock scanner.
    
    Scrapes the Finviz screener to find stocks matching specified criteria.
    Uses XPath expressions for efficient HTML parsing with lxml.
    
    Configuration is loaded from config.yaml and can be overridden via environment variables.
    """

    def __init__(self, http_client: Optional[httpx.AsyncClient] = None):
        """Initialize the FinvizScanner.
        
        Args:
            http_client: Optional httpx AsyncClient instance. If not provided,
                        a new client will be created for each request.
        """
        self._http_client = http_client
        self._managed_client = http_client is None
        # Load configuration from settings
        self._config = settings.finviz
        self.BASE_URL = self._config.base_url

    async def scan(self, params: ScannerParams) -> List[str]:
        """Scan Finviz for stocks matching the given parameters."""
        client = self._http_client or httpx.AsyncClient(timeout=30.0, follow_redirects=True)
        tickers = await self._get_tickers(client, params)
        if self._managed_client:
            await client.aclose()
        return tickers

    async def _get_tickers(self, client: httpx.AsyncClient, params: ScannerParams) -> List[str]:
        """Fetch tickers from Finviz screener with pagination."""
        tickers: List[str] = []
        
        for page in range(self._config.max_pages):
            page_tickers = await self._fetch_page(client, params, page)
            if not page_tickers:
                break
            tickers.extend(page_tickers)
        
        return tickers

    async def _fetch_page(self, client: httpx.AsyncClient, params: ScannerParams, page: int) -> List[str]:
        """Fetch and parse a single page of results."""
        url = self._build_url(params, page)
        
        headers = {
            "User-Agent": UserAgentManager.get_random_user_agent(),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
            "Referer": "https://finviz.com/",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
        }
        
        response = await client.get(url, headers=headers)
        if response.status_code != 200:
            logger.error(f"Failed to fetch Finviz page {page + 1}: {response.status_code}")
            return []
        return self._parse_tickers(response.text)

    def _build_url(self, params: ScannerParams, page: int) -> str:
        """Build Finviz screener URL with filters and pagination.
        
        Args:
            params: ScannerParams containing filter criteria.
            page: Page number (0-indexed).
            
        Returns:
            Complete URL with filters and pagination parameter.
        """
        url = self.BASE_URL
        
        # Add pagination (Finviz uses 1-indexed, results_per_page results per page)
        pagination_param = f"&r={page * self._config.results_per_page + 1}"
        
        return url + pagination_param

    def _parse_tickers(self, html_content: str) -> List[str]:
        """Parse tickers from Finviz HTML response using XPath."""
        tree = html.fromstring(html_content)
        comments = tree.xpath('//comment()')
        
        ticker_data = None
        for comment in comments:
            comment_text = comment.text or ""
            if "TS" in comment_text:
                ticker_data = comment_text
                break
        
        if not ticker_data:
            return []
        
        start_idx = ticker_data.find("TS")
        end_idx = ticker_data.find("TE")
        
        if start_idx == -1 or end_idx == -1:
            return []
        
        data_section = ticker_data[start_idx + 2:end_idx].strip()
        
        tickers: List[str] = []
        for line in data_section.split("\n"):
            if not line.strip():
                continue
            parts = line.split("|")
            if parts and parts[0].strip():
                tickers.append(parts[0].strip())
        
        return tickers
