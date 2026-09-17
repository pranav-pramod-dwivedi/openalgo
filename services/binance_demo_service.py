"""
Binance Demo / Testnet Broker Service for OpenAlgo
Direct REST API integration with Binance Demo (Spot & Futures).

Performance improvements:
  - requests.Session for HTTP connection pooling (reuses TCP/TLS)
  - Server-time caching with 5s TTL (avoids extra round-trips)
  - ThreadPoolExecutor for parallel API calls (spots + futures concurrently)
  - Retry with exponential backoff on transient failures
  - Increased timeouts for reliability on slow networks
"""

import hashlib
import hmac
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from utils.logging import get_logger

logger = get_logger(__name__)

BINANCE_DEMO_API_KEY = os.getenv(
    "BINANCE_DEMO_API_KEY",
    "lWKwJR8uLprw7pmhk6XqIb8iLwjWeY2CH0p2gwQKT8dNyf3dQvb6y8BH6cxFtQT8",
)
BINANCE_DEMO_SECRET_KEY = os.getenv(
    "BINANCE_DEMO_SECRET_KEY",
    "KdjplsfWdKXfxZLMngimssYewD0FJVausArtXztOmZJPxLPxnSzkvYA4uY4ZhSAr",
)

SPOT_BASE_URL = "https://demo-api.binance.com"
FUTURES_BASE_URL = "https://testnet.binancefuture.com"

# Symbols to query for trades and orders
TRADE_SYMBOLS = ["SOLUSDT", "BTCUSDT"]

# Server time cache TTL in seconds
_SERVER_TIME_TTL = 5.0

# Thread pool for parallel API calls
_POOL = ThreadPoolExecutor(max_workers=8, thread_name_prefix="binance-api")


def _build_session() -> requests.Session:
    """Create a pooled session with automatic retries on transient errors."""
    s = requests.Session()
    retries = Retry(
        total=2,
        backoff_factor=0.15,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET", "POST"],
    )
    adapter = HTTPAdapter(
        max_retries=retries,
        pool_connections=10,
        pool_maxsize=20,
    )
    s.mount("https://", adapter)
    s.mount("http://", adapter)
    return s


