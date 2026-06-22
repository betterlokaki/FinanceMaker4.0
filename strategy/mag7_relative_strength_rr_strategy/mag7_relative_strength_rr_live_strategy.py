"""Live Mag7 relative-strength rotation strategy using Yahoo data and Alpaca."""
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
from common.models.order import OrderSide, TimeInForce
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

logger = logging.getLogger(__name__)

UTC = timezone.utc
NY_TZ = ZoneInfo("America/New_York")
REGULAR_OPEN_TIME = time(hour=9, minute=30)
REGULAR_CLOSE_TIME = time(hour=16, minute=0)


@dataclass(frozen=True)
class RelativeStrengthSetup:
    """Completed-daily setup selected for next live-session entry."""

    ticker: str
    signal_date: date
    rank: int
    score: float
    close_price: float
    trend_ema: float
    atr: float


class Mag7RelativeStrengthRRLiveStrategy(RealTimeTradingBase):
    """Daily Mag7 cross-sectional momentum strategy with 1:2 brackets."""

    MAG7_TICKERS: tuple[str, ...] = (
        "AAPL",
        "MSFT",
        "NVDA",
        "AMZN",
        "META",
        "TSLA",
        "GOOGL",
    )
    HISTORY_LOOKBACK_DAYS = 650
    FAST_MOMENTUM_BARS = 21
    MID_MOMENTUM_BARS = 63
    SLOW_MOMENTUM_BARS = 126
    FAST_WEIGHT = 1.0
    MID_WEIGHT = 0.5
    SLOW_WEIGHT = 2.0
    TREND_EMA_PERIOD = 30
    ATR_PERIOD = 20
    ENTRY_RANK_THRESHOLD = 3
    MIN_SCORE = -0.1
    ATR_STOP_MULTIPLIER = 4.0
    MIN_STOP_PCT = 0.04
    MAX_STOP_PCT = 0.2

    def __init__(
        self,
        realtime_provider: IRealtimeProvider,
        market_provider: IMarketProvider,
        broker: IBroker,
        *,
        strategy_input: StrategyInputModel | None = None,
        scan_concurrency: int = 4,
        now_provider: Callable[[], datetime] | None = None,
    ) -> None:
        super().__init__(realtime_provider, broker=broker)
        self._market_provider = market_provider
        self._broker = broker
        self._strategy_input = strategy_input or StrategyInputModel(
            portfolio_pct_per_trade=1.0,
            risk_pct=self.MIN_STOP_PCT,
            reward_pct=self.MIN_STOP_PCT * 2.0,
        )
        self._scan_concurrency = max(1, int(scan_concurrency))
        self._now_provider = now_provider or (lambda: datetime.now(UTC))
        self._active_setups: dict[str, RelativeStrengthSetup] = {}
        self._submitted_today: set[tuple[str, date]] = set()
        self._reserved_notional: dict[str, float] = {}
        self._order_locks: dict[str, asyncio.Lock] = {}

    @property
    def active_setups(self) -> dict[str, RelativeStrengthSetup]:
        """Return currently selected completed-daily setups."""
        return dict(self._active_setups)

    async def load_tickers(self) -> list[str]:
        """Return fixed Mag7 ticker universe."""
        return list(self.MAG7_TICKERS)

    async def _before_subscribe(self) -> None:
        self._tickers = [ticker.upper() for ticker in self._tickers]
        self._active_setups.clear()
        self._submitted_today.clear()
        self._reserved_notional.clear()
        setups = await self.scan_setups()
        self._active_setups = {setup.ticker: setup for setup in setups}
        logger.info(
            "Mag7 relative-strength setups: %s",
            [
                (setup.ticker, setup.signal_date.isoformat(), setup.rank, round(setup.score, 4))
                for setup in setups
            ],
        )

    async def scan_setups(self) -> list[RelativeStrengthSetup]:
        """Fetch completed daily candles and build next-session setup list."""
        as_of = _ensure_utc(self._now_provider())
        start_time = as_of - timedelta(days=self.HISTORY_LOOKBACK_DAYS)
        semaphore = asyncio.Semaphore(self._scan_concurrency)

        async def fetch(ticker: str) -> tuple[str, pd.DataFrame | None]:
            async with semaphore:
                try:
                    frame = await self._market_provider.get_prices(
                        ticker=ticker,
                        start_time=start_time,
                        end_time=as_of,
                        period=Period.DAILY,
                    )
                    return ticker, frame
                except Exception as exc:
                    logger.warning("Daily setup scan failed for %s: %s", ticker, exc)
                    return ticker, None

        results = await asyncio.gather(*(fetch(ticker) for ticker in self.MAG7_TICKERS))
        return self.build_setups_from_daily_frames(dict(results), as_of=as_of)

    @classmethod
    def build_setups_from_daily_frames(
        cls,
        data_by_ticker: dict[str, pd.DataFrame | None],
        *,
        as_of: datetime,
    ) -> list[RelativeStrengthSetup]:
        """Build setups from completed candles only; no future/current-day rows."""
        as_of_ny = _ensure_utc(as_of).astimezone(NY_TZ)
        completed_by_ticker: dict[str, pd.DataFrame] = {}
        close_by_ticker: dict[str, pd.Series] = {}

        for ticker, raw in data_by_ticker.items():
            frame = _normalize_daily_frame(raw)
            if frame.empty:
                continue
            completed = frame[frame.index.tz_convert(NY_TZ).date < as_of_ny.date()]
            if len(completed) < cls.SLOW_MOMENTUM_BARS + 2:
                continue
            ticker_key = str(ticker).strip().upper()
            completed_by_ticker[ticker_key] = completed
            close_by_ticker[ticker_key] = completed["close"].astype(float)

        if len(close_by_ticker) < cls.ENTRY_RANK_THRESHOLD:
            return []

        close = pd.DataFrame(close_by_ticker).dropna(how="any")
        if len(close) < cls.SLOW_MOMENTUM_BARS + 2:
            return []

        score = (
            close.pct_change(cls.FAST_MOMENTUM_BARS) * cls.FAST_WEIGHT
            + close.pct_change(cls.MID_MOMENTUM_BARS) * cls.MID_WEIGHT
            + close.pct_change(cls.SLOW_MOMENTUM_BARS) * cls.SLOW_WEIGHT
        )
        rank = score.rank(axis=1, ascending=False, method="first")
        signal_ts = close.index[-1]
        setups: list[RelativeStrengthSetup] = []

        for ticker in close.columns:
            frame = completed_by_ticker[ticker].loc[:signal_ts]
            ticker_close = frame["close"].astype(float)
            trend_ema = ticker_close.ewm(
                span=cls.TREND_EMA_PERIOD,
                adjust=False,
                min_periods=1,
            ).mean()
            atr = _compute_atr(frame, cls.ATR_PERIOD)
            setup = RelativeStrengthSetup(
                ticker=ticker,
                signal_date=signal_ts.tz_convert(NY_TZ).date(),
                rank=int(rank.at[signal_ts, ticker]),
                score=float(score.at[signal_ts, ticker]),
                close_price=float(ticker_close.iloc[-1]),
                trend_ema=float(trend_ema.iloc[-1]),
                atr=float(atr.iloc[-1]),
            )
            if cls._is_valid_setup(setup):
                setups.append(setup)

        return sorted(setups, key=lambda item: item.rank)

    async def on_tick(self, data: PricingData) -> None:
        """Submit one bracket entry per selected ticker on regular-session ticks."""
        ticker = data.id.upper()
        setup = self._active_setups.get(ticker)
        if setup is None:
            return
        tick_time_ny = _ensure_utc(data.time).astimezone(NY_TZ)
        if not _is_regular_market_time(tick_time_ny):
            return
        entry_price = float(data.price)
        if not math.isfinite(entry_price) or entry_price <= 0.0:
            return
        await self._submit_entry(
            ticker=ticker,
            setup=setup,
            entry_price=entry_price,
            session_date=tick_time_ny.date(),
        )

    async def on_candle(self, ticker: str, candle: CandleStick) -> None:
        """Unused: entries are driven by completed daily setup scan plus live ticks."""
        return None

    async def shutdown(self) -> None:
        """Shutdown strategy and clear runtime state."""
        await super().shutdown()
        self._active_setups.clear()
        self._submitted_today.clear()
        self._reserved_notional.clear()
        self._order_locks.clear()

    async def _submit_entry(
        self,
        *,
        ticker: str,
        setup: RelativeStrengthSetup,
        entry_price: float,
        session_date: date,
    ) -> None:
        key = (ticker, session_date)
        if key in self._submitted_today:
            return

        lock = self._order_locks.setdefault(ticker, asyncio.Lock())
        async with lock:
            if key in self._submitted_today:
                return
            portfolio = await self._broker.get_portfolio()
            if portfolio.has_position(ticker) or portfolio.has_open_order(ticker):
                self._submitted_today.add(key)
                logger.info("Skipping %s: existing position/open order", ticker)
                return

            order_input = self._order_input_for_entry(entry_price=entry_price, atr=setup.atr)
            quantity = PositionSizer.quantity_for_entry(
                portfolio=portfolio,
                entry_price=entry_price,
                strategy_input=order_input,
                reserved_notional=sum(self._reserved_notional.values()),
            )
            if quantity < 1:
                return

            request = self._build_entry_order_request(
                ticker=ticker,
                quantity=quantity,
                entry_price=entry_price,
                strategy_input=order_input,
            )
            response = await self._broker.place_order(request)
            self._submitted_today.add(key)
            self._reserve_notional(response=response, request=request)
            await self._record_submitted_trade(
                order_request=request,
                order_response=response,
                note=f"mag7-relative-strength-rank-{setup.rank}",
            )
            logger.info(
                "Placed Mag7 relative-strength bracket for %s qty=%d entry=%.2f stop=%.2f tp=%.2f rank=%d score=%.4f",
                ticker,
                quantity,
                request.limit_price or 0.0,
                request.stop_loss_price or 0.0,
                request.take_profit_price or 0.0,
                setup.rank,
                setup.score,
            )

    def _order_input_for_entry(self, *, entry_price: float, atr: float) -> StrategyInputModel:
        risk_pct = self._risk_pct_for_entry(entry_price=entry_price, atr=atr)
        return StrategyInputModel(
            portfolio_pct_per_trade=self._strategy_input.portfolio_pct_per_trade,
            risk_pct=risk_pct,
            reward_pct=risk_pct * 2.0,
            max_notional_per_trade=self._strategy_input.max_notional_per_trade,
        )

    @classmethod
    def _risk_pct_for_entry(cls, *, entry_price: float, atr: float) -> float:
        stop_distance = max(float(atr) * cls.ATR_STOP_MULTIPLIER, float(entry_price) * cls.MIN_STOP_PCT)
        stop_distance = min(stop_distance, float(entry_price) * cls.MAX_STOP_PCT)
        return max(0.0001, stop_distance / float(entry_price))

    @staticmethod
    def _build_entry_order_request(
        *,
        ticker: str,
        quantity: int,
        entry_price: float,
        strategy_input: StrategyInputModel,
    ) -> OrderRequest:
        return OrderRequestFactory.bracket_entry(
            ticker=ticker,
            quantity=quantity,
            side=OrderSide.BUY,
            entry_price=entry_price,
            strategy_input=strategy_input,
            time_in_force=TimeInForce.GTC,
            buy_limit_rth=True,
            take_profit_rth=True,
            stop_loss_rth=False,
        )

    def _reserve_notional(self, *, response: OrderResponse, request: OrderRequest) -> None:
        reserve_key = response.order_id or f"{request.ticker}-{len(self._reserved_notional)}"
        self._reserved_notional[reserve_key] = float(request.quantity) * float(
            request.limit_price or 0.0
        )

    @classmethod
    def _is_valid_setup(cls, setup: RelativeStrengthSetup) -> bool:
        return (
            setup.rank <= cls.ENTRY_RANK_THRESHOLD
            and setup.score >= cls.MIN_SCORE
            and setup.close_price > setup.trend_ema
            and math.isfinite(setup.atr)
            and setup.atr > 0.0
        )


