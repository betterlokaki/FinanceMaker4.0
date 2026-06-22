"""Live daily EMA pullback strategy using Yahoo market data and Alpaca."""
from __future__ import annotations

import asyncio
import logging
import math
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import pandas as pd

from common.models.candlestick import CandleStick
from common.models.order import OrderSide, OrderType, TimeInForce
from common.models.order_request import OrderRequest
from common.models.order_response import OrderResponse
from common.models.period import Period
from common.models.portfolio import Portfolio
from common.models.pricing_data import PricingData
from common.models.strategy_input import StrategyInputModel
from common.trading.order_request_factory import OrderRequestFactory
from common.trading.position_sizing import PositionSizer
from publishers.abstracts.i_broker import IBroker
from pullers.market.abstracts.i_market_provider import IMarketProvider
from pullers.realtime.abstracts.i_realtime_provider import IRealtimeProvider
from strategy.abstracts.realtime_trading_base import RealTimeTradingBase
from strategy.helpers.realtime_tick_logger import RealtimeTickLogger

logger: logging.Logger = logging.getLogger(__name__)

UTC = timezone.utc
NY_TZ = ZoneInfo("America/New_York")
MARKET_OPEN_TIME = time(hour=9, minute=30)
REGULAR_CLOSE_TIME = time(hour=16, minute=0)


@dataclass(frozen=True)
class PullbackSignal:
    """Validated daily pullback signal for one ticker."""

    ticker: str
    signal_date: date
    entry_price: float
    ema20: float
    ema50: float
    rsi: float
    open_price: float
    high_price: float
    low_price: float
    close_price: float


@dataclass(frozen=True)
class PullbackWatchContext:
    """Daily indicator context used for live pullback confirmation."""

    ticker: str
    signal_date: date
    previous_close: float
    ema20: float
    ema50: float
    rsi: float


@dataclass(frozen=True)
class _PullbackSetup:
    context: PullbackWatchContext | None
    signal: PullbackSignal | None


@dataclass
class _IntradayPullbackState:
    session_date: date
    open_price: float
    low_price: float


