"""Finviz earnings tomorrow stock scanner implementation."""
import logging

import httpx

from pullers.scanners.finviz.finviz_base import FinvizScanner

logger: logging.Logger = logging.getLogger(__name__)


class CustomFinviz(FinvizScanner):
    """Scanner for stocks with earnings announcements today.
    
    Extends FinvizScanner with a pre-configured URL filter for stocks
    that have earnings announcements scheduled for today and meet
    minimum average volume requirements.
    
    Filter criteria:
    - Earnings date: Today
    - Average volume: Over 1 million shares
    """

    def __init__(self, http_client: httpx.AsyncClient, url: str):
        """Initialize the EarningTommrow scanner.
        
        Args:
            http_client: httpx AsyncClient instance for HTTP requests.
        """
        super().__init__(http_client)
        self.BASE_URL: str = url