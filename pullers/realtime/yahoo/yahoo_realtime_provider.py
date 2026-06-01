"""Yahoo Finance real-time WebSocket provider."""
import asyncio
import base64
import json
import logging

import websockets
from websockets.asyncio.client import ClientConnection
from websockets.exceptions import ConnectionClosed, ConnectionClosedOK, ConnectionClosedError

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
        self._wire_lock: asyncio.Lock = asyncio.Lock()

    async def _connect(self) -> None:
        """Establish WebSocket connection to Yahoo Finance."""
        # Clean up old websocket if it exists
        if self._websocket is not None:
            try:
                await self._websocket.close()
            except Exception as e:
                logger.debug("Error closing old websocket: %s", e)
            finally:
                self._websocket = None
        
        # Create new connection
        self._websocket = await websockets.connect(self._base_url)
        self._is_connected = True
        self._reconnect_count = 0  # Reset on successful connection
        logger.info("Connected to Yahoo Finance WebSocket")

    async def _send_subscribe_message(self, tickers: list[str]) -> None:
        """Send subscription message to Yahoo Finance.
        
        Args:
            tickers: Tickers to subscribe to.
        """
        reconnect_needed = False

        async with self._wire_lock:
            if not self._is_connected:
                await self._connect()
                self._start_listener()

            if self._websocket is None:
                logger.warning("Cannot subscribe to %s: websocket is None", tickers)
                return

            try:
                all_tickers = await self._current_subscription_tickers()
                message: dict[str, list[str]] = {"subscribe": all_tickers}
                await self._websocket.send(json.dumps(message))
                logger.info(
                    "Yahoo subscribe message sent for %d ticker(s): %s",
                    len(all_tickers),
                    all_tickers,
                )
            except (ConnectionClosedOK, ConnectionClosedError, ConnectionClosed) as e:
                logger.warning(
                    "Connection closed while subscribing to %s: %s, reconnecting...",
                    tickers,
                    e,
                )
                self._is_connected = False
                reconnect_needed = True
            except Exception as e:
                logger.error("Error subscribing to %s: %s", tickers, e)

        if reconnect_needed:
            await self._handle_reconnect()

    async def _send_unsubscribe_message(self, tickers: list[str]) -> None:
        """Send unsubscription message to Yahoo Finance.
        
        Args:
            tickers: Tickers to unsubscribe from.
        """
        reconnect_needed = False

        async with self._wire_lock:
            if self._websocket is None or not self._is_connected:
                logger.debug("Cannot unsubscribe from %s: websocket not connected", tickers)
                return

            try:
                message: dict[str, list[str]] = {"unsubscribe": tickers}
                await self._websocket.send(json.dumps(message))
                logger.debug("Unsubscribed from tickers: %s", tickers)
            except (ConnectionClosedOK, ConnectionClosedError, ConnectionClosed) as e:
                logger.debug("Cannot unsubscribe from %s: websocket connection closed (%s)", tickers, e)
                self._is_connected = False
                remaining_tickers = await self._current_subscription_tickers()
                if remaining_tickers:
                    logger.info(
                        "Connection closed during unsubscribe, will reconnect and resubscribe to %d tickers",
                        len(remaining_tickers),
                    )
                    reconnect_needed = True
            except Exception as e:
                logger.warning("Error unsubscribing from %s: %s", tickers, e)

        if reconnect_needed:
            await self._handle_reconnect()

    def _start_listener(self) -> None:
        """Start the background listener task."""
        current_task: asyncio.Task[None] | None
        try:
            current_task = asyncio.current_task()
        except RuntimeError:
            current_task = None

        # If we're already in the active listener task, don't restart.
        # Restarting from inside itself can create concurrent recv loops.
        if self._listener_task is not None and self._listener_task is current_task and not self._listener_task.done():
            logger.debug("Listener restart skipped: already running in current task")
            return

        # Cancel existing task if it's still running
        if self._listener_task is not None and not self._listener_task.done():
            self._listener_task.cancel()
            # Note: We don't await cancellation here to avoid blocking
            # The task will be cleaned up when it's done
        
        # Create new listener task
        self._listener_task = asyncio.create_task(self._listen())

    async def _current_subscription_tickers(self) -> list[str]:
        async with self._lock:
            return sorted(self._subscriptions.keys())

    async def _listen(self) -> None:
        """Listen for incoming messages and dispatch to callbacks."""
        while self._should_reconnect:
            try:
                await self._receive_messages()
            except asyncio.CancelledError:
                # Task was cancelled, exit cleanly
                logger.debug("Listener task cancelled")
                raise
            except websockets.ConnectionClosed:
                logger.warning("WebSocket connection closed in listener")
                await self._handle_reconnect()
            except Exception as e:
                logger.error("Error in WebSocket listener: %s", e, exc_info=True)
                await self._handle_reconnect()

    async def _receive_messages(self) -> None:
        """Receive and process WebSocket messages."""
        if self._websocket is None:
            return
            
        async for raw_message in self._websocket:
            await self._process_message(raw_message)

        if self._should_reconnect:
            logger.warning("Yahoo WebSocket receive loop ended; reconnecting")
            self._is_connected = False
            await self._handle_reconnect()

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
        # Update state: mark as disconnected
        self._is_connected = False
        
        # Clean up old websocket
        old_websocket = self._websocket
        self._websocket = None
        if old_websocket is not None:
            try:
                await old_websocket.close()
            except Exception as e:
                logger.debug("Error closing old websocket during reconnect: %s", e)
        
        if not self._should_reconnect:
            logger.debug("Reconnect disabled, not reconnecting")
            return
            
        if self._reconnect_count >= self._max_reconnect_attempts:
            logger.error("Max reconnection attempts (%d) reached, giving up", self._max_reconnect_attempts)
            self._should_reconnect = False  # Stop trying
            return
            
        # Calculate delay with exponential backoff
        delay: float = self._reconnect_delay * (2 ** self._reconnect_count)
        current_attempt: int = self._reconnect_count + 1
        
        logger.info("Reconnecting in %.1f seconds (attempt %d/%d)",
                    delay, current_attempt, self._max_reconnect_attempts)
        
        # Increment reconnect count before delay
        self._reconnect_count += 1
        
        await asyncio.sleep(delay)
        
        # Attempt reconnection
        await self._reconnect()

    async def _reconnect(self) -> None:
        """Reconnect and resubscribe to all tickers."""
        try:
            # Connect to websocket (this updates _is_connected and resets _reconnect_count)
            await self._connect()
            
            # Restart listener task
            self._start_listener()
            
            # Get all currently subscribed tickers (from internal subscriptions dict)
            # Use lock to ensure thread-safe access
            async with self._lock:
                tickers: list[str] = list(self._subscriptions.keys())
            
            if tickers:
                logger.info("Resubscribing to %d tickers after reconnect: %s", len(tickers), tickers)
                # Send subscribe message directly to avoid recursion
                if self._websocket is not None and self._is_connected:
                    try:
                        message: dict[str, list[str]] = {"subscribe": tickers}
                        await self._websocket.send(json.dumps(message))
                        logger.info("Successfully resubscribed to %d tickers after reconnect", len(tickers))
                    except (ConnectionClosedOK, ConnectionClosedError, ConnectionClosed) as e:
                        logger.warning("Connection closed during resubscription: %s, will retry", e)
                        self._is_connected = False
                        # Retry reconnection
                        if self._should_reconnect:
                            await self._handle_reconnect()
                        return
                    except Exception as e:
                        logger.error("Error sending resubscription message: %s", e)
                        # Connection might be bad, try reconnecting again
                        self._is_connected = False
                        if self._should_reconnect:
                            await self._handle_reconnect()
                        return
                else:
                    logger.error("WebSocket not ready for resubscription (connected=%s, websocket=%s)",
                                self._is_connected, self._websocket is not None)
                    # Try reconnecting again
                    self._is_connected = False
                    if self._should_reconnect:
                        await self._handle_reconnect()
                    return
            else:
                logger.debug("No tickers to resubscribe after reconnect")
                
        except Exception as e:
            logger.error("Error during reconnect: %s", e, exc_info=True)
            # Update state: mark as disconnected
            self._is_connected = False
            # If reconnect fails, try again (if we haven't exceeded max attempts)
            if self._should_reconnect and self._reconnect_count < self._max_reconnect_attempts:
                await self._handle_reconnect()
            else:
                logger.error("Reconnection failed and max attempts reached or reconnect disabled")

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
