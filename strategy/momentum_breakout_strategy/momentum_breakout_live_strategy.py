"""Live hard-coded momentum breakout strategy using Yahoo + Alpaca."""
from __future__ import annotations

import asyncio
import logging
import math
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from common.models.candlestick import CandleStick
from common.models.order import OrderSide, OrderType, TimeInForce
from common.models.order_request import OrderRequest
from common.models.order_response import OrderResponse
from common.models.period import Period
from common.models.portfolio import Portfolio
from common.models.pricing_data import PricingData
from publishers.abstracts.i_broker import IBroker
from pullers.market.abstracts.i_market_provider import IMarketProvider
from pullers.realtime.abstracts.i_realtime_provider import IRealtimeProvider
from strategy.abstracts.realtime_trading_base import RealTimeTradingBase

logger: logging.Logger = logging.getLogger(__name__)

UTC = timezone.utc
NY_TZ = ZoneInfo("America/New_York")
MARKET_OPEN_TIME = time(hour=9, minute=30)
OPENING_RANGE_END_TIME = time(hour=9, minute=35)
REGULAR_CLOSE_TIME = time(hour=16, minute=0)
PREMARKET_OPEN_TIME = time(hour=4, minute=0)


@dataclass(frozen=True)
class MomentumCandidate:
    """Ranked momentum scan output for one ticker."""

    ticker: str
    score: float
    one_day_return: float
    rvol: float
    distance_from_52_week_high: float
    gap_return: float
    current_price: float
    ema20: float
    ema50: float


@dataclass
class _OpeningRangeState:
    """Intraday state needed for breakout confirmation."""

    session_date: date
    opening_high: float = 0.0
    opening_low: float = 0.0
    opening_volume: int = 0
    opening_complete: bool = False
    cumulative_price_volume: float = 0.0
    cumulative_volume: int = 0

    @property
    def vwap(self) -> float:
        if self.cumulative_volume <= 0:
            return 0.0
        return self.cumulative_price_volume / self.cumulative_volume


