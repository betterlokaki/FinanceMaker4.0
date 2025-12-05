"""HTML parsing helper utilities."""
import logging

from lxml import html

logger: logging.Logger = logging.getLogger(__name__)


def parse_finviz_tickers(html_content: str) -> list[str]:
    """Parse tickers from Finviz HTML response using XPath.
    
    Finviz embeds ticker data in HTML comments with TS/TE markers.
    This function extracts and parses that data.
    
    Args:
        html_content: Raw HTML content from Finviz screener page.
        
    Returns:
        List of ticker symbols extracted from the HTML.
    """
    tree = html.fromstring(html_content)
    comments = tree.xpath('//comment()')
    
    ticker_data: str | None = None
    for comment in comments:
        comment_text: str = comment.text or ""
        if "TS" in comment_text:
            ticker_data = comment_text
            break
    
    if not ticker_data:
        logger.debug("No ticker data found in HTML comments")
        return []
    
    start_idx: int = ticker_data.find("TS")
    end_idx: int = ticker_data.find("TE")
    
    if start_idx == -1 or end_idx == -1:
        logger.debug("TS/TE markers not found in ticker data")
        return []
    
    data_section: str = ticker_data[start_idx + 2:end_idx].strip()
    
    tickers: list[str] = []
    for line in data_section.split("\n"):
        if not line.strip():
            continue
        parts: list[str] = line.split("|")
        if parts and parts[0].strip():
            tickers.append(parts[0].strip())
    
    logger.debug(f"Parsed {len(tickers)} tickers from Finviz HTML")
    return tickers
