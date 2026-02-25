#!/usr/bin/env python3
"""Custom Finviz scan → Grok + Gemini AI consensus → print agreed tickers."""
import asyncio
import logging
import sys
from typing import Final

from common.di_container import container
from common.helpers.ai_consensus_helpers import find_consensus, get_ai_suggestions
from common.helpers.prompt_helpers import build_ticker_analysis_prompt
from common.models.scanner_params import ScannerParams
from pullers.scanners.finviz.custom_finviz import CustomFinviz

# ── Logging ───────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

logger: logging.Logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────
FINVIZ_URL: Final[str] = (
    "https://finviz.com/screener.ashx?v=111&f=cap_midover%2Cearningsdate_tomorrow%2Csh_avgvol_o1000&ft=4"
)

PROMPT_TEMPLATE: Final[str] =  """
    Role: You are the "Earnings Explosion Algorithm," a specialized financial model designed to predict which stocks will rise >10% within 48 hours of an earnings report. Your logic is strictly governed by the "2025-2026 Explosive Equities Cycle" research.

Objective: Analyze the user-provided list of tickers, calculate a Breakout Probability Score (0-100) for each using the indicators below, and output ONLY the Top 3 highest-scoring tickers.

Input: {TICKERS}\n\n

Scoring Methodology (Strictly from the Analysis File):

1. The "Fuel" - Fundamental Indicators (Max 40 Points)

Revision Trend (15 pts): Have consensus EPS estimates increased by >1% in the last 30 days? (Score 15 for >10% revision; scale down for less).


Earnings ESP (10 pts): Is the Zacks Earnings ESP positive (Most Accurate Estimate > Consensus)? 

Institutional Scarcity (10 pts): Is Institutional Ownership > 85%? (Creates "float scarcity" for more violent moves). 


Valuation Gap (5 pts): Is the Forward P/E or PEG ratio lower than the industry or historical average? 

2. The "Structure" - Technical Indicators (Max 40 Points)


Momentum Persistence (10 pts): Is the 30-day performance significantly beating the S&P 500? 
+1

RSI Strength (10 pts): Is the RSI (14) between 65 and 80? (In this model, high RSI = strength, not exhaustion). 


Trend & Highs (10 pts): Is the stock within 5-10% of its 52-week high, or is a "Golden Cross" (50 SMA > 200 SMA) intact? 
+1

Volatility Multiplier (5 pts): Is the Beta > 1.0? (Acts as a multiplier for the "surprise delta"). 
+1


Band Expansion (5 pts): Are Bollinger Bands widening with price riding the upper band? 

3. The "Catalyst" - Narrative & News (Max 20 Points)


Insider Buying (10 pts): Has there been "Cluster Buying" (3+ insiders) in the last quarter? 


Thematic Alignment (5 pts): Is the news flow centered on "AI Infrastructure," "Reshoring," or "Energy Security"? 
+2


Quiet Period Guidance (5 pts): Has management raised full-year guidance in the last 2 weeks? 
+1

AI Output Instructions:
Evaluate every ticker in the user's list, but only display the top 3 results. Use the following format:

"Based on the Explosive Equities framework, I have analyzed your list. Here are my Top 3 highest-probability picks for a 10%+ earnings move:"

Rank #1: [TICKER]

Breakout Score: [X]/100

Primary Driver: [The single strongest indicator found, e.g., "98% Institutional Ownership"]

Technical Setup: [Summarize the RSI/Momentum/Trend status]

Narrative Alignment: [Identify the news catalyst/theme]

Rank #2: [TICKER]

[Same format as above]

Rank #3: [TICKER]

[Same format as above]
"""

TICKER_ONLY_SUFFIX: Final[str] = (
    "\n\nIMPORTANT: Your response must contain ONLY the ticker symbols "
    "from the list above. Do not include any explanations, analysis, or "
    "additional text. Return only the ticker symbols, one per line or in "
    "a JSON array format."
)


async def main() -> None:
    """Scan Finviz, ask Grok + Gemini, print consensus tickers."""
    print("\n" + "=" * 70)
    print("CUSTOM FINVIZ → GROK + GEMINI CONSENSUS")
    print("=" * 70)

    http_client = container.http_client()

    try:
        # ── Step 1: Finviz scan ───────────────────────────────────────
        print(f"\n📍 FINVIZ URL:\n   {FINVIZ_URL}")
        scanner = CustomFinviz(http_client=http_client, url=FINVIZ_URL)
        tickers: list[str] = await scanner.scan(ScannerParams("custom_ai_consensus"))

        print(f"\n✅ Found {len(tickers)} tickers from Finviz")
        if not tickers:
            print("⚠️  No tickers found — nothing to analyse.")
            return

        print(f"   Tickers: {', '.join(tickers)}")

        # ── Step 2: Build prompt ──────────────────────────────────────
        full_prompt: str = (
            build_ticker_analysis_prompt(tickers, PROMPT_TEMPLATE)
            + TICKER_ONLY_SUFFIX
        )

        print(f"\n📤 Sending {len(tickers)} tickers to Grok and Gemini...")

        # ── Step 3: AI analysis (concurrent) ──────────────────────────
        grok_client = container.grok_client()
        gemini_client = container.gemini_search_client()

        grok_task = get_ai_suggestions(grok_client, full_prompt, tickers, "Grok")
        gemini_task = get_ai_suggestions(gemini_client, full_prompt, tickers, "Gemini")

        grok_suggestions, gemini_suggestions = await asyncio.gather(
            grok_task, gemini_task,
        )

        print(f"\n🤖 Grok selected:   {sorted(grok_suggestions)}  ({len(grok_suggestions)})")
        print(f"🤖 Gemini selected: {sorted(gemini_suggestions)}  ({len(gemini_suggestions)})")

        # ── Step 4: Consensus ─────────────────────────────────────────
        consensus: set[str] = find_consensus(
            grok_suggestions, gemini_suggestions, "Grok", "Gemini",
        )
        consensus_list: list[str] = sorted(consensus)

        grok_only = sorted(grok_suggestions - gemini_suggestions)
        gemini_only = sorted(gemini_suggestions - grok_suggestions)

        print("\n" + "=" * 70)
        print("CONSENSUS RESULT")
        print("=" * 70)
        print(f"   Grok only:        {grok_only if grok_only else '(none)'}")
        print(f"   Gemini only:      {gemini_only if gemini_only else '(none)'}")
        print(f"   Both (consensus): {consensus_list if consensus_list else '(none)'}")

        if consensus_list:
            print(f"\n✅ {len(consensus_list)} tickers agreed by BOTH AIs:")
            print(f"   {', '.join(consensus_list)}")
        else:
            print("\n⚠️  NO CONSENSUS — Grok and Gemini did not agree on any tickers.")

        print("\n" + "=" * 70)

    except Exception as e:
        logger.error(f"❌ Error: {e}", exc_info=True)
        raise
    finally:
        await http_client.aclose()


if __name__ == "__main__":
    asyncio.run(main())
