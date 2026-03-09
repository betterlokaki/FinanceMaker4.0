#!/usr/bin/env python3
"""Rule-based TORA earnings breakout scorer.

Implements the scoring framework from:
"Earnings Breakout Prediction Framework.docx"

Public API:
    get_today_earnings_scores(tickers: list[str]) -> list[dict]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import math
import threading
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from statistics import median
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import pandas as pd
from lxml import html as lxml_html

from common.di_container import container
from common.models.period import Period
from common.models.scanner_params import ScannerParams
from common.settings import settings
from pullers.market.abstracts.i_market_provider import IMarketProvider
from pullers.scanners.finviz.custom_finviz import CustomFinviz

logger: logging.Logger = logging.getLogger(__name__)
NY_TZ = ZoneInfo("America/New_York")

FINVIZ_EARNINGS_TODAY_URL = "https://finviz.com/screener.ashx?v=111&f=earningsdate_nextweek,sh_avgvol_o1000,sh_price_o5,ta_sma200_pa&ft=4"
# Intentionally conservative to reduce Yahoo API 429s.
MAX_WORKERS = 1
LOOKBACK_DAYS = 450
# Force query1-only endpoints (user requirement: avoid query2).
YAHOO_BASE = settings.yahoo.base_url.replace("query2.", "query1.")
YAHOO_HTTP_MIN_INTERVAL_SECONDS = 1.25
YAHOO_HTTP_MAX_CONCURRENT = 1
YAHOO_HTTP_MAX_RETRIES = 6
YAHOO_429_BACKOFF_BASE_SECONDS = 2.0
HTTP_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36"

FUNDAMENTALS_TYPES: list[str] = [
    # Core operational series (prefer quarterly, fallback annual/trailing).
    "quarterlyTotalRevenue",
    "annualTotalRevenue",
    "trailingTotalRevenue",
    "quarterlyOperatingIncome",
    "annualOperatingIncome",
    "trailingOperatingIncome",
    "quarterlyGrossProfit",
    "annualGrossProfit",
    "trailingGrossProfit",
    "quarterlyOperatingCashFlow",
    "quarterlyTotalCashFromOperatingActivities",
    "annualOperatingCashFlow",
    "annualTotalCashFromOperatingActivities",
    "trailingOperatingCashFlow",
    "trailingTotalCashFromOperatingActivities",
    # EPS trend proxies for revision section.
    "quarterlyNormalizedDilutedEPS",
    "annualNormalizedDilutedEPS",
    "trailingNormalizedDilutedEPS",
    "quarterlyDilutedEPS",
    "annualDilutedEPS",
    "trailingDilutedEPS",
]


@dataclass(frozen=True)
class TechnicalContext:
    """Technical signals reused across sections."""

    vcp: bool
    bb_squeeze: bool
    snapshot_date: date


class AsyncRequestLimiter:
    """Simple deterministic async rate limiter + concurrency gate."""

    def __init__(self, max_concurrent: int, min_interval_seconds: float):
        self._semaphore = asyncio.Semaphore(max(1, max_concurrent))
        self._lock = asyncio.Lock()
        self._min_interval_seconds = max(0.0, float(min_interval_seconds))
        self._next_allowed_time = 0.0

    async def __aenter__(self) -> None:
        await self._semaphore.acquire()
        async with self._lock:
            loop = asyncio.get_running_loop()
            now = loop.time()
            wait = max(0.0, self._next_allowed_time - now)
            if wait > 0:
                await asyncio.sleep(wait)
                now = loop.time()
            self._next_allowed_time = now + self._min_interval_seconds

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self._semaphore.release()


def get_today_earnings_scores(tickers: list[str]) -> list[dict[str, Any]]:
    """Score today's earnings names with deterministic, rule-based TORA logic.

    The function always starts from the full Finviz "earnings today" universe,
    then optionally filters to ``tickers`` if provided.
    """
    normalized_input = _normalize_tickers(tickers)
    return _run_coro_sync(_get_today_earnings_scores_async(normalized_input))


async def _get_today_earnings_scores_async(tickers: list[str]) -> list[dict[str, Any]]:
    today_universe = await _scan_earnings_today_tickers()

    if tickers:
        allowed = set(tickers)
        universe = sorted(t for t in today_universe if t in allowed)
    else:
        universe = sorted(today_universe)

    if not universe and not tickers:
        return []
    if tickers and not universe:
        universe = tickers

    market_provider = container.yahoo_market_provider()
    http_client = container.http_client()
    earnings_date = datetime.now(tz=NY_TZ).date()
    yahoo_limiter = AsyncRequestLimiter(
        max_concurrent=YAHOO_HTTP_MAX_CONCURRENT,
        min_interval_seconds=YAHOO_HTTP_MIN_INTERVAL_SECONDS,
    )

    semaphore = asyncio.Semaphore(MAX_WORKERS)

    async def _score_one(symbol: str) -> dict[str, Any] | None:
        async with semaphore:
            return await _score_single_ticker(
                ticker=symbol,
                market_provider=market_provider,
                http_client=http_client,
                earnings_date=earnings_date,
                yahoo_limiter=yahoo_limiter,
            )

    raw_results = await asyncio.gather(*[_score_one(symbol) for symbol in universe], return_exceptions=True)

    scored: list[dict[str, Any]] = []
    for symbol, result in zip(universe, raw_results):
        if isinstance(result, Exception):
            logger.warning("Failed to score %s: %s", symbol, result)
            continue
        if result is not None:
            scored.append(result)

    scored.sort(key=lambda row: (-int(row["score"]), row["ticker"]))
    return scored


async def _scan_earnings_today_tickers() -> list[str]:
    """Load today's earnings tickers from Finviz using the requested scanner pattern."""
    finviz_url = FINVIZ_EARNINGS_TODAY_URL
    finviz_scanner = CustomFinviz(http_client=container.http_client(), url=finviz_url)
    try:
        scanned = await finviz_scanner.scan(ScannerParams(name="tora_earnings_today"))
    except Exception as exc:
        logger.warning("Finviz scan failed: %s", exc)
        return []
    return _normalize_tickers(scanned)