class PullbackTradingLiveStrategy(RealTimeTradingBase):
    """Daily swing pullback strategy with isolated Alpaca execution."""

    PULLBACK_TICKERS: tuple[str, ...] = (
        "NVDA",
        "AMD",
        "META",
        "AMZN",
        "TSLA",
        "PLTR",
        "ASTS",
        "RKLB",
        "SMR",
        "OKLO",
    )
    HISTORY_LOOKBACK_DAYS: int = 365

    def __init__(
        self,
        realtime_provider: IRealtimeProvider,
        market_provider: IMarketProvider,
        broker: IBroker,
        *,
        cash_allocation_pct: float = 0.25,
        stop_loss_pct: float = 0.015,
        take_profit_pct: float = 0.04,
        ema_fast_period: int = 20,
        ema_slow_period: int = 50,
        rsi_period: int = 14,
        min_rsi: float = 50.0,
        scan_concurrency: int = 4,
        now_provider: Callable[[], datetime] | None = None,
        strategy_input: StrategyInputModel | None = None,
    ) -> None:
        super().__init__(realtime_provider, broker=broker)
        self._market_provider = market_provider
        self._broker = broker
        self._strategy_input = strategy_input or StrategyInputModel(
            portfolio_pct_per_trade=min(1.0, max(0.0001, float(cash_allocation_pct))),
            risk_pct=max(0.0, float(stop_loss_pct)),
            reward_pct=max(0.0, float(take_profit_pct)),
        )
        self._cash_allocation_pct = self._strategy_input.portfolio_pct_per_trade
        self._stop_loss_pct = self._strategy_input.risk_pct
        self._take_profit_pct = self._strategy_input.reward_pct
        self._ema_fast_period = max(2, int(ema_fast_period))
        self._ema_slow_period = max(2, int(ema_slow_period))
        self._rsi_period = max(2, int(rsi_period))
        self._min_rsi = float(min_rsi)
        self._scan_concurrency = max(1, int(scan_concurrency))
        self._now_provider = now_provider or (lambda: datetime.now(UTC))

        self._active_signals: dict[str, PullbackSignal] = {}
        self._watch_contexts: dict[str, PullbackWatchContext] = {}
        self._intraday_states: dict[str, _IntradayPullbackState] = {}
        self._submitted_today: set[tuple[str, date]] = set()
        self._reserved_cash: dict[str, float] = {}
        self._order_locks: dict[str, asyncio.Lock] = {}
        self._tick_logger = RealtimeTickLogger()

    @property
    def active_signals(self) -> dict[str, PullbackSignal]:
        """Return currently active daily pullback signals."""
        return dict(self._active_signals)

    async def load_tickers(self) -> list[str]:
        """Return the fixed pullback trading universe."""
        return list(self.PULLBACK_TICKERS)

    async def _before_subscribe(self) -> None:
        self._tickers = [ticker.upper() for ticker in self._tickers]
        self._active_signals.clear()
        self._watch_contexts.clear()
        self._intraday_states.clear()
        self._submitted_today.clear()
        self._reserved_cash.clear()
        self._tick_logger.reset()

        setups = await self._scan_setups()
        signals = [setup.signal for setup in setups if setup.signal is not None]
        self._active_signals = {signal.ticker: signal for signal in signals}
        self._watch_contexts = {
            setup.context.ticker: setup.context
            for setup in setups
            if setup.context is not None
        }
        if not self._active_signals:
            logger.warning(
                "Pullback scan found no daily signals; listening to hard-coded live "
                "watchlist for intraday EMA20 confirmations: %s",
                sorted(self._watch_contexts),
            )
        logger.info(
            "Pullback active signals: %s",
            [
                (
                    signal.ticker,
                    signal.signal_date.isoformat(),
                    round(signal.entry_price, 2),
                    round(signal.ema20, 2),
                    round(signal.ema50, 2),
                    round(signal.rsi, 2),
                )
                for signal in signals
            ],
        )

    async def scan_signals(self) -> list[PullbackSignal]:
        """Scan all hard-coded tickers using completed daily Yahoo candles."""
        setups = await self._scan_setups()
        return [setup.signal for setup in setups if setup.signal is not None]

    async def _scan_setups(self) -> list[_PullbackSetup]:
        """Scan all tickers and keep both signal and live-watch context."""
        as_of = self._ensure_utc(self._now_provider())
        start_time = as_of - timedelta(days=self.HISTORY_LOOKBACK_DAYS)
        semaphore = asyncio.Semaphore(self._scan_concurrency)

        async def _bounded_scan(ticker: str) -> _PullbackSetup:
            async with semaphore:
                return await self._scan_ticker_setup(
                    ticker=ticker,
                    start_time=start_time,
                    end_time=as_of,
                    as_of=as_of,
                )

        results = await asyncio.gather(
            *(_bounded_scan(ticker) for ticker in self.PULLBACK_TICKERS),
            return_exceptions=True,
        )

        setups: list[_PullbackSetup] = []
        for ticker, result in zip(self.PULLBACK_TICKERS, results):
            if isinstance(result, Exception):
                logger.warning("Pullback scan failed for %s: %s", ticker, result)
            else:
                setups.append(result)

        return setups

    async def _scan_ticker(
        self,
        ticker: str,
        start_time: datetime,
        end_time: datetime,
        as_of: datetime,
    ) -> PullbackSignal | None:
        daily_df = await self._market_provider.get_prices(
            ticker=ticker,
            start_time=start_time,
            end_time=end_time,
            period=Period.DAILY,
        )
        return self._setup_from_daily_frame(
            ticker=ticker,
            daily_df=daily_df,
            as_of=as_of,
        ).signal

    async def _scan_ticker_setup(
        self,
        ticker: str,
        start_time: datetime,
        end_time: datetime,
        as_of: datetime,
    ) -> _PullbackSetup:
        daily_df = await self._market_provider.get_prices(
            ticker=ticker,
            start_time=start_time,
            end_time=end_time,
            period=Period.DAILY,
        )
        return self._setup_from_daily_frame(ticker=ticker, daily_df=daily_df, as_of=as_of)

    def _signal_from_daily_frame(
        self,
        ticker: str,
        daily_df: pd.DataFrame | None,
        as_of: datetime,
    ) -> PullbackSignal | None:
        return self._setup_from_daily_frame(
            ticker=ticker,
            daily_df=daily_df,
            as_of=as_of,
        ).signal

    def _setup_from_daily_frame(
        self,
        ticker: str,
        daily_df: pd.DataFrame | None,
        as_of: datetime,
    ) -> _PullbackSetup:
        daily = _normalize_ohlcv(daily_df)
        if daily.empty:
            logger.info("Skipping %s: no daily OHLCV data", ticker)
            return _PullbackSetup(context=None, signal=None)

        as_of_ny = self._ensure_utc(as_of).astimezone(NY_TZ)
        daily_ny = daily.copy()
        daily_ny.index = daily_ny.index.tz_convert(NY_TZ)
        completed = daily_ny[daily_ny.index.date < as_of_ny.date()]
        min_bars = max(self._ema_slow_period, self._rsi_period) + 2
        if len(completed) < min_bars:
            logger.info("Skipping %s: insufficient completed daily bars (%d)", ticker, len(completed))
            return _PullbackSetup(context=None, signal=None)

        close = completed["close"].astype(float)
        ema20 = close.ewm(span=self._ema_fast_period, adjust=False, min_periods=1).mean()
        ema50 = close.ewm(span=self._ema_slow_period, adjust=False, min_periods=1).mean()
        rsi = _compute_wilder_rsi(close=close, period=self._rsi_period)

        row = completed.iloc[-1]
        open_price = float(row["open"])
        high_price = float(row["high"])
        low_price = float(row["low"])
        close_price = float(row["close"])
        ema20_value = float(ema20.iloc[-1])
        ema50_value = float(ema50.iloc[-1])
        rsi_value = float(rsi.iloc[-1])

        if not all(
            math.isfinite(value)
            for value in (
                open_price,
                high_price,
                low_price,
                close_price,
                ema20_value,
                ema50_value,
                rsi_value,
            )
        ):
            logger.info("Skipping %s: invalid pullback indicator values", ticker)
            return _PullbackSetup(context=None, signal=None)

        context = PullbackWatchContext(
            ticker=ticker.upper(),
            signal_date=completed.index[-1].date(),
            previous_close=close_price,
            ema20=ema20_value,
            ema50=ema50_value,
            rsi=rsi_value,
        )

        rejection_reasons = self._signal_rejection_reasons(
            open_price=open_price,
            low_price=low_price,
            close_price=close_price,
            ema20=ema20_value,
            ema50=ema50_value,
            rsi=rsi_value,
        )
        if rejection_reasons:
            logger.info(
                "Skipping %s pullback signal: reasons=%s open=%.2f low=%.2f "
                "close=%.2f ema20=%.2f ema50=%.2f rsi=%.2f min_rsi=%.2f",
                ticker,
                rejection_reasons,
                open_price,
                low_price,
                close_price,
                ema20_value,
                ema50_value,
                rsi_value,
                self._min_rsi,
            )
            return _PullbackSetup(context=context, signal=None)

        signal = PullbackSignal(
            ticker=ticker.upper(),
            signal_date=completed.index[-1].date(),
            entry_price=close_price,
            ema20=ema20_value,
            ema50=ema50_value,
            rsi=rsi_value,
            open_price=open_price,
            high_price=high_price,
            low_price=low_price,
            close_price=close_price,
        )
        return _PullbackSetup(context=context, signal=signal)

    def _passes_signal_filters(
        self,
        *,
        open_price: float,
        low_price: float,
        close_price: float,
        ema20: float,
        ema50: float,
        rsi: float,
    ) -> bool:
        return not self._signal_rejection_reasons(
            open_price=open_price,
            low_price=low_price,
            close_price=close_price,
            ema20=ema20,
            ema50=ema50,
            rsi=rsi,
        )

    def _signal_rejection_reasons(
        self,
        *,
        open_price: float,
        low_price: float,
        close_price: float,
        ema20: float,
        ema50: float,
        rsi: float,
    ) -> list[str]:
        reasons: list[str] = []
        if close_price <= ema50:
            reasons.append("trend_below_ema50")
        if low_price > ema20:
            reasons.append("no_ema20_touch")
        if close_price < ema20:
            reasons.append("close_below_ema20")
        if rsi <= self._min_rsi:
            reasons.append("rsi")
        if close_price <= open_price:
            reasons.append("not_bullish_daily_close")
        return reasons

    async def on_tick(self, data: PricingData) -> None:
        """Submit one long bracket entry on the first RTH tick for active signals."""
        ticker = data.id.upper()
        tick_time_ny = self._ensure_utc(data.time).astimezone(NY_TZ)
        if not self._is_regular_market_time(tick_time_ny):
            return
        price = float(data.price)
        session_date = tick_time_ny.date()
        signal = self._active_signals.get(ticker)
        if signal is None:
            context = self._watch_contexts.get(ticker)
            state = await self._intraday_state_for_tick(
                ticker=ticker,
                price=price,
                session_date=session_date,
                tick_time_ny=tick_time_ny,
            )
            self._tick_logger.log(
                logger,
                strategy_name=self.__class__.__name__,
                data=data,
                tick_time=tick_time_ny,
                state=self._watch_state_label(context, state),
            )
            if context is None:
                return
            if not self._is_live_pullback_confirmed(context, state, price):
                return
            await self._submit_entry(
                ticker=ticker,
                entry_price=price,
                session_date=session_date,
                signal_date=context.signal_date,
                source="live-confirmed",
            )
            return

        self._tick_logger.log(
            logger,
            strategy_name=self.__class__.__name__,
            data=data,
            tick_time=tick_time_ny,
            state="active_pullback_signal",
        )

        await self._submit_entry(
            ticker=ticker,
            entry_price=signal.entry_price,
            session_date=session_date,
            signal_date=signal.signal_date,
            source="daily-signal",
        )

    async def _submit_entry(
        self,
        *,
        ticker: str,
        entry_price: float,
        session_date: date,
        signal_date: date,
        source: str,
    ) -> None:
        key = (ticker, session_date)
        if key in self._submitted_today:
            return

        lock = self._get_lock(ticker)
        async with lock:
            if key in self._submitted_today:
                return

            portfolio = await self._broker.get_portfolio()
            if portfolio.has_position(ticker) or portfolio.has_open_order(ticker):
                self._submitted_today.add(key)
                logger.info("Skipping %s: existing position/open order", ticker)
                return

            quantity = self._calculate_quantity_from_cash(portfolio, entry_price)
            if quantity < 1:
                logger.warning(
                    "Skipping %s: actual cash cannot buy one share after reservations",
                    ticker,
                )
                return

            request = self._build_entry_order_request(
                ticker=ticker,
                quantity=quantity,
                entry_price=entry_price,
            )
            response = await self._broker.place_order(request)
            self._submitted_today.add(key)
            self._reserve_cash(response, ticker, session_date, request)
            await self._record_submitted_trade(
                order_request=request,
                order_response=response,
                note="pullback-trading",
            )
            logger.info(
                "Placed pullback bracket for %s | qty=%d entry=%.2f stop=%.2f tp=%.2f "
                "signal_date=%s source=%s order_id=%s",
                ticker,
                quantity,
                request.limit_price or 0.0,
                request.stop_loss_price or 0.0,
                request.take_profit_price or 0.0,
                signal_date.isoformat(),
                source,
                response.order_id,
            )

    async def on_candle(self, ticker: str, candle: CandleStick) -> None:
        """Unused: pullback entries are submitted directly from live ticks."""
        return None

    async def shutdown(self) -> None:
        """Shutdown strategy and clear isolated runtime state."""
        await super().shutdown()
        self._active_signals.clear()
        self._watch_contexts.clear()
        self._intraday_states.clear()
        self._submitted_today.clear()
        self._reserved_cash.clear()
        self._order_locks.clear()

    def _update_intraday_state(
        self,
        ticker: str,
        price: float,
        session_date: date,
    ) -> _IntradayPullbackState:
        state = self._intraday_states.get(ticker)
        if state is None or state.session_date != session_date:
            state = _IntradayPullbackState(
                session_date=session_date,
                open_price=price,
                low_price=price,
            )
            self._intraday_states[ticker] = state
            return state

        state.low_price = min(state.low_price, price)
        return state

    async def _intraday_state_for_tick(
        self,
        *,
        ticker: str,
        price: float,
        session_date: date,
        tick_time_ny: datetime,
    ) -> _IntradayPullbackState:
        state = self._intraday_states.get(ticker)
        if state is None and tick_time_ny.time() > MARKET_OPEN_TIME:
            recovered = await self._recover_intraday_state_from_history(
                ticker=ticker,
                session_date=session_date,
                tick_time_ny=tick_time_ny,
            )
            if recovered is not None:
                self._intraday_states[ticker] = recovered

        return self._update_intraday_state(ticker, price, session_date)

    async def _recover_intraday_state_from_history(
        self,
        *,
        ticker: str,
        session_date: date,
        tick_time_ny: datetime,
    ) -> _IntradayPullbackState | None:
        start_ny = datetime.combine(session_date, MARKET_OPEN_TIME, tzinfo=NY_TZ)
        try:
            minute_df = await self._market_provider.get_prices(
                ticker=ticker,
                start_time=start_ny.astimezone(UTC),
                end_time=tick_time_ny.astimezone(UTC),
                period=Period.MINUTE,
            )
        except Exception as exc:
            logger.warning("Could not recover intraday pullback state for %s: %s", ticker, exc)
            return None

        session = _session_intraday_frame(_normalize_ohlcv(minute_df), session_date)
        session = session[
            (session.index.time >= MARKET_OPEN_TIME)
            & (session.index.time <= tick_time_ny.time())
        ]
        if session.empty:
            return None

        state = _IntradayPullbackState(
            session_date=session_date,
            open_price=float(session["open"].iloc[0]),
            low_price=float(session["low"].min()),
        )
        logger.info(
            "Recovered %s pullback intraday state from Yahoo minute history: open=%.2f low=%.2f",
            ticker,
            state.open_price,
            state.low_price,
        )
        return state

    def _is_live_pullback_confirmed(
        self,
        context: PullbackWatchContext,
        state: _IntradayPullbackState,
        price: float,
    ) -> bool:
        return (
            context.rsi > self._min_rsi
            and state.low_price <= context.ema20
            and price >= context.ema20
            and price > context.ema50
            and price > state.open_price
        )

    @staticmethod
    def _watch_state_label(
        context: PullbackWatchContext | None,
        state: _IntradayPullbackState,
    ) -> str:
        if context is None:
            return "no_pullback_context"
        return (
            "watching_live_pullback "
            f"open={state.open_price:.2f} low={state.low_price:.2f} "
            f"ema20={context.ema20:.2f} ema50={context.ema50:.2f} rsi={context.rsi:.2f}"
        )

    def _calculate_quantity_from_cash(self, portfolio: Portfolio, entry_price: float) -> int:
        return PositionSizer.quantity_for_entry(
            portfolio=portfolio,
            entry_price=entry_price,
            strategy_input=self._strategy_input,
            reserved_notional=sum(self._reserved_cash.values()),
        )

    def _build_entry_order_request(
        self,
        ticker: str,
        quantity: int,
        entry_price: float,
    ) -> OrderRequest:
        return OrderRequestFactory.bracket_entry(
            ticker=ticker,
            quantity=quantity,
            side=OrderSide.BUY,
            entry_price=entry_price,
            strategy_input=self._strategy_input,
            time_in_force=TimeInForce.GTC,
            buy_limit_rth=True,
            take_profit_rth=True,
            stop_loss_rth=False,
        )

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


def _session_intraday_frame(intraday: pd.DataFrame, session_date: date) -> pd.DataFrame:
    if intraday.empty:
        return intraday

    frame = intraday.copy()
    frame.index = frame.index.tz_convert(NY_TZ)
    return frame[frame.index.date == session_date]


def _compute_wilder_rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """Compute RSI using Wilder-style exponential smoothing."""
    close = close.astype(float)
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    alpha = 1.0 / max(2, int(period))
    avg_gain = gain.ewm(alpha=alpha, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=alpha, adjust=False, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0.0, math.nan)
    rsi = 100.0 - (100.0 / (1.0 + rs))
    rsi = rsi.mask((avg_loss == 0.0) & (avg_gain > 0.0), 100.0)
    rsi = rsi.mask((avg_gain == 0.0) & (avg_loss > 0.0), 0.0)
    return rsi.fillna(50.0).astype(float)
