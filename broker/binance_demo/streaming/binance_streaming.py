"""
binance_streaming.py
OpenAlgo WebSocket adapter for Binance public market data (binance_demo broker).

One upstream connection to the public combined-stream endpoint, no API keys,
no user data:

    wss://stream.binance.com:9443/stream

Subscriptions ride as JSON control frames on that single connection:

    {"method": "SUBSCRIBE", "params": ["btcusdt@miniTicker"], "id": 1}
    {"method": "UNSUBSCRIBE", "params": ["btcusdt@miniTicker"], "id": 2}

Incoming miniTicker frame (combined-stream envelope):

    {"stream": "btcusdt@miniTicker",
     "data": {"e": "24hrMiniTicker", "E": 1720000000000, "s": "BTCUSDT",
              "c": "67000.10", "o": "66800.00", "h": "67200.00",
              "l": "66500.00", "v": "1234.5", "q": "82000000.0"}}

LTP-only: last price (c) + event timestamp (E), plus the OHLC/volume fields
that ride in the same miniTicker frame. No depth streams are opened.

Each normalized tick is published on the proxy ZMQ bus as
``EXCHANGE_SYMBOL_MODE`` (e.g. ``CRYPTO_BTCUSDT_LTP``), one publish per
subscribed mode, mirroring the Delta adapter fan-out so LTP and Quote
subscribers are both served by the single upstream stream.
"""

import json
import ssl
import threading
import time

import websocket

from utils.logging import get_logger
from websocket_proxy.base_adapter import BaseBrokerWebSocketAdapter

logger = get_logger("binance_streaming")


def _f(value, default: float = 0.0) -> float:
    """Parse a Binance numeric field, which arrives as a string."""
    try:
        return float(value) if value is not None else default
    except (TypeError, ValueError):
        return default


_QUOTE_SUFFIXES = ("USDT", "USDC", "BUSD", "USD")

_STREAM_SUFFIX = "@miniTicker"


def normalize_binance_symbol(symbol: str) -> str:
    """Normalize an OpenAlgo symbol to a Binance stream symbol (e.g. BTCUSDT)."""
    s = (symbol or "").strip()
    for token in ("(Perp)", "(perp)", "(Spot)", "(spot)", "(PERP)", "(SPOT)"):
        s = s.replace(token, "")
    s = s.replace("/", "").replace(" ", "").upper()
    if s and not s.endswith(_QUOTE_SUFFIXES):
        s = f"{s}USDT"
    return s


def stream_name(br_symbol: str) -> str:
    """Return the Binance stream name for a normalized symbol."""
    return f"{br_symbol.lower()}{_STREAM_SUFFIX}"


def mode_str(mode: int) -> str:
    """Return the ZMQ topic mode label for a subscription mode."""
    return {1: "LTP", 2: "QUOTE"}.get(mode, "LTP")


