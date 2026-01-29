#!/usr/bin/env python3
"""Test script for Gemini Deep Research Agent - Get stock recommendations."""
import asyncio
import json
import logging
import sys
from datetime import datetime
from pathlib import Path

import httpx

from gpt.gemini.gemini_base import GeminiClient

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)

logger: logging.Logger = logging.getLogger(__name__)

# Test prompt for stock recommendations
TEST_PROMPT = """
Based on current market conditions, recent news, and technical analysis, 
provide your top 10 stock recommendations (tickers) for swing trading 
in the current month. 

Focus on stocks that:
- Are showing strong fundamentals
- Have positive momentum indicators
- Are near key support levels (demand zones)
- Have recent positive news or catalysts
- Show potential for at least 8% gains in the current month

Please provide:
1. The ticker symbols (e.g., AAPL, MSFT, etc.)
2. Brief rationale for each recommendation
3. Key technical levels or catalysts for each stock
"""


async def main() -> None:
    """Test Deep Research Agent and save stock recommendations."""
    print("\n" + "=" * 70)
    print("TESTING GEMINI DEEP RESEARCH AGENT")
    print("=" * 70)
    print("\nThis will use the Deep Research Agent to analyze stocks.")
    print("Note: This may take several minutes as the agent performs deep research...")
    print("\nStarting research task...\n")
    
    try:
        async with httpx.AsyncClient() as http_client:
            gemini_client = GeminiClient(http_client=http_client)
            
            # Run the Deep Research task
            response = await gemini_client.generate_text(TEST_PROMPT)
            
            print("\n" + "=" * 70)
            print("DEEP RESEARCH RESULTS")
            print("=" * 70)
            print(response)
            print("\n" + "=" * 70)
            
            # Extract tickers from response (simple extraction)
            tickers = extract_tickers(response)
            
            # Save results
            results = {
                "timestamp": datetime.now().isoformat(),
                "prompt": TEST_PROMPT,
                "response": response,
                "extracted_tickers": tickers,
                "ticker_count": len(tickers)
            }
            
            # Save to file
            output_file = Path("deep_research_results.json")
            with open(output_file, "w", encoding="utf-8") as f:
                json.dump(results, f, indent=2, ensure_ascii=False)
            
            print(f"\n✅ Results saved to: {output_file}")
            print(f"\n📊 Extracted {len(tickers)} tickers:")
            if tickers:
                print(f"   {', '.join(sorted(tickers))}")
            else:
                print("   (No tickers extracted - check full response above)")
            
            print("\n" + "=" * 70)
            print("TEST COMPLETE")
            print("=" * 70)
            
    except Exception as e:
        logger.error(f"❌ Error during Deep Research test: {e}", exc_info=True)
        raise


def extract_tickers(text: str) -> list[str]:
    """Extract ticker symbols from the response text.
    
    Looks for common patterns like:
    - Ticker symbols in parentheses: (AAPL), (MSFT)
    - Ticker symbols after stock names: Apple (AAPL)
    - Ticker symbols in lists or tables
    """
    import re
    
    # Common ticker pattern: 1-5 uppercase letters
    ticker_pattern = r'\b([A-Z]{1,5})\b'
    
    # Find all potential tickers
    potential_tickers = re.findall(ticker_pattern, text)
    
    # Filter out common words that aren't tickers
    common_words = {
        'THE', 'AND', 'FOR', 'ARE', 'BUT', 'NOT', 'YOU', 'ALL', 'CAN', 'HER',
        'WAS', 'ONE', 'OUR', 'OUT', 'DAY', 'GET', 'HAS', 'HIM', 'HIS', 'HOW',
        'ITS', 'MAY', 'NEW', 'NOW', 'OLD', 'SEE', 'TWO', 'WHO', 'WAY', 'USE',
        'YOUR', 'YEAR', 'WITH', 'FROM', 'THAT', 'THIS', 'THEY', 'THAN', 'THEN',
        'THEM', 'THESE', 'THERE', 'THEIR', 'WHAT', 'WHEN', 'WHERE', 'WHICH',
        'WHILE', 'AFTER', 'BEFORE', 'ABOUT', 'ABOVE', 'BELOW', 'BETWEEN',
        'DURING', 'SINCE', 'UNTIL', 'WITHIN', 'WITHOUT', 'AGAINST', 'AMONG',
        'AROUND', 'BEHIND', 'BESIDE', 'BESIDES', 'BEYOND', 'EXCEPT', 'INSIDE',
        'OUTSIDE', 'THROUGH', 'THROUGHOUT', 'TOWARD', 'TOWARDS', 'UNDER',
        'UNDERNEATH', 'UPON', 'VERSUS', 'VIA', 'WITHIN', 'WITHOUT'
    }
    
    # Filter and deduplicate
    tickers = []
    seen = set()
    for ticker in potential_tickers:
        if ticker not in common_words and ticker not in seen:
            # Additional validation: tickers are usually 2-5 chars and all uppercase
            if 2 <= len(ticker) <= 5 and ticker.isupper():
                tickers.append(ticker)
                seen.add(ticker)
    
    return sorted(tickers)


if __name__ == "__main__":
    asyncio.run(main())
