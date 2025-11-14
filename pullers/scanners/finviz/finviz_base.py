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
        """Scan Finviz for stocks matching the given parameters.
        
        Iterates through Finviz screener pages (up to 30 pages of 20 results each)
        and extracts ticker symbols until no results are found.
        
        Args:
            params: ScannerParams object containing scan configuration.
            
        Returns:
            List of stock ticker symbols (strings) found by the scanner.
            
        Raises:
            httpx.RequestError: If HTTP request fails.
            Exception: If HTML parsing fails.
        """
        tickers: List[str] = []
        
        try:
            # Use provided client or create temporary client
            client = self._http_client or httpx.AsyncClient(
                timeout=30.0,
                follow_redirects=True
            )
            
            try:
                tickers = await self._get_tickers(client, params)
            finally:
                # Close client if we created it
                if self._managed_client:
                    await client.aclose()
                    
        except Exception as e:
            logger.error(f"Error scanning Finviz: {str(e)}", exc_info=True)
            raise

        return tickers

    async def _get_tickers(
        self,
        client: httpx.AsyncClient,
        params: ScannerParams
    ) -> List[str]:
        """Fetch tickers from Finviz screener with pagination.
        
        Args:
            client: httpx AsyncClient for making requests.
            params: ScannerParams containing scan filters.
            
        Returns:
            List of ticker symbols extracted from all pages.
        """
        tickers: List[str] = []
        max_pages = self._config.max_pages

        for page in range(max_pages):
            try:
                page_tickers = await self._fetch_page(client, params, page)
                
                if not page_tickers:
                    logger.debug(f"No tickers found on page {page}, stopping scan")
                    break
                    
                tickers.extend(page_tickers)
                logger.debug(f"Page {page}: Found {len(page_tickers)} tickers")
                
            except Exception as e:
                logger.error(f"Error fetching page {page}: {str(e)}")
                break

        return tickers

    async def _fetch_page(
        self,
        client: httpx.AsyncClient,
        params: ScannerParams,
        page: int
    ) -> List[str]:
        """Fetch and parse a single page of results.
        
        Args:
            client: httpx AsyncClient for making requests.
            params: ScannerParams containing scan filters.
            page: Page number (0-indexed).
            
        Returns:
            List of ticker symbols from this page.
            
        Raises:
            httpx.HTTPStatusError: If response status is not 2xx.
        """
        # Construct URL with pagination
        url = self._build_url(params, page)
        
        # Make request with random user-agent
        headers = {
            "User-Agent": UserAgentManager.get_random_user_agent(),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
            "Referer": "https://finviz.com/",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
        }
        
        response = await client.get(url, headers=headers)
        response.raise_for_status()
        
        # Parse HTML and extract tickers
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
        """Parse tickers from Finviz HTML response using XPath.
        
        XPath Strategy:
        ---------------
        The HTML structure uses HTML comments as delimiters:
            <!-- TS (START of ticker data)
            ticker1|data1|data2...
            ticker2|data2|data2...
            TE --> (END of ticker data)
        
        Approach:
        1. Split by HTML comment markers containing 'TS' and 'TE'
        2. Extract middle section containing ticker data
        3. Split by newlines to get individual ticker rows
        4. Split each row by '|' and take first element (ticker symbol)
        5. Filter out empty entries
        
        Args:
            html_content: Raw HTML from Finviz response.
            
        Returns:
            List of ticker symbols extracted from the HTML.
        """
        try:
            # Parse HTML
            tree = html.fromstring(html_content)
            
            # Get all comments in the document
            comments = tree.xpath('//comment()')
            
            # Find the comment containing ticker data markers
            ticker_data = None
            for comment in comments:
                comment_text = comment.text or ""
                if "TS" in comment_text:
                    ticker_data = comment_text
                    break
            
            if not ticker_data:
                logger.debug("No ticker data found in HTML comments")
                return []
            
            # Extract content between TS and TE markers
            # Format: <!-- TS\n<ticker_data>\nTE -->
            start_marker = "TS"
            end_marker = "TE"
            
            start_idx = ticker_data.find(start_marker)
            end_idx = ticker_data.find(end_marker)
            
            if start_idx == -1 or end_idx == -1:
                logger.debug("Could not find TS/TE markers in comment")
                return []
            
            # Extract data between markers
            data_section = ticker_data[start_idx + len(start_marker):end_idx].strip()
            
            # Parse individual ticker rows
            tickers: List[str] = []
            for line in data_section.split("\n"):
                if not line.strip():
                    continue
                    
                # Each row format: ticker|data1|data2|...
                parts = line.split("|")
                if parts and parts[0].strip():
                    tickers.append(parts[0].strip())
            
            return tickers
            
        except Exception as e:
            logger.error(f"Error parsing ticker HTML: {str(e)}", exc_info=True)
            raise