async def _score_single_ticker(
    ticker: str,
    market_provider: IMarketProvider,
    http_client: httpx.AsyncClient,
    earnings_date: date,
    yahoo_limiter: AsyncRequestLimiter,
) -> dict[str, Any] | None:
    end_time = datetime.combine(earnings_date + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc)
    start_time = end_time - timedelta(days=LOOKBACK_DAYS)

    daily_task = market_provider.get_prices(
        ticker=ticker,
        start_time=start_time,
        end_time=end_time,
        period=Period.DAILY,
    )
    bundle_task = _fetch_yahoo_bundle(
        http_client=http_client,
        ticker=ticker,
        yahoo_limiter=yahoo_limiter,
    )

    daily_df, yahoo_bundle = await asyncio.gather(daily_task, bundle_task)

    snapshot_df, snapshot_date = _load_pre_earnings_snapshot(
        daily_df=_normalize_ohlcv(daily_df),
        earnings_date=earnings_date,
    )
    if snapshot_df is None:
        logger.info("Skipping %s: no valid 1-5 day pre-earnings snapshot", ticker)
        return None

    technical_score, technical_meta = _score_technical(snapshot_df)
    tech_ctx = TechnicalContext(
        vcp=technical_meta["vcp"],
        bb_squeeze=technical_meta["bb_squeeze"],
        snapshot_date=snapshot_date,
    )

    operational_score = _score_operational(yahoo_bundle, snapshot_date)
    revision_score = _score_revision(yahoo_bundle, snapshot_date)
    alpha_score = _score_alpha_positioning(yahoo_bundle, tech_ctx)

    red_flags, red_flag_deduction = _evaluate_red_flags(
        yahoo_bundle=yahoo_bundle,
        snapshot_df=snapshot_df,
        earnings_date=earnings_date,
    )

    raw_total = technical_score + operational_score + revision_score + alpha_score + red_flag_deduction
    total_score = max(0, min(100, int(round(raw_total))))

    tier, breakout_probability = _map_tier(total_score)

    return {
        "ticker": ticker,
        "score": total_score,
        "tier": tier,
        "breakout_probability": breakout_probability,
        "section_scores": {
            "technical": int(round(technical_score)),
            "operational": int(round(operational_score)),
            "revision": int(round(revision_score)),
            "alpha_positioning": int(round(alpha_score)),
            "red_flags_deduction": int(round(red_flag_deduction)),
            "total_before_clamp": int(round(raw_total)),
        },
        "red_flags": red_flags,
    }


async def _fetch_yahoo_bundle(
    http_client: httpx.AsyncClient,
    ticker: str,
    yahoo_limiter: AsyncRequestLimiter,
) -> dict[str, Any]:
    fundamentals_task = _fetch_fundamentals_timeseries(
        http_client=http_client,
        ticker=ticker,
        yahoo_limiter=yahoo_limiter,
    )
    key_stats_task = _fetch_key_statistics_short_interest(
        http_client=http_client,
        ticker=ticker,
        yahoo_limiter=yahoo_limiter,
    )
    options_task = _fetch_options_chain(
        http_client=http_client,
        ticker=ticker,
        yahoo_limiter=yahoo_limiter,
    )

    fundamentals_map, key_stats_snapshot, options_result = await asyncio.gather(
        fundamentals_task,
        key_stats_task,
        options_task,
        return_exceptions=False,
    )

    return {
        "fundamentals_timeseries": fundamentals_map,
        "key_statistics_short_interest": key_stats_snapshot,
        "options": options_result,
    }


async def _fetch_fundamentals_timeseries(
    http_client: httpx.AsyncClient,
    ticker: str,
    yahoo_limiter: AsyncRequestLimiter,
) -> dict[str, list[dict[str, Any]]]:
    now_utc = datetime.now(timezone.utc)
    period1 = int(datetime(2016, 12, 31, tzinfo=timezone.utc).timestamp())
    period2 = int(now_utc.timestamp())

    url = f"{YAHOO_BASE}/ws/fundamentals-timeseries/v1/finance/timeseries/{ticker}"
    params = {
        "symbol": ticker,
        "merge": "false",
        "padTimeSeries": "true",
        "period1": str(period1),
        "period2": str(period2),
        "type": ",".join(FUNDAMENTALS_TYPES),
        "lang": "en-US",
        "region": "US",
    }

    data = await _http_get_json(
        http_client=http_client,
        url=url,
        params=params,
        yahoo_limiter=yahoo_limiter,
    )
    return _parse_timeseries_result(data)


