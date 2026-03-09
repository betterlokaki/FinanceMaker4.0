"""Helpers for parsing and aggregating weekly AI trading responses."""
from __future__ import annotations

from dataclasses import dataclass
import logging
import re
import statistics
from typing import Iterable, Mapping

logger: logging.Logger = logging.getLogger(__name__)

_TICKER_LINE_RE = re.compile(r"^\s*\**\s*TICKER\s*:\s*(.+?)\s*$", re.IGNORECASE)
_SCORE_RE = re.compile(r"(?im)^\s*\**\s*SCORE\s*:\s*([0-9]+(?:\.[0-9]+)?)\s*$")
_WHY_RE = re.compile(
    r"(?ism)^\s*\**\s*WHY\??\s*:?\s*(.+?)(?=\n\s*\**\s*(?:BUY|SELL|STOP|SCORE|TICKER)\b|\Z)"
)
_BUY_RE = re.compile(r"(?im)^\s*\**\s*BUY\s*:\s*(.+?)\s*$")
_SELL_RE = re.compile(r"(?im)^\s*\**\s*SELL\s*:\s*(.+?)\s*$")
_STOP_RE = re.compile(r"(?im)^\s*\**\s*STOP\s*:\s*(.+?)\s*$")
_PRICE_RE = re.compile(r"-?\d+(?:\.\d+)?")


@dataclass(frozen=True)
class AITradeIdea:
    """Single AI recommendation parsed from model output."""

    ticker: str
    score: float
    why: str
    buy: float
    sell: float
    stop: float
    source: str


@dataclass(frozen=True)
class ConsensusTradeIdea:
    """Ticker recommendation agreed by all four model runs."""

    ticker: str
    score: float
    why: str
    buy: float
    sell: float
    stop: float
    sources: tuple[str, ...]


def parse_trade_ideas(
    response: str,
    source: str,
    min_score: float = 80.0,
    valid_tickers: Iterable[str] | None = None,
) -> dict[str, AITradeIdea]:
    """Parse model response blocks into structured ideas."""
    allowed: set[str] | None = None
    if valid_tickers is not None:
        allowed = {ticker.strip().upper() for ticker in valid_tickers if ticker and ticker.strip()}

    blocks = _split_blocks(response)
    parsed: dict[str, AITradeIdea] = {}

    for block in blocks:
        idea = _parse_single_block(block, source, min_score, allowed)
        if idea is None:
            continue
        # Keep the strongest version if ticker appears multiple times in one run.
        existing = parsed.get(idea.ticker)
        if existing is None or idea.score > existing.score:
            parsed[idea.ticker] = idea

    logger.info("%s parsed %d valid trade ideas", source, len(parsed))
    return parsed


def find_four_way_consensus(
    runs: list[Mapping[str, AITradeIdea]],
) -> dict[str, ConsensusTradeIdea]:
    """Return only tickers present in all four runs."""
    if len(runs) != 4:
        raise ValueError("runs must contain exactly 4 mappings")

    common = set(runs[0].keys())
    for run in runs[1:]:
        common.intersection_update(run.keys())

    result: dict[str, ConsensusTradeIdea] = {}
    for ticker in sorted(common):
        ideas = [run[ticker] for run in runs]
        score_values = [idea.score for idea in ideas]
        buy_values = [idea.buy for idea in ideas]
        sell_values = [idea.sell for idea in ideas]
        stop_values = [idea.stop for idea in ideas]
        strongest = max(ideas, key=lambda idea: idea.score)

        result[ticker] = ConsensusTradeIdea(
            ticker=ticker,
            score=float(statistics.median(score_values)),
            why=strongest.why,
            buy=float(statistics.median(buy_values)),
            sell=float(statistics.median(sell_values)),
            stop=float(statistics.median(stop_values)),
            sources=tuple(idea.source for idea in ideas),
        )

    logger.info("4-way consensus tickers: %s", sorted(result.keys()))
    return result


def _split_blocks(response: str) -> list[str]:
    """Split a response into TICKER-led blocks."""
    lines = response.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    blocks: list[list[str]] = []
    current: list[str] = []

    for line in lines:
        if _TICKER_LINE_RE.match(line):
            if current:
                blocks.append(current)
            current = [line]
            continue
        if current:
            current.append(line)

    if current:
        blocks.append(current)

    return ["\n".join(block).strip() for block in blocks if block]


def _parse_single_block(
    block: str,
    source: str,
    min_score: float,
    allowed: set[str] | None,
) -> AITradeIdea | None:
    ticker_match = _TICKER_LINE_RE.search(block.splitlines()[0] if block else "")
    if not ticker_match:
        return None

    raw_ticker = ticker_match.group(1).strip().upper().replace("$", "")
    ticker = re.sub(r"[^A-Z.\-]", "", raw_ticker)
    if not ticker:
        return None
    if allowed is not None and ticker not in allowed:
        return None

    score_match = _SCORE_RE.search(block)
    buy_match = _BUY_RE.search(block)
    sell_match = _SELL_RE.search(block)
    stop_match = _STOP_RE.search(block)
    why_match = _WHY_RE.search(block)

    if not score_match or not buy_match or not sell_match or not stop_match:
        return None

    score = float(score_match.group(1))
    if score < min_score:
        return None

    buy = _extract_price(buy_match.group(1))
    sell = _extract_price(sell_match.group(1))
    stop = _extract_price(stop_match.group(1))
    if buy is None or sell is None or stop is None:
        return None
    if not (buy > 0 and sell > 0 and stop > 0):
        return None

    why = ""
    if why_match:
        why = " ".join(why_match.group(1).strip().split())

    return AITradeIdea(
        ticker=ticker,
        score=score,
        why=why,
        buy=buy,
        sell=sell,
        stop=stop,
        source=source,
    )


def _extract_price(raw_value: str) -> float | None:
    cleaned = raw_value.replace(",", "")
    match = _PRICE_RE.search(cleaned)
    if not match:
        return None
    try:
        return float(match.group(0))
    except ValueError:
        return None