class BinancePublicWebSocket:
    """Thin client for the Binance public combined-stream endpoint.

    Holds one connection and multiplexes every subscribed miniTicker stream
    over it via SUBSCRIBE/UNSUBSCRIBE control frames. The stream registry
    doubles as the reconnect replay list: it is never cleared on a drop, so
    every reconnect restores all streams without the caller re-subscribing.
    """

    STREAM_URL = "wss://stream.binance.com:9443/stream"
    HEARTBEAT_INTERVAL = 30  # seconds between pings

    def __init__(
        self,
        on_message=None,
        on_error=None,
        on_open=None,
        on_close=None,
        max_retry_attempt: int = 10,
        retry_delay: int = 2,
        retry_multiplier: int = 2,
    ):
        self.on_message = on_message or (lambda ws, msg: None)
        self.on_error = on_error or (lambda ws, err: None)
        self.on_open = on_open or (lambda ws: None)
        self.on_close = on_close or (lambda ws: None)

        self.max_retry_attempt = max_retry_attempt
        self.retry_delay = retry_delay
        self.retry_multiplier = retry_multiplier

        self.wsapp: websocket.WebSocketApp | None = None
        self._lock = threading.Lock()
        self._connected = False
        self._stop_flag = False
        self._connect_succeeded = False
        self._active_streams: set[str] = set()
        self._next_id = 1

    @property
    def is_connected(self) -> bool:
        """True while the socket is up and able to carry frames."""
        return self._connected

    def _control_frame(self, method: str, streams: list[str]) -> str:
        # Caller must hold self._lock (subscribe_streams / unsubscribe_streams /
        # _ws_on_open all do); taking the lock again here would self-deadlock.
        frame_id = self._next_id
        self._next_id += 1
        return json.dumps({"method": method, "params": streams, "id": frame_id})

    def _send_locked(self, frame: str) -> None:
        """Send a frame, or drop it when the socket is down. Caller holds _lock."""
        if not self._connected or not self.wsapp:
            logger.debug("BinanceWS buffered a control frame (not connected)")
            return
        try:
            self.wsapp.send(frame)
        except Exception as exc:
            logger.error("BinanceWS send error: %s", exc)

    def subscribe_streams(self, streams: list[str]) -> None:
        """Register streams and SUBSCRIBE for the ones not already active."""
        if not streams:
            return
        with self._lock:
            fresh = [s for s in streams if s not in self._active_streams]
            self._active_streams.update(streams)
            if fresh:
                self._send_locked(self._control_frame("SUBSCRIBE", fresh))

    def unsubscribe_streams(self, streams: list[str]) -> None:
        """Drop streams from the registry and UNSUBSCRIBE them upstream."""
        if not streams:
            return
        with self._lock:
            gone = [s for s in streams if s in self._active_streams]
            self._active_streams.difference_update(streams)
            if gone:
                self._send_locked(self._control_frame("UNSUBSCRIBE", gone))

    def forget_subscriptions(self) -> None:
        """Drop the replay registry (explicit teardown only)."""
        with self._lock:
            self._active_streams.clear()

    def connect(self) -> None:
        """Start the connection (blocking — run in a thread)."""
        self._stop_flag = False
        retry_attempts = 0
        delay = self.retry_delay

        while not self._stop_flag and retry_attempts <= self.max_retry_attempt:
            try:
                logger.info(
                    "BinanceWS connecting to %s (attempt %s)", self.STREAM_URL, retry_attempts + 1
                )
                self.wsapp = websocket.WebSocketApp(
                    self.STREAM_URL,
                    on_open=self._ws_on_open,
                    on_message=self._ws_on_message,
                    on_error=self._ws_on_error,
                    on_close=self._ws_on_close,
                )
                self.wsapp.run_forever(
                    sslopt={"cert_reqs": ssl.CERT_REQUIRED},
                    ping_interval=self.HEARTBEAT_INTERVAL,
                    ping_timeout=10,
                )
                if self._stop_flag:
                    break
                if self._connect_succeeded:
                    self._connect_succeeded = False
                    retry_attempts = 0
                    delay = self.retry_delay
                retry_attempts += 1
                logger.warning("BinanceWS disconnected; retry in %ss", delay)
                time.sleep(delay)
                delay = min(delay * self.retry_multiplier, 60)

            except Exception as exc:
                logger.error("BinanceWS connect error: %s", exc)
                retry_attempts += 1
                time.sleep(delay)
                delay = min(delay * self.retry_multiplier, 60)

        if retry_attempts > self.max_retry_attempt:
            logger.error("BinanceWS max reconnect attempts reached; giving up")

    def close_connection(self) -> None:
        """Cleanly stop the WebSocket."""
        self._stop_flag = True
        if self.wsapp:
            try:
                self.wsapp.close()
            except Exception:
                pass

    def _ws_on_open(self, wsapp) -> None:
        logger.info("BinanceWS connected")
        with self._lock:
            self._connected = True
            self._connect_succeeded = True
            replay = sorted(self._active_streams)
            frame = self._control_frame("SUBSCRIBE", replay) if replay else None

        if frame:
            try:
                wsapp.send(frame)
            except Exception as exc:
                logger.error("BinanceWS subscription replay send error: %s", exc)

        try:
            self.on_open(wsapp)
        except Exception as exc:
            logger.error("BinanceWS on_open error: %s", exc)

    def _ws_on_message(self, wsapp, raw) -> None:
        try:
            msg = json.loads(raw)
        except Exception:
            logger.debug("BinanceWS non-JSON message received")
            return

        if not isinstance(msg, dict):
            return
        if "result" in msg and "stream" not in msg:
            logger.debug("BinanceWS control ack: %s", msg)
            return
        if "error" in msg and "data" not in msg:
            logger.error("BinanceWS server error: %s", msg)
            return

        try:
            self.on_message(wsapp, msg)
        except Exception as exc:
            logger.error("BinanceWS on_message error: %s", exc)

    def _ws_on_error(self, wsapp, error) -> None:
        logger.error("BinanceWS error: %s", error)
        try:
            self.on_error(wsapp, error)
        except Exception as exc:
            logger.error("BinanceWS on_error callback error: %s", exc)

    def _ws_on_close(self, wsapp, *args) -> None:
        logger.info("BinanceWS closed")
        with self._lock:
            self._connected = False
        try:
            self.on_close(wsapp)
        except Exception as exc:
            logger.error("BinanceWS on_close callback error: %s", exc)


