"""
Binance Demo Market Data API
Provides quotes, market depth, and historical candlestick data for Binance Demo.
"""

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
import requests
import pandas as pd

from services.binance_demo_service import (
    binance_demo_service,
    FUTURES_BASE_URL,
    SPOT_BASE_URL,
)
from utils.constants import SUPPORTED_INTERVALS
from utils.logging import get_logger

logger = get_logger(__name__)

# Map OpenAlgo intervals to Binance intervals
BINANCE_INTERVAL_MAP = {
    "1s": "1s",
    "1m": "1m",
    "3m": "3m",
    "5m": "5m",
    "15m": "15m",
    "30m": "30m",
    "1h": "1h",
    "2h": "2h",
    "4h": "4h",
    "6h": "6h",
    "8h": "8h",
    "12h": "12h",
    "1d": "1d",
    "D": "1d",
    "1w": "1w",
    "W": "1w",
    "1M": "1M",
    "M": "1M",
}


class BrokerData:
    """Binance Demo market data provider."""

    TIMEFRAME_MAP = {iv: iv for iv in SUPPORTED_INTERVALS}

    def __init__(self, auth_token: str = "", *args, **kwargs):
        self.auth_token = auth_token
        self.timeframe_map = self.TIMEFRAME_MAP
        self._session = requests.Session()

    def get_quotes(self, symbol: str, exchange: str = "CRYPTO") -> Dict[str, Any]:
        """Fetch real-time 24hr ticker quote for a symbol."""
        clean_sym = binance_demo_service.clean_symbol(symbol)
        ticker = None

        # 1. Try Futures testnet
        try:
            r = self._session.get(
                f"{FUTURES_BASE_URL}/fapi/v1/ticker/24hr",
                params={"symbol": clean_sym},
                timeout=4.0,
            )
            if r.status_code == 200:
                ticker = r.json()
        except Exception:
            pass

        # 2. Fallback to Spot demo
        if not ticker or "lastPrice" not in ticker:
            try:
                r = self._session.get(
                    f"{SPOT_BASE_URL}/api/v3/ticker/24hr",
                    params={"symbol": clean_sym},
                    timeout=4.0,
                )
                if r.status_code == 200:
                    ticker = r.json()
            except Exception:
                pass

        # 3. Fallback to Binance public production API for real market price
        if not ticker or "lastPrice" not in ticker:
            try:
                r = self._session.get(
                    "https://api.binance.com/api/v3/ticker/24hr",
                    params={"symbol": clean_sym},
                    timeout=4.0,
                )
                if r.status_code == 200:
                    ticker = r.json()
            except Exception:
                pass

        if not ticker:
            ticker = {}

        ltp = float(ticker.get("lastPrice", 0.0) or 0.0)
        open_p = float(ticker.get("openPrice", ltp) or ltp)
        high_p = float(ticker.get("highPrice", ltp) or ltp)
        low_p = float(ticker.get("lowPrice", ltp) or ltp)
        prev_close = float(ticker.get("prevClosePrice", open_p) or open_p)
        volume = int(float(ticker.get("volume", 0.0) or 0.0))
        bid = float(ticker.get("bidPrice", ltp) or ltp)
        ask = float(ticker.get("askPrice", ltp) or ltp)

        return {
            "ltp": ltp,
            "open": open_p,
            "high": high_p,
            "low": low_p,
            "volume": volume,
            "prev_close": prev_close,
            "oi": 0.0,
            "bid": bid,
            "ask": ask,
        }

    def get_depth(self, symbol: str, exchange: str = "CRYPTO") -> Dict[str, Any]:
        """Fetch 5-level market depth order book."""
        clean_sym = binance_demo_service.clean_symbol(symbol)
        quotes = self.get_quotes(clean_sym, exchange)
        depth_data = None

        # Try futures depth
        try:
            r = self._session.get(
                f"{FUTURES_BASE_URL}/fapi/v1/depth",
                params={"symbol": clean_sym, "limit": 5},
                timeout=4.0,
            )
            if r.status_code == 200:
                depth_data = r.json()
        except Exception:
            pass

        # Fallback to spot depth
        if not depth_data or "bids" not in depth_data:
            try:
                r = self._session.get(
                    f"{SPOT_BASE_URL}/api/v3/depth",
                    params={"symbol": clean_sym, "limit": 5},
                    timeout=4.0,
                )
                if r.status_code == 200:
                    depth_data = r.json()
            except Exception:
                pass

        # Fallback to public live depth
        if not depth_data or "bids" not in depth_data:
            try:
                r = self._session.get(
                    "https://api.binance.com/api/v3/depth",
                    params={"symbol": clean_sym, "limit": 5},
                    timeout=4.0,
                )
                if r.status_code == 200:
                    depth_data = r.json()
            except Exception:
                pass

        bids = []
        asks = []
        if depth_data:
            for item in depth_data.get("bids", [])[:5]:
                bids.append({"price": float(item[0]), "quantity": float(item[1])})
            for item in depth_data.get("asks", [])[:5]:
                asks.append({"price": float(item[0]), "quantity": float(item[1])})

        while len(bids) < 5:
            bids.append({"price": 0.0, "quantity": 0.0})
        while len(asks) < 5:
            asks.append({"price": 0.0, "quantity": 0.0})

        totalbuyqty = sum(b["quantity"] for b in bids)
        totalsellqty = sum(a["quantity"] for a in asks)

        return {
            "bids": bids,
            "asks": asks,
            "ltp": quotes["ltp"],
            "ltq": 0,
            "volume": quotes["volume"],
            "open": quotes["open"],
            "high": quotes["high"],
            "low": quotes["low"],
            "prev_close": quotes["prev_close"],
            "oi": 0.0,
            "totalbuyqty": totalbuyqty,
            "totalsellqty": totalsellqty,
        }

    def get_history(
        self,
        symbol: str,
        exchange: str,
        interval: str,
        start_date: Any = None,
        end_date: Any = None,
    ) -> pd.DataFrame:
        """Fetch historical candlestick candles."""
        clean_sym = binance_demo_service.clean_symbol(symbol)
        b_interval = BINANCE_INTERVAL_MAP.get(interval, "5m")

        # Convert dates to milliseconds if provided
        start_ts = None
        end_ts = None
        try:
            if isinstance(start_date, str) and start_date:
                start_ts = int(
                    datetime.strptime(start_date[:10], "%Y-%m-%d")
                    .replace(tzinfo=timezone.utc)
                    .timestamp()
                    * 1000
                )
            if isinstance(end_date, str) and end_date:
                end_ts = int(
                    (
                        datetime.strptime(end_date[:10], "%Y-%m-%d")
                        + timedelta(days=1)
                    )
                    .replace(tzinfo=timezone.utc)
                    .timestamp()
                    * 1000
                )
        except Exception:
            pass

        params: Dict[str, Any] = {
            "symbol": clean_sym,
            "interval": b_interval,
            "limit": 500,
        }
        if start_ts:
            params["startTime"] = start_ts
        if end_ts:
            params["endTime"] = end_ts

        klines = None
        # 1. Try futures testnet
        try:
            r = self._session.get(
                f"{FUTURES_BASE_URL}/fapi/v1/klines",
                params=params,
                timeout=5.0,
            )
            if r.status_code == 200 and isinstance(r.json(), list) and len(r.json()) > 0:
                klines = r.json()
        except Exception:
            pass

        # 2. Try spot demo
        if not klines:
            try:
                r = self._session.get(
                    f"{SPOT_BASE_URL}/api/v3/klines",
                    params=params,
                    timeout=5.0,
                )
                if r.status_code == 200 and isinstance(r.json(), list) and len(r.json()) > 0:
                    klines = r.json()
            except Exception:
                pass

        # 3. Fallback to public Binance live API
        if not klines:
            try:
                r = self._session.get(
                    "https://api.binance.com/api/v3/klines",
                    params=params,
                    timeout=5.0,
                )
                if r.status_code == 200 and isinstance(r.json(), list) and len(r.json()) > 0:
                    klines = r.json()
            except Exception:
                pass

        records = []
        if klines and isinstance(klines, list):
            for k in klines:
                if len(k) >= 6:
                    records.append(
                        {
                            "timestamp": int(k[0] / 1000),
                            "open": float(k[1]),
                            "high": float(k[2]),
                            "low": float(k[3]),
                            "close": float(k[4]),
                            "volume": float(k[5]),
                            "oi": 0,
                        }
                    )

        if not records:
            # Provide at least 1 current candle so charts don't crash
            quotes = self.get_quotes(clean_sym, exchange)
            now_sec = int(datetime.now(timezone.utc).timestamp())
            records = [
                {
                    "timestamp": now_sec,
                    "open": quotes["open"],
                    "high": quotes["high"],
                    "low": quotes["low"],
                    "close": quotes["ltp"],
                    "volume": quotes["volume"],
                    "oi": 0,
                }
            ]

        df = pd.DataFrame(records)
        return df