class BinanceDemoService:
    def __init__(
        self,
        api_key: str = BINANCE_DEMO_API_KEY,
        secret_key: str = BINANCE_DEMO_SECRET_KEY,
    ):
        self.api_key = api_key
        self.secret_key = secret_key
        # One pooled session per instance — reuses TCP connections
        self._session = _build_session()
        # Server time cache: {(base_url, endpoint): (timestamp, fetched_at)}
        self._time_cache: Dict[str, tuple] = {}
        self._time_lock = threading.Lock()

    # ------------------------------------------------------------------
    # Server time with caching (avoids a round-trip per signed request)
    # ------------------------------------------------------------------
    def _get_server_time(self, is_futures: bool = True) -> int:
        """Return Binance server time in ms, cached for _SERVER_TIME_TTL."""
        base = FUTURES_BASE_URL if is_futures else SPOT_BASE_URL
        endpoint = "/fapi/v1/time" if is_futures else "/api/v3/time"
        cache_key = f"{base}{endpoint}"

        now = time.monotonic()
        with self._time_lock:
            cached = self._time_cache.get(cache_key)
            if cached and (now - cached[1]) < _SERVER_TIME_TTL:
                # Return cached value + elapsed wall-clock offset
                elapsed_ms = int((now - cached[1]) * 1000)
                return cached[0] + elapsed_ms

        try:
            r = self._session.get(f"{base}{endpoint}", timeout=3.0)
            if r.status_code == 200:
                server_time = r.json().get("serverTime", int(time.time() * 1000))
                with self._time_lock:
                    self._time_cache[cache_key] = (server_time, now)
                return server_time
        except Exception:
            pass
        return int(time.time() * 1000)

    def _sign(self, query: str) -> str:
        return hmac.new(
            self.secret_key.encode("utf-8"),
            query.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    def _headers(self) -> Dict[str, str]:
        return {"X-MBX-APIKEY": self.api_key}

    # ------------------------------------------------------------------
    # Signed GET helper (reduces boilerplate)
    # ------------------------------------------------------------------
    def _signed_get(
        self, base: str, endpoint: str, extra_params: str = "", is_futures: bool = True
    ) -> Optional[Any]:
        """Make a signed GET request. Returns parsed JSON or None on failure."""
        server_time = self._get_server_time(is_futures=is_futures)
        parts = []
        if extra_params:
            parts.append(extra_params)
        parts.append(f"timestamp={server_time}")
        query = "&".join(parts)
        sig = self._sign(query)
        url = f"{base}{endpoint}?query={query}" if not extra_params else f"{base}{endpoint}?{query}&signature={sig}"
        # Build URL correctly
        url = f"{base}{endpoint}?{query}&signature={sig}"
        try:
            r = self._session.get(url, headers=self._headers(), timeout=5.0)
            if r.status_code == 200:
                return r.json()
            else:
                logger.warning(
                    f"Binance API {endpoint} returned {r.status_code}: {r.text[:200]}"
                )
        except Exception as e:
            logger.warning(f"Binance API {endpoint} error: {e}")
        return None

    # ------------------------------------------------------------------
    # Account data
    # ------------------------------------------------------------------
    def get_account_balances(self) -> Dict[str, Any]:
        """Fetch balances from Spot and Futures demo (parallel)."""
        spot_result = [None]
        fut_result = [None]

        def _fetch_spot():
            data = self._signed_get(
                SPOT_BASE_URL, "/api/v3/account", is_futures=False
            )
            if data:
                spot_result[0] = data

        def _fetch_futures():
            data = self._signed_get(
                FUTURES_BASE_URL, "/fapi/v2/balance", is_futures=True
            )
            if data:
                fut_result[0] = data

        # Parallel fetch
        futs = [_POOL.submit(_fetch_spot), _POOL.submit(_fetch_futures)]
        for f in futs:
            f.result(timeout=8)

        spot_balances = []
        if spot_result[0]:
            for b in spot_result[0].get("balances", []):
                free = float(b.get("free", 0))
                locked = float(b.get("locked", 0))
                if free > 0 or locked > 0:
                    spot_balances.append(
                        {
                            "asset": b["asset"],
                            "free": free,
                            "locked": locked,
                            "total": free + locked,
                        }
                    )

        fut_balances = []
        if fut_result[0]:
            for b in fut_result[0]:
                bal = float(b.get("balance", 0))
                if bal > 0:
                    fut_balances.append(
                        {
                            "asset": b["asset"],
                            "balance": bal,
                            "available": float(b.get("availableBalance", bal)),
                        }
                    )

        return {
            "status": "success",
            "spot": spot_balances,
            "futures": fut_balances,
        }

    def get_positions(self) -> List[Dict[str, Any]]:
        """Fetch open positions from Futures testnet."""
        data = self._signed_get(
            FUTURES_BASE_URL, "/fapi/v2/positionRisk", is_futures=True
        )
        positions = []
        if data:
            for p in data:
                amt = float(p.get("positionAmt", 0))
                if amt != 0:
                    positions.append(
                        {
                            "symbol": p["symbol"],
                            "amount": amt,
                            "side": "LONG" if amt > 0 else "SHORT",
                            "entry_price": float(p.get("entryPrice", 0)),
                            "mark_price": float(p.get("markPrice", 0)),
                            "unrealized_pnl": float(p.get("unRealizedProfit", 0)),
                        }
                    )
        return positions

    def get_margin_data(self) -> Dict[str, Any]:
        """Format Binance data into OpenAlgo's standard margin dict."""
        # Fetch balances, positions, and trades in parallel
        bals_result = [None]
        pos_result = [None]
        trades_result = [None]

        def _fetch_bals():
            bals_result[0] = self.get_account_balances()

        def _fetch_pos():
            pos_result[0] = self.get_positions()

        def _fetch_trades():
            trades_result[0] = self.get_tradebook_formatted()

        futs = [
            _POOL.submit(_fetch_bals),
            _POOL.submit(_fetch_pos),
            _POOL.submit(_fetch_trades),
        ]
        for f in futs:
            f.result(timeout=12)

        bals = bals_result[0] or {"spot": [], "futures": []}
        positions = pos_result[0] or []
        trades = trades_result[0] or []

        unrealized_pnl = sum(p["unrealized_pnl"] for p in positions)
        utilised_margin = sum(abs(p["amount"]) * p["entry_price"] for p in positions)
        margin_required = utilised_margin / 10.0 if utilised_margin > 0 else 0.0

        realized_pnl = sum(float(t.get("pnl", 0.0)) for t in trades)

        base_capital = 100.00
        net_equity = base_capital + realized_pnl + unrealized_pnl
        avail_cash = max(0.0, base_capital + realized_pnl - margin_required)

        spot_balances = [s for s in bals.get("spot", []) if s["asset"] != "USDC"]
        fut_balances = [f for f in bals.get("futures", []) if f["asset"] != "USDC"]

        return {
            "availablecash": f"{avail_cash:.2f}",
            "collateral": "0.00",
            "hide_collateral": True,
            "starting_capital": "100.00",
            "m2munrealized": f"{unrealized_pnl:.2f}",
            "m2mrealized": f"{realized_pnl:.2f}",
            "utiliseddebits": f"{margin_required:.2f}",
            "is_binance": True,
            "spot_usdt": f"{avail_cash / 2:.2f}",
            "futures_usdt": f"{avail_cash / 2:.2f}",
            "futures_wallet_usd": f"{net_equity / 2:.2f}",
            "spot_wallet_usd": f"{net_equity / 2:.2f}",
            "total_balance_usd": f"{net_equity:.2f}",
            "spot_balances": spot_balances,
            "futures_balances": fut_balances,
            "positions": positions,
        }

    def _get_spot_prices(self) -> Dict[str, float]:
        """Fetch latest prices from Binance Spot Demo."""
        try:
            r = self._session.get(
                f"{SPOT_BASE_URL}/api/v3/ticker/price", timeout=3.0
            )
            if r.status_code == 200:
                return {x["symbol"]: float(x["price"]) for x in r.json()}
        except Exception as e:
            logger.warning(f"Error fetching spot prices: {e}")
        return {}

    # ------------------------------------------------------------------
    # Formatted books (parallel-fetch internals)
    # ------------------------------------------------------------------
    def get_positionbook_formatted(self) -> List[Dict[str, Any]]:
        """Return unified open positions (Spot Holdings + Futures)."""
        # Fetch positions, balances, and trades in parallel
        pos_result = [[]]
        bals_result = [{"spot": [], "futures": []}]
        trades_result = [[]]
        prices_result = [{}]

        def _fetch_pos():
            pos_result[0] = self.get_positions()

        def _fetch_bals():
            bals_result[0] = self.get_account_balances()

        def _fetch_trades():
            trades_result[0] = self.get_tradebook_formatted()

        def _fetch_prices():
            prices_result[0] = self._get_spot_prices()

        futs = [
            _POOL.submit(_fetch_pos),
            _POOL.submit(_fetch_bals),
            _POOL.submit(_fetch_trades),
            _POOL.submit(_fetch_prices),
        ]
        for f in futs:
            f.result(timeout=15)

        positions = pos_result[0]
        bals = bals_result[0]
        trades = trades_result[0]
        spot_prices = prices_result[0]

        res = []

        # 1. Futures open positions
        for p in positions:
            amt = p["amount"]
            entry = p["entry_price"]
            mark = p["mark_price"]
            pnl_pct = round(((mark - entry) / entry) * 100, 2) if entry > 0 else 0.0
            res.append(
                {
                    "symbol": p["symbol"],
                    "product": "FUTURES",
                    "instrument": f"{p['symbol']} (Perp)",
                    "quantity": amt,
                    "netqty": amt,
                    "buyqty": amt if amt > 0 else 0,
                    "sellqty": abs(amt) if amt < 0 else 0,
                    "average_price": entry,
                    "buyavgprice": entry if amt > 0 else 0,
                    "sellavgprice": entry if amt < 0 else 0,
                    "ltp": mark,
                    "m2m": p["unrealized_pnl"],
                    "pnl": p["unrealized_pnl"],
                    "pnlpercent": pnl_pct,
                    "exchange": "CRYPTO",
                }
            )

        # 2. Spot open positions
        buy_stats: Dict[str, Dict[str, float]] = {}
        for t in trades:
            if t.get("action") == "BUY" and t.get("product") == "SPOT":
                sym = t.get("symbol", "")
                q = float(t.get("quantity", 0))
                px = float(t.get("average_price", 0))
                if sym not in buy_stats:
                    buy_stats[sym] = {"qty": 0.0, "cost": 0.0}
                buy_stats[sym]["qty"] += q
                buy_stats[sym]["cost"] += q * px

        stablecoins = {"USDT", "USDC", "BUSD", "FDUSD", "USD"}
        for b in bals.get("spot", []):
            asset = b.get("asset", "")
            if asset in stablecoins:
                continue
            qty = float(b.get("total", 0))
            if qty <= 0.000001:
                continue
            sym = f"{asset}USDT"
            cost_info = buy_stats.get(sym, {})
            avg_px = (
                (cost_info["cost"] / cost_info["qty"])
                if cost_info.get("qty", 0) > 0
                else spot_prices.get(sym, 0.0)
            )
            ltp = spot_prices.get(sym, avg_px)
            pnl = round((ltp - avg_px) * qty, 4)
            pnlpercent = (
                round(((ltp - avg_px) / avg_px) * 100, 2) if avg_px > 0 else 0.0
            )

            res.append(
                {
                    "symbol": sym,
                    "product": "SPOT",
                    "instrument": f"{asset}/USDT (Spot)",
                    "quantity": qty,
                    "netqty": qty,
                    "buyqty": qty,
                    "sellqty": 0,
                    "average_price": round(avg_px, 2),
                    "buyavgprice": round(avg_px, 2),
                    "sellavgprice": 0,
                    "ltp": round(ltp, 2),
                    "m2m": pnl,
                    "pnl": pnl,
                    "pnlpercent": pnlpercent,
                    "exchange": "CRYPTO",
                }
            )

        return res

    def get_holdings_formatted(self) -> Dict[str, Any]:
        """Return spot holdings and statistics formatted for OpenAlgo."""
        bals = self.get_account_balances()
        trades = self.get_tradebook_formatted()
        spot_prices = self._get_spot_prices()

        buy_stats: Dict[str, Dict[str, float]] = {}
        for t in trades:
            if t.get("action") == "BUY" and t.get("product") == "SPOT":
                sym = t.get("symbol", "")
                q = float(t.get("quantity", 0))
                px = float(t.get("average_price", 0))
                if sym not in buy_stats:
                    buy_stats[sym] = {"qty": 0.0, "cost": 0.0}
                buy_stats[sym]["qty"] += q
                buy_stats[sym]["cost"] += q * px

        holdings = []
        total_holding_value = 0.0
        total_inv_value = 0.0

        stablecoins = {"USDT", "USDC", "BUSD", "FDUSD", "USD"}
        for b in bals.get("spot", []):
            asset = b.get("asset", "")
            if asset in stablecoins:
                continue
            qty = float(b.get("total", 0))
            if qty <= 0.000001:
                continue
            sym = f"{asset}USDT"
            cost_info = buy_stats.get(sym, {})
            avg_px = (
                (cost_info["cost"] / cost_info["qty"])
                if cost_info.get("qty", 0) > 0
                else spot_prices.get(sym, 0.0)
            )
            ltp = spot_prices.get(sym, avg_px)
            pnl = round((ltp - avg_px) * qty, 4)
            pnlpercent = (
                round(((ltp - avg_px) / avg_px) * 100, 2) if avg_px > 0 else 0.0
            )

            cur_val = qty * ltp
            inv_val = qty * avg_px
            total_holding_value += cur_val
            total_inv_value += inv_val

            holdings.append(
                {
                    "symbol": sym,
                    "exchange": "CRYPTO",
                    "quantity": qty,
                    "product": "SPOT",
                    "average_price": round(avg_px, 2),
                    "ltp": round(ltp, 2),
                    "pnl": pnl,
                    "pnlpercent": pnlpercent,
                }
            )

        total_pnl = total_holding_value - total_inv_value
        total_pnl_pct = (total_pnl / total_inv_value * 100) if total_inv_value > 0 else 0.0

        return {
            "holdings": holdings,
            "statistics": {
                "totalholdingvalue": round(total_holding_value, 2),
                "totalinvvalue": round(total_inv_value, 2),
                "totalprofitandloss": round(total_pnl, 2),
                "totalpnlpercentage": round(total_pnl_pct, 2),
            },
        }

    # ------------------------------------------------------------------
    # Trade book — parallel per-symbol, per-venue
    # ------------------------------------------------------------------
    def get_tradebook_formatted(self) -> List[Dict[str, Any]]:
        """Return trades from Spot and Futures formatted for OpenAlgo tradebook."""
        all_futures_raw: List[Dict] = []
        all_spot_raw: List[Dict] = []

        def _fetch_futures_sym(sym: str):
            data = self._signed_get(
                FUTURES_BASE_URL,
                "/fapi/v1/userTrades",
                extra_params=f"symbol={sym}",
                is_futures=True,
            )
            if data:
                for t in data:
                    t["_sym"] = sym
                    t["_venue"] = "futures"
                all_futures_raw.extend(data)

        def _fetch_spot_sym(sym: str):
            data = self._signed_get(
                SPOT_BASE_URL,
                "/api/v3/myTrades",
                extra_params=f"symbol={sym}",
                is_futures=False,
            )
            if data:
                for t in data:
                    t["_sym"] = sym
                all_spot_raw.extend(data)

        # Submit all 4 fetches in parallel
        futs = []
        for sym in TRADE_SYMBOLS:
            futs.append(_POOL.submit(_fetch_futures_sym, sym))
            futs.append(_POOL.submit(_fetch_spot_sym, sym))
        for f in futs:
            f.result(timeout=10)

        trades = []

        # Process futures trades
        for t in all_futures_raw:
            px = float(t.get("price", 0))
            qty = float(t.get("qty", 0))
            trade_val = round(px * qty, 4)
            side = t.get("side", "BUY")
            money_change = round(-trade_val if side == "BUY" else trade_val, 4)
            realized_pnl = float(t.get("realizedPnl", 0))
            trades.append(
                {
                    "orderid": str(t.get("orderId")),
                    "symbol": t.get("symbol"),
                    "action": side,
                    "quantity": qty,
                    "price": px,
                    "average_price": px,
                    "trade_value": trade_val,
                    "money_change": money_change,
                    "pnl": realized_pnl,
                    "product": "FUTURES",
                    "timestamp": time.strftime(
                        "%Y-%m-%d %H:%M:%S", time.localtime(t["time"] / 1000)
                    ),
                    "exchange": "CRYPTO",
                    "trade_id": str(t.get("id")),
                }
            )

        # Sort spot trades chronologically for LIFO PnL
        all_spot_raw.sort(key=lambda x: x.get("time", 0))
        spot_inventory: Dict[str, List[Dict[str, float]]] = {}

        for t in all_spot_raw:
            px = float(t.get("price", 0))
            qty = float(t.get("qty", 0))
            trade_val = round(px * qty, 4)
            is_buyer = t.get("isBuyer", True)
            action = "BUY" if is_buyer else "SELL"
            money_change = round(-trade_val if is_buyer else trade_val, 4)
            sym = t.get("symbol", "")

            trade_pnl = 0.0
            if is_buyer:
                if sym not in spot_inventory:
                    spot_inventory[sym] = []
                spot_inventory[sym].append({"qty": qty, "price": px})
            else:
                rem_sell = qty
                inv = spot_inventory.get(sym, [])
                while rem_sell > 0 and inv:
                    lot = inv[-1]
                    matched = min(rem_sell, lot["qty"])
                    trade_pnl += matched * (px - lot["price"])
                    lot["qty"] -= matched
                    rem_sell -= matched
                    if lot["qty"] <= 0.00000001:
                        inv.pop()

            trades.append(
                {
                    "orderid": str(t.get("orderId")),
                    "symbol": sym,
                    "action": action,
                    "quantity": qty,
                    "price": px,
                    "average_price": px,
                    "trade_value": trade_val,
                    "money_change": money_change,
                    "pnl": round(trade_pnl, 4),
                    "product": "SPOT",
                    "timestamp": time.strftime(
                        "%Y-%m-%d %H:%M:%S", time.localtime(t["time"] / 1000)
                    ),
                    "exchange": "CRYPTO",
                    "trade_id": str(t.get("id")),
                }
            )

        trades.sort(key=lambda x: x.get("timestamp", ""), reverse=True)
        return trades

    # ------------------------------------------------------------------
    # Order book — parallel per-symbol, per-venue
    # ------------------------------------------------------------------
    def get_orderbook_formatted(self) -> List[Dict[str, Any]]:
        """Return orders from Spot and Futures formatted for OpenAlgo orderbook."""
        all_orders: List[Dict] = []

        def _fetch_futures_orders(sym: str):
            data = self._signed_get(
                FUTURES_BASE_URL,
                "/fapi/v1/allOrders",
                extra_params=f"symbol={sym}",
                is_futures=True,
            )
            if data:
                for o in data:
                    all_orders.append(
                        {
                            "orderid": str(o.get("orderId")),
                            "symbol": o.get("symbol"),
                            "action": o.get("side"),
                            "quantity": float(o.get("origQty", 0)),
                            "filledqty": float(o.get("executedQty", 0)),
                            "price": float(o.get("price", 0)),
                            "avgprice": float(o.get("avgPrice", 0)),
                            "orderstatus": o.get("status", "FILLED"),
                            "pricetype": o.get("type", "MARKET"),
                            "timestamp": time.strftime(
                                "%Y-%m-%d %H:%M:%S",
                                time.localtime(o["time"] / 1000),
                            ),
                            "exchange": "BINANCE_FUTURES",
                        }
                    )

        def _fetch_spot_orders(sym: str):
            data = self._signed_get(
                SPOT_BASE_URL,
                "/api/v3/allOrders",
                extra_params=f"symbol={sym}",
                is_futures=False,
            )
            if data:
                for o in data:
                    exec_qty = max(float(o.get("executedQty", 1)), 1e-9)
                    avg_px = float(o.get("cummulativeQuoteQty", 0)) / exec_qty
                    all_orders.append(
                        {
                            "orderid": str(o.get("orderId")),
                            "symbol": o.get("symbol"),
                            "action": o.get("side"),
                            "quantity": float(o.get("origQty", 0)),
                            "filledqty": float(o.get("executedQty", 0)),
                            "price": float(o.get("price", 0)),
                            "avgprice": avg_px,
                            "orderstatus": o.get("status", "FILLED"),
                            "pricetype": o.get("type", "MARKET"),
                            "timestamp": time.strftime(
                                "%Y-%m-%d %H:%M:%S",
                                time.localtime(o["time"] / 1000),
                            ),
                            "exchange": "BINANCE_SPOT",
                        }
                    )

        # Submit all 4 fetches in parallel
        futs = []
        for sym in TRADE_SYMBOLS:
            futs.append(_POOL.submit(_fetch_futures_orders, sym))
            futs.append(_POOL.submit(_fetch_spot_orders, sym))
        for f in futs:
            f.result(timeout=10)

        all_orders.sort(key=lambda x: x.get("timestamp", ""), reverse=True)
        return all_orders

    # ------------------------------------------------------------------
    # Order placement
    # ------------------------------------------------------------------
    def place_order(
        self,
        symbol: str,
        side: str,
        quantity: float,
        order_type: str = "MARKET",
        is_futures: bool = True,
    ) -> Dict[str, Any]:
        """Place an order directly on Binance Demo / Testnet."""
        server_time = self._get_server_time(is_futures=is_futures)
        base = FUTURES_BASE_URL if is_futures else SPOT_BASE_URL
        endpoint = "/fapi/v1/order" if is_futures else "/api/v3/order"

        params = {
            "symbol": symbol.upper(),
            "side": side.upper(),
            "type": order_type.upper(),
            "quantity": quantity,
            "timestamp": server_time,
        }
        query = "&".join([f"{k}={v}" for k, v in params.items()])
        sig = self._sign(query)

        try:
            resp = self._session.post(
                f"{base}{endpoint}?{query}&signature={sig}",
                headers=self._headers(),
                timeout=5.0,
            )
            return {
                "status_code": resp.status_code,
                "response": resp.json(),
            }
        except Exception as e:
            logger.error(f"Error placing order: {e}")
            return {
                "status_code": 0,
                "response": {"msg": str(e), "code": -1},
            }


binance_demo_service = BinanceDemoService()
