#!/usr/bin/env python3
"""Main entry point for Unified AI Scanner - prints full pipeline results."""
import asyncio
import logging
import sys

import httpx

from common.di_container import container
from common.helpers.ai_consensus_helpers import get_ai_suggestions, find_consensus
from common.helpers.prompt_helpers import build_ticker_analysis_prompt
from common.models.scanner_params import ScannerParams
from pullers.scanners.ai_scanners.unified_ai_scanner import UnifiedAIScanner

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)

# Set third-party loggers to WARNING to reduce noise
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

logger: logging.Logger = logging.getLogger(__name__)

HARDCODED_TICKER_ONLY_SUFFIX: str = (
    "\n\nIMPORTANT: Your response must contain ONLY the ticker symbols "
    "from the list above. Do not include any explanations, analysis, or "
    "additional text. Return only the ticker symbols, one per line or in "
    "a JSON array format."
)

# Swing trading prompt template
SWING_TRADING_PROMPT: str = (
    "You are an expert of swing trading, you always always always base on "
    "fundamentals, technical, news, market situation sector news and situation, "
    "and of course donald trump twits and actions\n"
    "Your goal is to predict from the following stock list which is a list of "
    "stocks that are good but fell down to a keylevel, which of the stocks would "
    "most probably explode (explode means, go up by at least 8% in the current month)\n"
    "based on the data that is above\n"
    "the tickers are from this list - {TICKERS}\n"
    "your answer should look like this (6 most possible stocks only not more you can "
    "do less if nothing seems to be suited for the task than give me an empty list [])"
)

# Finviz URL
FINVIZ_URL: str = (
    "https://finviz.com/screener.ashx?v=111&f=cap_midover%2Cfa_epsqoq_pos%2Cta_perf_13wdown%2Cta_sma200_pa&ft=4"
)


async def main() -> None:
    """Run unified AI scanner and print full pipeline results."""
    print("\n" + "=" * 70)
    print("UNIFIED AI SCANNER - FULL PIPELINE")
    print("=" * 70)
    
    try:
        # Get components
        http_client = container.http_client()
        grok_client = container.grok_client()
        gemini_client = container.gemini_client()
        
        # Show configuration
        print(f"\n📍 FINVIZ URL:")
        print(f"   {FINVIZ_URL}")
        print(f"\n📝 PROMPT TEMPLATE:")
        print("-" * 70)
        print(SWING_TRADING_PROMPT)
        print("-" * 70)
        
        # Create unified scanner
        scanner = UnifiedAIScanner(
            http_client=http_client,
            finviz_url=FINVIZ_URL,
            prompt_template=SWING_TRADING_PROMPT,
            grok_client=grok_client,
            gemini_client=gemini_client,
        )
        
        # Get demand zone tickers first (for detailed output)
        params = ScannerParams("unified_ai_scanner")
        
        print("\n" + "=" * 70)
        print("STEP 1: FINVIZ SCANNER → DEMAND ZONE FILTER")
        print("=" * 70)
        print("Scanning Finviz and filtering by demand zones...")
        print("This may take a few minutes...")
        
        from pullers.scanners.finviz.zone_filtered_scanner import ZoneFilteredScanner
        zone_scanner = ZoneFilteredScanner(http_client=http_client, url=FINVIZ_URL)
        demand_tickers = await zone_scanner.scan(params)
        
        print(f"\n✅ Found {len(demand_tickers)} tickers close to demand zones")
        if demand_tickers:
            print(f"Tickers: {', '.join(demand_tickers)}")
        else:
            print("⚠️  No tickers found - stopping pipeline")
            return
        
        # Step 2: AI Analysis
        print("\n" + "=" * 70)
        print("STEP 2: AI CONSENSUS ANALYSIS (Grok + Gemini)")
        print("=" * 70)
        
        base_prompt = build_ticker_analysis_prompt(demand_tickers, SWING_TRADING_PROMPT)
        full_prompt = base_prompt + HARDCODED_TICKER_ONLY_SUFFIX
        
        print(f"\n📤 Sending {len(demand_tickers)} tickers to Grok and Gemini...")
        
        # Get Grok suggestions
        print("\n🤖 GROK ANALYSIS:")
        print("-" * 70)
        grok_suggestions = await get_ai_suggestions(
            grok_client, full_prompt, demand_tickers, "Grok"
        )
        print(f"✅ Grok selected: {sorted(grok_suggestions)}")
        print(f"   Total: {len(grok_suggestions)} tickers")
        
        # Get Gemini suggestions
        print("\n🤖 GEMINI ANALYSIS:")
        print("-" * 70)
        gemini_suggestions = await get_ai_suggestions(
            gemini_client, full_prompt, demand_tickers, "Gemini"
        )
        print(f"✅ Gemini selected: {sorted(gemini_suggestions)}")
        print(f"   Total: {len(gemini_suggestions)} tickers")
        
        # Step 3: Consensus
        print("\n" + "=" * 70)
        print("STEP 3: CONSENSUS ANALYSIS")
        print("=" * 70)
        
        consensus = find_consensus(
            grok_suggestions, gemini_suggestions, "Grok", "Gemini"
        )
        
        print(f"\n📊 BREAKDOWN:")
        grok_only = sorted(grok_suggestions - gemini_suggestions)
        gemini_only = sorted(gemini_suggestions - grok_suggestions)
        consensus_list = sorted(consensus)
        
        print(f"   - Grok only: {grok_only if grok_only else '(none)'}")
        print(f"   - Gemini only: {gemini_only if gemini_only else '(none)'}")
        print(f"   - Both (CONSENSUS): {consensus_list if consensus_list else '(none)'}")
        
        # Final result
        print("\n" + "=" * 70)
        print("FINAL RESULT: CONSENSUS TICKERS")
        print("=" * 70)
        
        if consensus_list:
            print(f"\n✅ {len(consensus_list)} tickers selected by BOTH AIs:")
            print(f"   {', '.join(consensus_list)}")
            print(f"\n📋 Comma-separated: {','.join(consensus_list)}")
        else:
            print("\n⚠️  NO CONSENSUS - Grok and Gemini did not agree on any tickers")
        
        print("\n" + "=" * 70)
        logger.info("✅ Unified AI scanner complete")
        
    except Exception as e:
        logger.error(f"❌ Error: {e}", exc_info=True)
        raise
    finally:
        http_client = container.http_client()
        await http_client.aclose()


if __name__ == "__main__":
    asyncio.run(main())