async def _fetch_key_statistics_short_interest(
    http_client: httpx.AsyncClient,
    ticker: str,
    yahoo_limiter: AsyncRequestLimiter,
) -> dict[str, Any]:
    url = f"https://finance.yahoo.com/quote/{ticker}/key-statistics/"
    html_text = await _http_get_text(
        http_client=http_client,
        url=url,
        yahoo_limiter=yahoo_limiter,
    )
    if not html_text:
        return {}
    return _parse_key_statistics_short_interest(html_text)


async def _fetch_options_chain(
    http_client: httpx.AsyncClient,
    ticker: str,
    yahoo_limiter: AsyncRequestLimiter,
) -> dict[str, Any]:
    url = f"https://finance.yahoo.com/quote/{ticker}/options/"
    html_text = await _http_get_text(
        http_client=http_client,
        url=url,
        yahoo_limiter=yahoo_limiter,
    )
    if not html_text:
        return {"calls": [], "puts": []}

    return _parse_options_html(html_text)


async def _http_get_json(
    http_client: httpx.AsyncClient,
    url: str,
    params: dict[str, Any] | None = None,
    yahoo_limiter: AsyncRequestLimiter | None = None,
) -> dict[str, Any]:
    headers = {
        "User-Agent": HTTP_USER_AGENT,
        "Accept": "application/json",
        "Accept-Language": "en-US,en;q=0.9",
    }

    for attempt in range(YAHOO_HTTP_MAX_RETRIES):
        try:
            if yahoo_limiter is not None:
                async with yahoo_limiter:
                    response = await http_client.get(url, params=params, headers=headers)
            else:
                response = await http_client.get(url, params=params, headers=headers)

            if response.status_code == 200:
                payload = response.json()
                return payload if isinstance(payload, dict) else {}
            if response.status_code == 429:
                retry_after = _get_retry_after_seconds(response)
                backoff = YAHOO_429_BACKOFF_BASE_SECONDS * (2 ** attempt)
                sleep_seconds = retry_after if retry_after is not None else backoff
                await asyncio.sleep(min(60.0, max(1.0, sleep_seconds)))
                continue

            if 500 <= response.status_code < 600:
                await asyncio.sleep(min(30.0, 1.0 + attempt))
                continue
        except Exception:
            await asyncio.sleep(min(30.0, 1.0 + attempt))
            continue

    return {}


async def _http_get_text(
    http_client: httpx.AsyncClient,
    url: str,
    params: dict[str, Any] | None = None,
    yahoo_limiter: AsyncRequestLimiter | None = None,
) -> str:
    headers = {
        "User-Agent": HTTP_USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Cookie": "A1=d=AQABBPAhi2kCEITvaE1r7bzBcNAW9OAI6tUFEgABCAFsjGm8aV5DyyMA9qMCAAvI7SGLadUxsKE&S=AQAAAqtFYrrwjWOD2f4gc9xcI6c; A3=d=AQABBPAhi2kCEITvaE1r7bzBcNAW9OAI6tUFEgABCAFsjGm8aV5DyyMA9qMCAAcI7SGLadUxsKE&S=AQAAAqtFYrrwjWOD2f4gc9xcI6c; A1S=d=AQABBPAhi2kCEITvaE1r7bzBcNAW9OAI6tUFEgABCAFsjGm8aV5DyyMA9qMCAAcI7SGLadUxsKE&S=AQAAAqtFYrrwjWOD2f4gc9xcI6c; GUC=AQABCAFpjGxpvEIf5gS3&s=AQAAAOUya55d&g=aYsh-g; EuConsent=CQfaJIAQfaJIAAOACBHECRFoAP_gAEPgACiQL2NB9G7eTXFneTJ2YPskOYwX0VBJ4MAwBgCBAUABzBIUIBwGVmAzJEyIICACGAIAIGBBIABtGAhAQEAAIIAVAABIAEgAIBAAIGAAACAIQABACAAAAAAAAAAQgEAXMBQgmCYEBFoIQUhAggAgAQAAAAAEAIgBCAQAEAAAQAAACAAAACgAggAAAAAAAAAEAFAIEQAAIAECAgPkdAAAAAAAAAAIAAYACEABAAAAAIAAAgCAAAAAAAAAAAAAAAAAAABBWkAEg0KiCIsCAAIBAwggQACCgIAKBAEAAAQIAAACYICBAGACowGQAgBAAAAAAAAAAAAIAAAIAEIAAgAABACAAABAAEABAAAAAQAAAAACAAAAAAAAAAAAAAAAAAAxAIEEAQAAIIACCgAAgAEAAAAAAAAABEAAQAAAAAAAAAAAAAAEAAAEAAAAAAAAAAAAAABAiAABAAAAFAYgsAAAAAAAAAAAAAAAQgAIAAAABAAAEAA; _ga=GA1.1.228086384.1770725876; axids=gam=y-mcRTuKJE2uJBvfg7wSopxwD_wnUywWZI~A&dv360=eS12RmlqaWJaRTJ1RWltWWpONlNRd0Y4NlJqU2htZldYbn5B&ydsp=y-Q3NNSNRE2uLxB462YuEVPmpiJqsF5Erk~A&tbla=y-46zRo3hE2uIqqYkDRdFnOt2crm876rdW~A; tbla_id=1f7cdae8-cacf-4ce0-b45a-cef15626ee0c-tuct10833c1f; fes-ds-PolymarketBadge=1771339924637; cmp=t=1770739336&j=1&u=1---&v=120; PRF=t%3DNIO%252BSPOT%252BMSFT%252BLYFT%252BCVS%252BCAN%252BDDOG%252BGXO%252BUPXI%252BXIFR%252BKVYO%252BJMIA%252BZ%252BOGI%252BEW%26dock-collapsed%3Dtrue; _ga_YD9K1W9DLN=GS2.1.s1770739556$o4$g1$t1770739572$j44$l0$h0",
    }

    for attempt in range(YAHOO_HTTP_MAX_RETRIES):
        try:
            if yahoo_limiter is not None:
                async with yahoo_limiter:
                    response = await http_client.get(url, params=params, headers=headers)
            else:
                response = await http_client.get(url, params=params, headers=headers)

            if response.status_code == 200:
                return response.text
            if response.status_code == 429:
                retry_after = _get_retry_after_seconds(response)
                backoff = YAHOO_429_BACKOFF_BASE_SECONDS * (2 ** attempt)
                sleep_seconds = retry_after if retry_after is not None else backoff
                await asyncio.sleep(min(60.0, max(1.0, sleep_seconds)))
                continue

            if 500 <= response.status_code < 600:
                await asyncio.sleep(min(30.0, 1.0 + attempt))
                continue
        except Exception:
            await asyncio.sleep(min(30.0, 1.0 + attempt))
            continue

    return ""


