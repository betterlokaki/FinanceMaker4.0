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
from publishers.abstracts.i_broker import IBroker
from pullers.market.abstracts.i_market_provider import IMarketProvider
from pullers.realtime.abstracts.i_realtime_provider import IRealtimeProvider
from strategy.abstracts.realtime_trading_base import RealTimeTradingBase

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
    ) -> None:
        super().__init__(realtime_provider, broker=broker)
        self._market_provider = market_provider
        self._broker = broker
        self._cash_allocation_pct = min(1.0, max(0.0, float(cash_allocation_pct)))
        self._stop_loss_pct = max(0.0, float(stop_loss_pct))
        self._take_profit_pct = max(0.0, float(take_profit_pct))
        self._ema_fast_period = max(2, int(ema_fast_period))
        self._ema_slow_period = max(2, int(ema_slow_period))
        self._rsi_period = max(2, int(rsi_period))
        self._min_rsi = float(min_rsi)
        self._scan_concurrency = max(1, int(scan_concurrency))
        self._now_provider = now_provider or (lambda: datetime.now(UTC))

        self._active_signals: dict[str, PullbackSignal] = {}
        self._submitted_today: set[tuple[str, date]] = set()
        self._reserved_cash: dict[str, float] = {}
        self._order_locks: dict[str, asyncio.Lock] = {}

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
        self._submitted_today.clear()
        self._reserved_cash.clear()

        signals = await self.scan_signals()
        self._active_signals = {signal.ticker: signal for signal in signals}
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
        as_of = self._ensure_utc(self._now_provider())
        start_time = as_of - timedelta(days=self.HISTORY_LOOKBACK_DAYS)
        semaphore = asyncio.Semaphore(self._scan_concurrency)

        async def _bounded_scan(ticker: str) -> PullbackSignal | None:
            async with semaphore:
                return await self._scan_ticker(
                    ticker=ticker,
                    start_time=start_time,
                    end_time=as_of,
                    as_of=as_of,
                )

        results = await asyncio.gather(
            *(_bounded_scan(ticker) for ticker in self.PULLBACK_TICKERS),
            return_exceptions=True,
        )

        signals: list[PullbackSignal] = []
        for ticker, result in zip(self.PULLBACK_TICKERS, results):
            if isinstance(result, Exception):
                logger.warning("Pullback scan failed for %s: %s", ticker, result)
            elif result is not None:
                signals.append(result)

        return signals

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
        return self._signal_from_daily_frame(
            ticker=ticker,
            daily_df=daily_df,
            as_of=as_of,
        )

    def _signal_from_daily_frame(
        self,
        ticker: str,
        daily_df: pd.DataFrame | None,
        as_of: datetime,
    ) -> PullbackSignal | None:
        daily = _normalize_ohlcv(daily_df)
        if daily.empty:
            logger.info("Skipping %s: no daily OHLCV data", ticker)
            return None

        as_of_ny = self._ensure_utc(as_of).astimezone(NY_TZ)
        daily_ny = daily.copy()
        daily_ny.index = daily_ny.index.tz_convert(NY_TZ)
        completed = daily_ny[daily_ny.index.date < as_of_ny.date()]
        min_bars = max(self._ema_slow_period, self._rsi_period) + 2
        if len(completed) < min_bars:
            logger.info("Skipping %s: insufficient completed daily bars (%d)", ticker, len(completed))
            return None

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
            return None

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
            return None

        return PullbackSignal(
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
        signal = self._active_signals.get(ticker)
        if signal is None:
            return

        tick_time_ny = self._ensure_utc(data.time).astimezone(NY_TZ)
        if not self._is_regular_market_time(tick_time_ny):
            return

        session_date = tick_time_ny.date()
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

            quantity = self._calculate_quantity_from_cash(portfolio, signal.entry_price)
            if quantity < 1:
                logger.warning(
                    "Skipping %s: actual cash cannot buy one share after reservations",
                    ticker,
                )
                return

            request = self._build_entry_order_request(
                ticker=ticker,
                quantity=quantity,
                entry_price=signal.entry_price,
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
                "signal_date=%s order_id=%s",
                ticker,
                quantity,
                request.limit_price or 0.0,
                request.stop_loss_price or 0.0,
                request.take_profit_price or 0.0,
                signal.signal_date.isoformat(),
                response.order_id,
            )

    async def on_candle(self, ticker: str, candle: CandleStick) -> None:
        """Unused: pullback entries are submitted directly from live ticks."""
        return None

    async def shutdown(self) -> None:
        """Shutdown strategy and clear isolated runtime state."""
        await super().shutdown()
        self._active_signals.clear()
        self._submitted_today.clear()
        self._reserved_cash.clear()
        self._order_locks.clear()

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
        return OrderRequest(
            ticker=ticker.upper(),
            quantity=quantity,
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            limit_price=entry,
            stop_loss_price=round(entry * (1.0 - self._stop_loss_pct), 2),
            take_profit_price=round(entry * (1.0 + self._take_profit_pct), 2),
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
