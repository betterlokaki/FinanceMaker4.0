"""AI-powered stock scanner using consensus from multiple AI providers.

This scanner combines the power of multiple AI providers to find stocks
that have consensus recommendations for trading.

Workflow:
1. Get earnings tickers from EarningTomorrow scanner
2. Create prompt with those tickers
3. Send prompt to Grok AI
4. Send prompt to Gemini AI
5. Extract suggested tickers from both AI responses
6. Find intersection - only return tickers suggested by BOTH AIs
"""
import logging

import httpx

from common.helpers.ai_consensus_helpers import find_consensus, get_ai_suggestions
from common.helpers.prompt_helpers import build_ticker_analysis_prompt
from common.models.scanner_params import ScannerParams
from common.settings import settings
from gpt.abstracts.gpt_base import GPTBase
from pullers.scanners.abstracts.scanner import ScannerBase
from pullers.scanners.finviz.earning_tommrow import EarningTommrow

logger: logging.Logger = logging.getLogger(__name__)


class EarningTomorrowAI(ScannerBase):
    """AI-powered scanner using consensus from Grok and Gemini.
    
    This scanner combines earnings date filtering with AI analysis:
    1. Gets stocks earning tomorrow using EarningTomorrow scanner
    2. Sends them to both Grok and Gemini AI providers
    3. Extracts ticker suggestions from both AI responses
    4. Returns only tickers suggested by BOTH AIs (consensus)
    
    This consensus approach reduces false positives and increases
    confidence in the trading recommendations.
    
    Configuration is loaded from config.yaml.
    """

    def __init__(
        self,
        http_client: httpx.AsyncClient,
        earnings_scanner: EarningTommrow,
        grok_client: GPTBase,
        gemini_client: GPTBase,
    ):
        """Initialize the AI consensus scanner.
        
        Args:
            http_client: httpx AsyncClient instance for HTTP requests.
            earnings_scanner: EarningTomorrow scanner instance.
            grok_client: Grok client instance.
            gemini_client: Gemini client instance.
            
        Raises:
            ValueError: If any required dependency is None.
        """
        if http_client is None:
            raise ValueError("http_client is required")
        if earnings_scanner is None:
            raise ValueError("earnings_scanner is required")
        if grok_client is None:
            raise ValueError("grok_client is required")
        if gemini_client is None:
            raise ValueError("gemini_client is required")
            
        self._http_client: httpx.AsyncClient = http_client
        self._earnings_scanner: EarningTommrow = earnings_scanner
        self._grok_client: GPTBase = grok_client
        self._gemini_client: GPTBase = gemini_client
        self._config = settings.ai_scanner

    async def scan(self, params: ScannerParams) -> list[str]:
        """Scan for earnings stocks with AI consensus recommendation.
        
        Args:
            params: ScannerParams object containing scan configuration.
            
        Returns:
            List of stock ticker symbols recommended by both Grok and Gemini.
            
        Raises:
            Exception: If scanning fails.
        """
        try:
            logger.info("Starting AI consensus scan for earnings stocks...")
            
            earnings_tickers: list[str] = await self._earnings_scanner.scan(params)
            logger.info(f"Found {len(earnings_tickers)} stocks earning tomorrow")
            
            if not earnings_tickers:
                logger.warning("No earnings stocks found")
                return []
            
            prompt: str = build_ticker_analysis_prompt(
                earnings_tickers, self._config.prompt_template
            )
            
            grok_suggestions: set[str] = await get_ai_suggestions(
                self._grok_client, prompt, earnings_tickers, "Grok"
            )
            gemini_suggestions: set[str] = await get_ai_suggestions(
                self._gemini_client, prompt, earnings_tickers, "Gemini"
            )
            
            logger.info(f"Grok suggested: {len(grok_suggestions)} tickers")
            logger.info(f"Gemini suggested: {len(gemini_suggestions)} tickers")
            
            consensus_tickers: set[str] = find_consensus(
                grok_suggestions, gemini_suggestions, "Grok", "Gemini"
            )
            
            logger.info(
                f"✅ Consensus achieved! {len(consensus_tickers)} tickers "
                f"recommended by both AIs: {consensus_tickers}"
            )
            
            return list(consensus_tickers)
            
        except Exception as e:
            logger.error(f"Error during AI consensus scan: {str(e)}", exc_info=True)
            raise
