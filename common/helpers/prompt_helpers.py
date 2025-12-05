"""Prompt building helper functions for AI analysis."""


def build_ticker_analysis_prompt(tickers: list[str], template: str) -> str:
    """Build a prompt for AI ticker analysis.
    
    Args:
        tickers: List of tickers to include in the prompt.
        template: Prompt template with {TICKERS} placeholder.
        
    Returns:
        Formatted prompt string.
    """
    tickers_str: str = ", ".join(tickers)
    return template.format(TICKERS=tickers_str)
