"""Earnings-based trading strategy using AI consensus."""
import logging
from datetime import datetime, time

from zoneinfo import ZoneInfo

from common.models.candlestick import CandleStick
from common.models.order import OrderSide, OrderType
from common.models.order_request import OrderRequest
from common.models.scanner_params import ScannerParams
from common.settings import AIScannerConfig
from publishers.abstracts.i_broker import IBroker
from pullers.realtime.abstracts.i_realtime_provider import IRealtimeProvider
from pullers.scanners.ai_scanners.earning_tommrow_ai import EarningTomorrowAI
from strategy.abstracts.realtime_trading_base import RealTimeTradingBase

logger: logging.Logger = logging.getLogger(__name__)

NY_TZ: ZoneInfo = ZoneInfo("America/New_York")
MARKET_WARMUP_TIME: time = time(9, 35)  # 9:35 AM NY (market open + 5 min)

# Strategy constants
ENTRY_OFFSET_PCT: float = 0.01  # 1% below candle low
STOP_LOSS_PCT: float = 0.04  # 4% below entry
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
       - Stop Loss = entry - 4%
       - Take Profit = entry + 8%
       - Places LIMIT BUY order via IBroker
    5. No duplicate orders per ticker
    """

    def __init__(
        self,
        realtime_provider: IRealtimeProvider,
        earnings_scanner: EarningTomorrowAI,
        broker: IBroker,
        ai_scanner_config: AIScannerConfig,
    ) -> None:
        """Initialize the earnings strategy.
        
        Args:
            realtime_provider: Real-time market data provider.
            earnings_scanner: EarningTomorrowAI scanner (concrete, run twice).
            broker: Broker interface for placing orders.
            ai_scanner_config: AI scanner configuration with scan_passes.
        """
        super().__init__(realtime_provider)
        self._earnings_scanner: EarningTomorrowAI = earnings_scanner
        self._broker: IBroker = broker
        self._ai_scanner_config: AIScannerConfig = ai_scanner_config
        self._warmup_complete: bool = False
        self._orders_placed: set[str] = set()  # Track tickers with orders
        self._buying_power_per_ticker: float = 0.0  # Allocated buying power per ticker
        self._total_tickers: int = 0  # Total number of tickers to trade

    async def load_tickers(self) -> list[str]:
        """Load tickers by running AI consensus scanner multiple passes."""
        params: ScannerParams = ScannerParams(
            name="earning_strategy",
            config={"source": "ai_consensus"},
        )
        
        combined: set[str] = set()
        scan_passes: int = self._ai_scanner_config.scan_passes
        
        for pass_num in range(1, scan_passes + 1):
            logger.info("Running earnings scanner - pass %d/%d...", pass_num, scan_passes)
            scan_result: list[str] = await self._earnings_scanner.scan(params)
            logger.info("Pass %d returned %d tickers: %s", pass_num, len(scan_result), scan_result)
            combined.update(scan_result)
        
        result: list[str] = sorted(combined)
        
        logger.info("Combined %d unique tickers: %s", len(result), result)
        
        # Calculate buying power allocation per ticker
        self._total_tickers = len(result)
        if self._total_tickers > 0:
            buying_power: float = await self._broker.get_buying_power()
            self._buying_power_per_ticker = buying_power / self._total_tickers
            logger.info(
                "💰 Buying power: $%.2f, Tickers: %d, Per ticker: $%.2f",
                buying_power,
                self._total_tickers,
                self._buying_power_per_ticker,
            )
        
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
        
        Places limit buy order on first candle only (no duplicates).
        Entry = LOW - 1%, SL = entry - 4%, TP = entry + 8%.
        """
        if not self._is_warmup_complete():
            logger.debug("Ignoring candle for %s - warmup not complete", ticker)
            return
        
        # Check for duplicate - only trade first candle per ticker
        if ticker in self._orders_placed:
            logger.debug("Ignoring candle for %s - order already placed", ticker)
            return
        
        logger.info(
            "🕯️ %s 5-min candle: O=%.2f H=%.2f L=%.2f C=%.2f V=%d",
            ticker,
            candle.open,
            candle.high,
            candle.low,
            candle.close,
            candle.volume,
        )
        
        # Calculate entry, stop loss, and take profit prices
        entry_price: float = round(candle.low * (1 - ENTRY_OFFSET_PCT), 2)
        stop_loss_price: float = round(entry_price * (1 - STOP_LOSS_PCT), 2)
        take_profit_price: float = round(entry_price * (1 + TAKE_PROFIT_PCT), 2)
        
        # Calculate quantity based on allocated buying power per ticker
        quantity: int = self._calculate_quantity(entry_price)
        if quantity < MIN_QUANTITY:
            logger.warning(
                "⚠️ %s: Insufficient buying power for minimum quantity (entry=%.2f, allocated=$%.2f)",
                ticker,
                entry_price,
                self._buying_power_per_ticker,
            )
            return
        
        logger.info(
            "📊 %s order: Entry=%.2f (LOW-1%%), SL=%.2f (-4%%), TP=%.2f (+8%%), Qty=%d ($%.2f)",
            ticker,
            entry_price,
            stop_loss_price,
            take_profit_price,
            quantity,
            self._buying_power_per_ticker,
        )
        
        # Create and place the order
        order_request: OrderRequest = OrderRequest(
            ticker=ticker,
            quantity=quantity,
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            limit_price=entry_price,
            stop_loss_price=stop_loss_price,
            take_profit_price=take_profit_price,
        )
        
        response = await self._broker.place_order(order_request)
        
        # Mark ticker as having an order placed
        self._orders_placed.add(ticker)
        
        logger.info(
            "✅ %s order placed: ID=%s, Status=%s",
            ticker,
            response.order_id,
            response.status,
        )

    def _calculate_quantity(self, entry_price: float) -> int:
        """Calculate order quantity based on allocated buying power.
        
        Args:
            entry_price: The entry price per share.
            
        Returns:
            Number of shares to buy (floored to int).
        """
        if entry_price <= 0:
            return 0
        return int(self._buying_power_per_ticker / entry_price)
