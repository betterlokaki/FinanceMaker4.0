"""Abstract base class for real-time trading strategies."""
import asyncio
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date, datetime, timezone
from email.message import EmailMessage
import smtplib
from zoneinfo import ZoneInfo

from common.helpers.market_calendar import MarketCalendar
from common.models.candlestick import CandleStick
from common.models.order_request import OrderRequest
from common.models.order_response import OrderResponse
from common.models.period import Period
from common.models.pnl_summary import PnlSummary
from common.models.pricing_data import PricingData
from common.settings import settings
from publishers.abstracts.i_broker import IBroker
from pullers.realtime.abstracts.i_realtime_provider import IRealtimeProvider
from strategy.abstracts.i_trading_strategy import ITradingStrategy

logger: logging.Logger = logging.getLogger(__name__)
NY_TZ: ZoneInfo = ZoneInfo("America/New_York")
UTC = timezone.utc

send_mails: dict[datetime, bool] = {}
@dataclass(frozen=True)
class StrategyTradeRecord:
    """Trade submission record for end-of-day reporting."""

    timestamp_utc: datetime
    ticker: str
    side: str
    quantity: int
    order_type: str
    requested_price: float | None
    order_id: str
    status: str
    note: str


class RealTimeTradingBase(ITradingStrategy, ABC):
    """Abstract base class for real-time trading strategies.
    
    Provides common functionality for strategies that trade based on
    real-time market data. Handles ticker loading, subscription management,
    tick dispatching, and candle building.
    
    Subclasses must implement:
        - load_tickers(): Return list of tickers to trade
        - on_candle(ticker, candle): Handle confirmed candles
    """

    CANDLE_TICKS: int = 1  # Number of ticks per candle

    def __init__(
        self,
        realtime_provider: IRealtimeProvider,
        broker: IBroker | None = None,
    ) -> None:
        """Initialize the real-time trading strategy.
        
        Args:
            realtime_provider: Real-time market data provider for subscriptions.
            broker: Optional broker interface used for end-of-day reporting.
        """
        self._realtime_provider: IRealtimeProvider = realtime_provider
        self._broker: IBroker | None = broker
        self._tickers: list[str] = []
        self._is_initialized: bool = False
        self._building_candles: dict[str, dict] = {}
        self._market_calendar: MarketCalendar = MarketCalendar()
        self._eod_report_task: asyncio.Task[None] | None = None
        self._last_reported_trading_day: date | None = None
        self._last_smtp_warning_trading_day: date | None = None
        self._trade_records: list[StrategyTradeRecord] = []
        self._trade_records_lock = asyncio.Lock()

    @property
    def tickers(self) -> list[str]:
        """Get the list of tickers this strategy is trading."""
        return self._tickers.copy()

    @property
    def is_initialized(self) -> bool:
        """Check if the strategy has been initialized."""
        return self._is_initialized

    async def initialize(self) -> None:
        """Initialize the strategy.
        
        Loads tickers via load_tickers() and subscribes to real-time
        market data for all loaded tickers.
        
        Raises:
            Exception: If ticker loading or subscription fails.
        """
        logger.info("Initializing %s...", self.__class__.__name__)
        
        self._tickers = await self.load_tickers()
        
        if not self._tickers:
            logger.warning("No tickers loaded for %s", self.__class__.__name__)
            self._is_initialized = True
            return

        await self._before_subscribe()
        
        logger.info("Loaded %d tickers: %s", len(self._tickers), self._tickers)
        
        await self._realtime_provider.subscribe(self._tickers, self.on_tick)
        
        self._is_initialized = True
        self._start_end_of_day_report_task()
        logger.info(
            "%s initialized and subscribed to %d tickers",
            self.__class__.__name__,
            len(self._tickers),
        )

    async def _before_subscribe(self) -> None:
        """Hook for subclasses to prepare state after ticker loading."""
        return None

    @abstractmethod
    async def load_tickers(self) -> list[str]:
        """Load tickers to trade.
        
        Subclasses must implement this to return the list of ticker
        symbols the strategy will trade.
        
        Returns:
            List of ticker symbols to subscribe to.
        """
        ...

    @abstractmethod
    async def on_candle(self, ticker: str, candle: CandleStick) -> None:
        """Handle confirmed candle.
        
        Called when a candle period completes. Subclasses implement
        their trading logic here.
        
        Args:
            ticker: The ticker symbol.
            candle: The confirmed candlestick.
        """
        ...

    async def on_tick(self, data: PricingData) -> None:
        """Handle incoming price tick and build candles.
        
        Accumulates ticks into candles. When a candle period closes,
        calls on_candle() with the confirmed candle.
        
        Args:
            data: Real-time pricing data from the subscribed ticker.
        """
        ticker: str = data.id.upper()
        
        if ticker not in self._building_candles:
            self._building_candles[ticker] = self._create_candle_state(data)
            return
        
        state: dict = self._building_candles[ticker]
        elapsed: float = (data.time - state["start_time"]).total_seconds()
        print("Tick received for %s: price=%.2f, time=%s",
                     ticker, data.price, data.time  )
        if elapsed >= self.CANDLE_TICKS:
            candle: CandleStick = self._finalize_candle(state)
            await self.on_candle(ticker, candle)
            self._building_candles[ticker] = self._create_candle_state(data)
            return
        
        self._update_candle_state(state, data)

    def _create_candle_state(self, data: PricingData) -> dict:
        """Create initial candle state from first tick."""
        return {
            "open": data.price,
            "high": data.price,
            "low": data.price,
            "close": data.price,
            "volume": data.last_size,
            "start_time": data.time,
        }

    def _update_candle_state(self, state: dict, data: PricingData) -> None:
        """Update candle state with new tick."""
        state["high"] = max(state["high"], data.price)
        state["low"] = min(state["low"], data.price)
        state["close"] = data.price
        state["volume"] += data.last_size

    def _finalize_candle(self, state: dict) -> CandleStick:
        """Create CandleStick from accumulated state."""
        return CandleStick(
            open=state["open"],
            high=state["high"],
            low=state["low"],
            close=state["close"],
            volume=state["volume"],
            time=state["start_time"],
            period=Period.MINUTE,
        )

    async def shutdown(self) -> None:
        """Shutdown the strategy gracefully.
        
        Unsubscribes from all tickers and cleans up resources.
        """
        logger.info("Shutting down %s...", self.__class__.__name__)

        await self._stop_end_of_day_report_task()
        
        if self._tickers:
            await self._realtime_provider.unsubscribe(self._tickers, self.on_tick)
        
        self._tickers = []
        self._is_initialized = False
        
        logger.info("%s shutdown complete", self.__class__.__name__)

    async def _record_submitted_trade(
        self,
        order_request: OrderRequest,
        order_response: OrderResponse,
        note: str = "",
    ) -> None:
        """Record submitted trade/order for EOD reporting."""
        requested_price = order_request.limit_price
        if requested_price is None:
            requested_price = order_request.stop_price

        record = StrategyTradeRecord(
            timestamp_utc=datetime.now(UTC),
            ticker=order_request.ticker.upper(),
            side=order_request.side.value,
            quantity=int(order_request.quantity),
            order_type=order_request.order_type.value,
            requested_price=requested_price,
            order_id=order_response.order_id,
            status=order_response.status.value,
            note=note,
        )
        async with self._trade_records_lock:
            self._trade_records.append(record)

    def _start_end_of_day_report_task(self) -> None:
        """Start background task for end-of-day email reports."""
        if self._eod_report_task is not None and not self._eod_report_task.done():
            return
        if self._broker is None:
            return
        if not settings.eod_report.enabled:
            return
        self._eod_report_task = asyncio.create_task(self._run_end_of_day_report_loop())
        logger.info("Started end-of-day report loop for %s", self.__class__.__name__)

    async def _stop_end_of_day_report_task(self) -> None:
        """Stop background task for end-of-day email reports."""
        task = self._eod_report_task
        if task is None:
            return
        if task.done():
            self._eod_report_task = None
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        self._eod_report_task = None

    async def _run_end_of_day_report_loop(self) -> None:
        """Poll clock and send EOD report once per trading day."""
        while True:
            try:
                if not send_mails.get(datetime.now().date(), False):
                    logger.info("Sending EOD report...")
                    await self._maybe_send_end_of_day_report()
                    send_mails[datetime.now().date()] = True
                    return
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error(
                    "EOD report loop error in %s: %s",
                    self.__class__.__name__,
                    exc,
                    exc_info=True,
                )
                await asyncio.sleep(30)

    async def _maybe_send_end_of_day_report(self) -> None:
        """Send report when after-hours close has passed."""
        
        now_ny = self._market_calendar.now()
        

        trading_day = now_ny.date()


        after_hours_close = self._market_calendar.get_after_hours_close(now_ny)
       

        if not self._smtp_configured():
            if self._last_smtp_warning_trading_day != trading_day:
                logger.warning(
                    "EOD report enabled but SMTP settings are incomplete for %s",
                    self.__class__.__name__,
                )
                self._last_smtp_warning_trading_day = trading_day
            return

        since_date = self._resolve_since_date(trading_day)
        summary = await self._broker.get_pnl_summary(since_date=since_date)
        todays_records = await self._trade_records_for_day(trading_day)
        subject, body = self._build_end_of_day_email(summary, todays_records)
        self._send_email(subject, body)

        self._last_reported_trading_day = trading_day
        logger.info(
            "Sent EOD strategy report for %s (%s)",
            self.__class__.__name__,
            trading_day.isoformat(),
        )

    async def _trade_records_for_day(self, trading_day: date) -> list[StrategyTradeRecord]:
        """Return trade records for the NY trading day."""
        async with self._trade_records_lock:
            return [
                record
                for record in self._trade_records
                if record.timestamp_utc.astimezone(NY_TZ).date() == trading_day
            ]

    def _resolve_since_date(self, trading_day: date) -> date:
        """Resolve configured cumulative baseline date."""
        cfg = settings.eod_report
        candidate = date(trading_day.year, cfg.since_month, cfg.since_day)
        if candidate > trading_day:
            return date(trading_day.year - 1, cfg.since_month, cfg.since_day)
        return candidate

    def _build_end_of_day_email(
        self,
        summary: PnlSummary,
        todays_records: list[StrategyTradeRecord],
    ) -> tuple[str, str]:
        """Build end-of-day report subject/body."""
        subject = f"{self.__class__.__name__} EOD Report - {summary.as_of_date.isoformat()}"

        lines = [
            f"Strategy: {self.__class__.__name__}",
            f"As of: {summary.as_of_date.isoformat()}",
            "",
            f"Today's P&L: {self._fmt_money(summary.daily_pnl, summary.currency)}",
            (
                f"P&L since {summary.since_date.isoformat()}: "
                f"{self._fmt_money(summary.pnl_since_date, summary.currency)}"
            ),
            "",
            "Trades made today:",
        ]

        if todays_records:
            for record in todays_records:
                record_time = record.timestamp_utc.astimezone(NY_TZ).strftime("%H:%M:%S")
                price = "MKT" if record.requested_price is None else f"{record.requested_price:.2f}"
                note = f" ({record.note})" if record.note else ""
                lines.append(
                    f"- {record_time} {record.ticker} {record.side} qty={record.quantity} "
                    f"type={record.order_type} px={price} id={record.order_id} "
                    f"status={record.status}{note}"
                )
        else:
            lines.append("- No trades were submitted today.")

        if summary.pnl_since_date is None:
            lines.append("")
            lines.append("Note: Could not compute cumulative P&L from IBKR performance data.")

        return subject, "\n".join(lines)

    @staticmethod
    def _fmt_money(value: float | None, currency: str) -> str:
        if value is None:
            return "N/A"
        sign = "+" if value >= 0 else "-"
        return f"{sign}{currency} {abs(value):,.2f}"

    def _smtp_configured(self) -> bool:
        cfg = settings.eod_report
        sender = cfg.sender_email or cfg.smtp_username
        return all(
            [
                bool(sender),
                bool(cfg.recipient_email),
                bool(cfg.smtp_host),
                cfg.smtp_port > 0,
                bool(cfg.smtp_username),
                bool(cfg.smtp_password),
            ]
        )

    def _send_email(self, subject: str, body: str) -> None:
        cfg = settings.eod_report
        sender = cfg.sender_email or cfg.smtp_username

        message = EmailMessage()
        message["Subject"] = subject
        message["From"] = sender
        message["To"] = cfg.recipient_email
        message.set_content(body)

        with smtplib.SMTP(cfg.smtp_host, int(cfg.smtp_port), timeout=30) as smtp:
            if cfg.use_tls:
                smtp.starttls()
            smtp.login(cfg.smtp_username, cfg.smtp_password)
            smtp.send_message(message)