def _normalize_daily_frame(raw: pd.DataFrame | None) -> pd.DataFrame:
    if raw is None or raw.empty:
        return pd.DataFrame()
    frame = raw.copy()
    frame.columns = [str(column).strip().lower() for column in frame.columns]
    required = ["open", "high", "low", "close", "volume"]
    if not all(column in frame.columns for column in required):
        return pd.DataFrame()
    frame = frame[required].copy()
    for column in required:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    index = pd.to_datetime(frame.index, utc=True, errors="coerce")
    valid = ~index.isna()
    if not valid.any():
        return pd.DataFrame()
    frame = frame.loc[valid].copy()
    frame.index = index[valid]
    frame = frame.dropna(subset=required)
    return frame.sort_index()


def _compute_atr(frame: pd.DataFrame, period: int) -> pd.Series:
    previous_close = frame["close"].shift(1)
    true_range = pd.concat(
        [
            (frame["high"] - frame["low"]).abs(),
            (frame["high"] - previous_close).abs(),
            (frame["low"] - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return true_range.ewm(alpha=1.0 / max(2, int(period)), adjust=False, min_periods=period).mean()


def _ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _is_regular_market_time(tick_time_ny: datetime) -> bool:
    return REGULAR_OPEN_TIME <= tick_time_ny.time() < REGULAR_CLOSE_TIME
