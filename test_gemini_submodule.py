#!/usr/bin/env python3
"""
Gemini Earnings Scoring Script

Phase 1: For each ticker, send an earnings-scoring prompt to GeminiSearchClient (thinking).
Phase 2: Send all collected data to GeminiSearchClient (fast) to extract structured scores.
"""

import json
import re
import asyncio
import traceback

import httpx

from gpt.gemini.gemini_search_client import GeminiSearchClient, ThinkingLevel
from common.di_container import container

from common.models.scanner_params import ScannerParams
MAX_CONCURRENT_REQUESTS = 5

# TICKERS = ['AEE', 'ALB', 'AM', 'APP', 'AR', 'AVTR', 'BWA', 'BXMT', 'CFLT', 'CGNX', 'CLBT', 'CRK', 'CSCO', 'DAR', 'EPRT', 'FSLY', 'GFL', 'GFS', 'GNRC', 'HLT', 'HUBS', 'HUM', 'IFF', 'INSP', 'IRT', 'KHC', 'LBRDK', 'LEG', 'LPTH', 'MCD', 'MFC', 'MSI', 'NBIX', 'NE', 'NI', 'NNN', 'PRCH', 'PSN', 'QDEL', 'QS', 'ROL', 'RPRX', 'RWT', 'RYN', 'SHOP', 'SN', 'SOLS', 'STAG', 'SW']
# TICKERS = ['AEE', 'ALB', 'AM', 'APP', 'AR', 'CFLT', 'CGNX', 'CRK', 'CSCO', 'DAR', 'EPRT', 'FSLY', 'GFL', 'HUBS', 'IFF', 'INSP', 'IRT', 'LEG', 'LPTH', 'MCD', 'MFC', 'MSI', 'NBIX', 'NE', 'PRCH', 'QDEL', 'QS', 'ROL', 'RWT', 'RYN', 'STAG', 'VKTX', 'VNDA', 'WCN']
finviz_scanner = container.finviz_scanner()
PROMPT_TEMPLATE = (
    """
"Act as an expert Quantitative Equity Analyst. Your goal is to evaluate to determine the likelihood of a 10%+ price 'explosion' within 48 hours of its upcoming earnings report.
Use your internal search capabilities to find the current pre-earnings data for and score the ticker {TICKER} from 1-100 based on these criteria:
1. Technicals (30% weight):

Is the 30-day momentum > 15%? 

Is the RSI (14) between 65 and 80 (Strength zone)? 

Is there a 'Golden Cross' (50-day SMA > 200-day SMA)? 

Is the Beta > 1.2? 

Is the stock within 5% of its 52-week high? 

2. Fundamentals (40% weight):

Is the Zacks Earnings ESP positive (> +1.5%)? 

Have EPS estimates been revised upward by > 1% in the last 30 days? 

Is Institutional Ownership > 85% (Scarcity factor)? 

Does the company have a 'Beat and Raise' history for the last 3 quarters? 

Is the Forward P/E or PEG ratio at a discount to the industry? 

3. Narrative & News (30% weight):

Is there a 'Second Wave AI' or infrastructure narrative? 

Has there been cluster insider buying (3+ insiders) recently? 

Are there news catalysts regarding onshoring, debt redemption, or buybacks? 

Output Requirements:
Provide a detailed breakdown of each category and a final 'Explosion Probability Score' from 1-100. A score above 80 indicates a high-conviction candidate."
    """
)

SUMMARY_PROMPT_TEMPLATE = (
    "Extract me for each ticker from the following data the score\n\n"
    "your response should look like this for each ticker\n\n"
    "Ticker: ticker,\n"
    "Score: score,\n"
    "why: why?\n\n"
    "{DATA}"
)


def _parse_score_value(raw: str | int | float) -> int:
    """Extract the numeric score from values like 85, "85", "70/100", etc."""
    s = str(raw).strip().strip('"')
    m = re.match(r"(\d+)", s)
    return int(m.group(1)) if m else 0