class BinanceDemoWebSocketAdapter(BaseBrokerWebSocketAdapter):
    """Binance public-feed adapter for the ``binance_demo`` broker."""

    SUPPORTED_EXCHANGE = "CRYPTO"
    SUPPORTED_MODES = (1, 2)  # LTP and Quote; depth is not implemented

    def __init__(self):
        super().__init__()
        self.logger = get_logger("binance_demo_adapter")
        self.ws_client: BinancePublicWebSocket | None = None
        self.user_id: str | None = None
        self.broker_name = "binance_demo"
        self.running = False
        self._lock = threading.Lock()

    def initialize(
        self,
        broker_name: str,
        user_id: str,
        auth_data: dict | None = None,
        force: bool = False,
    ) -> None:
        """Build the public-stream client. Public market data needs no keys."""
        self.user_id = user_id
        self.broker_name = broker_name
        self.ws_client = BinancePublicWebSocket(
            on_open=self._on_open,
            on_message=self._on_data,
            on_error=self._on_error,
            on_close=self._on_close,
        )
        self.running = True
        self.logger.info("BinanceDemoWebSocketAdapter initialised for user %s", user_id)

    def connect(self) -> None:
        """Spin up the public-stream connection in a daemon thread."""
        if not self.ws_client:
            self.logger.error("Call initialize() before connect()")
            return
        threading.Thread(target=self.ws_client.connect, daemon=True).start()

    def disconnect(self) -> None:
        """Close the connection and clean up ZeroMQ resources."""
        self.running = False
        self.connected = False
        if self.ws_client:
            self.ws_client.forget_subscriptions()
            self.ws_client.close_connection()
        self.cleanup_zmq()

    def subscribe(
        self,
        symbol: str,
        exchange: str,
        mode: int = 2,
        depth_level: int = 5,
    ) -> dict:
        """Subscribe to LTP data for a single symbol (modes 1/2 only)."""
        if mode not in self.SUPPORTED_MODES:
            return self._create_error_response(
                "UNSUPPORTED_MODE",
                f"Mode {mode} is not supported by binance_demo (LTP-only feed).",
            )
        if (exchange or "").upper() != self.SUPPORTED_EXCHANGE:
            return self._create_error_response(
                "UNSUPPORTED_EXCHANGE",
                f"Exchange {exchange} is not supported by binance_demo "
                f"(only {self.SUPPORTED_EXCHANGE}).",
            )
        if not self.ws_client:
            return self._create_error_response("NOT_INITIALIZED", "Call initialize() first")

        br_symbol = normalize_binance_symbol(symbol)
        if not br_symbol:
            return self._create_error_response("INVALID_SYMBOL", f"Empty symbol: {symbol!r}")
        corr_id = f"{symbol}_{exchange}_{mode}"

        with self._lock:
            self.subscriptions[corr_id] = {
                "symbol": symbol.upper(),
                "exchange": exchange.upper(),
                "br_symbol": br_symbol,
                "mode": mode,
            }
            needed = {sub["br_symbol"] for sub in self.subscriptions.values()}

        self.ws_client.subscribe_streams([stream_name(br_symbol)])
        self.logger.info(
            "Subscribed: %s.%s mode=%s stream=%s (%s streams total)",
            symbol,
            exchange,
            mode,
            stream_name(br_symbol),
            len(needed),
        )
        return self._create_success_response(
            f"Subscription requested for {symbol}.{exchange}",
            symbol=symbol,
            exchange=exchange,
            mode=mode,
        )

    def unsubscribe(self, symbol: str, exchange: str, mode: int = 2) -> dict:
        """Unsubscribe a single symbol/mode; drops the stream when unneeded."""
        if not self.ws_client:
            return self._create_error_response("NOT_INITIALIZED", "Call initialize() first")

        corr_id = f"{symbol}_{exchange}_{mode}"
        with self._lock:
            stored = self.subscriptions.pop(corr_id, None)
            if stored is None:
                return self._create_success_response(
                    f"Not subscribed to {symbol}.{exchange}",
                    symbol=symbol,
                    exchange=exchange,
                    mode=mode,
                )
            br_symbol = stored.get("br_symbol") or normalize_binance_symbol(symbol)
            still_needed = any(
                sub.get("br_symbol") == br_symbol for sub in self.subscriptions.values()
            )

        if not still_needed:
            self.ws_client.unsubscribe_streams([stream_name(br_symbol)])

        return self._create_success_response(
            f"Unsubscribed from {symbol}.{exchange}", symbol=symbol, exchange=exchange, mode=mode
        )

    def _on_open(self, wsapp) -> None:
        self.logger.info("BinanceWS connection opened")
        self.connected = True

    def _on_error(self, wsapp, error) -> None:
        self.logger.error("BinanceWS error: %s", error)

    def _on_close(self, wsapp) -> None:
        self.logger.info("BinanceWS closed")
        if not (self.ws_client and self.ws_client.is_connected):
            self.connected = False

    def _on_data(self, wsapp, msg: dict) -> None:
        """Normalize a miniTicker frame and publish one tick per subscriber mode."""
        try:
            data = msg.get("data", msg)
            if not isinstance(data, dict):
                return
            br_symbol = str(data.get("s", "")).upper()
            if not br_symbol:
                return
            ltp = _f(data.get("c"), default=float("nan"))
            if ltp != ltp:  # NaN: no last price in this frame
                return

            event_ts = data.get("E")
            try:
                timestamp = int(event_ts) if event_ts is not None else int(time.time() * 1000)
            except (TypeError, ValueError):
                timestamp = int(time.time() * 1000)

            fields = {
                "ltp": ltp,
                "open": _f(data.get("o")),
                "high": _f(data.get("h")),
                "low": _f(data.get("l")),
                "close": _f(data.get("c")),
                "volume": _f(data.get("v")),
            }

            subscriptions = self._find_subscriptions(br_symbol)
            if not subscriptions:
                self.logger.debug("No subscription for br_symbol=%s", br_symbol)
                return

            for subscription in subscriptions:
                oa_symbol = subscription["symbol"]
                oa_exchange = subscription["exchange"]
                oa_mode = subscription["mode"]
                topic = f"{oa_exchange}_{oa_symbol}_{mode_str(oa_mode)}"
                market_data = dict(fields)
                market_data.update(
                    {
                        "symbol": oa_symbol,
                        "exchange": oa_exchange,
                        "mode": oa_mode,
                        "timestamp": timestamp,
                    }
                )
                try:
                    self.publish_market_data(topic, market_data)
                except Exception as exc:
                    self.logger.error("Publish failed for %s: %s", topic, exc)

        except Exception as exc:
            self.logger.error("_on_data error: %s", exc)

    def _find_subscriptions(self, br_symbol: str) -> list[dict]:
        """Return every subscription matching an upstream symbol."""
        with self._lock:
            return [sub for sub in self.subscriptions.values() if sub.get("br_symbol") == br_symbol]


# Alias to the factory-expected name: broker_factory resolves
# ``broker.{name}.streaming.{name}_adapter`` + ``{Name.capitalize()}WebSocketAdapter``,
# which for ``binance_demo`` is ``Binance_demoWebSocketAdapter``.
Binance_demoWebSocketAdapter = BinanceDemoWebSocketAdapter

__all__ = [
    "BinanceDemoWebSocketAdapter",
    "BinancePublicWebSocket",
    "normalize_binance_symbol",
]
