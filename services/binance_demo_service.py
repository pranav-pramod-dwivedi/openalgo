"""
Binance Demo / Testnet Broker Service for OpenAlgo
Direct REST API integration with Binance Demo (Spot & Futures).
"""

import hashlib
import hmac
import os
import time
from typing import Any, Dict, List, Optional
import requests
from utils.logging import get_logger

logger = get_logger(__name__)

BINANCE_DEMO_API_KEY = os.getenv("BINANCE_DEMO_API_KEY", "lWKwJR8uLprw7pmhk6XqIb8iLwjWeY2CH0p2gwQKT8dNyf3dQvb6y8BH6cxFtQT8")
BINANCE_DEMO_SECRET_KEY = os.getenv("BINANCE_DEMO_SECRET_KEY", "KdjplsfWdKXfxZLMngimssYewD0FJVausArtXztOmZJPxLPxnSzkvYA4uY4ZhSAr")

SPOT_BASE_URL = "https://demo-api.binance.com"
FUTURES_BASE_URL = "https://testnet.binancefuture.com"

class BinanceDemoService:
    def __init__(self, api_key: str = BINANCE_DEMO_API_KEY, secret_key: str = BINANCE_DEMO_SECRET_KEY):
        self.api_key = api_key
        self.secret_key = secret_key

    def _get_server_time(self, is_futures: bool = True) -> int:
        base = FUTURES_BASE_URL if is_futures else SPOT_BASE_URL
        endpoint = "/fapi/v1/time" if is_futures else "/api/v3/time"
        try:
            r = requests.get(f"{base}{endpoint}", timeout=2.0)
            if r.status_code == 200:
                return r.json().get("serverTime", int(time.time() * 1000))
        except Exception:
            pass
        return int(time.time() * 1000)

    def _sign(self, query: str) -> str:
        return hmac.new(self.secret_key.encode("utf-8"), query.encode("utf-8"), hashlib.sha256).hexdigest()

    def get_account_balances(self) -> Dict[str, Any]:
        """Fetch balances from Spot and Futures demo."""
        server_time = self._get_server_time(is_futures=False)
        query = f"timestamp={server_time}"
        sig = self._sign(query)
        headers = {"X-MBX-APIKEY": self.api_key}

        # Spot balances
        spot_resp = requests.get(f"{SPOT_BASE_URL}/api/v3/account?{query}&signature={sig}", headers=headers, timeout=3.0)
        spot_balances = []
        if spot_resp.status_code == 200:
            for b in spot_resp.json().get("balances", []):
                if float(b.get("free", 0)) > 0 or float(b.get("locked", 0)) > 0:
                    spot_balances.append({
                        "asset": b["asset"],
                        "free": float(b["free"]),
                        "locked": float(b["locked"]),
                        "total": float(b["free"]) + float(b["locked"]),
                    })

        # Futures balances
        fut_time = self._get_server_time(is_futures=True)
        fut_query = f"timestamp={fut_time}"
        fut_sig = self._sign(fut_query)
        fut_resp = requests.get(f"{FUTURES_BASE_URL}/fapi/v2/balance?{fut_query}&signature={fut_sig}", headers=headers, timeout=3.0)
        fut_balances = []
        if fut_resp.status_code == 200:
            for b in fut_resp.json():
                bal = float(b.get("balance", 0))
                if bal > 0:
                    fut_balances.append({
                        "asset": b["asset"],
                        "balance": bal,
                        "available": float(b.get("availableBalance", bal)),
                    })

        return {
            "status": "success",
            "spot": spot_balances,
            "futures": fut_balances,
        }

    def get_positions(self) -> List[Dict[str, Any]]:
        """Fetch open positions from Futures testnet."""
        server_time = self._get_server_time(is_futures=True)
        query = f"timestamp={server_time}"
        sig = self._sign(query)
        headers = {"X-MBX-APIKEY": self.api_key}

        resp = requests.get(f"{FUTURES_BASE_URL}/fapi/v2/positionRisk?{query}&signature={sig}", headers=headers, timeout=3.0)
        positions = []
        if resp.status_code == 200:
            for p in resp.json():
                amt = float(p.get("positionAmt", 0))
                if amt != 0:
                    positions.append({
                        "symbol": p["symbol"],
                        "amount": amt,
                        "side": "LONG" if amt > 0 else "SHORT",
                        "entry_price": float(p.get("entryPrice", 0)),
                        "mark_price": float(p.get("markPrice", 0)),
                        "unrealized_pnl": float(p.get("unRealizedProfit", 0)),
                    })
        return positions

    def get_margin_data(self) -> Dict[str, Any]:
        """Format Binance live Demo data into OpenAlgo's standard margin dict with $100 USD baseline starting capital."""
        bals = self.get_account_balances()
        positions = self.get_positions()

        unrealized_pnl = sum(p["unrealized_pnl"] for p in positions)
        utilised_margin = sum(abs(p["amount"]) * p["entry_price"] for p in positions)
        margin_required = utilised_margin / 10.0 if utilised_margin > 0 else 0.0

        # Calculate realized PnL from trades
        trades = self.get_tradebook_formatted()
        realized_pnl = sum(float(t.get("pnl", 0.0)) for t in trades)

        # Baseline starting capital requested: $100.00 USD
        base_capital = 100.00
        net_equity = base_capital + realized_pnl + unrealized_pnl
        avail_cash = max(0.0, base_capital + realized_pnl - margin_required)

        # Filter out USDC entirely to hide 15k USDC collateral as requested
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
            r = requests.get(f"{SPOT_BASE_URL}/api/v3/ticker/price", timeout=2.0)
            if r.status_code == 200:
                return {x["symbol"]: float(x["price"]) for x in r.json()}
        except Exception as e:
            logger.warning(f"Error fetching spot prices: {e}")
        return {}

    def get_positionbook_formatted(self) -> List[Dict[str, Any]]:
        """Return unified open positions (Spot Holdings + Futures) for OpenAlgo positionbook."""
        res = []

        # 1. Futures open positions
        positions = self.get_positions()
        for p in positions:
            amt = p["amount"]
            res.append({
                "symbol": p["symbol"],
                "product": "FUTURES",
                "instrument": f"{p['symbol']} (Perp)",
                "quantity": amt,
                "netqty": amt,
                "buyqty": amt if amt > 0 else 0,
                "sellqty": abs(amt) if amt < 0 else 0,
                "average_price": p["entry_price"],
                "buyavgprice": p["entry_price"] if amt > 0 else 0,
                "sellavgprice": p["entry_price"] if amt < 0 else 0,
                "ltp": p["mark_price"],
                "m2m": p["unrealized_pnl"],
                "pnl": p["unrealized_pnl"],
                "pnlpercent": round(((p["mark_price"] - p["entry_price"]) / p["entry_price"]) * 100, 2) if p["entry_price"] > 0 else 0.0,
                "exchange": "CRYPTO",
            })

        # 2. Spot open positions (unified crypto holdings)
        bals = self.get_account_balances()
        trades = self.get_tradebook_formatted()
        spot_prices = self._get_spot_prices()

        # Calculate average buy prices per symbol from tradebook
        buy_stats: Dict[str, Dict[str, float]] = {}
        for t in trades:
            if t.get("action") == "BUY" and t.get("product") == "SPOT":
                sym = t.get("symbol", "")
                q = float(t.get("quantity", 0))
                p = float(t.get("average_price", 0))
                if sym not in buy_stats:
                    buy_stats[sym] = {"qty": 0.0, "cost": 0.0}
                buy_stats[sym]["qty"] += q
                buy_stats[sym]["cost"] += q * p

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
            avg_px = (cost_info["cost"] / cost_info["qty"]) if cost_info.get("qty", 0) > 0 else spot_prices.get(sym, 0.0)
            ltp = spot_prices.get(sym, avg_px)
            pnl = round((ltp - avg_px) * qty, 4)
            pnlpercent = round(((ltp - avg_px) / avg_px) * 100, 2) if avg_px > 0 else 0.0

            res.append({
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
            })

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
                p = float(t.get("average_price", 0))
                if sym not in buy_stats:
                    buy_stats[sym] = {"qty": 0.0, "cost": 0.0}
                buy_stats[sym]["qty"] += q
                buy_stats[sym]["cost"] += q * p

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
            avg_px = (cost_info["cost"] / cost_info["qty"]) if cost_info.get("qty", 0) > 0 else spot_prices.get(sym, 0.0)
            ltp = spot_prices.get(sym, avg_px)
            pnl = round((ltp - avg_px) * qty, 4)
            pnlpercent = round(((ltp - avg_px) / avg_px) * 100, 2) if avg_px > 0 else 0.0

            cur_val = qty * ltp
            inv_val = qty * avg_px
            total_holding_value += cur_val
            total_inv_value += inv_val

            holdings.append({
                "symbol": sym,
                "exchange": "CRYPTO",
                "quantity": qty,
                "product": "SPOT",
                "average_price": round(avg_px, 2),
                "ltp": round(ltp, 2),
                "pnl": pnl,
                "pnlpercent": pnlpercent,
            })

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

    def get_tradebook_formatted(self) -> List[Dict[str, Any]]:
        """Return trades from Spot and Futures formatted for OpenAlgo tradebook with money change and value."""
        headers = {"X-MBX-APIKEY": self.api_key}
        trades = []

        # 1. Futures trades for SOLUSDT and BTCUSDT
        for sym in ["SOLUSDT", "BTCUSDT"]:
            fut_time = self._get_server_time(is_futures=True)
            q = f"symbol={sym}&timestamp={fut_time}"
            sig = self._sign(q)
            try:
                r = requests.get(f"{FUTURES_BASE_URL}/fapi/v1/userTrades?{q}&signature={sig}", headers=headers, timeout=2.0)
                if r.status_code == 200:
                    for t in r.json():
                        px = float(t.get("price", 0))
                        qty = float(t.get("qty", 0))
                        trade_val = round(px * qty, 4)
                        side = t.get("side", "BUY")
                        # Money change: Cash outflow (-) on BUY, Cash inflow (+) on SELL
                        money_change = round(-trade_val if side == "BUY" else trade_val, 4)
                        realized_pnl = float(t.get("realizedPnl", 0))
                        trades.append({
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
                            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t["time"] / 1000)),
                            "exchange": "CRYPTO",
                            "trade_id": str(t.get("id")),
                        })
            except Exception as e:
                logger.warning(f"Error fetching futures trades for {sym}: {e}")

        # 2. Spot trades for SOLUSDT and BTCUSDT
        raw_spot_trades = []
        for sym in ["SOLUSDT", "BTCUSDT"]:
            spot_time = self._get_server_time(is_futures=False)
            q = f"symbol={sym}&timestamp={spot_time}"
            sig = self._sign(q)
            try:
                r = requests.get(f"{SPOT_BASE_URL}/api/v3/myTrades?{q}&signature={sig}", headers=headers, timeout=2.0)
                if r.status_code == 200:
                    raw_spot_trades.extend(r.json())
            except Exception as e:
                logger.warning(f"Error fetching spot trades for {sym}: {e}")

        # Sort raw spot trades chronologically to compute FIFO realized PnL
        raw_spot_trades.sort(key=lambda x: x.get("time", 0))
        spot_inventory: Dict[str, List[Dict[str, float]]] = {}

        for t in raw_spot_trades:
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
                # Sell: match against most recent buy (LIFO) so scalp PnL accurately reflects the trade
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

            trades.append({
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
                "timestamp": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t["time"] / 1000)),
                "exchange": "CRYPTO",
                "trade_id": str(t.get("id")),
            })

        trades.sort(key=lambda x: x.get("timestamp", ""), reverse=True)
        return trades

    def get_orderbook_formatted(self) -> List[Dict[str, Any]]:
        """Return orders from Spot and Futures formatted for OpenAlgo orderbook."""
        headers = {"X-MBX-APIKEY": self.api_key}
        orders = []

        # 1. Futures orders
        for sym in ["SOLUSDT", "BTCUSDT"]:
            fut_time = self._get_server_time(is_futures=True)
            q = f"symbol={sym}&timestamp={fut_time}"
            sig = self._sign(q)
            try:
                r = requests.get(f"{FUTURES_BASE_URL}/fapi/v1/allOrders?{q}&signature={sig}", headers=headers, timeout=2.0)
                if r.status_code == 200:
                    for o in r.json():
                        orders.append({
                            "orderid": str(o.get("orderId")),
                            "symbol": o.get("symbol"),
                            "action": o.get("side"),
                            "quantity": float(o.get("origQty", 0)),
                            "filledqty": float(o.get("executedQty", 0)),
                            "price": float(o.get("price", 0)),
                            "avgprice": float(o.get("avgPrice", 0)),
                            "orderstatus": o.get("status", "FILLED"),
                            "pricetype": o.get("type", "MARKET"),
                            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(o["time"] / 1000)),
                            "exchange": "BINANCE_FUTURES",
                        })
            except Exception as e:
                logger.warning(f"Error fetching futures orders for {sym}: {e}")

        # 2. Spot orders
        for sym in ["SOLUSDT", "BTCUSDT"]:
            spot_time = self._get_server_time(is_futures=False)
            q = f"symbol={sym}&timestamp={spot_time}"
            sig = self._sign(q)
            try:
                r = requests.get(f"{SPOT_BASE_URL}/api/v3/allOrders?{q}&signature={sig}", headers=headers, timeout=2.0)
                if r.status_code == 200:
                    for o in r.json():
                        orders.append({
                            "orderid": str(o.get("orderId")),
                            "symbol": o.get("symbol"),
                            "action": o.get("side"),
                            "quantity": float(o.get("origQty", 0)),
                            "filledqty": float(o.get("executedQty", 0)),
                            "price": float(o.get("price", 0)),
                            "avgprice": float(o.get("cummulativeQuoteQty", 0)) / max(float(o.get("executedQty", 1)), 1e-9),
                            "orderstatus": o.get("status", "FILLED"),
                            "pricetype": o.get("type", "MARKET"),
                            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(o["time"] / 1000)),
                            "exchange": "BINANCE_SPOT",
                        })
            except Exception as e:
                logger.warning(f"Error fetching spot orders for {sym}: {e}")

        orders.sort(key=lambda x: x.get("timestamp", ""), reverse=True)
        return orders

    def place_order(
        self,
        symbol: str,
        side: str,
        quantity: float,
        order_type: str = "MARKET",
        is_futures: bool = True
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
        headers = {"X-MBX-APIKEY": self.api_key}

        resp = requests.post(f"{base}{endpoint}?{query}&signature={sig}", headers=headers, timeout=4.0)
        return {
            "status_code": resp.status_code,
            "response": resp.json(),
        }

binance_demo_service = BinanceDemoService()
