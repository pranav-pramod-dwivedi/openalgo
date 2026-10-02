"""
binance_demo_adapter.py
Alias module so broker_factory.py can find the Binance Demo adapter under
the expected name: broker.binance_demo.streaming.binance_demo_adapter

The factory uses `{broker_name.capitalize()}WebSocketAdapter` as the class name,
which resolves to `Binance_demoWebSocketAdapter`.
"""

from broker.binance_demo.streaming.binance_streaming import (
    Binance_demoWebSocketAdapter,
    BinanceDemoWebSocketAdapter,
)

__all__ = ["BinanceDemoWebSocketAdapter", "Binance_demoWebSocketAdapter"]
