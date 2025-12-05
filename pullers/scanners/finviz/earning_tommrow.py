"""Finviz stock scanner implementation."""
import logging
from typing import Optional

import httpx
from lxml import html

from pullers.scanners.finviz.finviz_base import FinvizScanner


logger = logging.getLogger(__name__)

class EarningTommrow(FinvizScanner):
    
    def __init__(self, http_client: Optional[httpx.AsyncClient] = None):
        super().__init__(http_client)
        self.BASE_URL = "https://finviz.com/screener.ashx?v=111&f=earningsdate_today%2Csh_avgvol_o1000&ft=4"