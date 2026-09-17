"""
Binance Demo Market Data API
"""

from utils.constants import SUPPORTED_INTERVALS


class BrokerData:
    """Binance Demo market data provider."""

    TIMEFRAME_MAP = {iv: iv for iv in SUPPORTED_INTERVALS}

    def __init__(self, auth_token: str = ""):
        self.auth_token = auth_token
        self.timeframe_map = self.TIMEFRAME_MAP