class MomentumBreakoutLiveStrategy(RealTimeTradingBase):
    """Hard-coded momentum breakout strategy with isolated Alpaca execution."""

    MOMENTUM_TICKERS: tuple[str, ...] = (
        "NVDA",
        "AMD",
        "PLTR",
        "RKLB",
        "ASTS",
        "SMR",
        "OKLO",
        "HUT",
        "CRWV",
    )
    LIQUID_TICKERS: frozenset[str] = frozenset({"NVDA", "AMD", "PLTR"})
    DAILY_LOOKBACK_DAYS: int = 430
    INTRADAY_LOOKBACK_DAYS: int = 10
    MIN_DAILY_BARS: int = 55

    def __init__(
        self,
        realtime_provider: IRealtimeProvider,
        market_provider: IMarketProvider,
        broker: IBroker,
        *,
        cash_allocation_pct: float = 0.25,
        max_positions: int = 3,
        min_rvol: float = 2.0,
        high_proximity_pct: float = 0.03,
        liquid_stop_loss_pct: float = 0.01,
        volatile_stop_loss_pct: float = 0.02,
        reward_to_risk: float = 2.0,
        scan_concurrency: int = 4,
        now_provider: Callable[[], datetime] | None = None,
    ) -> None:
        super().__init__(realtime_provider, broker=broker)
        self._market_provider = market_provider
        self._broker = broker
        self._cash_allocation_pct = min(1.0, max(0.0, float(cash_allocation_pct)))
        self._max_positions = max(1, int(max_positions))
        self._min_rvol = max(0.0, float(min_rvol))
        self._high_proximity_pct = max(0.0, float(high_proximity_pct))
        self._liquid_stop_loss_pct = max(0.0, float(liquid_stop_loss_pct))
        self._volatile_stop_loss_pct = max(0.0, float(volatile_stop_loss_pct))
        self._reward_to_risk = max(0.0, float(reward_to_risk))
        self._scan_concurrency = max(1, int(scan_concurrency))
        self._now_provider = now_provider or (lambda: datetime.now(UTC))

        self._candidate_scores: dict[str, MomentumCandidate] = {}
        self._active_candidates: set[str] = set()
        self._opening_states: dict[str, _OpeningRangeState] = {}
        self._submitted_today: set[tuple[str, date]] = set()
        self._reserved_cash: dict[str, float] = {}
        self._order_locks: dict[str, asyncio.Lock] = {}

    async def load_tickers(self) -> list[str]:
        """Return the fixed momentum universe requested for this strategy."""
        return list(self.MOMENTUM_TICKERS)

    async def _before_subscribe(self) -> None:
        self._tickers = [ticker.upper() for ticker in self._tickers]
        self._candidate_scores.clear()
        self._active_candidates.clear()
        self._opening_states.clear()
        self._submitted_today.clear()
        self._reserved_cash.clear()

        candidates = await self.scan_candidates()
        self._candidate_scores = {candidate.ticker: candidate for candidate in candidates}
        self._active_candidates = {candidate.ticker for candidate in candidates[: self._max_positions]}
        logger.info(
            "Momentum active candidates: %s",
            [candidate.ticker for candidate in candidates[: self._max_positions]],
        )

    async def scan_candidates(self) -> list[MomentumCandidate]:
        """Scan and rank the hard-coded universe once for the trading day."""
        now = self._ensure_utc(self._now_provider())
        daily_start = now - timedelta(days=self.DAILY_LOOKBACK_DAYS)
        intraday_start = now - timedelta(days=self.INTRADAY_LOOKBACK_DAYS)
        semaphore = asyncio.Semaphore(self._scan_concurrency)

        async def _bounded_score(ticker: str) -> MomentumCandidate | None:
            async with semaphore:
                return await self._score_ticker(
                    ticker=ticker,
                    daily_start=daily_start,
                    intraday_start=intraday_start,
                    as_of=now,
                )

        results = await asyncio.gather(
            *(_bounded_score(ticker) for ticker in self.MOMENTUM_TICKERS),
            return_exceptions=True,
        )

        candidates: list[MomentumCandidate] = []
        for ticker, result in zip(self.MOMENTUM_TICKERS, results):
            if isinstance(result, Exception):
                logger.warning("Momentum scan failed for %s: %s", ticker, result)
            elif result is not None:
                candidates.append(result)

        candidates.sort(key=lambda candidate: candidate.score, reverse=True)
        logger.info(
            "Momentum scan retained %d/%d candidates: %s",
            len(candidates),
            len(self.MOMENTUM_TICKERS),
            [
                (
                    item.ticker,
                    round(item.score, 4),
                    round(item.one_day_return, 4),
                    round(item.rvol, 2),
                    round(item.distance_from_52_week_high, 4),
                )
                for item in candidates
            ],
        )
        return candidates

    async def _score_ticker(
        self,
        ticker: str,
        daily_start: datetime,
        intraday_start: datetime,
        as_of: datetime,
    ) -> MomentumCandidate | None:
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
        return self._score_ticker_from_data(
            ticker=ticker,
            daily_df=daily_df,
            intraday_df=intraday_df,
            as_of=as_of,
        )

    def _score_ticker_from_data(
        self,
        ticker: str,
        daily_df: pd.DataFrame | None,
        intraday_df: pd.DataFrame | None,
        as_of: datetime,
    ) -> MomentumCandidate | None:
        daily = _normalize_ohlcv(daily_df)
        intraday = _normalize_ohlcv(intraday_df)
        if len(daily) < self.MIN_DAILY_BARS:
            logger.info("Skipping %s: insufficient daily bars (%d)", ticker, len(daily))
            return None

        as_of_ny = self._ensure_utc(as_of).astimezone(NY_TZ)
        current_price = _latest_current_price(daily, intraday, as_of_ny)
        previous_close = _previous_daily_close(daily, as_of_ny.date())
        if current_price <= 0 or previous_close <= 0:
            logger.info(
                "Skipping %s: invalid current/previous price current=%.4f previous=%.4f",
                ticker,
                current_price,
                previous_close,
            )
            return None

        close_with_live = _close_series_with_live_price(daily, current_price, as_of_ny)
        if len(close_with_live) < 50:
            logger.info("Skipping %s: insufficient EMA bars (%d)", ticker, len(close_with_live))
            return None

        ema20 = float(close_with_live.ewm(span=20, adjust=False).mean().iloc[-1])
        ema50 = float(close_with_live.ewm(span=50, adjust=False).mean().iloc[-1])
        high_52 = float(daily["high"].tail(252).max())
        if high_52 <= 0:
            logger.info("Skipping %s: invalid 52-week high %.4f", ticker, high_52)
            return None

        one_day_return = current_price / previous_close - 1.0
        gap_return = _session_gap_return(intraday, previous_close, as_of_ny)
        distance_from_high = max(0.0, (high_52 - current_price) / high_52)
        rvol = _relative_intraday_volume(intraday, as_of_ny)
        if not math.isfinite(rvol) or rvol <= 0:
            rvol = _relative_daily_volume(daily)

        rejection_reasons = self._filter_rejection_reasons(
            current_price=current_price,
            ema20=ema20,
            ema50=ema50,
            rvol=rvol,
            distance_from_high=distance_from_high,
            one_day_return=one_day_return,
            gap_return=gap_return,
        )
        if rejection_reasons:
            logger.info(
                "Skipping %s momentum candidate: reasons=%s current=%.2f ema20=%.2f "
                "ema50=%.2f rvol=%.2f min_rvol=%.2f distance_from_52w_high=%.4f "
                "max_distance=%.4f one_day_return=%.4f gap_return=%.4f",
                ticker,
                rejection_reasons,
                current_price,
                ema20,
                ema50,
                rvol,
                self._min_rvol,
                distance_from_high,
                self._high_proximity_pct,
                one_day_return,
                gap_return,
            )
            return None

        high_factor = max(0.0, 1.0 - distance_from_high / max(self._high_proximity_pct, 0.0001))
        score = max(0.0, one_day_return) * max(0.0, rvol) * max(0.1, high_factor)
        return MomentumCandidate(
            ticker=ticker.upper(),
            score=float(score),
            one_day_return=float(one_day_return),
            rvol=float(rvol),
            distance_from_52_week_high=float(distance_from_high),
            gap_return=float(gap_return),
            current_price=float(current_price),
            ema20=float(ema20),
            ema50=float(ema50),
        )

    def _passes_filters(
        self,
        *,
        current_price: float,
        ema20: float,
        ema50: float,
        rvol: float,
        distance_from_high: float,
        one_day_return: float,
        gap_return: float,
    ) -> bool:
        return not self._filter_rejection_reasons(
            current_price=current_price,
            ema20=ema20,
            ema50=ema50,
            rvol=rvol,
            distance_from_high=distance_from_high,
            one_day_return=one_day_return,
            gap_return=gap_return,
        )

    def _filter_rejection_reasons(
        self,
        *,
        current_price: float,
        ema20: float,
        ema50: float,
        rvol: float,
        distance_from_high: float,
        one_day_return: float,
        gap_return: float,
    ) -> list[str]:
        reasons: list[str] = []
        if rvol < self._min_rvol:
            reasons.append("rvol")
        if current_price <= ema20:
            reasons.append("ema20")
        if current_price <= ema50:
            reasons.append("ema50")
        if distance_from_high > self._high_proximity_pct:
            reasons.append("52w_high_distance")
        if one_day_return <= 0.0 and gap_return <= 0.0:
            reasons.append("positive_momentum")
        return reasons

    async def on_tick(self, data: PricingData) -> None:
        """Track opening range/VWAP and submit confirmed breakout entries."""
        ticker = data.id.upper()
        if ticker not in self._active_candidates:
            return

        tick_time_ny = self._ensure_utc(data.time).astimezone(NY_TZ)
        if not self._is_regular_market_time(tick_time_ny):
            return

        session_date = tick_time_ny.date()
        key = (ticker, session_date)
        if key in self._submitted_today:
            return

        state = self._opening_states.get(ticker)
        if state is None or state.session_date != session_date:
            state = _OpeningRangeState(session_date=session_date)
            self._opening_states[ticker] = state

        if (
            tick_time_ny.time() >= OPENING_RANGE_END_TIME
            and not state.opening_complete
            and state.opening_volume == 0
        ):
            recovered_state = await self._recover_opening_range_from_history(
                ticker=ticker,
                session_date=session_date,
                as_of_ny=tick_time_ny,
            )
            if recovered_state is None:
                return
            state = recovered_state
            self._opening_states[ticker] = state

        self._update_intraday_state(state, data, tick_time_ny)
        if not state.opening_complete:
            return

        price = float(data.price)
        if price <= state.opening_high or price <= state.vwap:
            return

        await self._submit_confirmed_entry(
            ticker=ticker,
            price=price,
            session_date=session_date,
        )

    async def on_candle(self, ticker: str, candle: CandleStick) -> None:
        """Unused: this strategy confirms entries directly from live ticks."""
        return None

    async def shutdown(self) -> None:
        """Shutdown strategy and clear isolated runtime state."""
        await super().shutdown()
        self._candidate_scores.clear()
        self._active_candidates.clear()
        self._opening_states.clear()
        self._submitted_today.clear()
        self._reserved_cash.clear()
        self._order_locks.clear()

    def _update_intraday_state(
        self,
        state: _OpeningRangeState,
        data: PricingData,
        tick_time_ny: datetime,
    ) -> None:
        price = float(data.price)
        volume = max(1, int(data.last_size or 0))

        state.cumulative_price_volume += price * volume
        state.cumulative_volume += volume

        if tick_time_ny.time() < OPENING_RANGE_END_TIME:
            if state.opening_volume == 0:
                state.opening_high = price
                state.opening_low = price
            else:
                state.opening_high = max(state.opening_high, price)
                state.opening_low = min(state.opening_low, price)
            state.opening_volume += volume
            return

        state.opening_complete = state.opening_volume > 0

    async def _recover_opening_range_from_history(
        self,
        ticker: str,
        session_date: date,
        as_of_ny: datetime,
    ) -> _OpeningRangeState | None:
        """Recover the first five regular-market minutes when live ticks were late."""
        start_ny = datetime.combine(session_date, MARKET_OPEN_TIME, tzinfo=NY_TZ)
        end_ny = max(
            as_of_ny,
            datetime.combine(session_date, OPENING_RANGE_END_TIME, tzinfo=NY_TZ),
        )
        try:
            minute_df = await self._market_provider.get_prices(
                ticker=ticker,
                start_time=start_ny.astimezone(UTC),
                end_time=end_ny.astimezone(UTC),
                period=Period.MINUTE,
            )
        except Exception as exc:
            logger.warning("Could not recover opening range for %s: %s", ticker, exc)
            return None

        opening_minutes = _opening_range_minutes(_normalize_ohlcv(minute_df), session_date)
        if opening_minutes.empty:
            logger.debug("Opening range recovery unavailable for %s", ticker)
            return None

        volumes = opening_minutes["volume"].clip(lower=0)
        opening_volume = int(volumes.sum())
        if opening_volume <= 0:
            return None

        state = _OpeningRangeState(
            session_date=session_date,
            opening_high=float(opening_minutes["high"].max()),
            opening_low=float(opening_minutes["low"].min()),
            opening_volume=opening_volume,
            opening_complete=True,
            cumulative_price_volume=float((opening_minutes["close"] * volumes).sum()),
            cumulative_volume=opening_volume,
        )
        logger.info(
            "Recovered %s opening range from Yahoo minute history: high=%.2f low=%.2f vwap=%.2f",
            ticker,
            state.opening_high,
            state.opening_low,
            state.vwap,
        )
        return state

    async def _submit_confirmed_entry(
        self,
        ticker: str,
        price: float,
        session_date: date,
    ) -> None:
        lock = self._get_lock(ticker)
        async with lock:
            key = (ticker, session_date)
            if key in self._submitted_today:
                return

            portfolio = await self._broker.get_portfolio()
            if portfolio.has_position(ticker) or portfolio.has_open_order(ticker):
                self._submitted_today.add(key)
                logger.info("Skipping %s: existing position/open order", ticker)
                return

            quantity = self._calculate_quantity_from_cash(portfolio, price)
            if quantity < 1:
                logger.warning(
                    "Skipping %s: actual cash cannot buy one share after reservations",
                    ticker,
                )
                return

            request = self._build_entry_order_request(
                ticker=ticker,
                quantity=quantity,
                entry_price=price,
            )
            response = await self._broker.place_order(request)
            self._submitted_today.add(key)
            self._reserve_cash(response, ticker, session_date, request)
            await self._record_submitted_trade(
                order_request=request,
                order_response=response,
                note="momentum-breakout",
            )
            logger.info(
                "Placed momentum bracket for %s | qty=%d entry=%.2f stop=%.2f tp=%.2f order_id=%s",
                ticker,
                quantity,
                request.limit_price or 0.0,
                request.stop_loss_price or 0.0,
                request.take_profit_price or 0.0,
                response.order_id,
            )

    def _calculate_quantity_from_cash(self, portfolio: Portfolio, entry_price: float) -> int:
        cash_balance = max(0.0, float(portfolio.cash_balance))
        available_cash = max(0.0, cash_balance - sum(self._reserved_cash.values()))
        target_notional = cash_balance * self._cash_allocation_pct
        notional = min(target_notional, available_cash)
        return int(notional / max(entry_price, 0.01))

    def _build_entry_order_request(
        self,
        ticker: str,
        quantity: int,
        entry_price: float,
    ) -> OrderRequest:
        entry = round(entry_price, 2)
        stop_pct = self._stop_loss_pct_for_ticker(ticker)
        take_profit_pct = stop_pct * self._reward_to_risk
        return OrderRequest(
            ticker=ticker.upper(),
            quantity=quantity,
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            limit_price=entry,
            stop_loss_price=round(entry * (1.0 - stop_pct), 2),
            take_profit_price=round(entry * (1.0 + take_profit_pct), 2),
            time_in_force=TimeInForce.GTC,
            buy_limit_rth=True,
            take_profit_rth=True,
            stop_loss_rth=False,
        )

    def _stop_loss_pct_for_ticker(self, ticker: str) -> float:
        if ticker.upper() in self.LIQUID_TICKERS:
            return self._liquid_stop_loss_pct
        return self._volatile_stop_loss_pct

    def _reserve_cash(
        self,
        response: OrderResponse,
        ticker: str,
        session_date: date,
        request: OrderRequest,
    ) -> None:
        reserve_key = response.order_id or f"{ticker}-{session_date.isoformat()}"
        self._reserved_cash[reserve_key] = float(request.quantity) * float(request.limit_price or 0.0)

    @staticmethod
    def _is_regular_market_time(tick_time_ny: datetime) -> bool:
        return MARKET_OPEN_TIME <= tick_time_ny.time() < REGULAR_CLOSE_TIME

    def _get_lock(self, ticker: str) -> asyncio.Lock:
        lock = self._order_locks.get(ticker)
        if lock is None:
            lock = asyncio.Lock()
            self._order_locks[ticker] = lock
        return lock

    @staticmethod
    def _ensure_utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)