def _try_parse_json(text: str) -> list[dict[str, str | int]] | None:
    """Try to parse the response as JSON (possibly wrapped in ```json ... ``` fences)."""
    # Strip markdown code fences if present
    stripped = re.sub(r"^```(?:json)?\s*\n?", "", text.strip(), flags=re.MULTILINE)
    stripped = re.sub(r"\n?```\s*$", "", stripped.strip(), flags=re.MULTILINE)

    try:
        data: dict[str, dict[str, str | int]] = json.loads(stripped)
    except (json.JSONDecodeError, ValueError):
        return None

    if not isinstance(data, dict):
        return None

    results: list[dict[str, str | int]] = []
    for ticker_key, info in data.items():
        if not isinstance(info, dict):
            continue
        # Case-insensitive key lookup for Score / why
        score_raw: str | int = info.get("Score") or info.get("score") or 0  # type: ignore[assignment]
        why_raw: str = str(info.get("why") or info.get("Why") or "")
        results.append({
            "ticker": ticker_key.upper(),
            "score": _parse_score_value(score_raw),
            "why": why_raw.strip(),
        })
    return results if results else None


def parse_scores(text: str) -> list[dict[str, str | int]]:
    """
    Parse Gemini's summary response into a list of dicts.

    Supports two formats:
      1. JSON: { "TICKER": { "Score": 85, "why": "..." }, ... }
      2. Text:  Ticker: XYZ,  Score: 85,  why: Some reason here.
    """
    # Try JSON first (Gemini sometimes returns JSON wrapped in code fences)
    json_result = _try_parse_json(text)
    if json_result is not None:
        return json_result

    # Fall back to regex-based parsing for the text format
    results: list[dict[str, str | int]] = []
    pattern = re.compile(
        r"\*{0,2}Ticker:?\*{0,2}\s*([A-Z]{1,6})"
        r"[\s,]*\*{0,2}Score:?\*{0,2}\s*(\d+)[^\n]*"
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


async def fetch_ticker(
    ticker: str,
    semaphore: asyncio.Semaphore,
    client: GeminiSearchClient,
) -> tuple[str, str]:
    """Send a single ticker prompt to GeminiSearchClient (thinking), respecting the semaphore."""
    async with semaphore:
        prompt = PROMPT_TEMPLATE.replace("{TICKER}", ticker)
        print(f"[Phase 1] Sending prompt for {ticker}...")
        try:
            result = await client.generate_text(prompt)
            print(f"[Phase 1] Got response for {ticker} ({len(result)} chars)")
            return ticker, result
        except Exception as e:
            print(f"[Phase 1] ERROR for {ticker}: {e}")
            traceback.print_exc()
            return ticker, f"ERROR: {e}"


async def main():
    # --- Step 1: Fetch earning tickers ---
    scan_params = ScannerParams(
        name="earning_demand_zone_scoring",
        filters={},
        config={},
    )
    TICKERS: list[str] = await finviz_scanner.scan(scan_params)
    print(f"[Scan] Got {len(TICKERS)} tickers: {TICKERS}")

    async with httpx.AsyncClient() as http_client:
        # Create two clients: one with thinking for analysis, one fast for extraction
        client_thinking = GeminiSearchClient(
            http_client=http_client,
            model="gemini-3-pro-preview",
            thinking_level=ThinkingLevel.HIGH,
            thinking_budget=4000,
        )
        client_fast = GeminiSearchClient(
            http_client=http_client,
            model="gemini-2.5-flash",
            thinking_level=ThinkingLevel.MEDIUM,
            thinking_budget=4000,
        )   

        # ---- Phase 1: Collect per-ticker analysis via thinking client ----
        semaphore = asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)
        tasks = [fetch_ticker(ticker, semaphore, client_thinking) for ticker in TICKERS]
        results = await asyncio.gather(*tasks)
        collected_responses = dict(results)

        # ---- Phase 2: Extract structured scores via fast client ----
        all_data_parts: list[str] = []
        for ticker in TICKERS:
            all_data_parts.append(f"=== {ticker} ===\n{collected_responses[ticker]}")

        all_data = "\n\n".join(all_data_parts)
        summary_prompt = SUMMARY_PROMPT_TEMPLATE.replace("{DATA}", all_data)

        print("\n[Phase 2] Sending summary extraction to fast client...")
        try:
            summary_result = await client_fast.generate_text(summary_prompt)
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
