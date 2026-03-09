#!/usr/bin/env python3
"""Weekly Grok ticker discovery + Yahoo daily chart setup validation."""
from __future__ import annotations

import argparse
import asyncio
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
import json
import logging
from pathlib import Path
import re
import sys
from typing import Any, Final

import httpx
import pandas as pd

from common.models.period import Period
from gpt.grok.grok_base import GrokClient
from pullers.market.yahoo.yahoo_market_provider import YahooMarketProvider

logger: logging.Logger = logging.getLogger(__name__)

ALLOWED_EXCHANGES: Final[set[str]] = {"NASDAQ", "NYSE", "AMEX"}
VALID_DECISIONS: Final[set[str]] = {"SETUP", "WAIT", "AVOID"}

DEFAULT_FIRST_PROMPT: Final[str] = """
You are a professional market intelligence analyst.

Your task is to find U.S. stocks that could be strong LONG opportunities in the coming week.

DATA SOURCES (use real-time data):
- X (Twitter) posts and sentiment
- StockTwits discussions
- Latest financial news
- Unusual social media attention
- Recent catalysts (earnings, contracts, partnerships, product launches, regulation changes, analyst upgrades)

FILTERS (STRICT):
- Market Cap > $1B
- Listed ONLY on US exchanges (NASDAQ, NYSE, AMEX)
- Long opportunities ONLY (no short ideas)
- Exclude penny stocks
- Exclude stocks already extremely overextended or clearly pump-and-dump

ANALYSIS STEPS:
1. Scan the last 24-72 hours of discussions on X and StockTwits for tickers gaining unusual attention.
2. Cross-reference with recent news and catalysts.
3. Evaluate sentiment (bullish / neutral / bearish).
4. Identify early-stage narratives that could lead to price momentum this week.
5. Filter out spam, bot-driven hype, and low-credibility accounts.

OUTPUT FORMAT:

For each stock provide:

Ticker:
Company:
Exchange:
Market Cap:

Bullish Catalysts:
- (news, earnings, contracts, sector momentum, etc.)

Social Sentiment:
- X sentiment summary
- StockTwits sentiment summary
- Key influencers/accounts discussing it

Narrative:
Explain why this stock could attract buyers this week.

Risk Factors:
What could invalidate the thesis.

Momentum Score (1-10):
Likelihood of near-term upside.

FINAL RESULT:
Return the TOP 10 strongest LONG candidates for this week ranked by momentum score.
""".strip()


@dataclass(frozen=True)
class Stage1Candidate:
    ticker: str
    company: str
    exchange: str
    market_cap_usd: float
    momentum_score: float
    bullish_catalysts: list[str]
    social_sentiment: dict[str, Any]
    narrative: str
    risk_factors: list[str]


@dataclass(frozen=True)
class Stage2SetupAnalysis:
    ticker: str
    decision: str
    setup_type: str | None
    confidence: float | None
    entry: dict[str, Any] | None
    stop_loss: float | None
    take_profit: float | None
    risk_reward: float | None
    reasons: list[str]
    invalidations: list[str]
    next_step: str


@dataclass(frozen=True)
class TickerAnalysisRecord:
    ticker: str
    candle_count: int
    raw_response: str | None
    parsed_analysis: Stage2SetupAnalysis | None
    errors: list[str]


def _setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Discover weekly long candidates with Grok and validate daily setups.",
    )
    parser.add_argument(
        "--first-prompt-file",
        type=str,
        default="",
        help="Optional file path for stage-1 prompt. Defaults to built-in prompt.",
    )
    parser.add_argument(
        "--top-n",
        type=int,
        default=10,
        help="Number of stage-1 candidates to keep after validation.",
    )
    parser.add_argument(
        "--lookback-days",
        type=int,
        default=200,
        help="Daily candle lookback window per ticker.",
    )
    parser.add_argument(
        "--max-concurrency",
        type=int,
        default=2,
        help="Max concurrent per-ticker stage-2 analyses.",
    )
    parser.add_argument(
        "--output-json",
        type=str,
        default="",
        help="Output report path. Defaults to cache/grok_weekly_setup_<timestamp>.json",
    )
    return parser.parse_args()


def _default_output_path() -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return Path("cache") / f"grok_weekly_setup_{stamp}.json"