def _normalize_ohlcv(df: pd.DataFrame | None) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])

    frame = df.copy()
    frame.columns = [str(column).lower() for column in frame.columns]
    required = ["open", "high", "low", "close", "volume"]
    if not all(column in frame.columns for column in required):
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
    return frame.sort_index()


def _latest_current_price(
    daily: pd.DataFrame,
    intraday: pd.DataFrame,
    as_of_ny: datetime,
) -> float:
    if not intraday.empty:
        intraday_ny = intraday.copy()
        intraday_ny.index = intraday_ny.index.tz_convert(NY_TZ)
        current_day = intraday_ny[intraday_ny.index.date == as_of_ny.date()]
        if not current_day.empty:
            return float(current_day["close"].iloc[-1])
    return float(daily["close"].iloc[-1])


def _previous_daily_close(daily: pd.DataFrame, current_date: date) -> float:
    daily_ny = daily.copy()
    daily_ny.index = daily_ny.index.tz_convert(NY_TZ)
    previous_rows = daily_ny[daily_ny.index.date < current_date]
    if len(previous_rows) >= 1:
        return float(previous_rows["close"].iloc[-1])
    if len(daily_ny) >= 2:
        return float(daily_ny["close"].iloc[-2])
    return 0.0


def _session_gap_return(
    intraday: pd.DataFrame,
    previous_close: float,
    as_of_ny: datetime,
) -> float:
    if previous_close <= 0 or intraday.empty:
        return 0.0

    current_day = _session_intraday_frame(intraday, as_of_ny.date())
    if current_day.empty:
        return 0.0

    regular_session = current_day[current_day.index.time >= MARKET_OPEN_TIME]
    source = regular_session if not regular_session.empty else current_day
    session_open = float(source["open"].iloc[0])
    if session_open <= 0:
        return 0.0
    return session_open / previous_close - 1.0


