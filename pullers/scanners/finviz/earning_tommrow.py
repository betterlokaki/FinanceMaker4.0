"""Finviz earnings tomorrow stock scanner implementation."""
import logging

import httpx

from pullers.scanners.finviz.finviz_base import FinvizScanner

logger: logging.Logger = logging.getLogger(__name__)


class EarningTommrow(FinvizScanner):
    """Scanner for stocks with earnings announcements today.
    
    Extends FinvizScanner with a pre-configured URL filter for stocks
    that have earnings announcements scheduled for today and meet
    minimum average volume requirements.
    
    Filter criteria:
    - Earnings date: Today
    - Average volume: Over 1 million shares
    """

    def __init__(self, http_client: httpx.AsyncClient):
        """Initialize the EarningTommrow scanner.
        
        Args:
            http_client: httpx AsyncClient instance for HTTP requests.
        """
        super().__init__(http_client)
        self.BASE_URL: str = (
            "https://finviz.com/screener.ashx?v=111"
            "&f=earningsdate_today%2Csh_avgvol_o1000&ft=4"
        )
