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
import asyncio
import logging
import re
from typing import List, Optional, Set

import httpx

from common.models.scanner_params import ScannerParams
from common.settings import settings
from gpt.abstracts.gpt_base import GPTBase
from pullers.scanners.abstracts.scanner import Scanner
from pullers.scanners.finviz.earning_tommrow import EarningTommrow

logger = logging.getLogger(__name__)


class EarningTomorrowAI(Scanner):
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
        http_client: Optional[httpx.AsyncClient] = None,
        earnings_scanner: Optional[EarningTommrow] = None,
        grok_client: Optional[GPTBase] = None,
        gemini_client: Optional[GPTBase] = None,
    ):
        """Initialize the AI consensus scanner.
        
        Args:
            http_client: Optional httpx AsyncClient instance.
            earnings_scanner: Optional EarningTomorrow scanner instance.
            grok_client: Optional Grok client instance.
            gemini_client: Optional Gemini client instance.
                          If not provided, will be obtained from DI container.
        """
        self._http_client = http_client
        self._managed_client = http_client is None
        self._earnings_scanner = earnings_scanner
        self._grok_client = grok_client
        self._gemini_client = gemini_client
        self._config = settings.ai_scanner

    async def scan(self, params: ScannerParams) -> List[str]:
        """Scan for earnings stocks with AI consensus recommendation.
        
        Args:
            params: ScannerParams object containing scan configuration.
            
        Returns:
            List of stock ticker symbols recommended by both Grok and Gemini.
            
        Raises:
            ValueError: If Grok or Gemini clients are not available.
            Exception: If scanning fails.
        """
        try:
            logger.info("Starting AI consensus scan for earnings stocks...")
            
            # Get Grok and Gemini clients if not provided
            if self._grok_client is None or self._gemini_client is None:
                from common.di_container import container
                self._grok_client = container.grok_client()
                self._gemini_client = container.gemini_client()
            
            # Step 1: Get earnings tickers
            logger.info("Step 1: Fetching earnings tickers from EarningTomorrow...")
            earnings_tickers = await self._get_earnings_tickers(params)
            logger.info(f"Found {len(earnings_tickers)} stocks earning tomorrow")
            
            if not earnings_tickers:
                logger.warning("No earnings stocks found")
                return []
            
            # Step 2: Send to both AIs
            logger.info("Step 2: Sending tickers to AI providers...")
            grok_suggestions = await self._get_ai_suggestions(
                self._grok_client, earnings_tickers, "Grok"
            )
            # grok_suggestions = set()  # Temporarily disable Grok suggestions    
            gemini_suggestions = await self._get_ai_suggestions(
                self._gemini_client, earnings_tickers, "Gemini"
            )
            
            logger.info(f"Grok suggested: {len(grok_suggestions)} tickers")
            logger.info(f"Gemini suggested: {len(gemini_suggestions)} tickers")
            
            # Step 3: Find consensus (intersection)
            logger.info("Step 3: Finding consensus between AI providers...")
            consensus_tickers = await self._find_consensus(
                grok_suggestions, gemini_suggestions
            )
            
            logger.info(
                f"✅ Consensus achieved! {len(consensus_tickers)} tickers "
                f"recommended by both AIs: {consensus_tickers}"
            )
            
            return list(consensus_tickers)
            
        except ValueError as e:
            logger.error(f"Configuration error: {str(e)}")
            raise
        except Exception as e:
            logger.error(f"Error during AI consensus scan: {str(e)}", exc_info=True)
            raise

    async def _get_earnings_tickers(self, params: ScannerParams) -> List[str]:
        """Get stocks with earnings tomorrow.
        
        Args:
            params: Scan parameters.
            
        Returns:
            List of tickers earning tomorrow.
        """
        try:
            # Create earnings scanner if not provided
            if self._earnings_scanner is None:
                self._earnings_scanner = EarningTommrow(self._http_client)
            
            # Scan for earnings tickers
            tickers = await self._earnings_scanner.scan(params)
            return tickers
            
        except Exception as e:
            logger.error(f"Error fetching earnings tickers: {str(e)}", exc_info=True)
            raise

    async def _get_ai_suggestions(
        self, ai_client: GPTBase, tickers: List[str], ai_name: str
    ) -> Set[str]:
        """Get ticker suggestions from an AI provider.
        
        Args:
            ai_client: The AI client (Grok or Gemini).
            tickers: List of tickers to analyze.
            ai_name: Name of the AI provider (for logging).
            
        Returns:
            Set of suggested tickers extracted from AI response.
        """
        try:
            # Create prompt with tickers
            tickers_str = ", ".join(tickers)
            prompt = self._config.prompt_template.format(TICKERS=tickers_str)
            
            logger.debug(f"Sending prompt to {ai_name}:\n{prompt[:100]}...")
            
            # Get AI response
            response = await ai_client.generate_text(prompt)
            logger.debug(f"{ai_name} response length: {len(response)} chars")
            
            # Extract tickers from response
            suggested_tickers = self._extract_tickers_from_response(response, tickers)
            logger.info(f"{ai_name} extracted {len(suggested_tickers)} tickers")
            
            return suggested_tickers
            
        except Exception as e:
            logger.error(
                f"Error getting suggestions from {ai_name}: {str(e)}", exc_info=True
            )
            raise

    def _extract_tickers_from_response(
        self, response: str, valid_tickers: List[str]
    ) -> Set[str]:
        """Extract ticker symbols from AI response.
        
        Strategy:
        1. Look for common ticker patterns (uppercase, 1-5 chars, alphanumeric)
        2. Extract symbols that appear in the response
        3. Filter to only include valid tickers from the original list
        
        Args:
            response: AI provider's response text.
            valid_tickers: List of original tickers to filter against.
            
        Returns:
            Set of extracted tickers that are in the valid list.
        """
        try:
            # Create a set of valid tickers for quick lookup
            valid_set = {ticker.upper() for ticker in valid_tickers}
            suggested = set()
            
            # Pattern to match potential ticker symbols
            # Matches: 1-5 uppercase letters, optionally followed by numbers or dots
            ticker_pattern = r'\b([A-Z]{1,5}(?:\.[A-Z]{1,2})?)(?:\s|,|\.|\n|$)'
            
            # Find all potential tickers in response
            matches = re.finditer(ticker_pattern, response)
            
            for match in matches:
                ticker = match.group(1).upper()
                
                # Only include if it's in our valid list
                if ticker in valid_set:
                    suggested.add(ticker)
                    logger.debug(f"Extracted ticker: {ticker}")
            
            return suggested
            
        except Exception as e:
            logger.error(f"Error extracting tickers: {str(e)}", exc_info=True)
            # Return empty set if extraction fails
            return set()

    async def _find_consensus(
        self, grok_suggestions: Set[str], gemini_suggestions: Set[str]
    ) -> Set[str]:
        """Find tickers suggested by both AI providers.
        
        Args:
            grok_suggestions: Set of tickers suggested by Grok.
            gemini_suggestions: Set of tickers suggested by Gemini.
            
        Returns:
            Set of tickers in both suggestions (intersection).
        """
        # Find intersection - only tickers both AIs suggested
        consensus = grok_suggestions.intersection(gemini_suggestions)
        
        logger.info(
            f"Consensus analysis:\n"
            f"  - Grok only: {grok_suggestions - gemini_suggestions}\n"
            f"  - Gemini only: {gemini_suggestions - grok_suggestions}\n"
            f"  - Both (consensus): {consensus}"
        )
        
        return consensus
