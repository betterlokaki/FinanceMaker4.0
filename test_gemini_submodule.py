#!/usr/bin/env python3
"""
Gemini Earnings Scoring Script

Phase 1: For each ticker, send an earnings-scoring prompt to gemini_thinking.
Phase 2: Send all collected data to gemini_fast to extract structured scores.
"""

import sys
import os
import re
import asyncio
import traceback

# # Add the Gemini submodule directory to sys.path so we can import its src package
# sys.path.insert(0, os.path.join(os.path.dirname(__file__), "Gemini"))

from Gemini.src.gemini_functions import gemini_thinking, gemini_fast

MAX_CONCURRENT_REQUESTS = 1

TICKERS = ['AEE', 'ALB', 'AM', 'APP', 'AR', 'AVTR', 'BWA', 'BXMT', 'CFLT', 'CGNX', 'CLBT', 'CRK', 'CSCO', 'DAR', 'EPRT', 'FSLY', 'GFL', 'GFS', 'GNRC', 'HLT', 'HUBS', 'HUM', 'IFF', 'INSP', 'IRT', 'KHC', 'LBRDK', 'LEG', 'LPTH', 'MCD', 'MFC', 'MSI', 'NBIX', 'NE', 'NI', 'NNN', 'PRCH', 'PSN', 'QDEL', 'QS', 'ROL', 'RPRX', 'RWT', 'RYN', 'SHOP', 'SN', 'SOLS', 'STAG', 'SW']
PROMPT_TEMPLATE = (
    "You are a quant financial advisor, can you tell me whether {TICKER} stock "
    "will go up today because of their earnings? Your answer based on fundamentals "
    "news and little technicals, after you've done that search if the market overall "
    "has gotten up in the last Quarter. For example if it is NVDA then yes because "
    "many people need AI and AI needs GPU which NVDA are developing. Another example "
    "can be missiles, because the world is headed towards wars a lot of them, and "
    "every country needs missiles. So your answer based on all of that. "
    "Give for {TICKER} a score between 1 - 100 which determines how much likely the "
    "Earnings will gap up the stock by at least 8%. "
    "Your answer should look like this and oonly  only only this \n\n "
    "{TICKER}\n"
    "score : x\n"
    "Why: 12 words paragraph that explains why\n\n"
    "for example: the score for ticker NVDA is 50/100 because it is a GPU company and "
    "the market is headed towards AI and AI needs GPUs and NVDA is a GPU company and "
    "the market is headed towards AI and AI needs GPUs and NVDA is a GPU company and "
)

SUMMARY_PROMPT_TEMPLATE = (
    "Extract me for each ticker from the following data the score\n\n"
    "your response should look like this for each ticker\n\n"
    "Ticker: ticker,\n"
    "Score: score,\n"
    "why: why?\n\n"
    "{DATA}"
)


def parse_scores(text: str) -> list[dict]:
    """
    Parse Gemini's summary response into a list of dicts.
    Expects blocks like:
        Ticker: XYZ,
        Score: 85,
        why: Some reason here.
    """
    results: list[dict] = []
    # Match ticker/score/why blocks regardless of markdown bold markers (**)
    pattern = re.compile(
        r"\*{0,2}Ticker:?\*{0,2}\s*([A-Z]{1,6})"
        r"[\s,]*\*{0,2}Score:?\*{0,2}\s*(\d+)"
        r"[\s,]*\*{0,2}[Ww]hy:?\*{0,2}\s*(.+?)(?=\n\s*\n|\*{0,2}Ticker|\Z)",
        re.DOTALL,
    )
    for match in pattern.finditer(text):
        ticker = match.group(1).strip()
        try:
            score = int(match.group(2).strip())
        except ValueError:
            score = 0
        why = match.group(3).strip().rstrip(",").rstrip("---").strip()
        results.append({"ticker": ticker, "score": score, "why": why})
    return results


async def fetch_ticker(ticker: str, semaphore: asyncio.Semaphore) -> tuple[str, str]:
    """Send a single ticker prompt to gemini_thinking, respecting the semaphore."""
    async with semaphore:
        prompt = PROMPT_TEMPLATE.replace("{TICKER}", ticker)
        print(f"[Phase 1] Sending prompt for {ticker}...")
        try:
            result = await asyncio.to_thread(gemini_thinking, prompt)
            print(f"[Phase 1] Got response for {ticker} ({len(result)} chars)")
            return ticker, result
        except Exception as e:
            print(f"[Phase 1] ERROR for {ticker}: {e}")
            traceback.print_exc()
            return ticker, f"ERROR: {e}"


async def main():
    # ---- Phase 1: Collect per-ticker analysis via gemini_thinking (async) ----
    semaphore = asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)
    tasks = [fetch_ticker(ticker, semaphore) for ticker in TICKERS]
    results = await asyncio.gather(*tasks)
    collected_responses = dict(results)

    # ---- Phase 2: Extract structured scores via gemini_fast ----
    all_data_parts = []
    for ticker in TICKERS:
        all_data_parts.append(f"=== {ticker} ===\n{collected_responses[ticker]}")

    all_data = "\n\n".join(all_data_parts)
    summary_prompt = SUMMARY_PROMPT_TEMPLATE.replace("{DATA}", all_data)

    print("\n[Phase 2] Sending summary extraction to gemini_fast...")
    try:
        summary_result = await asyncio.to_thread(gemini_fast, summary_prompt)
        print("\n--- Raw Gemini Response ---")
        print(summary_result)
        print("--- End Raw ---")
    except Exception as e:
        print(f"[Phase 2] ERROR: {e}")
        traceback.print_exc()
        return

    # ---- Phase 3: Parse into list of dicts, sort, keep top/bottom 5 ----
    parsed = parse_scores(summary_result)
    parsed.sort(key=lambda d: d["score"], reverse=True)

    top_5 = parsed[:5]
    bottom_5 = parsed[-5:] if len(parsed) > 5 else []

    # Avoid duplicates if there are 10 or fewer results
    combined = top_5 + [e for e in bottom_5 if e not in top_5]

    print("\n" + "=" * 70)
    print("  TOP 5 (highest score)")
    print("-" * 70)
    for entry in top_5:
        print(f"  {entry['ticker']:<8}  Score: {entry['score']:>3}  | {entry['why']}")

    print("\n  BOTTOM 5 (lowest score)")
    print("-" * 70)
    for entry in bottom_5:
        print(f"  {entry['ticker']:<8}  Score: {entry['score']:>3}  | {entry['why']}")
    print("=" * 70)

    print("\n[Final list] Top 5 + Bottom 5:")
    for entry in combined:
        print(entry)


if __name__ == "__main__":
    asyncio.run(main())
