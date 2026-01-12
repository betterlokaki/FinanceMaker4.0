"""Generic AI ticker analyzer for consensus-based ticker filtering."""
import logging

from common.helpers.ai_consensus_helpers import find_consensus, get_ai_suggestions
from common.helpers.prompt_helpers import build_ticker_analysis_prompt
from gpt.abstracts.gpt_base import GPTBase

logger: logging.Logger = logging.getLogger(__name__)

HARDCODED_TICKER_ONLY_SUFFIX: str = (
    "\n\nIMPORTANT: Your response must contain ONLY the ticker symbols "
    "from the list above. Do not include any explanations, analysis, or "
    "additional text. Return only the ticker symbols, one per line or in "
    "a JSON array format."
)


class AITickerAnalyzer:
    """Generic helper for AI consensus-based ticker analysis.
    
    Sends prompts to multiple AI providers (Grok, Gemini) and returns
    only tickers that both AIs agree on (consensus/intersection).
    """

    @staticmethod
    async def analyze_tickers(
        tickers: list[str],
        prompt_template: str,
        grok_client: GPTBase,
        gemini_client: GPTBase,
    ) -> list[str]:
        """Analyze tickers using AI consensus.
        
        Args:
            tickers: List of ticker symbols to analyze.
            prompt_template: Prompt template with {TICKERS} placeholder.
            grok_client: Grok AI client instance.
            gemini_client: Gemini AI client instance.
            
        Returns:
            List of tickers that both AIs agree on (consensus).
        """
        if not tickers:
            logger.warning("No tickers provided for AI analysis")
            return []
        
        base_prompt: str = build_ticker_analysis_prompt(tickers, prompt_template)
        full_prompt: str = base_prompt + HARDCODED_TICKER_ONLY_SUFFIX
        
        grok_suggestions: set[str] = await get_ai_suggestions(
            grok_client, full_prompt, tickers, "Grok"
        )
        gemini_suggestions: set[str] = await get_ai_suggestions(
            gemini_client, full_prompt, tickers, "Gemini"
        )
        
        consensus: set[str] = find_consensus(
            grok_suggestions, gemini_suggestions, "Grok", "Gemini"
        )
        
        result: list[str] = sorted(list(consensus))
        logger.info(
            "AI consensus: %d of %d tickers agreed upon: %s",
            len(result),
            len(tickers),
            result,
        )
        
        return result
