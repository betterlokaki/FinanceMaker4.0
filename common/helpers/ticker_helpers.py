"""Ticker extraction helper utilities."""
import json
import logging
import re

logger: logging.Logger = logging.getLogger(__name__)


def extract_tickers_from_response(response: str, valid_tickers: list[str]) -> set[str]:
    """Extract ticker symbols from AI response.
    
    Supports multiple formats:
    1. JSON array format: [{"Ticker": "NVDA", "Score": 94}, ...]
    2. JSON object format: {"NVDA": 94, "LOW": 86, ...}
    3. JSON in markdown code blocks: ```json\\n[...]\\n```
    4. JSON embedded in text (strips preamble before first [ or {)
    5. Text with ticker patterns: "NVDA is a strong buy..."
    
    Strategy:
    1. Remove markdown code block markers (```)
    2. Check if response starts with [ (array) or { (object)
    3. Parse JSON array and extract "Ticker" field from each object
    4. Parse JSON object and extract keys as tickers
    5. Fall back to regex pattern matching for text format
    6. Filter to only include valid tickers from the original list
    
    Args:
        response: AI provider's response text.
        valid_tickers: List of original tickers to filter against.
        
    Returns:
        Set of extracted tickers that are in the valid list.
    """
    try:
        # Create a set of valid tickers for quick lookup
        valid_set: set[str] = {ticker.upper() for ticker in valid_tickers}
        suggested: set[str] = set()
        
        # Pre-process: Remove markdown code block markers
        cleaned_response: str = response
        if "```" in cleaned_response:
            # Extract content between ``` markers
            parts: list[str] = cleaned_response.split("```")
            # Typically format is: text```json\n{...}\n```more text
            # So parts[1] would be the code block content
            if len(parts) >= 2:
                cleaned_response = parts[1]
                # Remove language identifier like "json"
                if cleaned_response.startswith("json"):
                    cleaned_response = cleaned_response[4:]
        
        # Strategy 1: Try to parse as JSON (with intelligent extraction)
        suggested = _try_extract_from_json(cleaned_response, valid_set)
        if suggested:
            return suggested
        
        # Strategy 2: Use regex pattern matching for text format
        suggested = _extract_from_text(cleaned_response, valid_set)
        
        logger.info(f"Extracted {len(suggested)} tickers total from response")
        return suggested
        
    except Exception as e:
        logger.error(f"Error extracting tickers: {str(e)}", exc_info=True)
        return set()


def _try_extract_from_json(cleaned_response: str, valid_set: set[str]) -> set[str]:
    """Try to extract tickers from JSON format.
    
    Args:
        cleaned_response: Pre-processed response text.
        valid_set: Set of valid ticker symbols.
        
    Returns:
        Set of extracted tickers, empty if JSON parsing fails.
    """
    suggested: set[str] = set()
    
    try:
        # First, try to find a JSON array (starts with [)
        json_array_start: int = cleaned_response.find('[')
        json_obj_start: int = cleaned_response.find('{')
        
        # Determine if it's an array or object format
        if json_array_start != -1 and (json_obj_start == -1 or json_array_start < json_obj_start):
            suggested = _extract_from_json_array(cleaned_response, json_array_start, valid_set)
        elif json_obj_start != -1:
            suggested = _extract_from_json_object(cleaned_response, json_obj_start, valid_set)
            
    except (json.JSONDecodeError, ValueError, KeyError, IndexError) as e:
        logger.debug(f"JSON extraction failed: {str(e)}, trying regex pattern matching")
    
    return suggested


def _extract_from_json_array(
    cleaned_response: str, 
    json_array_start: int, 
    valid_set: set[str]
) -> set[str]:
    """Extract tickers from JSON array format.
    
    Args:
        cleaned_response: Pre-processed response text.
        json_array_start: Index where JSON array starts.
        valid_set: Set of valid ticker symbols.
        
    Returns:
        Set of extracted tickers.
    """
    suggested: set[str] = set()
    json_end: int = cleaned_response.rfind(']')
    
    if json_end != -1 and json_end > json_array_start:
        json_str: str = cleaned_response[json_array_start:json_end + 1]
        logger.debug(f"Attempting to parse JSON array: {json_str[:100]}...")
        json_data = json.loads(json_str)
        
        if isinstance(json_data, list):
            for item in json_data:
                if isinstance(item, dict):
                    ticker_value = _find_ticker_in_dict(item)
                    if ticker_value:
                        ticker: str = str(ticker_value).upper().strip()
                        if ticker in valid_set:
                            suggested.add(ticker)
                            logger.debug(f"Extracted ticker from JSON array: {ticker}")
            
            if suggested:
                logger.info(f"Successfully extracted {len(suggested)} tickers from JSON array response")
    
    return suggested


def _extract_from_json_object(
    cleaned_response: str, 
    json_obj_start: int, 
    valid_set: set[str]
) -> set[str]:
    """Extract tickers from JSON object format.
    
    Args:
        cleaned_response: Pre-processed response text.
        json_obj_start: Index where JSON object starts.
        valid_set: Set of valid ticker symbols.
        
    Returns:
        Set of extracted tickers.
    """
    suggested: set[str] = set()
    json_end: int = cleaned_response.rfind('}')
    
    if json_end != -1 and json_end > json_obj_start:
        json_str: str = cleaned_response[json_obj_start:json_end + 1]
        logger.debug(f"Attempting to parse JSON object: {json_str[:100]}...")
        json_data = json.loads(json_str)
        
        if isinstance(json_data, dict):
            for key in json_data.keys():
                ticker: str = key.upper().strip()
                if ticker in valid_set:
                    suggested.add(ticker)
                    logger.debug(f"Extracted ticker from JSON: {ticker}")
            
            if suggested:
                logger.info(f"Successfully extracted {len(suggested)} tickers from JSON response")
    
    return suggested


def _find_ticker_in_dict(item: dict) -> str | None:
    """Find ticker value in a dictionary (case-insensitive key lookup).
    
    Args:
        item: Dictionary that may contain a ticker key.
        
    Returns:
        Ticker value if found, None otherwise.
    """
    for key in item.keys():
        if key.lower() == "ticker":
            return item[key]
    return None


def _extract_from_text(cleaned_response: str, valid_set: set[str]) -> set[str]:
    """Extract tickers using regex pattern matching for text format.
    
    Args:
        cleaned_response: Pre-processed response text.
        valid_set: Set of valid ticker symbols.
        
    Returns:
        Set of extracted tickers.
    """
    suggested: set[str] = set()
    
    # Matches: 1-5 uppercase letters, optionally followed by dots
    ticker_pattern: str = r'\b([A-Z]{1,5}(?:\.[A-Z]{1,2})?)(?:\s|,|\.|\n|$|:)'
    
    matches = re.finditer(ticker_pattern, cleaned_response)
    
    for match in matches:
        ticker: str = match.group(1).upper()
        if ticker in valid_set:
            suggested.add(ticker)
            logger.debug(f"Extracted ticker from text: {ticker}")
    
    return suggested
