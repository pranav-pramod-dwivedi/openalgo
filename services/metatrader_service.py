"""
OpenAlgo MetaTrader 5 Bridge & Account Management Service
Integrates MetaQuotes-Demo account credentials with OpenAlgo multi-market trading.
"""

import os
import socket
from decimal import Decimal
from typing import Any, Dict
from utils.logging import get_logger

logger = get_logger(__name__)

class MetaTraderService:
    def __init__(self):
        self.server = os.getenv("MT5_SERVER", "MetaQuotes-Demo")
        self.login = os.getenv("MT5_LOGIN", "5056109912")
        self.password = os.getenv("MT5_PASSWORD", "!wVbP4At")
        self.investor = os.getenv("MT5_INVESTOR", "*6RoYcRt")
        self.account_name = os.getenv("MT5_ACCOUNT_NAME", "pranav dw")
        self.account_type = os.getenv("MT5_ACCOUNT_TYPE", "Forex Hedged USD")
        self.currency = os.getenv("MT5_CURRENCY", "USD")
        self.demo_host = "demo.metaquotes.net"
        self.demo_port = 443

    def check_network_connectivity(self) -> bool:
        """Check if MetaQuotes demo server is reachable over TCP."""
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(3.0)
            result = sock.connect_ex((self.demo_host, self.demo_port))
            sock.close()
            return result == 0
        except Exception as e:
            logger.error(f"MetaQuotes network check error: {e}")
            return False

    def get_account_details(self) -> Dict[str, Any]:
        """Return configured MetaTrader demo account credentials and bridge state."""
        network_ok = self.check_network_connectivity()
        return {
            "account_name": self.account_name,
            "login": self.login,
            "server": self.server,
            "account_type": self.account_type,
            "currency": self.currency,
            "investor_mode": False,
            "server_host": self.demo_host,
            "server_port": self.demo_port,
            "network_reachable": network_ok,
            "bridge_mode": "OpenAlgo-USD-Forex-Hedged",
            "active_sandbox_user": "openalgo_usd",
            "supported_forex_symbols": ["EURUSD", "GBPUSD", "USDJPY", "XAUUSD"],
        }

metatrader_service = MetaTraderService()
