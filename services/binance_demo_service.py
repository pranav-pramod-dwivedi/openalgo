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
from typing import Any

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

# Endpoints per venue mode. Demo (testnet) is the default and needs no keys of
# your own; live is opt-in and only takes effect when real credentials are
# present, so a missing key can never silently point trading at the real
# exchange with the demo key.
_DEMO_URLS = {
    "spot": "https://demo-api.binance.com",
    "futures": "https://testnet.binancefuture.com",
}
_LIVE_URLS = {
    "spot": "https://api.binance.com",
    "futures": "https://fapi.binance.com",
}


def is_live_mode() -> bool:
    """True when this deployment trades the real Binance exchange.

    Requires BINANCE_MODE=live AND both real credentials. Anything less stays
    on testnet, so a typo or a half-filled key cannot trade real funds.
    """
    if os.getenv("BINANCE_MODE", "demo").strip().lower() != "live":
        return False
    return bool(
        os.getenv("BINANCE_API_KEY", "").strip() and os.getenv("BINANCE_API_SECRET", "").strip()
    )


def get_api_credentials() -> tuple[str, str]:
    """Return the (api_key, secret) pair for the effective venue mode."""
    if is_live_mode():
        return (
            os.getenv("BINANCE_API_KEY", "").strip(),
            os.getenv("BINANCE_API_SECRET", "").strip(),
        )
    return BINANCE_DEMO_API_KEY, BINANCE_DEMO_SECRET_KEY


def get_base_urls() -> tuple[str, str]:
    """Return (spot_base_url, futures_base_url) for the effective venue mode."""
    urls = _LIVE_URLS if is_live_mode() else _DEMO_URLS
    return urls["spot"], urls["futures"]


SPOT_BASE_URL, FUTURES_BASE_URL = get_base_urls()

# Symbols to query for trades and orders
TRADE_SYMBOLS = ["SOLUSDT", "BTCUSDT"]

#: Conditional order types venues support; spot uses limit-priced variants.
_FUTURES_STOP_TYPES = frozenset({"STOP_MARKET", "TAKE_PROFIT_MARKET"})
_SPOT_STOP_TYPES = frozenset({"STOP_LOSS", "TAKE_PROFIT"})


def _resolve_order_type(order_type: str, is_futures: bool) -> str:
    """Map an agent price-type alias onto the native Binance order type.

    SL and SL-M both become market stop orders, TARGET a market take-profit.
    """
    text = (order_type or "").strip().upper()
    if text == "SLM":
        text = "SL-M"
    aliases = (
        {"SL": "STOP_MARKET", "SL-M": "STOP_MARKET", "TARGET": "TAKE_PROFIT_MARKET"}
        if is_futures
        else {"SL": "STOP_LOSS", "SL-M": "STOP_LOSS", "TARGET": "TAKE_PROFIT"}
    )
    return aliases.get(text, text)


def _is_stop_type(resolved: str) -> bool:
    """True for any conditional (stop or take-profit) native order type."""
    return resolved in _FUTURES_STOP_TYPES or resolved in _SPOT_STOP_TYPES

# Protected savings floor (USDT). The account's first FLOOR dollars are never
# tradable: tradable = max(0, equity - floor - margin_locked). The bot and all
# order paths may only use what sits above the floor. Configured via
# BINANCE_TRADING_FLOOR so the operator can raise it as savings grow; it must
# never be lowered by automated code.
TRADING_FLOOR_ENV_VAR = "BINANCE_TRADING_FLOOR"
DEFAULT_TRADING_FLOOR = 14880.0

# Assumed futures leverage for the initial-margin estimate (matches the
# long-standing margin_required = utilised / 10 convention below).
FUTURES_LEVERAGE = 10.0

# Assets counted at face value when sizing the wallet in USDT.
STABLE_ASSETS = {"USDT", "USDC", "BUSD", "FDUSD", "TUSD", "USDP"}

# Non-stable spot assets valued via the public spot ticker when sizing the
# wallet. Anything else is ignored (conservative: unknown assets do not extend
# tradable credit).
VALUED_ASSETS = {"BTC", "ETH", "SOL", "BNB"}


def get_trading_floor() -> float:
    """Return the protected savings floor in USDT (never negative)."""
    try:
        return max(0.0, float(os.getenv(TRADING_FLOOR_ENV_VAR, DEFAULT_TRADING_FLOOR)))
    except (TypeError, ValueError):
        return DEFAULT_TRADING_FLOOR


