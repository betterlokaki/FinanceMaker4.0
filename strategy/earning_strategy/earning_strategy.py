"""Earnings-based trading strategy using AI consensus."""
import logging
from datetime import date, datetime, time

from zoneinfo import ZoneInfo

from common.cache.abstracts import ITickerCache
from common.models.candlestick import CandleStick
from common.models.order import OrderSide, OrderType, TimeInForce
from common.models.order_request import OrderRequest
from common.models.order_response import OrderResponse
from common.models.period import Period
from common.models.pricing_data import PricingData
from common.models.position import Position
from common.models.portfolio import Portfolio
from common.models.scanner_params import ScannerParams
from common.settings import AIScannerConfig, OrderParamsConfig, PortfolioAllocationConfig
from dynamic_stop_loss.interfaces.i_dynamic_stop_loss_manager import (
    IDynamicStopLossManager,
)
from publishers.abstracts.i_broker import IBroker
from pullers.realtime.abstracts.i_realtime_provider import IRealtimeProvider
from pullers.scanners.ai_scanners.earning_tommrow_ai import EarningTomorrowAI
from strategy.abstracts.realtime_trading_base import RealTimeTradingBase

logger: logging.Logger = logging.getLogger(__name__)

NY_TZ: ZoneInfo = ZoneInfo("America/New_York")
MARKET_WARMUP_TIME: time = time(9, 35)  # 9:35 AM NY (market open + 5 min)

# Strategy constants
ENTRY_OFFSET_PCT: float = 0.01  # 1% below candle low
STOP_LOSS_PCT: float = 0.04  # Synthetic 4% stop trigger
TAKE_PROFIT_PCT: float = 0.08  # 8% above entry
MIN_QUANTITY: int = 1  # Minimum shares per order


