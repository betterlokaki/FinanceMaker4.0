"""Demand zone trading strategy using AI consensus filtering."""
import logging

import httpx

from common.cache.abstracts.i_ticker_cache import ITickerCache
from common.helpers.ai_ticker_analyzer import AITickerAnalyzer
from common.helpers.risk_reward_calculator import RiskRewardCalculator
from common.models.candlestick import CandleStick
from common.models.order import OrderSide, OrderType, TimeInForce
from common.models.order_request import OrderRequest
from common.models.pricing_data import PricingData
from common.models.scanner_params import ScannerParams
from gpt.abstracts.gpt_base import GPTBase
from publishers.abstracts.i_broker import IBroker
from pullers.realtime.abstracts.i_realtime_provider import IRealtimeProvider
from pullers.scanners.abstracts.i_scanner import IScanner
from strategy.abstracts.realtime_trading_base import RealTimeTradingBase

logger: logging.Logger = logging.getLogger(__name__)

CACHE_KEY: str = "demand_zone_tickers"
CACHE_TTL_HOURS: float = 2.0


class DemandZoneStrategy(RealTimeTradingBase):
    """Strategy that trades stocks close to demand zones using AI consensus.
    
    Workflow:
    1. Scans Finviz for stocks close to demand zones
    2. Filters through AI consensus (Grok + Gemini)
    3. For each AI-filtered ticker:
       - Get current price from Yahoo Finance
       - Calculate order params (entry = current - 2%, SL = entry - 4.5%, TP = entry + 10%)
       - Check portfolio (skip if position exists)
       - Place limit order with stop loss and take profit (GTC, RTH-only for limit and profit)
    """

    def __init__(
        self,
        http_client: httpx.AsyncClient,
        zone_scanner: IScanner,
        ai_analyzer: AITickerAnalyzer,
        grok_client: GPTBase,
        gemini_client: GPTBase,
        realtime_provider: IRealtimeProvider,
        broker: IBroker,
        risk_calculator: RiskRewardCalculator,
        ticker_cache: ITickerCache,
        prompt_template: str,
        finviz_url: str,
        trade_value: float = 3000.0,
    ) -> None:
        """Initialize the demand zone strategy.
        
        Args:
            http_client: HTTP client for requests.
            zone_scanner: ZoneFilteredScanner instance.
            ai_analyzer: AITickerAnalyzer instance.
            grok_client: Grok AI client.
            gemini_client: Gemini AI client.
            realtime_provider: Real-time market data provider.
            broker: Broker interface for placing orders.
            risk_calculator: Risk/reward calculator.
            ticker_cache: Cache for storing/loading tickers with TTL.
            prompt_template: AI prompt template with {TICKERS} placeholder.
            finviz_url: Finviz screener URL.
            trade_value: Trade value per order (default: $3000).
        """
        super().__init__(realtime_provider)
        self._http_client: httpx.AsyncClient = http_client
        self._zone_scanner: IScanner = zone_scanner
        self._ai_analyzer: AITickerAnalyzer = ai_analyzer
        self._grok_client: GPTBase = grok_client
        self._gemini_client: GPTBase = gemini_client
        self._broker: IBroker = broker
        self._risk_calculator: RiskRewardCalculator = risk_calculator
        self._ticker_cache: ITickerCache = ticker_cache
        self._prompt_template: str = prompt_template
        self._finviz_url: str = finviz_url
        self._trade_value: float = trade_value
        self._processed_tickers: set[str] = set()  # Track processed tickers to avoid duplicates
        self._orders_placed: set[str] = set()  # Track tickers with orders placed (including pending)

    async def _safe_unsubscribe(self, tickers: list[str] | str) -> None:
        """Safely unsubscribe from tickers, handling connection errors gracefully.
        
        Args:
            tickers: Single ticker string or list of tickers to unsubscribe from.
        """
        if isinstance(tickers, str):
            tickers = [tickers]
        
        try:
            await self._realtime_provider.unsubscribe(tickers)
        except Exception as e:
            # Connection errors are handled in the provider, but catch any other exceptions
            logger.debug("Error unsubscribing from %s (non-critical): %s", tickers, e)

    async def load_tickers(self) -> list[str]:
        """Load tickers to trade via scanning and AI consensus.
        
        Checks cache first (2-hour TTL), then runs full scan if needed.
        Portfolio check happens later in _process_ticker_with_price().
        
        Returns:
            List of ticker symbols to subscribe to.
        """
        # Check cache first (2-hour TTL)
        cached_tickers: list[str] | None = None
        if hasattr(self._ticker_cache, "load_tickers_with_ttl"):
            cached_tickers = self._ticker_cache.load_tickers_with_ttl(CACHE_KEY)
        
        if cached_tickers:
            logger.info("Using cached demand zone tickers (%d): %s", len(cached_tickers), cached_tickers)
            return cached_tickers
        
        # No valid cache - run full scan
        params: ScannerParams = ScannerParams("demand_zone_strategy")
        demand_tickers: list[str] = await self._zone_scanner.scan(params)
        logger.info("Found %d demand zone tickers", len(demand_tickers))
        
        if not demand_tickers:
            logger.warning("No demand zone tickers found")
            return []
        
        ai_tickers: list[str] = await self._ai_analyzer.analyze_tickers(
            demand_tickers, self._prompt_template, self._grok_client, self._gemini_client
        )
        logger.info("AI consensus: %d tickers selected", len(ai_tickers))
        
        # Save to cache with 2-hour TTL
        if ai_tickers and hasattr(self._ticker_cache, "save_tickers_with_timestamp"):
            self._ticker_cache.save_tickers_with_timestamp(ai_tickers, CACHE_KEY, CACHE_TTL_HOURS)
        
        return ai_tickers

    async def on_tick(self, data: PricingData) -> None:
        """Handle incoming price tick - process first tick for each ticker immediately.
        
        Overrides base class to skip candle building and process tick-by-tick prices.
        Processes each ticker only once (on first price received).
        Checks portfolio positions and open orders BEFORE processing to avoid duplicate orders.
        
        Args:
            data: Real-time pricing data from the subscribed ticker.
        """
        ticker: str = data.id.upper()
        
        # Skip if already processed or order already placed
        if ticker in self._processed_tickers or ticker in self._orders_placed:
            return
        
        # Check portfolio positions and open orders BEFORE marking as processed to avoid race conditions
        portfolio = await self._broker.get_portfolio()
        if portfolio.has_position(ticker):
            logger.info("Skipping %s: already has position in portfolio", ticker)
            self._processed_tickers.add(ticker)
            await self._safe_unsubscribe(ticker)
            return
        
        if portfolio.has_open_order(ticker):
            open_order = portfolio.get_open_order(ticker)
            logger.info("Skipping %s: already has open order (ID=%s, Status=%s)", 
                       ticker, open_order.order_id if open_order else "unknown", 
                       open_order.status if open_order else "unknown")
            self._processed_tickers.add(ticker)
            await self._safe_unsubscribe(ticker)
            return
        
        # Mark as processed immediately to avoid race conditions
        self._processed_tickers.add(ticker)
        
        # Process ticker with price immediately (event-driven, no waiting)
        await self._process_ticker_with_price(ticker, data.price)

    async def on_candle(self, ticker: str, candle: CandleStick) -> None:
        """Handle confirmed candle (required by base class, but not used).
        
        This method is required by RealTimeTradingBase but is never called
        because we override on_tick() to skip candle building.
        
        Args:
            ticker: The ticker symbol.
            candle: The confirmed candlestick.
        """
        # Not used - we process ticks directly in on_tick()
        pass

    async def _process_ticker_with_price(self, ticker: str, price: float) -> None:
        """Process a ticker with its current price and place order if valid.
        
        Event-driven processing: called immediately when price arrives via on_tick().
        After placing order successfully, unsubscribes from that ticker.
        No timeouts, no waiting - pure event-driven architecture.
        
        Args:
            ticker: Ticker symbol to process.
            price: Current price from real-time feed.
        """
        try:
            # Double-check portfolio (defensive check, already checked in on_tick)
            portfolio = await self._broker.get_portfolio()
            if portfolio.has_position(ticker):
                logger.info("Skipping %s: already has position, unsubscribing", ticker)
                await self._safe_unsubscribe(ticker)
                return
            
            # Double-check open orders (defensive check, already checked in on_tick)
            if portfolio.has_open_order(ticker):
                open_order = portfolio.get_open_order(ticker)
                logger.info("Skipping %s: already has open order (ID=%s, Status=%s), unsubscribing",
                           ticker, open_order.order_id if open_order else "unknown",
                           open_order.status if open_order else "unknown")
                await self._safe_unsubscribe(ticker)
                return
            
            # Double-check if order already placed (defensive check)
            if ticker in self._orders_placed:
                logger.info("Skipping %s: order already placed", ticker)
                await self._safe_unsubscribe(ticker)
                return
            
            params = self._risk_calculator.calculate_order_params(
                price, self._trade_value
            )
            
            order_request = OrderRequest(
                ticker=ticker,
                quantity=params.quantity,
                side=OrderSide.BUY,
                order_type=OrderType.LIMIT,
                limit_price=params.entry_price,
                stop_loss_price=params.stop_loss_price,
                take_profit_price=params.take_profit_price,
                time_in_force=TimeInForce.GTC,
            )
            
            # Mark as order placed BEFORE placing to prevent race conditions
            self._orders_placed.add(ticker)
            
            response = await self._broker.place_order(order_request)
            logger.info(
                "Order placed for %s: ID=%s, Status=%s (price=%.2f)",
                ticker,
                response.order_id,
                response.status,
                price,
            )
            
            # Unsubscribe from this ticker after placing order (listener stays alive)
            await self._safe_unsubscribe(ticker)
            logger.debug("Unsubscribed from %s after placing order", ticker)
            
        except Exception as e:
            logger.error("Error processing %s with price %.2f: %s", ticker, price, e, exc_info=True)
            # Remove from both sets so it can be retried if needed
            self._processed_tickers.discard(ticker)
            self._orders_placed.discard(ticker)

    async def shutdown(self) -> None:
        """Shutdown the strategy gracefully.
        
        Note: This should rarely be called. The listener should stay alive
        and only unsubscribe from individual tickers after processing them.
        """
        logger.info("Shutting down DemandZoneStrategy...")
        self._processed_tickers.clear()
        self._orders_placed.clear()
        # Don't call super().shutdown() - we want to keep the listener alive
        # Only unsubscribe from remaining tickers if any
        if self._tickers:
            await self._safe_unsubscribe(self._tickers)
        self._is_initialized = False
        logger.info("DemandZoneStrategy shutdown complete (listener may still be running)")
