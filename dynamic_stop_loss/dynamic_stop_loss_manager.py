"""Dynamic stop loss manager — pulls positions from broker, fires LIMIT SELL ORH."""
import logging

from common.models.order import OrderSide, OrderType, TimeInForce
from common.models.order_request import OrderRequest
from common.models.portfolio import Portfolio
from common.models.position import Position
from common.models.pricing_data import PricingData
from dynamic_stop_loss.interfaces.i_dynamic_stop_loss_policy import (
    IDynamicStopLossPolicy,
)
from publishers.abstracts.i_broker import IBroker

logger: logging.Logger = logging.getLogger(__name__)


class DynamicStopLossManager:
    """Monitors watched tickers and fires LIMIT SELL when trailing stop breached.

    Does NOT hold positions internally — pulls from the broker every tick.
    The broker (Interactive Brokers) is the single source of truth.

    Flow:
        watch(ticker, trailing_pct) → registers ticker for monitoring
        on_tick(data) → refresh portfolio from broker → if position exists → trail stop
        stop breached → cancel TP → LIMIT SELL
    """

    def __init__(
        self,
        broker: IBroker,
        policy: IDynamicStopLossPolicy,
        limit_sell_offset_pct: float = 0.5,
    ) -> None:
        self._broker: IBroker = broker
        self._policy: IDynamicStopLossPolicy = policy
        self._limit_sell_offset_pct: float = limit_sell_offset_pct
        self._watched_tickers: dict[str, float] = {}  # ticker → trailing_pct
        self._high_watermarks: dict[str, float] = {}
        self._triggered: set[str] = set()
        self._portfolio: Portfolio | None = None

    @property
    def portfolio(self) -> Portfolio | None:
        """Latest portfolio snapshot — refreshed every tick."""
        return self._portfolio

    async def watch(self, ticker: str, trailing_pct: float) -> None:
        """Register a ticker for stop loss monitoring."""
        ticker_upper: str = ticker.upper()
        self._watched_tickers[ticker_upper] = trailing_pct
        logger.info("👁️ Watching %s: trail=%.1f%%", ticker_upper, trailing_pct)

    async def on_tick(self, data: PricingData) -> None:
        """Process tick — refresh portfolio from broker, trail stop if position exists."""
        ticker: str = data.id.upper()
        # if ticker not in self._watched_tickers or ticker in self._triggered:
        #     return

        # Refresh portfolio from broker — single source of truth
        try:
            self._portfolio =  self._broker.portfolio
        except Exception as e:
            logger.warning("Portfolio refresh failed: %s", e)
            return

        position: Position | None = self._portfolio.get_position(ticker)

        if position is None:
            return  # Not filled yet — broker doesn't have it

        trailing_pct: float = self._watched_tickers[ticker]

        # Init or update high watermark
        if ticker not in self._high_watermarks:
            self._high_watermarks[ticker] = data.price
            logger.info(
                "✅ Position detected for %s (qty=%d, avg=$%.2f) — trailing ACTIVE",
                ticker, position.quantity, position.average_cost,
            )
        elif data.price > self._high_watermarks[ticker]:
            self._high_watermarks[ticker] = data.price

        stop_level: float = self._policy.calculate_stop_level(
            self._high_watermarks[ticker], trailing_pct,
        )

        if data.price <= stop_level:
            await self._trigger_stop_loss(ticker, position, data.price, stop_level)

    async def unwatch(self, ticker: str) -> None:
        """Stop watching a ticker."""
        ticker_upper: str = ticker.upper()
        self._watched_tickers.pop(ticker_upper, None)
        self._high_watermarks.pop(ticker_upper, None)
        self._triggered.discard(ticker_upper)

    async def shutdown(self) -> None:
        """Shutdown — clear everything."""
        self._watched_tickers.clear()
        self._high_watermarks.clear()
        self._triggered.clear()
        self._portfolio = None
        logger.info("DynamicStopLossManager shutdown complete")

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    async def _trigger_stop_loss(
        self, ticker: str, position: Position, current_price: float, stop_level: float,
    ) -> None:
        """Fire LIMIT SELL — cancel TP orders first, then place SL sell."""
        self._triggered.add(ticker)

        logger.warning(
            "🛑 STOP LOSS %s at $%.2f (stop=$%.2f, watermark=$%.2f, qty=%d)",
            ticker, current_price, stop_level,
            self._high_watermarks.get(ticker, 0.0), abs(position.quantity),
        )

        # Cancel open TP sell orders to avoid conflicts
        try:
            open_orders = await self._broker.get_open_orders()
            for order in open_orders:
                if order.ticker.upper() == ticker and order.side == OrderSide.SELL:
                    await self._broker.cancel_order(order.order_id)
                    logger.info("Cancelled TP order %s for %s", order.order_id, ticker)
        except Exception as e:
            logger.error("Error cancelling TP orders for %s: %s", ticker, e)

        # Place LIMIT SELL slightly below current price to ensure fill ORH
        sell_price: float = round(current_price * (1 - self._limit_sell_offset_pct / 100), 2)

        try:
            response = await self._broker.place_order(OrderRequest(
                ticker=ticker,
                quantity=abs(position.quantity),
                side=OrderSide.SELL,
                order_type=OrderType.LIMIT,
                limit_price=sell_price,
                time_in_force=TimeInForce.GTC,
            ))
            logger.info(
                "✅ SL LIMIT SELL for %s: ID=%s, price=$%.2f, qty=%d",
                ticker, response.order_id, sell_price, abs(position.quantity),
            )
        except Exception as e:
            logger.error("❌ Failed SL SELL for %s: %s", ticker, e, exc_info=True)

        await self.unwatch(ticker)