class EarningStrategy(RealTimeTradingBase):
    """Strategy that trades earnings stocks using AI consensus.
    
    Workflow:
    1. Runs EarningTomorrowAI scanner TWICE for AI consensus
    2. Subscribes to real-time price updates for those tickers
    3. Waits until 9:35 AM NY time (5 min after market open)
    4. On FIRST 5-min candle per ticker:
       - Entry = candle LOW - 1%
       - Take Profit = entry + 8%
       - Places plain extended-hours LIMIT BUY via Alpaca
    5. On each tick after fill:
       - Places plain extended-hours LIMIT SELL take profit at +8%
       - Triggers a synthetic plain extended-hours LIMIT SELL stop at -4%
    6. No duplicate orders per ticker
    """

    def __init__(
        self,
        realtime_provider: IRealtimeProvider,
        earnings_scanner: EarningTomorrowAI,
        broker: IBroker,
        ai_scanner_config: AIScannerConfig,
        ticker_cache: ITickerCache,
        portfolio_allocation_config: PortfolioAllocationConfig,
        order_params_config: OrderParamsConfig,
        notional_per_trade: float = 14_000.0,
        dynamic_stop_loss_manager: IDynamicStopLossManager | None = None,
    ) -> None:
        """Initialize the earnings strategy.
        
        Args:
            realtime_provider: Real-time market data provider.
            earnings_scanner: EarningTomorrowAI scanner (concrete, run twice).
            broker: Broker interface for placing orders.
            ai_scanner_config: AI scanner configuration with scan_passes.
            ticker_cache: Cache for storing/loading tickers across restarts.
            portfolio_allocation_config: Portfolio allocation configuration.
            order_params_config: Order parameters configuration.
            notional_per_trade: Max dollars to allocate to each entry order.
            dynamic_stop_loss_manager: Legacy dependency; unused by the Alpaca synthetic stop path.
        """
        super().__init__(realtime_provider, broker=broker)
        self._earnings_scanner: EarningTomorrowAI = earnings_scanner
        self._broker: IBroker = broker
        self._ai_scanner_config: AIScannerConfig = ai_scanner_config
        self._ticker_cache: ITickerCache = ticker_cache
        self._portfolio_allocation_config: PortfolioAllocationConfig = portfolio_allocation_config
        self._order_params_config: OrderParamsConfig = order_params_config
        self._dynamic_stop_loss_manager: IDynamicStopLossManager | None = dynamic_stop_loss_manager
        self._warmup_complete: bool = False
        self._orders_placed: set[str] = set()  # Track tickers with orders
        self._entry_prices: dict[str, float] = {}
        self._take_profit_order_ids: dict[str, str] = {}
        self._stop_triggered: set[str] = set()
        self._notional_per_trade: float = max(0.0, float(notional_per_trade))
        self._total_tickers: int = 0  # Total number of tickers to trade

    async def load_tickers(self) -> list[str]:
        """Load tickers by running AI consensus scanner multiple passes.
        
        Checks cache first to avoid expensive AI calls on process restart.
        Saves results to cache for future use.
        """
        today: date = date.today()
        
        # Check cache first
        cached_tickers: list[str] | None = self._ticker_cache.load_tickers(today)
        if cached_tickers:
            logger.info(
                "📂 Using cached tickers (%d): %s",
                len(cached_tickers),
                cached_tickers,
            )
            result: list[str] = cached_tickers
        else:
            # No cache - run AI scanner
            result = await self._run_ai_scanner()
            
            # Save to cache for future restarts
            self._ticker_cache.save_tickers(result, today)
        
        self._total_tickers = len(result)
        logger.info(
            "💰 Earnings max notional per trade: $%.2f",
            self._notional_per_trade,
        )
        p = self._broker.portfolio.open_orders
        self._orders_placed = {order.ticker.upper() for order in p}
        return result

    async def _run_ai_scanner(self) -> list[str]:
        """Run AI consensus scanner for multiple passes.
        
        Returns:
            Combined unique tickers from all scan passes.
        """
        params: ScannerParams = ScannerParams(
            name="earning_strategy",
            config={"source": "ai_consensus"},
        )
        
        combined: set[str] = set()
        scan_passes: int = 1
        
        for pass_num in range(1, scan_passes + 1):
            logger.info("Running earnings scanner - pass %d/%d...", pass_num, scan_passes)
            scan_result: list[str] = await self._earnings_scanner.scan(params)
            logger.info("Pass %d returned %d tickers: %s", pass_num, len(scan_result), scan_result)
            combined.update(scan_result)
        
        result: list[str] = sorted(combined)
        logger.info("Combined %d unique tickers: %s", len(result), result)
        
        return result

    def _is_warmup_complete(self) -> bool:
        """Check if 5-minute warmup period after market open has passed."""
        if self._warmup_complete:
            return True
        
        now_ny: datetime = datetime.now(NY_TZ)
        if now_ny.time() >= MARKET_WARMUP_TIME:
            self._warmup_complete = True
            logger.info(
                "Warmup complete - starting to process candles at %s",
                now_ny.strftime("%H:%M:%S"),
            )
            return True
        
        return False

    async def on_candle(self, ticker: str, candle: CandleStick) -> None:
        """Handle confirmed 5-minute candle.
        
        Places the first extended-hours limit buy only. Exits are submitted
        after Alpaca reports an open position.
        """
        # if not self._is_warmup_complete():
        #     logger.debug("Ignoring candle for %s - warmup not complete", ticker)
        #     return

        ticker = ticker.upper()

        # Check for duplicate - only trade first candle per ticker
        logger.info(
            "🕯️ %s 5-min candle: O=%.2f H=%.2f L=%.2f C=%.2f V=%d",
            ticker,
            candle.open,
            candle.high,
            candle.low,
            candle.close,
            candle.volume,
        )
        if await self._has_existing_exposure(ticker):
            logger.debug("Ignoring candle for %s - order already placed", ticker)
            return

        # Alpaca extended-hours equities orders must be plain limit orders.
        # Take-profit and synthetic stop exits are submitted after fill.
        entry_price: float = round(candle.low * (1 - ENTRY_OFFSET_PCT), 2)

        # Cap entry size by configured notional and current buying power.
        quantity: int = await self._calculate_quantity(entry_price)
        if quantity < MIN_QUANTITY:
            logger.warning(
                "⚠️ %s: Insufficient buying power for minimum quantity (entry=%.2f, max_notional=$%.2f)",
                ticker,
                entry_price,
                self._notional_per_trade,
            )
            return
        
        logger.info(
            "📊 %s order: Entry=%.2f (LOW-1%%), SyntheticSL=%.1f%%, TP=%.1f%%, Qty=%d ($%.2f)",
            ticker,
            entry_price,
            STOP_LOSS_PCT * 100,
            TAKE_PROFIT_PCT * 100,
            quantity,
            self._notional_per_trade,
        )

        order_request: OrderRequest = OrderRequest(
            ticker=ticker,
            quantity=quantity,
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            limit_price=entry_price,
            time_in_force=TimeInForce.DAY,
            extended_hours=True,
            buy_limit_rth=False,
        )
        
        response = await self._broker.place_order(order_request)
        await self._record_submitted_trade(
            order_request=order_request,
            order_response=response,
            note="earnings-entry",
        )
        
        # Mark ticker as having an order placed
        self._orders_placed.add(ticker)
        self._entry_prices[ticker] = entry_price
        
        logger.info(
            "✅ %s extended-hours limit entry placed: ID=%s, Status=%s",
            ticker,
            response.order_id,
            response.status,
        )

    async def on_tick(self, data: PricingData) -> None:
        """Handle tick, synthetic exits, and entry candle evaluation."""
        await self._sync_position_exits(data)
        await super().on_tick(data)
        await self.on_candle(data.id, CandleStick(open=data.price, high=data.price, low=data.price, close=data.price, volume=data.last_size, time=data.time, period=Period.MINUTE))

    async def shutdown(self) -> None:
        """Shutdown strategy state."""
        if self._dynamic_stop_loss_manager is not None:
            await self._dynamic_stop_loss_manager.shutdown()
        await super().shutdown()

    async def _calculate_quantity(self, entry_price: float) -> int:
        """Calculate order quantity from configured notional and buying power.
        
        Args:
            entry_price: The entry price per share.
            
        Returns:
            Number of shares to buy (floored to int), or 0 if not enough cash.
        """
        if entry_price <= 0:
            return 0
        buying_power = max(0.0, await self._broker.get_buying_power())
        notional = min(self._notional_per_trade, buying_power)
        return int(notional / entry_price)

    async def _has_existing_exposure(self, ticker: str) -> bool:
        ticker = ticker.upper()
        if ticker in self._orders_placed or ticker in self._stop_triggered:
            return True

        try:
            portfolio = await self._broker.get_portfolio()
        except Exception as exc:
            logger.warning("Could not refresh portfolio before %s entry: %s", ticker, exc)
            portfolio = self._broker.portfolio

        return portfolio.has_open_order(ticker) or portfolio.has_position(ticker)

    async def _sync_position_exits(self, data: PricingData) -> None:
        ticker = data.id.upper()
        try:
            portfolio = await self._broker.get_portfolio()
        except Exception as exc:
            logger.warning("Could not refresh portfolio for %s exits: %s", ticker, exc)
            return

        position = portfolio.get_position(ticker)
        if position is None or position.quantity <= 0:
            return

        entry_price = self._resolve_entry_price(ticker, position)
        if entry_price <= 0:
            return

        stop_price = self._round_price(entry_price * (1 - STOP_LOSS_PCT))
        if data.price <= stop_price:
            await self._trigger_synthetic_stop(
                ticker=ticker,
                position=position,
                current_price=data.price,
                stop_price=stop_price,
                portfolio=portfolio,
            )
            return

        await self._ensure_take_profit_order(
            ticker=ticker,
            position=position,
            entry_price=entry_price,
            portfolio=portfolio,
        )

    async def _ensure_take_profit_order(
        self,
        ticker: str,
        position: Position,
        entry_price: float,
        portfolio: Portfolio,
    ) -> None:
        if ticker in self._stop_triggered:
            return

        existing_order = self._find_open_take_profit_order(ticker, portfolio)
        if existing_order is not None:
            self._take_profit_order_ids[ticker] = existing_order.order_id
            return
        if ticker in self._take_profit_order_ids:
            return

        quantity = abs(int(position.quantity))
        if quantity < MIN_QUANTITY:
            return

        take_profit_price = self._round_price(entry_price * (1 + TAKE_PROFIT_PCT))
        order_request = OrderRequest(
            ticker=ticker,
            quantity=quantity,
            side=OrderSide.SELL,
            order_type=OrderType.LIMIT,
            limit_price=take_profit_price,
            time_in_force=TimeInForce.DAY,
            extended_hours=True,
        )
        response = await self._broker.place_order(order_request)
        await self._record_submitted_trade(
            order_request=order_request,
            order_response=response,
            note="earnings-take-profit",
        )
        self._take_profit_order_ids[ticker] = response.order_id
        logger.info(
            "✅ %s extended-hours take-profit placed: ID=%s price=%.2f qty=%d",
            ticker,
            response.order_id,
            take_profit_price,
            quantity,
        )

    async def _trigger_synthetic_stop(
        self,
        ticker: str,
        position: Position,
        current_price: float,
        stop_price: float,
        portfolio: Portfolio,
    ) -> None:
        if ticker in self._stop_triggered:
            return

        self._stop_triggered.add(ticker)
        quantity = abs(int(position.quantity))
        if quantity < MIN_QUANTITY:
            return

        logger.warning(
            "🛑 %s synthetic stop triggered at %.2f (stop=%.2f, qty=%d)",
            ticker,
            current_price,
            stop_price,
            quantity,
        )

        await self._cancel_take_profit_order(ticker, portfolio)

        stop_limit_price = self._round_price(current_price)
        order_request = OrderRequest(
            ticker=ticker,
            quantity=quantity,
            side=OrderSide.SELL,
            order_type=OrderType.LIMIT,
            limit_price=stop_limit_price,
            time_in_force=TimeInForce.DAY,
            extended_hours=True,
        )
        response = await self._broker.place_order(order_request)
        await self._record_submitted_trade(
            order_request=order_request,
            order_response=response,
            note="earnings-synthetic-stop",
        )
        self._take_profit_order_ids.pop(ticker, None)
        logger.info(
            "✅ %s extended-hours synthetic stop limit placed: ID=%s price=%.2f qty=%d",
            ticker,
            response.order_id,
            stop_limit_price,
            quantity,
        )

    async def _cancel_take_profit_order(self, ticker: str, portfolio: Portfolio) -> None:
        orders = portfolio.open_orders
        try:
            orders = await self._broker.get_open_orders()
        except Exception as exc:
            logger.warning("Could not refresh open orders before cancelling %s TP: %s", ticker, exc)

        order_ids: set[str] = set()
        stored_order_id = self._take_profit_order_ids.get(ticker)
        if stored_order_id:
            order_ids.add(stored_order_id)

        for order in orders:
            if self._is_take_profit_order(ticker, order):
                order_ids.add(order.order_id)

        for order_id in order_ids:
            if not order_id:
                continue
            try:
                await self._broker.cancel_order(order_id)
                logger.info("Cancelled %s take-profit order %s", ticker, order_id)
            except Exception as exc:
                logger.warning("Failed cancelling %s take-profit order %s: %s", ticker, order_id, exc)

    def _resolve_entry_price(self, ticker: str, position: Position) -> float:
        if position.average_cost > 0:
            entry_price = position.average_cost
        else:
            entry_price = self._entry_prices.get(ticker.upper(), 0.0)
        if entry_price > 0:
            self._entry_prices[ticker.upper()] = entry_price
        return entry_price

    def _find_open_take_profit_order(
        self,
        ticker: str,
        portfolio: Portfolio,
    ) -> OrderResponse | None:
        for order in portfolio.open_orders:
            if self._is_take_profit_order(ticker, order):
                return order
        return None

    @staticmethod
    def _is_take_profit_order(ticker: str, order: OrderResponse) -> bool:
        return (
            order.ticker.upper() == ticker.upper()
            and order.side == OrderSide.SELL
            and order.order_type == OrderType.LIMIT
            and order.is_active
        )

    @staticmethod
    def _round_price(price: float) -> float:
        return round(price, 2 if price >= 1 else 4)