def _to_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _to_clean_str(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _to_string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [part for part in (_to_clean_str(item) for item in value) if part]
    if isinstance(value, str):
        cleaned = value.strip()
        return [cleaned] if cleaned else []
    return []


def _try_parse_json_object(raw_response: str) -> dict[str, Any] | None:
    cleaned = raw_response.strip()
    if not cleaned:
        return None

    try:
        parsed = json.loads(cleaned)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass

    fenced_blocks = re.findall(
        r"```(?:json)?\s*(.*?)```",
        cleaned,
        flags=re.IGNORECASE | re.DOTALL,
    )
    for block in fenced_blocks:
        try:
            parsed = json.loads(block.strip())
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            continue

    decoder = json.JSONDecoder()
    for idx, char in enumerate(cleaned):
        if char != "{":
            continue
        try:
            parsed, _ = decoder.raw_decode(cleaned[idx:])
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed

    return None


def build_stage1_prompt(base_prompt: str, top_n: int) -> str:
    strict_contract = f"""
STRICT OUTPUT REQUIREMENTS (MANDATORY):
- Return ONLY a valid JSON object.
- Do NOT include markdown, code fences, comments, or extra text.
- Return exactly {top_n} unique tickers, ranked by momentum_score descending.

Use this exact schema:
{{
  "candidates": [
    {{
      "ticker": "NVDA",
      "company": "NVIDIA Corp",
      "exchange": "NASDAQ",
      "market_cap_usd": 2900000000000,
      "momentum_score": 9,
      "bullish_catalysts": ["..."],
      "social_sentiment": {{
        "x_summary": "...",
        "stocktwits_summary": "...",
        "key_influencers": ["..."]
      }},
      "narrative": "...",
      "risk_factors": ["..."]
    }}
  ]
}}
"""
    return f"{base_prompt.strip()}\n\n{strict_contract.strip()}\n"


def parse_stage1_candidates(
    raw_response: str,
    top_n: int,
) -> tuple[list[Stage1Candidate], list[str]]:
    errors: list[str] = []
    parsed = _try_parse_json_object(raw_response)
    if parsed is None:
        return [], ["Could not parse stage-1 response as JSON object."]

    raw_candidates = parsed.get("candidates")
    if isinstance(raw_candidates, list):
        entries = raw_candidates
    else:
        entries = []
        errors.append("Missing or invalid 'candidates' list in stage-1 response.")

    dedup: dict[str, Stage1Candidate] = {}
    for item in entries:
        if not isinstance(item, dict):
            continue

        ticker = _to_clean_str(item.get("ticker")).upper()
        company = _to_clean_str(item.get("company"))
        exchange = _to_clean_str(item.get("exchange")).upper()
        market_cap_usd = _to_float(item.get("market_cap_usd"))
        momentum_score = _to_float(item.get("momentum_score"))

        if not ticker or not re.fullmatch(r"[A-Z]{1,5}(?:\.[A-Z]{1,2})?", ticker):
            continue
        if exchange not in ALLOWED_EXCHANGES:
            continue
        if market_cap_usd is None or market_cap_usd <= 1_000_000_000:
            continue
        if momentum_score is None:
            continue

        social = item.get("social_sentiment")
        social_sentiment: dict[str, Any] = {
            "x_summary": "",
            "stocktwits_summary": "",
            "key_influencers": [],
        }
        if isinstance(social, dict):
            social_sentiment = {
                "x_summary": _to_clean_str(social.get("x_summary")),
                "stocktwits_summary": _to_clean_str(social.get("stocktwits_summary")),
                "key_influencers": _to_string_list(social.get("key_influencers")),
            }

        candidate = Stage1Candidate(
            ticker=ticker,
            company=company,
            exchange=exchange,
            market_cap_usd=market_cap_usd,
            momentum_score=momentum_score,
            bullish_catalysts=_to_string_list(item.get("bullish_catalysts")),
            social_sentiment=social_sentiment,
            narrative=_to_clean_str(item.get("narrative")),
            risk_factors=_to_string_list(item.get("risk_factors")),
        )

        existing = dedup.get(ticker)
        if existing is None or candidate.momentum_score > existing.momentum_score:
            dedup[ticker] = candidate

    candidates = sorted(
        dedup.values(),
        key=lambda c: (-c.momentum_score, c.ticker),
    )[: max(0, top_n)]

    if not candidates:
        errors.append("No valid candidates passed validation filters.")

    return candidates, errors


def normalize_daily_candles(df: pd.DataFrame | None) -> list[dict[str, Any]]:
    if df is None or df.empty:
        return []

    frame = df.copy()
    frame.columns = [str(column).lower() for column in frame.columns]
    required_columns = ["open", "high", "low", "close", "volume"]
    if not all(column in frame.columns for column in required_columns):
        return []

    frame = frame[required_columns].copy()
    for column in required_columns:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame = frame.dropna(subset=required_columns)
    if frame.empty:
        return []

    index = pd.to_datetime(frame.index, utc=True, errors="coerce")
    valid_index = ~index.isna()
    if not valid_index.any():
        return []

    frame = frame.loc[valid_index].copy()
    frame.index = index[valid_index]
    frame = frame.sort_index()

    candles: list[dict[str, Any]] = []
    for ts, row in frame.iterrows():
        candles.append(
            {
                "time": pd.Timestamp(ts).isoformat(),
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
                "volume": float(row["volume"]),
            }
        )
    return candles


def build_stage2_prompt(ticker: str, candles: list[dict[str, Any]]) -> str:
    candles_payload = json.dumps(candles, separators=(",", ":"))
    return f"""
You are an expert technical analyst for U.S. equities.
Analyze ONLY the provided daily OHLCV candle data for ticker {ticker}.

Goal:
- Decide if the current daily chart offers a quality LONG swing setup right now.
- If not ready, explicitly indicate whether to WAIT or AVOID.

Decision meanings:
- SETUP: A valid long setup exists now with actionable levels.
- WAIT: The chart may become attractive, but entry should wait for confirmation.
- AVOID: Setup quality is poor or risk is currently unfavorable.

CANDLE_DATA_JSON:
{candles_payload}

Return ONLY a valid JSON object (no markdown, no extra text) in this exact schema:
{{
  "ticker": "{ticker}",
  "decision": "SETUP",
  "setup_type": "pullback_to_support",
  "confidence": 78,
  "entry": {{"type": "limit", "price": 905.5}},
  "stop_loss": 878.0,
  "take_profit": 962.0,
  "risk_reward": 2.1,
  "reasons": ["..."],
  "invalidations": ["..."],
  "next_step": "..."
}}

Rules:
- decision must be one of: SETUP, WAIT, AVOID.
- ticker must exactly be "{ticker}".
- If decision is SETUP: entry, stop_loss, take_profit, risk_reward must be numeric and valid.
- If decision is WAIT or AVOID: entry, stop_loss, take_profit, risk_reward must all be null.
- Keep reasons concise and technical.
""".strip()


def parse_stage2_setup_response(
    raw_response: str,
    requested_ticker: str,
) -> tuple[Stage2SetupAnalysis | None, list[str]]:
    errors: list[str] = []
    parsed = _try_parse_json_object(raw_response)
    if parsed is None:
        return None, ["Could not parse stage-2 response as JSON object."]

    ticker = _to_clean_str(parsed.get("ticker")).upper()
    expected = requested_ticker.upper()
    if ticker != expected:
        errors.append(f"Ticker mismatch: expected {expected}, got {ticker or '<empty>'}.")

    decision = _to_clean_str(parsed.get("decision")).upper()
    if decision not in VALID_DECISIONS:
        errors.append(f"Invalid decision '{decision}'. Must be one of {sorted(VALID_DECISIONS)}.")

    setup_type_raw = parsed.get("setup_type")
    setup_type = _to_clean_str(setup_type_raw) if setup_type_raw is not None else None
    confidence = _to_float(parsed.get("confidence"))
    if confidence is None:
        errors.append("Missing or invalid confidence.")
    elif confidence < 0 or confidence > 100:
        errors.append("Confidence must be between 0 and 100.")

    entry_raw = parsed.get("entry")
    stop_loss_raw = parsed.get("stop_loss")
    take_profit_raw = parsed.get("take_profit")
    risk_reward_raw = parsed.get("risk_reward")

    entry: dict[str, Any] | None = None
    stop_loss = _to_float(stop_loss_raw)
    take_profit = _to_float(take_profit_raw)
    risk_reward = _to_float(risk_reward_raw)

    reasons = _to_string_list(parsed.get("reasons"))
    invalidations = _to_string_list(parsed.get("invalidations"))
    next_step = _to_clean_str(parsed.get("next_step"))

    if decision == "SETUP":
        if not isinstance(entry_raw, dict):
            errors.append("SETUP requires entry object.")
            entry_price = None
            entry_type = ""
        else:
            entry_type = _to_clean_str(entry_raw.get("type"))
            entry_price = _to_float(entry_raw.get("price"))
            if not entry_type:
                errors.append("SETUP entry.type is required.")
            if entry_price is None or entry_price <= 0:
                errors.append("SETUP entry.price must be > 0.")
            else:
                entry = {"type": entry_type, "price": entry_price}

        if stop_loss is None or stop_loss <= 0:
            errors.append("SETUP stop_loss must be > 0.")
        if take_profit is None or take_profit <= 0:
            errors.append("SETUP take_profit must be > 0.")
        if risk_reward is None or risk_reward <= 0:
            errors.append("SETUP risk_reward must be > 0.")

        if entry is not None and stop_loss is not None and take_profit is not None:
            entry_price = float(entry["price"])
            if not (stop_loss < entry_price < take_profit):
                errors.append("SETUP levels invalid: require stop_loss < entry.price < take_profit.")
    else:
        if entry_raw is not None or stop_loss_raw is not None or take_profit_raw is not None or risk_reward_raw is not None:
            errors.append(
                f"{decision or 'WAIT/AVOID'} requires entry/stop_loss/take_profit/risk_reward to be null.",
            )

    if errors:
        return None, errors

    return (
        Stage2SetupAnalysis(
            ticker=ticker,
            decision=decision,
            setup_type=setup_type,
            confidence=confidence,
            entry=entry,
            stop_loss=stop_loss,
            take_profit=take_profit,
            risk_reward=risk_reward,
            reasons=reasons,
            invalidations=invalidations,
            next_step=next_step,
        ),
        [],
    )


def _format_level(level: float | None) -> str:
    if level is None:
        return "-"
    return f"{level:.2f}"


def _print_summary(records: list[TickerAnalysisRecord]) -> None:
    print("\n" + "=" * 130)
    print("GROK WEEKLY SETUP RESULTS")
    print("=" * 130)
    print(
        f"{'Ticker':<8} {'Decision':<8} {'Setup Type':<24} {'Conf':>6} "
        f"{'Entry':>10} {'Stop':>10} {'Target':>10} {'RR':>7}  Reason/Error"
    )
    print("-" * 130)
    for record in records:
        if record.parsed_analysis is None:
            error_text = record.errors[0] if record.errors else "Unknown error"
            print(
                f"{record.ticker:<8} {'ERROR':<8} {'-':<24} {'-':>6} "
                f"{'-':>10} {'-':>10} {'-':>10} {'-':>7}  {error_text[:60]}"
            )
            continue

        parsed = record.parsed_analysis
        entry_price = None
        if isinstance(parsed.entry, dict):
            entry_price = _to_float(parsed.entry.get("price"))
        reason = parsed.reasons[0] if parsed.reasons else parsed.next_step
        print(
            f"{parsed.ticker:<8} {parsed.decision:<8} {((parsed.setup_type or '-')[:24]):<24} "
            f"{_format_level(parsed.confidence):>6} {_format_level(entry_price):>10} "
            f"{_format_level(parsed.stop_loss):>10} {_format_level(parsed.take_profit):>10} "
            f"{_format_level(parsed.risk_reward):>7}  {reason[:60]}"
        )
    print("=" * 130)


def _load_stage1_prompt(prompt_file: str) -> str:
    if not prompt_file:
        return DEFAULT_FIRST_PROMPT
    path = Path(prompt_file).expanduser()
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        raise ValueError(f"Prompt file is empty: {path}")
    return text


async def _analyze_single_ticker(
    candidate: Stage1Candidate,
    grok_client: GrokClient,
    market_provider: YahooMarketProvider,
    lookback_days: int,
) -> TickerAnalysisRecord:
    ticker = candidate.ticker

    end_time = datetime.now(timezone.utc)
    start_time = end_time - timedelta(days=lookback_days)

    try:
        df = await market_provider.get_prices(
            ticker=ticker,
            start_time=start_time,
            end_time=end_time,
            period=Period.DAILY,
        )
    except Exception as exc:
        return TickerAnalysisRecord(
            ticker=ticker,
            candle_count=0,
            raw_response=None,
            parsed_analysis=None,
            errors=[f"Yahoo fetch failed: {exc}"],
        )

    candles = normalize_daily_candles(df)
    if not candles:
        return TickerAnalysisRecord(
            ticker=ticker,
            candle_count=0,
            raw_response=None,
            parsed_analysis=None,
            errors=["No valid daily candles returned from Yahoo."],
        )

    prompt = build_stage2_prompt(ticker=ticker, candles=candles)
    try:
        raw_response = await grok_client.generate_text(prompt)
    except Exception as exc:
        return TickerAnalysisRecord(
            ticker=ticker,
            candle_count=len(candles),
            raw_response=None,
            parsed_analysis=None,
            errors=[f"Stage-2 Grok call failed: {exc}"],
        )

    parsed_analysis, parse_errors = parse_stage2_setup_response(
        raw_response=raw_response,
        requested_ticker=ticker,
    )
    return TickerAnalysisRecord(
        ticker=ticker,
        candle_count=len(candles),
        raw_response=raw_response,
        parsed_analysis=parsed_analysis,
        errors=parse_errors,
    )


async def main() -> int:
    _setup_logging()
    args = _parse_args()
    top_n = max(1, args.top_n)
    lookback_days = max(1, args.lookback_days)
    max_concurrency = max(1, args.max_concurrency)

    output_path = Path(args.output_json).expanduser() if args.output_json else _default_output_path()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    started_at = datetime.now(timezone.utc)
    stage1_prompt_base = _load_stage1_prompt(args.first_prompt_file)
    stage1_prompt = build_stage1_prompt(stage1_prompt_base, top_n=top_n)

    global_errors: list[str] = []
    stage1_raw_response = ""
    stage1_candidates: list[Stage1Candidate] = []
    stage1_errors: list[str] = []
    stage2_records: list[TickerAnalysisRecord] = []

    http_client = httpx.AsyncClient(timeout=60.0, follow_redirects=True)
    try:
        grok_client = GrokClient(http_client=http_client)
        market_provider = YahooMarketProvider(http_client=http_client)

        logger.info("Stage-1: requesting weekly candidates from Grok (top_n=%d)...", top_n)
        stage1_raw_response = await grok_client.generate_text(stage1_prompt)
        stage1_candidates, stage1_errors = parse_stage1_candidates(stage1_raw_response, top_n=top_n)
        global_errors.extend([f"stage1: {error}" for error in stage1_errors])

        logger.info("Stage-1 valid candidates: %d", len(stage1_candidates))
        if stage1_candidates:
            semaphore = asyncio.Semaphore(max_concurrency)

            async def _bounded(candidate: Stage1Candidate) -> TickerAnalysisRecord:
                async with semaphore:
                    logger.info("Stage-2: analyzing %s (%d daily candles target)...", candidate.ticker, lookback_days)
                    return await _analyze_single_ticker(
                        candidate=candidate,
                        grok_client=grok_client,
                        market_provider=market_provider,
                        lookback_days=lookback_days,
                    )

            stage2_records = list(await asyncio.gather(*[_bounded(candidate) for candidate in stage1_candidates]))

            for record in stage2_records:
                for error in record.errors:
                    global_errors.append(f"stage2:{record.ticker}: {error}")
    finally:
        await http_client.aclose()

    _print_summary(stage2_records)

    successful_stage2 = [record for record in stage2_records if record.parsed_analysis is not None]
    finished_at = datetime.now(timezone.utc)
    report: dict[str, Any] = {
        "run_metadata": {
            "started_at_utc": started_at.isoformat(),
            "finished_at_utc": finished_at.isoformat(),
            "top_n": top_n,
            "lookback_days": lookback_days,
            "max_concurrency": max_concurrency,
            "first_prompt_file": args.first_prompt_file or None,
            "output_json": str(output_path),
        },
        "stage1": {
            "prompt": stage1_prompt,
            "raw_response": stage1_raw_response,
            "parsed_candidates": [asdict(candidate) for candidate in stage1_candidates],
            "errors": stage1_errors,
        },
        "stage2": {
            "results": [
                {
                    "ticker": record.ticker,
                    "candle_count": record.candle_count,
                    "raw_response": record.raw_response,
                    "parsed_analysis": (
                        asdict(record.parsed_analysis) if record.parsed_analysis is not None else None
                    ),
                    "errors": record.errors,
                }
                for record in stage2_records
            ],
            "successful_count": len(successful_stage2),
        },
        "errors": global_errors,
    }

    output_path.write_text(json.dumps(report, indent=2, ensure_ascii=True), encoding="utf-8")
    logger.info("Saved report to %s", output_path)

    if not stage1_candidates:
        logger.error("No valid stage-1 candidates. Exiting with failure.")
        return 1
    if not successful_stage2:
        logger.error("No valid stage-2 analyses. Exiting with failure.")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
