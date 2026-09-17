"""
Sandbox WebSocket Adapter for OpenAlgo.
Allows the frontend to connect to the WebSocket proxy seamlessly in Sandbox/Paper trading mode.
"""

from utils.logging import get_logger
from .base_adapter import BaseBrokerWebSocketAdapter

logger = get_logger(__name__)


class SandboxWebSocketAdapter(BaseBrokerWebSocketAdapter):
    """
    Adapter for sandbox paper trading mode.
    Handles client WebSocket connection cleanly without external broker WebSocket dependencies.
    """

    def __init__(self, use_shared_zmq: bool = False, shared_publisher=None):
        super().__init__(use_shared_zmq=use_shared_zmq, shared_publisher=shared_publisher)
        self.broker_name = "sandbox"
        self.user_id = None
        self.connected = True
        self.subscriptions = {}
        logger.info("SandboxWebSocketAdapter initialized")

    def initialize(self, broker_name: str, user_id: str, auth_data=None):
        self.broker_name = broker_name
        self.user_id = user_id
        self.connected = True
        logger.info(f"SandboxWebSocketAdapter initialized for user {user_id}")
        return {"status": "success", "message": "Sandbox adapter initialized"}

    def connect(self):
        self.connected = True
        logger.info("SandboxWebSocketAdapter connected")
        return {"status": "success", "message": "Connected"}

    def disconnect(self):
        self.connected = False
        logger.info("SandboxWebSocketAdapter disconnected")
        return {"status": "success", "message": "Disconnected"}

    def subscribe(self, symbol: str, exchange: str, mode: int = 2, depth_level: int = 5):
        key = f"{exchange}_{symbol}"
        self.subscriptions[key] = {
            "symbol": symbol,
            "exchange": exchange,
            "mode": mode,
            "depth": depth_level,
        }
        logger.info(f"SandboxWebSocketAdapter subscribed: {key}")
        return self._create_success_response(f"Subscribed to {symbol}")

    def unsubscribe(self, symbol: str, exchange: str, mode: int = 2):
        key = f"{exchange}_{symbol}"
        self.subscriptions.pop(key, None)
        logger.info(f"SandboxWebSocketAdapter unsubscribed: {key}")
        return self._create_success_response(f"Unsubscribed from {symbol}")
