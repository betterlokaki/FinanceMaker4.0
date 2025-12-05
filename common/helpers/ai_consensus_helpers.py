"""AI consensus helper functions for multi-provider analysis."""
import logging

from common.helpers.ticker_helpers import extract_tickers_from_response
from gpt.abstracts.gpt_base import GPTBase

logger: logging.Logger = logging.getLogger(__name__)


async def get_ai_suggestions(
    ai_client: GPTBase,
    prompt: str,
    valid_tickers: list[str],
    ai_name: str,
) -> set[str]:
    """Get ticker suggestions from an AI provider.
    
    Args:
        ai_client: The AI client (Grok, Gemini, etc.).
        prompt: The prompt to send to the AI.
        valid_tickers: List of valid tickers to filter against.
        ai_name: Name of the AI provider (for logging).
        
    Returns:
        Set of suggested tickers extracted from AI response.
    """
    logger.debug(f"Sending prompt to {ai_name}:\n{prompt[:100]}...")
    
    response: str = await ai_client.generate_text(prompt)
    logger.debug(f"{ai_name} response length: {len(response)} chars")
    
    suggested_tickers: set[str] = extract_tickers_from_response(response, valid_tickers)
    logger.info(f"{ai_name} extracted {len(suggested_tickers)} tickers")
    
    return suggested_tickers


def find_consensus(
    suggestions_a: set[str],
    suggestions_b: set[str],
    name_a: str = "Source A",
    name_b: str = "Source B",
) -> set[str]:
    """Find tickers suggested by both sources.
    
    Args:
        suggestions_a: Set of tickers from first source.
        suggestions_b: Set of tickers from second source.
        name_a: Name of first source (for logging).
        name_b: Name of second source (for logging).
        
    Returns:
        Set of tickers in both suggestions (intersection).
    """
    consensus: set[str] = suggestions_a.intersection(suggestions_b)
    
    logger.info(
        f"Consensus analysis:\n"
        f"  - {name_a} only: {suggestions_a - suggestions_b}\n"
        f"  - {name_b} only: {suggestions_b - suggestions_a}\n"
        f"  - Both (consensus): {consensus}"
    )
    
    return consensus