def _normalize_ohlcv(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])

    out = df.copy()
    out.columns = [str(c).strip().lower() for c in out.columns]
    required = ["open", "high", "low", "close", "volume"]
    if any(col not in out.columns for col in required):
        return pd.DataFrame(columns=required)

    out = out[required]
    for col in required:
        out[col] = pd.to_numeric(out[col], errors="coerce")

    out = out.dropna(subset=required)

    if isinstance(out.index, pd.DatetimeIndex):
        if out.index.tz is not None:
            out.index = out.index.tz_convert(NY_TZ).tz_localize(None)
        else:
            out.index = out.index.tz_localize(None)

    return out.sort_index()


def _load_pre_earnings_snapshot(
    daily_df: pd.DataFrame,
    earnings_date: date,
) -> tuple[pd.DataFrame | None, date]:
    if daily_df.empty:
        return None, earnings_date

    pre = daily_df[daily_df.index.date < earnings_date].copy()
    if pre.empty:
        return None, earnings_date

    snapshot_candidates = pre.tail(5)
    snapshot_end = snapshot_candidates.index[-1]
    snapshot_date = snapshot_end.date()

    if snapshot_date < earnings_date - timedelta(days=10):
        return None, earnings_date

    snapshot_df = pre.loc[:snapshot_end].copy()
    return snapshot_df, snapshot_date


def _score_technical(snapshot_df: pd.DataFrame) -> tuple[int, dict[str, Any]]:
    close = snapshot_df["close"].astype(float)
    high = snapshot_df["high"].astype(float)
    low = snapshot_df["low"].astype(float)
    volume = snapshot_df["volume"].astype(float)

    price = float(close.iloc[-1])
    sma20 = float(close.rolling(20).mean().iloc[-1]) if len(close) >= 20 else math.nan
    sma50 = float(close.rolling(50).mean().iloc[-1]) if len(close) >= 50 else math.nan
    sma200 = float(close.rolling(200).mean().iloc[-1]) if len(close) >= 200 else math.nan

    trend_score = 0
    if _all_finite(price, sma20, sma50, sma200):
        if price > sma20 > sma50 > sma200:
            trend_score = 10
        elif price > sma50 and price < sma20:
            trend_score = 5
        elif price < sma200:
            trend_score = 0

    vcp = _detect_vcp(close)
    bb_squeeze = _detect_bb_inside_keltner(close, high, low)
    compression_score = 10 if vcp else (5 if bb_squeeze else 0)

    vol30 = float(volume.tail(30).mean()) if len(volume) >= 30 else math.nan
    vol3 = float(volume.tail(3).mean()) if len(volume) >= 3 else math.nan
    vol_ratio = (vol3 / vol30) if _all_finite(vol3, vol30) and vol30 > 0 else math.nan

    price_tight = False
    if len(close) >= 3:
        recent = close.tail(3)
        denom = float(recent.mean())
        if denom > 0:
            price_tight = ((float(recent.max()) - float(recent.min())) / denom) <= 0.03

    volume_score = 0
    if math.isfinite(vol_ratio):
        if vol_ratio < 0.65:
            volume_score = 10
        elif 0.85 <= vol_ratio <= 1.15 and price_tight:
            volume_score = 5

        close_change_3d = 1.0
        if len(close) >= 3 and close.iloc[-3] != 0:
            close_change_3d = abs(float(close.iloc[-1] / close.iloc[-3] - 1.0))

        if vol_ratio > 1.5 and close_change_3d < 0.02:
            volume_score -= 5

    technical_score = max(0, min(30, int(round(trend_score + compression_score + volume_score))))

    return technical_score, {
        "vcp": vcp,
        "bb_squeeze": bb_squeeze,
        "trend_score": trend_score,
        "compression_score": compression_score,
        "volume_score": volume_score,
    }