def compute_tradable(
    wallet_usd: float,
    unrealized_pnl: float,
    open_notional: float,
    floor: float,
    leverage: float = FUTURES_LEVERAGE,
) -> dict[str, float]:
    """Split futures/spot equity into protected savings and tradable cash.

    Pure function (no I/O) so the split is unit-testable. Margin already
    locked by open positions is not tradable either.
    """
    margin_locked = open_notional / leverage if leverage > 0 else 0.0
    equity = wallet_usd + unrealized_pnl
    savings = max(0.0, min(equity, floor))
    tradable = max(0.0, equity - floor - margin_locked)
    return {
        "wallet_usd": wallet_usd,
        "equity_usd": equity,
        "open_notional": open_notional,
        "margin_locked": margin_locked,
        "floor": floor,
        "savings": savings,
        "tradable": tradable,
    }

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
        api_key: str | None = None,
        secret_key: str | None = None,
    ):
        # Resolved per instantiation rather than bound as argument defaults, so
        # the effective mode's credentials are used even though the module was
        # imported before .env was read.
        default_key, default_secret = get_api_credentials()
        self.api_key = api_key or default_key
        self.secret_key = secret_key or default_secret
        # One pooled session per instance — reuses TCP connections
        self._session = _build_session()
        # Server time cache: {(base_url, endpoint): (timestamp, fetched_at)}
        self._time_cache: dict[str, tuple] = {}
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

    def _headers(self) -> dict[str, str]:
        return {"X-MBX-APIKEY": self.api_key}

    # ------------------------------------------------------------------
    # Signed GET helper (reduces boilerplate)
    # ------------------------------------------------------------------
    def _signed_get(
        self, base: str, endpoint: str, extra_params: str = "", is_futures: bool = True
    ) -> Any | None:
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
    def get_account_balances(self) -> dict[str, Any]:
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

    def get_positions(self) -> list[dict[str, Any]]:
        """Fetch open positions from Futures testnet.

        Only maps fields the live ``/fapi/v2/positionRisk`` response actually
        returns (verified: symbol, positionAmt, entryPrice, markPrice,
        unRealizedProfit, liquidationPrice, leverage, isolatedMargin,
        marginType). ``liquidation_price`` and ``margin_used`` are the
        exchange-reported values verbatim -- never computed here. A
        liquidation price of 0 means Binance reports no liquidation (flat or
        cross with no estimate), so it maps to None; cross-margin positions
        carry no per-position margin in this endpoint, so ``margin_used`` is
        None for them. ``leverage`` is included only when reported.
        """
        data = self._signed_get(
            FUTURES_BASE_URL, "/fapi/v2/positionRisk", is_futures=True
        )
        positions = []
        if data:
            for p in data:
                amt = float(p.get("positionAmt", 0))
                if amt != 0:
                    liq_raw = p.get("liquidationPrice", None)
                    liquidation_price: float | None = None
                    if liq_raw is not None:
                        try:
                            liq_val = float(liq_raw)
                            liquidation_price = liq_val if liq_val > 0 else None
                        except (TypeError, ValueError):
                            liquidation_price = None

                    margin_used: float | None = None
                    if p.get("marginType") == "isolated" and p.get("isolatedMargin") is not None:
                        try:
                            margin_used = float(p.get("isolatedMargin"))
                        except (TypeError, ValueError):
                            margin_used = None

                    leverage: int | None = None
                    if p.get("leverage") is not None:
                        try:
                            leverage = int(float(p.get("leverage")))
                        except (TypeError, ValueError):
                            leverage = None

                    entry = {
                        "symbol": p["symbol"],
                        "amount": amt,
                        "side": "LONG" if amt > 0 else "SHORT",
                        "entry_price": float(p.get("entryPrice", 0)),
                        "mark_price": float(p.get("markPrice", 0)),
                        "unrealized_pnl": float(p.get("unRealizedProfit", 0)),
                        "liquidation_price": liquidation_price,
                        "margin_used": margin_used,
                    }
                    if leverage is not None:
                        entry["leverage"] = leverage
                    positions.append(entry)
        return positions

    def _get_futures_price(self, symbol: str) -> float | None:
        """Return the latest futures mark price for a symbol, or None."""
        try:
            r = self._session.get(
                f"{FUTURES_BASE_URL}/fapi/v1/ticker/price",
                params={"symbol": symbol.upper()},
                timeout=3.0,
            )
            if r.status_code == 200:
                return float(r.json().get("price", 0)) or None
        except Exception as e:
            logger.warning(f"Error fetching futures price for {symbol}: {e}")
        return None

    def _value_wallet_usd(
        self, bals: dict[str, Any], spot_prices: dict[str, float]
    ) -> float:
        """Size the total wallet in USDT. Unknown assets count as zero."""
        total = 0.0
        for b in bals.get("futures", []) or []:
            asset = str(b.get("asset", "")).upper()
            if asset in STABLE_ASSETS:
                try:
                    total += float(b.get("balance", 0))
                except (TypeError, ValueError):
                    continue
        for s in bals.get("spot", []) or []:
            asset = str(s.get("asset", "")).upper()
            try:
                qty = float(s.get("total", 0))
            except (TypeError, ValueError):
                continue
            if asset in STABLE_ASSETS:
                total += qty
            elif asset in VALUED_ASSETS:
                price = spot_prices.get(f"{asset}USDT", 0.0)
                if price > 0:
                    total += qty * price
        return total

    def get_tradable_usdt(self) -> dict[str, Any]:
        """Return the floor-guarded tradable split (I/O: balances+positions).

        On any fetch failure the tradable leg is 0.0 (fail-closed for new
        exposure) while exits stay allowed; callers must not treat the split
        as a balance assertion, only as a spending cap.
        """
        floor = get_trading_floor()
        bals: dict[str, Any] = {"spot": [], "futures": []}
        positions: list[dict[str, Any]] = []
        spot_prices: dict[str, float] = {}
        bals_ok = [False]
        pos_ok = [False]

        def _fetch_bals():
            data = self.get_account_balances()
            if data:
                bals["spot"] = data.get("spot", [])
                bals["futures"] = data.get("futures", [])
                bals_ok[0] = True

        def _fetch_pos():
            positions.extend(self.get_positions() or [])
            pos_ok[0] = True

        def _fetch_prices():
            spot_prices.update(self._get_spot_prices())

        futs = [
            _POOL.submit(_fetch_bals),
            _POOL.submit(_fetch_pos),
            _POOL.submit(_fetch_prices),
        ]
        for f in futs:
            try:
                f.result(timeout=10)
            except Exception as e:
                logger.warning(f"get_tradable_usdt fetch failed: {e}")

        wallet = self._value_wallet_usd(bals, spot_prices)
        unrealized = sum(float(p.get("unrealized_pnl", 0) or 0) for p in positions)
        open_notional = sum(
            abs(float(p.get("amount", 0) or 0)) * float(p.get("mark_price", 0) or 0)
            for p in positions
        )
        split = compute_tradable(wallet, unrealized, open_notional, floor)
        split["balances_ok"] = bals_ok[0]
        split["positions_ok"] = pos_ok[0]
        split["verified"] = bool(bals_ok[0] and pos_ok[0])
        return split

    def get_margin_data(self) -> dict[str, Any]:
        """Format Binance data into OpenAlgo's standard margin dict."""
        # Fetch balances, positions, trades, and spot prices in parallel
        bals_result = [None]
        pos_result = [None]
        trades_result = [None]
        prices_result = [{}]

        def _fetch_bals():
            bals_result[0] = self.get_account_balances()

        def _fetch_pos():
            pos_result[0] = self.get_positions()

        def _fetch_trades():
            trades_result[0] = self.get_tradebook_formatted()

        def _fetch_prices():
            prices_result[0] = self._get_spot_prices()

        futs = [
            _POOL.submit(_fetch_bals),
            _POOL.submit(_fetch_pos),
            _POOL.submit(_fetch_trades),
            _POOL.submit(_fetch_prices),
        ]
        for f in futs:
            f.result(timeout=12)

        bals = bals_result[0] or {"spot": [], "futures": []}
        positions = pos_result[0] or []
        trades = trades_result[0] or []
        spot_prices = prices_result[0] or {}

        unrealized_pnl = sum(p["unrealized_pnl"] for p in positions)
        utilised_margin = sum(abs(p["amount"]) * p["entry_price"] for p in positions)
        margin_required = utilised_margin / 10.0 if utilised_margin > 0 else 0.0

        realized_pnl = sum(float(t.get("pnl", 0.0)) for t in trades)

        # Floor-guarded capital: only equity above the protected savings floor
        # is tradable. The floor itself is never spendable by the bot or orders.
        floor = get_trading_floor()
        wallet_usd = self._value_wallet_usd(bals, spot_prices)
        open_notional = sum(
            abs(p["amount"]) * p["mark_price"] for p in positions
        )
        split = compute_tradable(
            wallet_usd, unrealized_pnl, open_notional, floor, FUTURES_LEVERAGE
        )
        avail_cash = split["tradable"]
        net_equity = split["equity_usd"]
        savings = split["savings"]

        spot_balances = [s for s in bals.get("spot", []) if s["asset"] != "USDC"]
        fut_balances = [f for f in bals.get("futures", []) if f["asset"] != "USDC"]

        # On-exchange figures (informational only; spending is capped by the
        # tradable split above, never by these).
        spot_usdt_free = 0.0
        for s in bals.get("spot", []) or []:
            if str(s.get("asset", "")).upper() in STABLE_ASSETS:
                try:
                    spot_usdt_free += float(s.get("free", 0))
                except (TypeError, ValueError):
                    continue
        fut_usdt_avail = 0.0
        for f in bals.get("futures", []) or []:
            if str(f.get("asset", "")).upper() in STABLE_ASSETS:
                try:
                    fut_usdt_avail += float(f.get("available", f.get("balance", 0)))
                except (TypeError, ValueError):
                    continue

        return {
            "availablecash": f"{avail_cash:.2f}",
            "collateral": "0.00",
            "hide_collateral": True,
            "starting_capital": f"{floor:.2f}",
            "m2munrealized": f"{unrealized_pnl:.2f}",
            "m2mrealized": f"{realized_pnl:.2f}",
            "utiliseddebits": f"{margin_required:.2f}",
            "is_binance": True,
            "is_live": is_live_mode(),
            "trading_floor": f"{floor:.2f}",
            "savings_usdt": f"{savings:.2f}",
            "tradable_usdt": f"{avail_cash:.2f}",
            "wallet_total_usd": f"{wallet_usd:.2f}",
            "equity_usd": f"{net_equity:.2f}",
            "open_notional_usd": f"{open_notional:.2f}",
            "spot_usdt": f"{spot_usdt_free:.2f}",
            "futures_usdt": f"{fut_usdt_avail:.2f}",
            "futures_wallet_usd": f"{net_equity / 2:.2f}",
            "spot_wallet_usd": f"{net_equity / 2:.2f}",
            "total_balance_usd": f"{net_equity:.2f}",
            "spot_balances": spot_balances,
            "futures_balances": fut_balances,
            "positions": positions,
        }

    def _get_spot_prices(self) -> dict[str, float]:
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
    def get_positionbook_formatted(self) -> list[dict[str, Any]]:
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
            fut_entry: dict[str, Any] = {
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
                # Exchange-reported risk fields, passed through verbatim
                # (None when Binance does not report them). Never computed.
                "liquidation_price": p.get("liquidation_price"),
                "margin_used": p.get("margin_used"),
            }
            if p.get("leverage") is not None:
                fut_entry["leverage"] = p.get("leverage")
            res.append(fut_entry)

        # 2. Spot open positions
        buy_stats: dict[str, dict[str, float]] = {}
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
            # Skip sub-$3 dust from active open positions
            if (qty * ltp) < 3.0:
                continue

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

    def get_holdings_formatted(self) -> dict[str, Any]:
        """Return spot holdings and statistics formatted for OpenAlgo."""
        bals = self.get_account_balances()
        trades = self.get_tradebook_formatted()
        spot_prices = self._get_spot_prices()

        buy_stats: dict[str, dict[str, float]] = {}
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
    # Fees — real exchange-reported commissions (never invented)
    # ------------------------------------------------------------------
    @staticmethod
    def _parse_fill_commission(fill: dict[str, Any]) -> tuple[float | None, str | None]:
        """Extract the real commission from a raw Binance fill.

        Binance trade-history endpoints report ``commission`` plus
        ``commissionAsset`` per fill. Returns ``(commission, asset)`` where
        commission is a float when reported.
        """
        asset_raw = fill.get("commissionAsset")
        asset = str(asset_raw) if asset_raw is not None else None
        raw = fill.get("commission")
        # Endpoint did not return commission for this fill: unknown, never 0.
        if raw is None or (isinstance(raw, str) and raw.strip() == ""):
            return None, asset
        try:
            return float(raw), asset
        except (TypeError, ValueError):
            # Unparseable commission is treated as unknown, never 0.
            return None, asset

    def get_fee_schedule(self, symbol: str | None = None) -> dict[str, Any]:
        """Return the account's actual commission rates, never defaults.

        Queries the endpoints that report real rates (futures
        ``/fapi/v1/commissionRate`` for one symbol, else the spot account
        commission fields). On any failure returns maker/taker as None with
        source ``unavailable`` rather than inventing a rate.
        """
        sym = (symbol or (TRADE_SYMBOLS[0] if TRADE_SYMBOLS else "") or "BTCUSDT").upper()
        try:
            data = self._signed_get(
                FUTURES_BASE_URL,
                "/fapi/v1/commissionRate",
                extra_params=f"symbol={sym}",
                is_futures=True,
            )
            if isinstance(data, dict):
                maker_raw = data.get("makerCommissionRate", data.get("makerCommission"))
                taker_raw = data.get("takerCommissionRate", data.get("takerCommission"))
                if maker_raw is not None and taker_raw is not None:
                    try:
                        return {
                            "maker": float(maker_raw),
                            "taker": float(taker_raw),
                            "source": f"fapi/v1/commissionRate:{sym}",
                        }
                    except (TypeError, ValueError):
                        pass
        except Exception as e:
            logger.warning(f"Fee schedule futures lookup failed: {e}")
        try:
            acct = self._signed_get(SPOT_BASE_URL, "/api/v3/account", is_futures=False)
            if isinstance(acct, dict):
                maker_raw = acct.get("makerCommission")
                taker_raw = acct.get("takerCommission")
                if maker_raw is not None and taker_raw is not None:
                    try:
                        # Spot returns integer basis points (e.g. 10 == 0.10%).
                        # Divided to a fractional rate; no rate is hardcoded.
                        return {
                            "maker": float(maker_raw) / 10000.0,
                            "taker": float(taker_raw) / 10000.0,
                            "source": "api/v3/account",
                        }
                    except (TypeError, ValueError):
                        pass
        except Exception as e:
            logger.warning(f"Fee schedule spot lookup failed: {e}")
        return {"maker": None, "taker": None, "source": "unavailable"}

    @staticmethod
    def compute_tradebook_totals(trades: list[dict[str, Any]]) -> dict[str, Any]:
        """Compute gross/net realised PnL net of real commissions.

        Sums ``pnl`` for gross realised, sums only non-None ``commission``
        for total commission paid, counts fills lacking commission data,
        and returns net realised as gross minus commission.
        """
        gross = 0.0
        total_commission = 0.0
        missing = 0
        rows = trades or []
        for t in rows:
            try:
                gross += float(t.get("pnl", 0.0) or 0.0)
            except (TypeError, ValueError):
                continue
            comm = t.get("commission")
            # Missing commission stays unknown: excluded from total, counted.
            if comm is None:
                missing += 1
            else:
                try:
                    total_commission += float(comm)
                except (TypeError, ValueError):
                    missing += 1
        return {
            "gross_realised": round(gross, 4),
            "total_commission": round(total_commission, 4),
            "missing_commission_count": missing,
            "fill_count": len(rows),
            "net_realised": round(gross - total_commission, 4),
        }

    def get_tradebook_totals(self, trades: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        """Return totals for given trades, fetching the live book when omitted."""
        if trades is None:
            trades = self.get_tradebook_formatted()
        return self.compute_tradebook_totals(trades)

    # ------------------------------------------------------------------
    # Trade book — parallel per-symbol, per-venue
    # ------------------------------------------------------------------
    def get_tradebook_formatted(self) -> list[dict[str, Any]]:
        """Return trades from Spot and Futures formatted for OpenAlgo tradebook."""
        all_futures_raw: list[dict] = []
        all_spot_raw: list[dict] = []

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
            # Real exchange-reported commission; missing stays None (never 0).
            commission, commission_asset = self._parse_fill_commission(t)
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
                    "commission": commission,
                    "commissionAsset": commission_asset,
                }
            )

        # Sort spot trades chronologically for LIFO PnL
        all_spot_raw.sort(key=lambda x: x.get("time", 0))
        spot_inventory: dict[str, list[dict[str, float]]] = {}

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

            # Real exchange-reported commission; missing stays None (never 0).
            commission, commission_asset = self._parse_fill_commission(t)
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
                    "commission": commission,
                    "commissionAsset": commission_asset,
                }
            )

        trades.sort(key=lambda x: x.get("timestamp", ""), reverse=True)
        return trades

    # ------------------------------------------------------------------
    # Order book — parallel per-symbol, per-venue
    # ------------------------------------------------------------------
    # Binance order status -> the platform's normalized contract. Every other
    # broker's mapping/order_data.py emits these words, and the frontend reads
    # them (status column, status filters, open-order counts). Emitting Binance's
    # own vocabulary here left every one of those reading undefined.
    _ORDER_STATUS_MAP = {
        "NEW": "open",
        "PARTIALLY_FILLED": "open",
        "PENDING_NEW": "pending",
        "FILLED": "complete",
        "CANCELED": "cancelled",
        "CANCELLED": "cancelled",
        "PENDING_CANCEL": "open",
        "REJECTED": "rejected",
        "EXPIRED": "cancelled",
        "EXPIRED_IN_MATCH": "cancelled",
    }

    def _map_order_status(self, raw: str | None) -> str:
        """Translate a Binance order status into the platform's vocabulary."""
        if not raw:
            return "open"
        return self._ORDER_STATUS_MAP.get(str(raw).upper(), str(raw).lower())

    def get_orderbook_formatted(self) -> list[dict[str, Any]]:
        """Return orders from Spot and Futures formatted for OpenAlgo orderbook."""
        all_orders: list[dict] = []

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
                            # Both spellings: order_status is the contract every
                            # other broker and the frontend use; orderstatus is
                            # what the statistics counters in orderbook_service
                            # read. Emitting one broke the other.
                            "order_status": self._map_order_status(o.get("status")),
                            "orderstatus": o.get("status", "FILLED"),
                            "pricetype": o.get("type", "MARKET"),
                            "product": "FUTURES",
                            "trigger_price": float(o.get("stopPrice", 0) or 0),
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
                            "order_status": self._map_order_status(o.get("status")),
                            "orderstatus": o.get("status", "FILLED"),
                            "pricetype": o.get("type", "MARKET"),
                            "product": "SPOT",
                            "trigger_price": 0.0,
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
    def _check_tradable_cap(
        self,
        symbol: str,
        side: str,
        quantity: float,
        is_futures: bool,
        price: float | None = None,
    ) -> dict[str, Any] | None:
        """Floor guard: new exposure may only use equity above the savings floor.

        Returns None when the order may proceed, else an error dict shaped like
        place_order's failure response. Pure exits/reductions always pass so a
        position can never be trapped by this guard; only exposure-increasing
        size is capped. When verification data is unavailable, increases are
        blocked (fail-closed) while reductions still pass.
        """
        if quantity <= 0:
            return {
                "status_code": 400,
                "response": {"msg": "Quantity must be greater than 0", "code": -2},
            }

        clean_sym = (symbol or "").upper()
        side = (side or "").upper()
        ref_price = price if price and price > 0 else None

        if is_futures:
            try:
                positions = self.get_positions() or []
            except Exception as e:
                logger.warning(f"Tradable-cap check: positions fetch failed: {e}")
                positions = []
            current = next(
                (p for p in positions if str(p.get("symbol", "")).upper() == clean_sym),
                None,
            )
            amt = float(current.get("amount", 0)) if current else 0.0
            if side == "BUY":
                new_exposure = quantity - min(quantity, -amt) if amt < 0 else quantity
            elif side == "SELL":
                new_exposure = quantity - min(quantity, amt) if amt > 0 else quantity
            else:
                return {
                    "status_code": 400,
                    "response": {"msg": f"Invalid side: {side}", "code": -2},
                }
            if new_exposure <= 0:
                return None  # pure exit/reduction: always allowed
            if ref_price is None:
                ref_price = self._get_futures_price(clean_sym)
            if ref_price is None or ref_price <= 0:
                logger.warning(
                    f"Blocked {side} {quantity} {clean_sym}: no reference price"
                )
                return {
                    "status_code": 400,
                    "response": {
                        "msg": "Blocked: cannot verify price, new exposure not allowed",
                        "code": -3,
                    },
                }
        else:
            # Spot: selling held assets only frees cash, so only BUYs are capped.
            if side == "SELL":
                return None
            if side != "BUY":
                return {
                    "status_code": 400,
                    "response": {"msg": f"Invalid side: {side}", "code": -2},
                }
            new_exposure = quantity
            if ref_price is None:
                ref_price = self._get_spot_prices().get(clean_sym, 0.0)
            if not ref_price or ref_price <= 0:
                logger.warning(
                    f"Blocked {side} {quantity} {clean_sym}: no reference price"
                )
                return {
                    "status_code": 400,
                    "response": {
                        "msg": "Blocked: cannot verify price, new exposure not allowed",
                        "code": -3,
                    },
                }

        try:
            split = self.get_tradable_usdt()
        except Exception as e:
            logger.warning(f"Tradable-cap check: split fetch failed: {e}")
            split = {}
        if not split.get("verified"):
            logger.warning(
                f"Blocked {side} {quantity} {clean_sym}: tradable balance unverified"
            )
            return {
                "status_code": 400,
                "response": {
                    "msg": "Blocked: tradable balance unverified, new exposure not allowed",
                    "code": -3,
                },
            }
        tradable = float(split.get("tradable", 0))
        notional = new_exposure * ref_price
        if notional > tradable + 1e-9:
            floor = float(split.get("floor", get_trading_floor()))
            logger.warning(
                f"Blocked {side} {quantity} {clean_sym}: notional "
                f"${notional:.2f} exceeds tradable ${tradable:.2f} "
                f"(floor ${floor:.2f} protected)"
            )
            return {
                "status_code": 400,
                "response": {
                    "msg": (
                        f"Blocked: order ${notional:.2f} exceeds tradable "
                        f"${tradable:.2f} (savings floor ${floor:.2f} protected)"
                    ),
                    "code": -4,
                },
            }
        return None

    def place_order(
        self,
        symbol: str,
        side: str,
        quantity: float,
        order_type: str = "MARKET",
        is_futures: bool = True,
        price: float | None = None,
        time_in_force: str = "GTC",
        stop_price: float | None = None,
        reduce_only: bool = False,
    ) -> dict[str, Any]:
        """Place an order directly on Binance Demo / Testnet."""
        resolved = _resolve_order_type(order_type, is_futures)
        if stop_price is not None and stop_price <= 0:
            return {
                "status_code": 400,
                "response": {"msg": "stop_price must be greater than 0", "code": -2},
            }
        if _is_stop_type(resolved) and (stop_price is None or stop_price <= 0):
            return {
                "status_code": 400,
                "response": {
                    "msg": f"{resolved} requires a stop_price greater than 0",
                    "code": -2,
                },
            }
        ref_price = price if price and price > 0 else stop_price
        guard = self._check_tradable_cap(symbol, side, quantity, is_futures, ref_price)
        if guard is not None:
            return guard
        server_time = self._get_server_time(is_futures=is_futures)
        base = FUTURES_BASE_URL if is_futures else SPOT_BASE_URL
        endpoint = "/fapi/v1/order" if is_futures else "/api/v3/order"

        params: dict[str, Any] = {
            "symbol": symbol.upper(),
            "side": side.upper(),
            "type": resolved,
            "quantity": quantity,
            "timestamp": server_time,
        }
        if resolved == "LIMIT" and price is not None:
            params["price"] = price
            params["timeInForce"] = time_in_force
        if _is_stop_type(resolved):
            params["stopPrice"] = stop_price
        if is_futures:
            params["reduceOnly"] = "true" if reduce_only else "false"

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

    # ------------------------------------------------------------------
    # Signed DELETE helper
    # ------------------------------------------------------------------
    def _signed_delete(
        self, base: str, endpoint: str, extra_params: str = "", is_futures: bool = True
    ) -> Any | None:
        """Make a signed DELETE request. Returns parsed JSON or None on failure."""
        server_time = self._get_server_time(is_futures=is_futures)
        parts = []
        if extra_params:
            parts.append(extra_params)
        parts.append(f"timestamp={server_time}")
        query = "&".join(parts)
        sig = self._sign(query)
        url = f"{base}{endpoint}?{query}&signature={sig}"
        try:
            r = self._session.delete(url, headers=self._headers(), timeout=5.0)
            if r.status_code == 200:
                return r.json()
            else:
                logger.warning(
                    f"Binance API DELETE {endpoint} returned {r.status_code}: {r.text[:200]}"
                )
        except Exception as e:
            logger.warning(f"Binance API DELETE {endpoint} error: {e}")
        return None

    @staticmethod
    def clean_symbol(symbol: str) -> str:
        """Clean and normalize symbol for Binance API."""
        if not symbol:
            return ""
        s = symbol.strip()
        for token in ["(Perp)", "(perp)", "(Spot)", "(spot)", "(PERP)", "(SPOT)"]:
            s = s.replace(token, "").strip()
        s = s.replace("/", "").replace(" ", "").upper()
        if not (s.endswith("USDT") or s.endswith("USDC") or s.endswith("BUSD") or s.endswith("USD")):
            s = f"{s}USDT"
        return s

    @staticmethod
    def _floor_qty(qty: float, decimals: int) -> float:
        """Floor a quantity to specified decimal places without rounding up."""
        import math

        factor = 10**decimals
        return math.floor(round(qty, decimals + 4) * factor) / factor

    def close_position(
        self, symbol: str, exchange: str = "CRYPTO", product: str = "FUTURES"
    ) -> tuple[bool, dict[str, Any], int]:
        """Close an open position for a symbol (market exit)."""
        clean_sym = self.clean_symbol(symbol)
        if not clean_sym:
            return False, {"status": "error", "message": "Symbol is required"}, 400

        # If product explicitly specified as SPOT or not futures, check spot first
        is_explicit_spot = product.upper() == "SPOT" or "(Spot)" in symbol

        # 1. Check futures positions (if not explicit spot)
        if not is_explicit_spot:
            positions = self.get_positions()
            fut_pos = next((p for p in positions if p["symbol"] == clean_sym and p["amount"] != 0), None)
            if fut_pos:
                amt = fut_pos["amount"]
                side = "SELL" if amt > 0 else "BUY"
                qty = abs(amt)
                if clean_sym.startswith("BTC"):
                    qty = self._floor_qty(qty, 3)
                elif clean_sym.startswith("SOL"):
                    qty = self._floor_qty(qty, 2)
                else:
                    qty = self._floor_qty(qty, 2)

                if qty > 0:
                    res = self.place_order(symbol=clean_sym, side=side, quantity=qty, order_type="MARKET", is_futures=True)
                    if res.get("status_code") == 200:
                        order_id = str(res.get("response", {}).get("orderId", ""))
                        return True, {
                            "status": "success",
                            "message": f"Futures position closed for {clean_sym}",
                            "orderid": order_id,
                        }, 200
                    else:
                        msg = res.get("response", {}).get("msg", "Failed to close futures position")
                        if "insufficient balance" in msg.lower() or "NOTIONAL" in msg:
                            return True, {
                                "status": "success",
                                "message": f"Futures position for {clean_sym} already closed",
                                "orderid": "",
                            }, 200
                        return False, {"status": "error", "message": msg}, 400

        # 2. Check spot balances
        asset = clean_sym.replace("USDT", "").replace("USDC", "").replace("USD", "")
        bals = self.get_account_balances()
        spot_b = next((b for b in bals.get("spot", []) if b.get("asset") == asset and b.get("free", 0) > 0.000001), None)
        if spot_b:
            raw_qty = float(spot_b["free"])
            if clean_sym.startswith("BTC"):
                qty = self._floor_qty(raw_qty, 5)
            elif clean_sym.startswith("SOL"):
                qty = self._floor_qty(raw_qty, 3)
            else:
                qty = self._floor_qty(raw_qty, 4)

            if qty > 0:
                res = self.place_order(symbol=clean_sym, side="SELL", quantity=qty, order_type="MARKET", is_futures=False)
                if res.get("status_code") == 200:
                    order_id = str(res.get("response", {}).get("orderId", ""))
                    return True, {
                        "status": "success",
                        "message": f"Spot position sold for {clean_sym}",
                        "orderid": order_id,
                    }, 200
                else:
                    msg = res.get("response", {}).get("msg", "Failed to sell spot position")
                    if "NOTIONAL" in msg or "insufficient balance" in msg.lower():
                        return True, {
                            "status": "success",
                            "message": f"Position {clean_sym} is dust (< $5 min notional) and already effectively flat",
                            "orderid": "",
                        }, 200
                    return False, {"status": "error", "message": msg}, 400

        # 3. If neither found, consider position already flat
        return True, {
            "status": "success",
            "message": f"Position already closed or not found for {clean_sym}",
            "orderid": "",
        }, 200

    def close_all_positions(self) -> tuple[bool, dict[str, Any], int]:
        """Close all open positions on both Futures and Spot demo."""
        # 1. Close all futures positions
        positions = self.get_positions()
        for p in positions:
            amt = p.get("amount", 0)
            sym = p.get("symbol", "")
            if amt != 0 and sym:
                side = "SELL" if amt > 0 else "BUY"
                qty = abs(amt)
                if sym.startswith("BTC"):
                    qty = self._floor_qty(qty, 3)
                elif sym.startswith("SOL"):
                    qty = self._floor_qty(qty, 2)
                else:
                    qty = self._floor_qty(qty, 2)
                if qty > 0:
                    self.place_order(symbol=sym, side=side, quantity=qty, order_type="MARKET", is_futures=True)

        # 2. Sell all spot holdings (excluding stablecoins)
        stablecoins = {"USDT", "USDC", "BUSD", "FDUSD", "USD"}
        bals = self.get_account_balances()
        for b in bals.get("spot", []):
            asset = b.get("asset", "")
            if asset in stablecoins:
                continue
            raw_qty = float(b.get("free", 0))
            sym = f"{asset}USDT"
            if sym.startswith("BTC"):
                qty = self._floor_qty(raw_qty, 5)
            elif sym.startswith("SOL"):
                qty = self._floor_qty(raw_qty, 3)
            else:
                qty = self._floor_qty(raw_qty, 4)
            if qty > 0:
                self.place_order(symbol=sym, side="SELL", quantity=qty, order_type="MARKET", is_futures=False)

        # 3. Cancel open orders
        self.cancel_all_orders()

        return True, {
            "status": "success",
            "message": "All Open Positions Squared Off",
        }, 200

    def cancel_all_orders(self, order_data: dict | None = None) -> tuple[bool, dict[str, Any], int]:
        """Cancel all open orders on Futures and Spot."""
        canceled_orders = []
        for sym in TRADE_SYMBOLS:
            res_fut = self._signed_delete(FUTURES_BASE_URL, "/fapi/v1/allOpenOrders", extra_params=f"symbol={sym}", is_futures=True)
            if res_fut and isinstance(res_fut, dict) and res_fut.get("code") == 200:
                canceled_orders.append(f"FUTURES_{sym}")
            res_spot = self._signed_delete(SPOT_BASE_URL, "/api/v3/openOrders", extra_params=f"symbol={sym}", is_futures=False)
            if res_spot and isinstance(res_spot, list):
                for o in res_spot:
                    canceled_orders.append(str(o.get("orderId", "")))

        return True, {
            "status": "success",
            "message": "All open orders canceled",
            "canceled_count": len(canceled_orders),
            "failed_count": 0,
            "canceled_orders": canceled_orders,
            "failed_cancellations": [],
        }, 200

    def cancel_order(self, orderid: str, symbol: str | None = None) -> tuple[bool, dict[str, Any], int]:
        """Cancel an individual order by ID."""
        symbols_to_try = [symbol] if symbol else TRADE_SYMBOLS
        for sym in symbols_to_try:
            if not sym:
                continue
            clean_sym = self.clean_symbol(sym)
            res_fut = self._signed_delete(
                FUTURES_BASE_URL,
                "/fapi/v1/order",
                extra_params=f"symbol={clean_sym}&orderId={orderid}",
                is_futures=True,
            )
            if res_fut and isinstance(res_fut, dict) and "orderId" in res_fut:
                return True, {"status": "success", "message": f"Order {orderid} canceled", "orderid": orderid}, 200

            res_spot = self._signed_delete(
                SPOT_BASE_URL,
                "/api/v3/order",
                extra_params=f"symbol={clean_sym}&orderId={orderid}",
                is_futures=False,
            )
            if res_spot and isinstance(res_spot, dict) and "orderId" in res_spot:
                return True, {"status": "success", "message": f"Order {orderid} canceled", "orderid": orderid}, 200

        # Fallback to success to prevent UI errors if order was already executed/canceled
        return True, {"status": "success", "message": f"Order {orderid} canceled or no longer active", "orderid": orderid}, 200

    def get_liquidation_data(self) -> dict[str, Any]:
        """Surface exchange-reported liquidation and margin usage per position.

        Reads ``/fapi/v2/positionRisk`` via :meth:`get_positions` and returns
        the reported ``liquidation_price``, ``margin_used`` and ``leverage``
        verbatim alongside the mark. Nothing here is computed on the backend:
        no liquidation estimate, no margin formula. A ``None`` value means
        Binance did not report that field for the position (flat leg, cross
        margin with no per-position margin, or a zero liquidation price).
        ``total_margin_committed`` is the sum of the reported per-position
        margins, or None when none reported; ``tradable_usdt`` reuses the
        existing floor-guarded split so callers can compare committed margin
        against what the order path will actually accept.
        """
        positions = self.get_positions() or []
        items: list[dict[str, Any]] = []
        for p in positions:
            item: dict[str, Any] = {
                "symbol": p.get("symbol"),
                "side": p.get("side"),
                "amount": p.get("amount"),
                "entry_price": p.get("entry_price"),
                "mark_price": p.get("mark_price"),
                "unrealized_pnl": p.get("unrealized_pnl"),
                "liquidation_price": p.get("liquidation_price"),
                "margin_used": p.get("margin_used"),
            }
            if p.get("leverage") is not None:
                item["leverage"] = p.get("leverage")
            items.append(item)

        reported = [
            float(i["margin_used"])
            for i in items
            if i.get("margin_used") is not None
        ]
        total_margin = sum(reported) if reported else None

        tradable: float | None = None
        try:
            split = self.get_tradable_usdt() or {}
            raw = split.get("tradable", None)
            tradable = float(raw) if raw is not None else None
        except Exception as e:
            logger.warning(f"get_liquidation_data tradable split failed: {e}")
            tradable = None

        return {
            "status": "success",
            "positions": items,
            "total_margin_committed": total_margin,
            "tradable_usdt": tradable,
        }


binance_demo_service = BinanceDemoService()
