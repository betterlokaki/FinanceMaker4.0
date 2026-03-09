"""Technical-only scorer for post-earnings breakout candidates.

The scorer implements a rule-based model from the research document with:
- Daily chart regime score (70 points max)
- Intraday chart regime score (30 points max)

Final score is clamped to 1-100.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from common.models.period import Period
from pullers.market.abstracts.i_market_provider import IMarketProvider

logger: logging.Logger = logging.getLogger(__name__)

NY_TZ: ZoneInfo = ZoneInfo("America/New_York")
SPY_TICKER: str = "SPY"

# Data windows
DAILY_LOOKBACK_DAYS: int = 420
INTRADAY_LOOKBACK_DAYS: int = 10

# Data quality thresholds
MIN_DAILY_BARS: int = 220
MIN_INTRADAY_BARS: int = 300
MIN_SESSION_BARS: int = 120

# Weighting requested in a 1-100 final score
DAILY_MAX_SCORE: int = 70
INTRADAY_MAX_SCORE: int = 30


@dataclass(frozen=True)
class EarningsBreakoutScore:
    """Score output for a single ticker."""

    ticker: str
    score: int
    daily_score: int
    intraday_score: int
    reasons: list[str]

    def as_output_string(self) -> str:
        """Serialize to requested list[str] format."""
        return f"{self.ticker}:{self.score}"


@dataclass(frozen=True)
class _DailyContext:
    """Daily context reused by intraday scoring."""

    latest_close: float
    atr14_pct: float


class EarningsBreakoutTechnicalScorer:
    """Rule-based technical scorer for earnings breakout candidates."""

    def __init__(self, market_provider: IMarketProvider, max_concurrency: int = 4):
        if market_provider is None:
            raise ValueError("market_provider is required")
        self._market_provider = market_provider
        self._max_concurrency = max(1, max_concurrency)

    async def score_tickers(
        self,
        tickers: list[str],
        as_of: datetime | None = None,
    ) -> list[EarningsBreakoutScore]:
        """Score tickers and return sorted scores (highest first)."""
        normalized_tickers: list[str] = sorted({t.strip().upper() for t in tickers if t and t.strip()})
        if not normalized_tickers:
            return []

        as_of_utc: datetime = _ensure_utc(as_of or datetime.now(timezone.utc))
        daily_start: datetime = as_of_utc - timedelta(days=DAILY_LOOKBACK_DAYS)
        intraday_start: datetime = as_of_utc - timedelta(days=INTRADAY_LOOKBACK_DAYS)

        spy_returns: pd.Series | None = await self._fetch_spy_returns(daily_start, as_of_utc)

        semaphore = asyncio.Semaphore(self._max_concurrency)

        async def _bounded_score(ticker: str) -> EarningsBreakoutScore | None:
            async with semaphore:
                return await self._score_single_ticker(
                    ticker=ticker,
                    daily_start=daily_start,
                    intraday_start=intraday_start,
                    as_of=as_of_utc,
                    spy_returns=spy_returns,
                )

        tasks = [_bounded_score(ticker) for ticker in normalized_tickers]
        raw_results = await asyncio.gather(*tasks, return_exceptions=True)

        results: list[EarningsBreakoutScore] = []
        for ticker, result in zip(normalized_tickers, raw_results):
            if isinstance(result, Exception):
                logger.warning("Failed scoring %s: %s", ticker, result)
                continue
            if result is not None:
                results.append(result)

        results.sort(key=lambda item: item.score, reverse=True)
        return results

    async def _fetch_spy_returns(
        self,
        start_time: datetime,
        end_time: datetime,
    ) -> pd.Series | None:
        """Fetch SPY returns used for beta estimation."""
        try:
            spy_df = await self._market_provider.get_prices(
                ticker=SPY_TICKER,
                start_time=start_time,
                end_time=end_time,
                period=Period.DAILY,
            )
            spy_df = _normalize_ohlcv(spy_df)
            if spy_df.empty:
                return None
            return spy_df["close"].pct_change().dropna()
        except Exception as exc:  # defensive: scoring should continue without beta
            logger.warning("Could not fetch %s for beta baseline: %s", SPY_TICKER, exc)
            return None

    async def _score_single_ticker(
        self,
        ticker: str,
        daily_start: datetime,
        intraday_start: datetime,
        as_of: datetime,
        spy_returns: pd.Series | None,
    ) -> EarningsBreakoutScore | None:
        daily_task = self._market_provider.get_prices(
            ticker=ticker,
            start_time=daily_start,
            end_time=as_of,
            period=Period.DAILY,
        )
        intraday_task = self._market_provider.get_prices(
            ticker=ticker,
            start_time=intraday_start,
            end_time=as_of,
            period=Period.MINUTE,
        )
        daily_df, intraday_df = await asyncio.gather(daily_task, intraday_task)

        daily_df = _normalize_ohlcv(daily_df)
        intraday_df = _normalize_ohlcv(intraday_df)

        if len(daily_df) < MIN_DAILY_BARS:
            return EarningsBreakoutScore(
                ticker=ticker,
                score=1,
                daily_score=0,
                intraday_score=0,
                reasons=["insufficient daily bars"],
            )

        daily_score, daily_reasons, daily_context = self._score_daily(daily_df, spy_returns)

        intraday_score = 0
        intraday_reasons: list[str] = []
        if len(intraday_df) >= MIN_INTRADAY_BARS:
            intraday_score, intraday_reasons = self._score_intraday(
                intraday_df,
                daily_context=daily_context,
            )

        score = max(1, min(100, int(round(daily_score + intraday_score))))
        reasons = (daily_reasons + intraday_reasons)[:6]
        if not reasons:
            reasons = ["technical setup not confirmed"]

        return EarningsBreakoutScore(
            ticker=ticker,
            score=score,
            daily_score=daily_score,
            intraday_score=intraday_score,
            reasons=reasons,
        )

    def _score_daily(
        self,
        daily_df: pd.DataFrame,
        spy_returns: pd.Series | None,
    ) -> tuple[int, list[str], _DailyContext]:
        close = daily_df["close"]
        high = daily_df["high"]
        low = daily_df["low"]
        volume = daily_df["volume"]

        close_last = float(close.iloc[-1])
        atr14_series = _atr(daily_df, 14)
        atr14 = float(atr14_series.iloc[-1]) if not atr14_series.empty else np.nan
        atr14_pct = float(atr14 / close_last) if close_last > 0 and np.isfinite(atr14) else np.nan

        score = 0
        reasons: list[str] = []

        # 1) High beta regime (10 pts)
        beta = _beta_vs_market(close, spy_returns)
        if np.isfinite(beta):
            if beta >= 1.5:
                score += 10
                reasons.append("high beta sensitivity")
            elif beta >= 1.2:
                score += 8
                reasons.append("beta above 1.2")
            elif beta >= 1.0:
                score += 6
            elif beta >= 0.8:
                score += 3

        # 2) 30-day momentum persistence (12 pts)
        if len(close) >= 22:
            momentum_30 = float(close.iloc[-1] / close.iloc[-21] - 1.0)
            if momentum_30 >= 0.20:
                score += 12
                reasons.append("strong 30-day momentum")
            elif momentum_30 >= 0.12:
                score += 10
                reasons.append("solid 30-day momentum")
            elif momentum_30 >= 0.06:
                score += 7
            elif momentum_30 >= 0.00:
                score += 3

        # 3) Proximity to 52-week high (10 pts)
        close_52 = close.tail(252)
        if not close_52.empty:
            high_52 = float(close_52.max())
            if high_52 > 0:
                dist_to_high = (high_52 - close_last) / high_52
                if dist_to_high <= 0.03:
                    score += 10
                    reasons.append("near 52-week high")
                elif dist_to_high <= 0.07:
                    score += 8
                    reasons.append("close to breakout highs")
                elif dist_to_high <= 0.12:
                    score += 5
                elif dist_to_high <= 0.18:
                    score += 2

        # 4) RSI strength-overbought context (10 pts)
        rsi14 = _rsi(close, 14)
        rsi_last = float(rsi14.iloc[-1]) if not rsi14.empty else np.nan
        if np.isfinite(rsi_last):
            if 60 <= rsi_last <= 78:
                score += 10
                reasons.append("RSI strength regime")
            elif 55 <= rsi_last < 60:
                score += 8
            elif 78 < rsi_last <= 84:
                score += 6
                reasons.append("RSI momentum extension")
            elif 50 <= rsi_last < 55:
                score += 4

        # 5) Moving-average structure / golden-cross context (12 pts)
        sma50 = close.rolling(50).mean()
        sma200 = close.rolling(200).mean()
        sma50_last = float(sma50.iloc[-1]) if len(sma50) else np.nan
        sma200_last = float(sma200.iloc[-1]) if len(sma200) else np.nan
        if np.isfinite(sma50_last) and np.isfinite(sma200_last):
            if close_last > sma50_last > sma200_last:
                score += 12
                reasons.append("bullish MA stack")
            elif sma50_last > sma200_last and close_last >= sma50_last * 0.99:
                score += 10
                reasons.append("golden-cross structure")
            elif close_last > sma200_last:
                convergence = abs(sma50_last - sma200_last) / sma200_last if sma200_last else np.inf
                if convergence <= 0.03:
                    score += 8
                    reasons.append("bullish MA convergence")
                else:
                    score += 6

        # 6) Bollinger expansion + upper-band pressure (8 pts)
        bb_mid = close.rolling(20).mean()
        bb_std = close.rolling(20).std()
        bb_upper = bb_mid + 2 * bb_std
        bb_lower = bb_mid - 2 * bb_std
        bb_width = (bb_upper - bb_lower) / bb_mid.replace(0, np.nan)
        width_now = float(bb_width.iloc[-1]) if len(bb_width) else np.nan
        width_avg = float(bb_width.tail(20).mean()) if len(bb_width) else np.nan
        bb_upper_last = float(bb_upper.iloc[-1]) if len(bb_upper) else np.nan
        bb_mid_last = float(bb_mid.iloc[-1]) if len(bb_mid) else np.nan
        if np.isfinite(width_now) and np.isfinite(width_avg) and np.isfinite(bb_upper_last):
            if width_avg > 0 and width_now >= width_avg * 1.15 and close_last >= bb_upper_last * 0.97:
                score += 8
                reasons.append("Bollinger expansion")
            elif width_avg > 0 and width_now >= width_avg * 1.05 and close_last >= bb_mid_last:
                score += 6
            elif close_last >= bb_upper_last * 0.92:
                score += 3

        # 7) Rising daily volume in advance (6 pts)
        if len(volume) >= 30:
            vol_10 = float(volume.tail(10).mean())
            vol_30 = float(volume.tail(30).mean())
            vol_ratio = vol_10 / vol_30 if vol_30 > 0 else np.nan
            last_10_close_diff = close.diff().tail(10)
            up_volume = float(volume.tail(10)[last_10_close_diff > 0].sum())
            total_10_volume = float(volume.tail(10).sum())
            up_volume_share = up_volume / total_10_volume if total_10_volume > 0 else 0.0
            if np.isfinite(vol_ratio):
                if vol_ratio >= 1.4 and up_volume_share >= 0.55:
                    score += 6
                    reasons.append("rising accumulation volume")
                elif vol_ratio >= 1.2:
                    score += 5
                elif vol_ratio >= 1.0:
                    score += 3

        # 8) Pivot-bottom reversal (2 pts)
        pivot_bonus = _pivot_bottom_bonus(daily_df)
        if pivot_bonus:
            score += pivot_bonus
            reasons.append("recent pivot-bottom reversal")

        score = max(0, min(DAILY_MAX_SCORE, score))
        context = _DailyContext(
            latest_close=close_last,
            atr14_pct=atr14_pct if np.isfinite(atr14_pct) else 0.0,
        )
        return score, reasons, context

    def _score_intraday(
        self,
        intraday_df: pd.DataFrame,
        daily_context: _DailyContext,
    ) -> tuple[int, list[str]]:
        frame = _prepare_intraday_rth(intraday_df)
        if frame.empty:
            return 0, []

        session_dates = list(frame["session_date"].drop_duplicates())
        if not session_dates:
            return 0, []

        last_session_date = session_dates[-1]
        session = frame[frame["session_date"] == last_session_date].copy()
        if session.empty:
            return 0, []

        score = 0
        reasons: list[str] = []

        session_high = float(session["high"].max())
        session_low = float(session["low"].min())
        session_close = float(session["close"].iloc[-1])
        session_range = session_high - session_low

        # 1) VWAP + short-term trend structure (10 pts)
        typical_price = (session["high"] + session["low"] + session["close"]) / 3.0
        cumulative_vol = session["volume"].replace(0, np.nan).cumsum()
        vwap = (typical_price * session["volume"]).cumsum() / cumulative_vol
        ema20 = session["close"].ewm(span=20, adjust=False).mean()
        vwap_last = float(vwap.iloc[-1]) if len(vwap) else np.nan
        ema_slope = float(ema20.iloc[-1] - ema20.iloc[-6]) if len(ema20) >= 6 else 0.0
        early_low = float(session["low"].iloc[:60].min()) if len(session) >= 60 else float(session["low"].iloc[0])
        late_low = float(session["low"].iloc[-60:].min()) if len(session) >= 60 else float(session["low"].iloc[-1])
        higher_lows = late_low > early_low * 1.003
        if np.isfinite(vwap_last):
            if session_close > vwap_last and ema_slope > 0 and higher_lows:
                score += 10
                reasons.append("intraday trend above VWAP")
            elif session_close > vwap_last and ema_slope > 0:
                score += 7
            elif session_close > vwap_last:
                score += 4

        # 2) Opening-range breakout pressure (8 pts)
        opening = session.between_time("09:30", "10:00", inclusive="left")
        if not opening.empty and len(opening) >= 15:
            or_high = float(opening["high"].max())
            or_low = float(opening["low"].min())
            broke_or = session_high > or_high * 1.001
            post_open = session.iloc[len(opening):]
            held_retest = not post_open.empty and float(post_open["low"].min()) >= or_high * 0.99
            if broke_or and session_close > or_high and held_retest:
                score += 8
                reasons.append("opening-range breakout hold")
            elif broke_or and session_close >= or_high * 0.998:
                score += 6
            elif or_high > or_low and session_close > (or_high + or_low) / 2:
                score += 2

        # 3) Intraday relative volume vs recent sessions (6 pts)
        today_volume = float(session["volume"].sum())
        prior_dates = session_dates[:-1][-5:]
        prior_volumes: list[float] = []
        for dt in prior_dates:
            day_df = frame[frame["session_date"] == dt]
            if not day_df.empty:
                prior_volumes.append(float(day_df["volume"].sum()))
        if prior_volumes:
            avg_prior_volume = float(np.mean(prior_volumes))
            if avg_prior_volume > 0:
                rel_volume = today_volume / avg_prior_volume
                if rel_volume >= 1.5:
                    score += 6
                    reasons.append("intraday relative volume surge")
                elif rel_volume >= 1.2:
                    score += 4
                elif rel_volume >= 1.0:
                    score += 2

        # 4) Range expansion + close location (6 pts)
        if session_range > 0 and daily_context.latest_close > 0:
            close_location = (session_close - session_low) / session_range
            range_pct = session_range / daily_context.latest_close
            if daily_context.atr14_pct > 0:
                range_vs_atr = range_pct / daily_context.atr14_pct
            else:
                range_vs_atr = 0.0
            if range_vs_atr >= 0.60 and close_location >= 0.75:
                score += 6
                reasons.append("range expansion with strong close")
            elif range_vs_atr >= 0.45 and close_location >= 0.60:
                score += 4
            elif close_location >= 0.60:
                score += 2

        score = max(0, min(INTRADAY_MAX_SCORE, score))
        return score, reasons


async def score_tickers_for_earnings_breakout_details(
    tickers: list[str],
    market_provider: IMarketProvider,
    as_of: datetime | None = None,
    max_concurrency: int = 4,
) -> list[EarningsBreakoutScore]:
    """Detailed scores with daily/intraday breakdown and reasons."""
    scorer = EarningsBreakoutTechnicalScorer(
        market_provider=market_provider,
        max_concurrency=max_concurrency,
    )
    return await scorer.score_tickers(tickers=tickers, as_of=as_of)


async def score_tickers_for_earnings_breakout(
    tickers: list[str],
    market_provider: IMarketProvider,
    as_of: datetime | None = None,
    max_concurrency: int = 4,
) -> list[str]:
    """Return requested output format: list[str] as 'TICKER:score'."""
    scored = await score_tickers_for_earnings_breakout_details(
        tickers=tickers,
        market_provider=market_provider,
        as_of=as_of,
        max_concurrency=max_concurrency,
    )
    return [item.as_output_string() for item in scored]


def _normalize_ohlcv(df: pd.DataFrame | None) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])

    frame = df.copy()
    frame.columns = [str(col).lower() for col in frame.columns]
    required = ["open", "high", "low", "close", "volume"]
    for column in required:
        if column not in frame.columns:
            return pd.DataFrame(columns=required)

    frame = frame[required].copy()
    for column in required:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")

    frame = frame.dropna(subset=required)
    if frame.empty:
        return pd.DataFrame(columns=required)

    index = pd.to_datetime(frame.index, utc=True, errors="coerce")
    valid = ~index.isna()
    if not valid.any():
        return pd.DataFrame(columns=required)

    frame = frame.loc[valid].copy()
    frame.index = index[valid]
    frame = frame.sort_index()
    return frame


def _prepare_intraday_rth(intraday_df: pd.DataFrame) -> pd.DataFrame:
    if intraday_df.empty:
        return intraday_df

    frame = intraday_df.copy().sort_index()
    if frame.index.tz is None:
        frame.index = frame.index.tz_localize("UTC")
    frame.index = frame.index.tz_convert(NY_TZ)
    frame = frame.between_time("09:30", "16:00")
    if frame.empty:
        return pd.DataFrame(columns=list(intraday_df.columns) + ["session_date"])

    frame["session_date"] = frame.index.date
    counts = frame.groupby("session_date").size()
    valid_days = counts[counts >= MIN_SESSION_BARS].index
    if len(valid_days) == 0:
        return pd.DataFrame(columns=list(intraday_df.columns) + ["session_date"])

    frame = frame[frame["session_date"].isin(valid_days)].copy()
    return frame


def _ensure_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high = df["high"]
    low = df["low"]
    prev_close = df["close"].shift(1)

    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()
    true_range = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    return true_range.rolling(window=period).mean()


def _rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = -delta.where(delta < 0, 0.0)
    avg_gain = gain.rolling(window=period).mean()
    avg_loss = loss.rolling(window=period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def _beta_vs_market(close: pd.Series, market_returns: pd.Series | None) -> float:
    if market_returns is None or market_returns.empty:
        return np.nan
    ticker_returns = close.pct_change().dropna()
    if ticker_returns.empty:
        return np.nan
    merged = pd.concat(
        [ticker_returns.rename("ticker"), market_returns.rename("market")],
        axis=1,
    ).dropna()
    merged = merged.tail(90)
    if len(merged) < 30:
        return np.nan
    market_variance = float(merged["market"].var())
    if market_variance <= 0:
        return np.nan
    return float(merged["ticker"].cov(merged["market"]) / market_variance)


def _pivot_bottom_bonus(df: pd.DataFrame) -> int:
    if len(df) < 20:
        return 0
    recent = df.tail(15)
    pivot_idx = recent["low"].idxmin()
    pivot_pos = list(recent.index).index(pivot_idx)
    pivot_low = float(recent.loc[pivot_idx, "low"])
    pivot_close = float(recent.loc[pivot_idx, "close"])
    latest_close = float(recent["close"].iloc[-1])
    latest_low_band = float(recent["low"].tail(5).min())
    recovery = (latest_close / pivot_close - 1.0) if pivot_close > 0 else 0.0
    if 2 <= pivot_pos <= 10 and recovery >= 0.04 and latest_low_band > pivot_low * 1.01:
        return 2
    return 0