def _score_operational(yahoo_bundle: dict[str, Any], snapshot_date: date) -> int:
    ts_map = yahoo_bundle.get("fundamentals_timeseries", {})
    if not isinstance(ts_map, dict) or not ts_map:
        return 0

    revenue_series = _series_values(
        ts_map=ts_map,
        type_keys=["quarterlyTotalRevenue", "annualTotalRevenue", "trailingTotalRevenue"],
        as_of=snapshot_date,
    )
    if len(revenue_series) < 2:
        return 0

    revenue = [v for _, v in revenue_series]

    growth_score = 0
    if len(revenue) >= 3:
        seq_recent = _pct_change(revenue[0], revenue[1])
        seq_prev = _pct_change(revenue[1], revenue[2])

        yoy_growth = 0.0
        if len(revenue) >= 5:
            yoy_growth = _pct_change(revenue[0], revenue[4])

        if seq_recent > seq_prev:
            growth_score = 15
        elif abs(seq_recent) <= 0.01 and yoy_growth > 0.20:
            growth_score = 7
        elif seq_recent < 0:
            growth_score = 0

    profitability_score = 0
    op_income_series = _series_map(
        ts_map=ts_map,
        type_keys=["quarterlyOperatingIncome", "annualOperatingIncome", "trailingOperatingIncome"],
        as_of=snapshot_date,
    )
    gross_profit_series = _series_map(
        ts_map=ts_map,
        type_keys=["quarterlyGrossProfit", "annualGrossProfit", "trailingGrossProfit"],
        as_of=snapshot_date,
    )
    revenue_map = dict(revenue_series)

    op_margin: list[float] = []
    for d, op_income in sorted(op_income_series.items(), reverse=True):
        rev = revenue_map.get(d)
        if rev is None or rev == 0:
            continue
        op_margin.append(float(op_income) / float(rev))

    gross_margin: list[float] = []
    for d, gp in sorted(gross_profit_series.items(), reverse=True):
        rev = revenue_map.get(d)
        if rev is None or rev == 0:
            continue
        gross_margin.append(float(gp) / float(rev))

    if len(op_margin) >= 3 and op_margin[0] > op_margin[1] > op_margin[2]:
        profitability_score = 15
    elif len(gross_margin) >= 2 and abs(gross_margin[0] - gross_margin[1]) <= 0.005:
        profitability_score = 7

    if len(op_margin) >= 2 and len(revenue) >= 2 and op_margin[0] < op_margin[1] and revenue[0] > revenue[1]:
        profitability_score -= 10

    return max(-10, min(30, int(round(growth_score + profitability_score))))


def _score_revision(yahoo_bundle: dict[str, Any], snapshot_date: date) -> int:
    ts_map = yahoo_bundle.get("fundamentals_timeseries", {})
    if not isinstance(ts_map, dict) or not ts_map:
        return 0

    # Proxy for revision momentum: normalized EPS trend from fundamentals timeseries.
    eps_series = _series_values(
        ts_map=ts_map,
        type_keys=[
            "quarterlyNormalizedDilutedEPS",
            "quarterlyDilutedEPS",
            "trailingNormalizedDilutedEPS",
            "annualNormalizedDilutedEPS",
            "annualDilutedEPS",
        ],
        as_of=snapshot_date,
    )
    if len(eps_series) < 2:
        return 0

    current_eps = eps_series[0][1]
    prev_eps = eps_series[1][1]
    if prev_eps == 0:
        return 0

    pct_change = (current_eps - prev_eps) / abs(prev_eps) * 100.0
    if pct_change > 3.0:
        return 20
    if 1.0 <= pct_change <= 3.0:
        return 10
    if pct_change < 0:
        return -20
    return 0


def _score_alpha_positioning(yahoo_bundle: dict[str, Any], tech_ctx: TechnicalContext) -> int:
    institutional_score = _score_institutional_flow()

    position_score = 0
    short_interest_pct = _extract_short_interest_percent(yahoo_bundle.get("key_statistics_short_interest", {}))
    if short_interest_pct is not None and short_interest_pct > 10.0 and tech_ctx.vcp:
        position_score = 10
    elif _has_bullish_call_skew(yahoo_bundle.get("options", {})):
        position_score = 5

    return max(0, min(20, int(round(institutional_score + position_score))))


def _score_institutional_flow() -> int:
    # fundamentals-timeseries endpoint does not expose institutional flow directly.
    return 0


def _evaluate_red_flags(
    yahoo_bundle: dict[str, Any],
    snapshot_df: pd.DataFrame,
    earnings_date: date,
) -> tuple[list[str], int]:
    red_flags: list[str] = []
    deduction = 0

    if _has_bearish_divergence(snapshot_df):
        red_flags.append("Bearish divergence")
        deduction -= 15

    if _has_negative_operating_cashflow(
        ts_map=yahoo_bundle.get("fundamentals_timeseries", {}),
        as_of=snapshot_df.index[-1].date(),
    ):
        red_flags.append("Negative operating cash flow")
        deduction -= 10

    close = float(snapshot_df["close"].iloc[-1])
    high_52w = float(snapshot_df["high"].tail(252).max()) if len(snapshot_df) >= 1 else close
    if high_52w > 0 and close < high_52w * 0.8:
        red_flags.append("Overhead supply (>20% below 52-week high)")
        deduction -= 10

    if _has_guidance_miss_proxy(
        ts_map=yahoo_bundle.get("fundamentals_timeseries", {}),
        earnings_date=earnings_date,
    ):
        red_flags.append("Guidance miss history proxy (>=2 negative surprises in last 4)")
        deduction -= 10

    return red_flags, deduction


