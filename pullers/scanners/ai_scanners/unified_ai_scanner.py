"""Unified AI scanner combining Finviz URL scanning, demand zone filtering, and AI consensus."""
import logging

import httpx

from common.helpers.ai_ticker_analyzer import AITickerAnalyzer
from common.models.scanner_params import ScannerParams
from gpt.abstracts.gpt_base import GPTBase
from pullers.scanners.abstracts.scanner import ScannerBase
from pullers.scanners.finviz.zone_filtered_scanner import ZoneFilteredScanner

logger: logging.Logger = logging.getLogger(__name__)


class UnifiedAIScanner(ScannerBase):
    """Unified scanner combining Finviz URL, demand zone filtering, and AI consensus.
    
    Workflow:
    1. Scans Finviz using provided URL
    2. Filters by 5-year demand zone presence
    3. Sends filtered tickers to AI consensus (Grok + Gemini)
    4. Returns only tickers both AIs agree on
    
    Takes as input:
    - finviz_url: Finviz screener URL
    - prompt_template: Prompt template with {TICKERS} placeholder
    """

    def __init__(
        self,
        http_client: httpx.AsyncClient,
        finviz_url: str,
        prompt_template: str,
        grok_client: GPTBase,
        gemini_client: GPTBase,
    ) -> None:
        """Initialize the unified AI scanner.
        
        Args:
            http_client: httpx AsyncClient instance for HTTP requests.
            finviz_url: Finviz screener URL to use.
            prompt_template: Prompt template with {TICKERS} placeholder.
            grok_client: Grok AI client instance.
            gemini_client: Gemini AI client instance.
            
        Raises:
            ValueError: If any required parameter is None or empty.
        """
        if http_client is None:
            raise ValueError("http_client is required")
        if not finviz_url:
            raise ValueError("finviz_url is required")
        if not prompt_template:
            raise ValueError("prompt_template is required")
        if grok_client is None:
            raise ValueError("grok_client is required")
        if gemini_client is None:
            raise ValueError("gemini_client is required")
        
        self._http_client: httpx.AsyncClient = http_client
        self._finviz_url: str = finviz_url
        self._prompt_template: str = prompt_template
        self._grok_client: GPTBase = grok_client
        self._gemini_client: GPTBase = gemini_client
        self._zone_scanner: ZoneFilteredScanner = ZoneFilteredScanner(
            http_client=http_client, url=finviz_url
        )
        self._ai_analyzer: AITickerAnalyzer = AITickerAnalyzer()

    async def scan(self, params: ScannerParams) -> list[str]:
        """Scan using unified pipeline: Finviz → Demand Zone → AI Consensus.
        
        Args:
            params: ScannerParams object containing scan configuration.
            
        Returns:
            List of stock ticker symbols recommended by both Grok and Gemini
            after demand zone filtering.
            
        Raises:
            Exception: If scanning fails.
        """
        try:
            logger.info("Starting unified AI scanner...")
            logger.info(f"Finviz URL: {self._finviz_url}")
            
            # Step 1: Run zone-filtered scanner
            demand_tickers: list[str] = await self._zone_scanner.scan(params)
            logger.info(f"Found {len(demand_tickers)} tickers close to demand zones")
            
            if not demand_tickers:
                logger.warning("No demand zone tickers found")
                return []
            
            # Step 2: Run AI consensus analysis
            ai_tickers: list[str] = await self._ai_analyzer.analyze_tickers(
                demand_tickers,
                self._prompt_template,
                self._grok_client,
                self._gemini_client,
            )
            
            logger.info(
                f"✅ Unified scan complete: {len(demand_tickers)} demand zone tickers → "
                f"{len(ai_tickers)} AI consensus tickers"
            )
            
            return ai_tickers
            
        except Exception as e:
            logger.error(f"Error during unified AI scan: {str(e)}", exc_info=True)
            raise
