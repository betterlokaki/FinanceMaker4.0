"""Yahoo Finance real-time WebSocket provider."""
import asyncio
import base64
import json
import logging

import websockets
from websockets.asyncio.client import ClientConnection

from common.models.pricing_data import PricingData
from pullers.realtime.abstracts.realtime_provider_base import RealtimeProviderBase
from pullers.realtime.yahoo.pricing_data_decoder import PricingDataDecoder


logger: logging.Logger = logging.getLogger(__name__)


class YahooRealtimeProvider(RealtimeProviderBase):
    """Real-time market data provider using Yahoo Finance WebSocket.
    
    Connects to Yahoo Finance streamer, subscribes to tickers,
    decodes protobuf messages, and dispatches to registered callbacks.
    Features auto-reconnect with exponential backoff.
    """

    def __init__(
        self,
        base_url: str = "wss://streamer.finance.yahoo.com/?version=2",
        reconnect_delay: float = 1.0,
        max_reconnect_attempts: int = 5,
    ) -> None:
        """Initialize the Yahoo realtime provider.
        
        Args:
            base_url: WebSocket URL for Yahoo Finance streamer.
            reconnect_delay: Initial delay between reconnection attempts.
            max_reconnect_attempts: Maximum number of reconnection attempts.
        """
        super().__init__()
        self._base_url: str = base_url
        self._reconnect_delay: float = reconnect_delay
        self._max_reconnect_attempts: int = max_reconnect_attempts
        self._websocket: ClientConnection | None = None
        self._listener_task: asyncio.Task[None] | None = None
        self._decoder: PricingDataDecoder = PricingDataDecoder()
        self._reconnect_count: int = 0
        self._should_reconnect: bool = True

    async def _connect(self) -> None:
        """Establish WebSocket connection to Yahoo Finance."""
        self._websocket = await websockets.connect(self._base_url)
        self._is_connected = True
        self._reconnect_count = 0
        logger.info("Connected to Yahoo Finance WebSocket")

    async def _send_subscribe_message(self, tickers: list[str]) -> None:
        """Send subscription message to Yahoo Finance.
        
        Args:
            tickers: Tickers to subscribe to.
        """
        if not self._is_connected:
            await self._connect()
            self._start_listener()
        
        if self._websocket is None:
            return
            
        message: dict[str, list[str]] = {"subscribe": tickers}
        await self._websocket.send(json.dumps(message))
        logger.debug("Subscribed to tickers: %s", tickers)

    async def _send_unsubscribe_message(self, tickers: list[str]) -> None:
        """Send unsubscription message to Yahoo Finance.
        
        Args:
            tickers: Tickers to unsubscribe from.
        """
        if self._websocket is None or not self._is_connected:
            return
            
        message: dict[str, list[str]] = {"unsubscribe": tickers}
        await self._websocket.send(json.dumps(message))
        logger.debug("Unsubscribed from tickers: %s", tickers)

    def _start_listener(self) -> None:
        """Start the background listener task."""
        if self._listener_task is None or self._listener_task.done():
            self._listener_task = asyncio.create_task(self._listen())

    async def _listen(self) -> None:
        """Listen for incoming messages and dispatch to callbacks."""
        while self._should_reconnect:
            try:
                await self._receive_messages()
            except websockets.ConnectionClosed:
                logger.warning("WebSocket connection closed")
                await self._handle_reconnect()
            except Exception as e:
                logger.error("Error in WebSocket listener: %s", e)
                await self._handle_reconnect()

    async def _receive_messages(self) -> None:
        """Receive and process WebSocket messages."""
        if self._websocket is None:
            return
            
        async for raw_message in self._websocket:
            await self._process_message(raw_message)

    async def _process_message(self, raw_message: str | bytes) -> None:
        """Process a single WebSocket message.
        
        Args:
            raw_message: Raw message from WebSocket.
        """
        if isinstance(raw_message, bytes):
            raw_message = raw_message.decode("utf-8")
            
        json_message: dict[str, str] = json.loads(raw_message)
        encoded_data: str | None = json_message.get("message")
        
        if not encoded_data:
            return
            
        protobuf_bytes: bytes = base64.b64decode(encoded_data)
        pricing_data: PricingData = self._decoder.decode(protobuf_bytes)
        
        await self._dispatch_tick(pricing_data)

    async def _handle_reconnect(self) -> None:
        """Handle reconnection with exponential backoff."""
        self._is_connected = False
        self._websocket = None
        
        if not self._should_reconnect:
            return
            
        if self._reconnect_count >= self._max_reconnect_attempts:
            logger.error("Max reconnection attempts reached")
            return
            
        delay: float = self._reconnect_delay * (2 ** self._reconnect_count)
        self._reconnect_count += 1
        
        logger.info("Reconnecting in %.1f seconds (attempt %d/%d)",
                    delay, self._reconnect_count, self._max_reconnect_attempts)
        
        await asyncio.sleep(delay)
        
        await self._reconnect()

    async def _reconnect(self) -> None:
        """Reconnect and resubscribe to all tickers."""
        await self._connect()
        
        tickers: list[str] = self.subscribed_tickers
        if tickers:
            await self._send_subscribe_message(tickers)

    async def disconnect(self) -> None:
        """Disconnect from Yahoo Finance WebSocket."""
        self._should_reconnect = False
        self._is_connected = False
        
        if self._listener_task is not None:
            self._listener_task.cancel()
            try:
                await self._listener_task
            except asyncio.CancelledError:
                pass
            self._listener_task = None
        
        if self._websocket is not None:
            await self._websocket.close()
            self._websocket = None
        
        async with self._lock:
            self._subscriptions.clear()
        
        logger.info("Disconnected from Yahoo Finance WebSocket")