def _has_bearish_divergence(snapshot_df: pd.DataFrame) -> bool:
    close = snapshot_df["close"].astype(float)
    if len(close) < 40:
        return False

    rsi = _rsi(close, 14)
    macd_hist = _macd_hist(close)

    prior = close.iloc[-30:-10]
    recent = close.iloc[-10:]
    if prior.empty or recent.empty:
        return False

    prior_idx = prior.idxmax()
    recent_idx = recent.idxmax()

    prior_close = float(close.loc[prior_idx])
    recent_close = float(close.loc[recent_idx])
    if recent_close <= prior_close:
        return False

    prior_rsi = float(rsi.loc[prior_idx]) if not pd.isna(rsi.loc[prior_idx]) else math.nan
    recent_rsi = float(rsi.loc[recent_idx]) if not pd.isna(rsi.loc[recent_idx]) else math.nan
    prior_macd = float(macd_hist.loc[prior_idx]) if not pd.isna(macd_hist.loc[prior_idx]) else math.nan
    recent_macd = float(macd_hist.loc[recent_idx]) if not pd.isna(macd_hist.loc[recent_idx]) else math.nan

    rsi_divergence = _all_finite(prior_rsi, recent_rsi) and recent_rsi < prior_rsi
    macd_divergence = _all_finite(prior_macd, recent_macd) and recent_macd < prior_macd

    return rsi_divergence or macd_divergence


def _has_negative_operating_cashflow(ts_map: dict[str, Any], as_of: date) -> bool:
    ocf_series = _series_values(
        ts_map=ts_map if isinstance(ts_map, dict) else {},
        type_keys=[
            "quarterlyOperatingCashFlow",
            "quarterlyTotalCashFromOperatingActivities",
            "annualOperatingCashFlow",
            "annualTotalCashFromOperatingActivities",
            "trailingOperatingCashFlow",
            "trailingTotalCashFromOperatingActivities",
        ],
        as_of=as_of,
    )
    if not ocf_series:
        return False
    latest_ocf = ocf_series[0][1]
    return latest_ocf < 0


def _has_guidance_miss_proxy(ts_map: dict[str, Any], earnings_date: date) -> bool:
    eps_series = _series_values(
        ts_map=ts_map if isinstance(ts_map, dict) else {},
        type_keys=["quarterlyDilutedEPS", "quarterlyNormalizedDilutedEPS"],
        as_of=earnings_date - timedelta(days=1),
    )
    if len(eps_series) < 4:
        return False

    last4_vals = [v for _, v in eps_series[:4]]
    declines = 0
    for i in range(1, len(last4_vals)):
        if last4_vals[i - 1] < last4_vals[i]:
            declines += 1
    return declines >= 2


def _detect_vcp(close: pd.Series) -> bool:
    if len(close) < 30:
        return False

    tail = close.tail(30)
    segments = [tail.iloc[i : i + 10] for i in (0, 10, 20)]
    ranges: list[float] = []
    for seg in segments:
        denom = float(seg.mean())
        if denom <= 0:
            return False
        ranges.append((float(seg.max()) - float(seg.min())) / denom)

    return ranges[0] > ranges[1] > ranges[2] and ranges[2] < 0.05


def _detect_bb_inside_keltner(close: pd.Series, high: pd.Series, low: pd.Series) -> bool:
    if len(close) < 20:
        return False

    ma20 = close.rolling(20).mean()
    std20 = close.rolling(20).std()

    bb_upper = ma20 + 2.0 * std20
    bb_lower = ma20 - 2.0 * std20

    tr = pd.concat(
        [
            high - low,
            (high - close.shift(1)).abs(),
            (low - close.shift(1)).abs(),
        ],
        axis=1,
    ).max(axis=1)
    atr20 = tr.rolling(20).mean()

    ema20 = close.ewm(span=20, adjust=False).mean()
    kc_upper = ema20 + 1.5 * atr20
    kc_lower = ema20 - 1.5 * atr20

    if any(pd.isna(x) for x in [bb_upper.iloc[-1], bb_lower.iloc[-1], kc_upper.iloc[-1], kc_lower.iloc[-1]]):
        return False

    return bool(bb_upper.iloc[-1] < kc_upper.iloc[-1] and bb_lower.iloc[-1] > kc_lower.iloc[-1])