def _session_intraday_frame(intraday: pd.DataFrame, session_date: date) -> pd.DataFrame:
    if intraday.empty:
        return intraday

    frame = intraday.copy()
    frame.index = frame.index.tz_convert(NY_TZ)
    return frame[frame.index.date == session_date]


def _opening_range_minutes(intraday: pd.DataFrame, session_date: date) -> pd.DataFrame:
    session = _session_intraday_frame(intraday, session_date)
    if session.empty:
        return session

    return session[
        (session.index.time >= MARKET_OPEN_TIME)
        & (session.index.time < OPENING_RANGE_END_TIME)
    ]


def _close_series_with_live_price(
    daily: pd.DataFrame,
    current_price: float,
    as_of_ny: datetime,
) -> pd.Series:
    daily_ny = daily.copy()
    daily_ny.index = daily_ny.index.tz_convert(NY_TZ)
    historical = daily_ny[daily_ny.index.date < as_of_ny.date()]["close"]
    if historical.empty:
        historical = daily_ny["close"]
    return pd.concat(
        [historical.astype(float), pd.Series([float(current_price)], index=[as_of_ny])]
    )


def _relative_intraday_volume(intraday: pd.DataFrame, as_of_ny: datetime) -> float:
    if intraday.empty:
        return 0.0

    frame = intraday.copy()
    frame.index = frame.index.tz_convert(NY_TZ)
    frame = frame.between_time(PREMARKET_OPEN_TIME, as_of_ny.time())
    if frame.empty:
        return 0.0

    frame["session_date"] = frame.index.date
    today = as_of_ny.date()
    today_volume = float(frame.loc[frame["session_date"] == today, "volume"].sum())
    prior_volumes = [
        float(group["volume"].sum())
        for session_date, group in frame.groupby("session_date")
        if session_date < today
    ]
    prior_volumes = prior_volumes[-5:]
    if today_volume <= 0 or not prior_volumes:
        return 0.0
    average_prior = float(np.mean(prior_volumes))
    if average_prior <= 0:
        return 0.0
    return today_volume / average_prior


def _relative_daily_volume(daily: pd.DataFrame) -> float:
    if len(daily) < 21:
        return 0.0
    current_volume = float(daily["volume"].iloc[-1])
    average_volume = float(daily["volume"].iloc[-21:-1].mean())
    if average_volume <= 0:
        return 0.0
    return current_volume / average_volume