def _rsi(close: pd.Series, window: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = gain.ewm(alpha=1.0 / window, adjust=False, min_periods=window).mean()
    avg_loss = loss.ewm(alpha=1.0 / window, adjust=False, min_periods=window).mean()
    rs = avg_gain / avg_loss.replace(0.0, pd.NA)
    return 100.0 - (100.0 / (1.0 + rs))


def _macd_hist(close: pd.Series) -> pd.Series:
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    macd = ema12 - ema26
    signal = macd.ewm(span=9, adjust=False).mean()
    return macd - signal


def _map_tier(score: int) -> tuple[str, str]:
    if 85 <= score <= 100:
        return "Tier 1", "High probability"
    if 70 <= score <= 84:
        return "Tier 2", "Medium"
    if 40 <= score <= 69:
        return "Tier 3", "Low"
    return "Tier 4", "Avoid"


def _parse_timeseries_result(data: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """Parse Yahoo fundamentals-timeseries response into keyed series lists."""
    result_list = ((data.get("timeseries", {}) or {}).get("result", []) if isinstance(data, dict) else [])
    if not isinstance(result_list, list):
        return {}

    parsed: dict[str, list[dict[str, Any]]] = {}
    for item in result_list:
        if not isinstance(item, dict):
            continue
        for key, values in item.items():
            if key in {"meta", "timestamp"}:
                continue
            if not isinstance(values, list):
                continue
            series_entries: list[dict[str, Any]] = []
            for row in values:
                if not isinstance(row, dict):
                    continue
                as_of = _as_of_date(row.get("asOfDate"))
                val = _to_float(_raw(row.get("reportedValue")))
                if as_of is None or val is None:
                    continue
                series_entries.append({"as_of_date": as_of, "value": val})
            if series_entries:
                series_entries.sort(key=lambda r: r["as_of_date"], reverse=True)
                parsed[key] = series_entries
    return parsed


def _series_values(
    ts_map: dict[str, list[dict[str, Any]]],
    type_keys: list[str],
    as_of: date | None = None,
) -> list[tuple[date, float]]:
    for key in type_keys:
        rows = ts_map.get(key, [])
        if not isinstance(rows, list):
            continue
        parsed: list[tuple[date, float]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            d = row.get("as_of_date")
            v = row.get("value")
            if not isinstance(d, date) or not isinstance(v, (int, float)):
                continue
            if as_of is not None and d > as_of:
                continue
            parsed.append((d, float(v)))
        if parsed:
            parsed.sort(key=lambda item: item[0], reverse=True)
            return parsed
    return []


def _series_map(
    ts_map: dict[str, list[dict[str, Any]]],
    type_keys: list[str],
    as_of: date | None = None,
) -> dict[date, float]:
    return {d: v for d, v in _series_values(ts_map=ts_map, type_keys=type_keys, as_of=as_of)}


def _extract_short_interest_percent(quote_snapshot: dict[str, Any]) -> float | None:
    if not isinstance(quote_snapshot, dict):
        return None

    float_pct = _to_float(quote_snapshot.get("short_percent_float"))
    shares_out_pct = _to_float(quote_snapshot.get("short_percent_shares_outstanding"))

    if float_pct is not None:
        return float_pct
    if shares_out_pct is not None:
        return shares_out_pct
    return None


def _has_bullish_call_skew(options_mod: dict[str, Any]) -> bool:
    calls = options_mod.get("calls", [])
    puts = options_mod.get("puts", [])
    if not isinstance(calls, list) or not isinstance(puts, list):
        return False

    call_iv = [_to_float(_raw(item.get("impliedVolatility"))) for item in calls if isinstance(item, dict)]
    put_iv = [_to_float(_raw(item.get("impliedVolatility"))) for item in puts if isinstance(item, dict)]

    call_iv = [v for v in call_iv if v is not None]
    put_iv = [v for v in put_iv if v is not None]

    if not call_iv or not put_iv:
        return False

    return float(median(call_iv)) > float(median(put_iv)) * 1.05


def _parse_options_html(html_text: str) -> dict[str, list[dict[str, float]]]:
    """Parse Yahoo options HTML using requested table/row XPath selectors."""
    try:
        root = lxml_html.fromstring(html_text)
    except Exception:
        return {"calls": [], "puts": []}

    tables = root.xpath("//table[@class='yf-1oeiges']")
    call_table = tables[0] if len(tables) >= 1 else None
    put_table = tables[1] if len(tables) >= 2 else None

    call_rows = call_table.xpath("./tbody/tr") if call_table is not None else []
    put_rows = put_table.xpath("./tbody/tr") if put_table is not None else []

    calls = _extract_option_rows_iv(call_rows)
    puts = _extract_option_rows_iv(put_rows)
    return {"calls": calls, "puts": puts}


def _parse_key_statistics_short_interest(html_text: str) -> dict[str, float]:
    """Parse short-interest metrics from Yahoo key-statistics page via XPath labels."""
    try:
        root = lxml_html.fromstring(html_text)
    except Exception:
        return {}

    labels = root.xpath("//td[@class='label yf-vaowmx']")
    if not labels:
        labels = root.xpath("//td[contains(@class,'label') and contains(@class,'yf-vaowmx')]")

    short_float: float | None = None
    short_shares_out: float | None = None

    for label_td in labels:
        label_text = " ".join(t.strip() for t in label_td.xpath(".//text()") if isinstance(t, str)).strip()
        if not label_text:
            continue

        value_text = _extract_second_td_text(label_td)
        if not value_text:
            continue

        if "Short % of Float" in label_text:
            short_float = _parse_percent_value(value_text)
        elif "Short % of Shares Outstanding" in label_text:
            short_shares_out = _parse_percent_value(value_text)

    result: dict[str, float] = {}
    if short_float is not None:
        result["short_percent_float"] = short_float
    if short_shares_out is not None:
        result["short_percent_shares_outstanding"] = short_shares_out
    return result


def _extract_second_td_text(label_td: Any) -> str:
    """Given label <td>, return the sibling value cell text (2nd td in same row)."""
    parent = label_td.getparent()
    if parent is None:
        return ""

    if str(getattr(parent, "tag", "")).lower() == "tr":
        tds = parent.xpath("./td")
        if len(tds) >= 2:
            return " ".join(t.strip() for t in tds[1].xpath(".//text()") if isinstance(t, str)).strip()

    following = label_td.xpath("./following-sibling::td[1]")
    if following:
        return " ".join(t.strip() for t in following[0].xpath(".//text()") if isinstance(t, str)).strip()
    return ""


def _parse_percent_value(raw_text: str) -> float | None:
    txt = (raw_text or "").strip().replace("%", "").replace(",", "")
    if not txt:
        return None
    return _to_float(txt)


def _extract_option_rows_iv(rows: list[Any]) -> list[dict[str, float]]:
    extracted: list[dict[str, float]] = []
    for row in rows:
        if row is None:
            continue

        tds = row.xpath("./td")
        if not tds:
            continue
        row: lxml_html.HtmlElement = row
        p = row_xpath = row.getroottree().getpath(row)
        # Strike: prefer bold cell in row, fallback to column 3.
        strike_texts = row.xpath(f'{row_xpath}//a[@data-ylk="elm:qte;elmt:strike;itc:0;sec:qsp-options"]/text()')
        strike_text = "".join(t.strip() for t in strike_texts if isinstance(t, str)).strip()
        if not strike_text and len(tds) >= 3:
            strike_text = "".join(tds[2].xpath(".//text()")).strip()

        iv_texts = row.xpath(".//td[contains(@aria-label,'Implied Volatility')]//text()")
        iv_texts = row.xpath(f"{row_xpath}//td[contains(normalize-space(.), '%')][2]//text()")
        iv_text = "".join(t.strip() for t in iv_texts if isinstance(t, str)).strip()

        if not iv_text:
            # Common Yahoo options table position (11th col), then user-suggested 8th, then last.
            if len(tds) >= 11:
                iv_text = "".join(tds[10].xpath(".//text()")).strip()
            elif len(tds) >= 8:
                iv_text = "".join(tds[7].xpath(".//text()")).strip()
            else:
                iv_text = "".join(tds[-1].xpath(".//text()")).strip()

        iv_value = _parse_iv_value(iv_text)
        if iv_value is None:
            continue

        row_data: dict[str, float] = {"impliedVolatility": iv_value}
        strike_value = _to_float(strike_text.replace(",", ""))
        if strike_value is not None:
            row_data["strike"] = strike_value
        extracted.append(row_data)

    return extracted


def _parse_iv_value(raw_text: str) -> float | None:
    txt = (raw_text or "").strip().replace("%", "")
    if not txt:
        return None

    val = _to_float(txt)
    if val is None:
        return None

    # HTML table typically displays percent values (e.g., "42.11%").
    if val > 2.0:
        return val / 100.0
    return val


def _normalize_tickers(tickers: list[str]) -> list[str]:
    return sorted({str(t).strip().upper() for t in tickers if str(t).strip()})


def _pct_change(current: float, previous: float) -> float:
    if previous == 0:
        return 0.0
    return (float(current) - float(previous)) / abs(float(previous))


def _build_margin_series(numerator: list[float], denominator: list[float]) -> list[float]:
    margins: list[float] = []
    for num, den in zip(numerator, denominator):
        if den == 0:
            continue
        margins.append(float(num) / float(den))
    return margins


def _run_coro_sync(coro: Any) -> Any:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    result: dict[str, Any] = {}
    error: dict[str, BaseException] = {}

    def _runner() -> None:
        try:
            result["value"] = asyncio.run(coro)
        except BaseException as exc:
            error["exc"] = exc

    thread = threading.Thread(target=_runner, daemon=True)
    thread.start()
    thread.join()

    if "exc" in error:
        raise error["exc"]
    return result.get("value")


def _raw(value: Any) -> Any:
    if isinstance(value, dict) and "raw" in value:
        return value.get("raw")
    return value


def _to_float(value: Any) -> float | None:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(f) or math.isinf(f):
        return None
    return f


def _epoch_to_date(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, str):
        parsed = pd.to_datetime(value, errors="coerce")
        if pd.isna(parsed):
            return None
        return parsed.date()

    try:
        epoch = int(value)
    except (TypeError, ValueError):
        return None

    return datetime.fromtimestamp(epoch, tz=timezone.utc).astimezone(NY_TZ).date()


def _as_of_date(value: Any) -> date | None:
    parsed = pd.to_datetime(value, errors="coerce")
    if pd.isna(parsed):
        return None
    if isinstance(parsed, pd.Timestamp):
        if parsed.tzinfo is not None:
            parsed = parsed.tz_convert(NY_TZ).tz_localize(None)
        return parsed.date()
    return None


def _all_finite(*values: float) -> bool:
    return all(math.isfinite(v) for v in values)


def _first_or_none(items: Any) -> Any:
    if isinstance(items, list) and items:
        return items[0]
    return None


def _get_retry_after_seconds(response: httpx.Response) -> float | None:
    header = response.headers.get("Retry-After")
    if not header:
        return None

    try:
        return max(0.0, float(header))
    except ValueError:
        pass

    try:
        dt = parsedate_to_datetime(header)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        return max(0.0, (dt - now).total_seconds())
    except Exception:
        return None


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="TORA earnings breakout scorer")
    parser.add_argument(
        "--tickers",
        type=str,
        default="",
        help="Optional comma-separated whitelist. Empty = all earnings-today names.",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    # input_tickers = ["NVDA"] _normalize_tickers(args.tickers.split(",")) if args.tickers.strip() else []
    input_tickers = ["LMT"]
    scores = get_today_earnings_scores(input_tickers)
    print(json.dumps(scores, indent=2))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
    main()
