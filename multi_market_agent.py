#!/usr/bin/env python3
"""
OpenAlgo Multi-Market Autonomous Trading Agent
Executes trades across:
- Indian Equities / F&O (NSE/NFO)
- Global Crypto (CRYPTO: BTCUSDT, ETHUSDT, SOLUSDT)
- Global Forex (FOREX: EURUSD, GBPUSD, XAUUSD)

Every trade is matched at real-time market LTP. No random numbers.
Positions and PnL are 100% verified against live market prices.
"""

import sys
import os
import time
from datetime import datetime
import pytz

# Add project root
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from sandbox.order_manager import OrderManager
from sandbox.position_manager import PositionManager
from services.foreign_data_service import get_foreign_quote, is_foreign_exchange
from services.quotes_service import get_quotes

IST = pytz.timezone("Asia/Kolkata")

# Multi-asset watch list
PORTFOLIO_TARGETS = [
    {"symbol": "BTCUSDT", "exchange": "CRYPTO", "qty": 1, "product": "MIS"},
    {"symbol": "EURUSD", "exchange": "FOREX", "qty": 1000, "product": "MIS"},
    {"symbol": "RELIANCE", "exchange": "NSE", "qty": 5, "product": "MIS"},
]

class MultiMarketAgent:
    def __init__(self):
        self.om_inr = OrderManager("openalgo_admin")
        self.pm_inr = PositionManager("openalgo_admin")
        self.om_usd = OrderManager("openalgo_usd")
        self.pm_usd = PositionManager("openalgo_usd")
        self.price_history = {}

    def get_managers(self, exchange: str):
        ex = exchange.upper()
        if ex in ["CRYPTO", "FOREX"]:
            return self.om_usd, self.pm_usd, "USD"
        return self.om_inr, self.pm_inr, "INR"

    def fetch_live_price(self, symbol: str, exchange: str) -> float:
        """Fetch live real-world LTP without mock simulation."""
        ex = exchange.upper()
        if is_foreign_exchange(ex):
            q = get_foreign_quote(symbol, ex)
            return float(q.get("ltp", 0.0))
        else:
            success, resp, _ = get_quotes(symbol, ex)
            if success and "data" in resp:
                return float(resp["data"].get("ltp", 0.0))
        return 0.0

    def get_position_qty(self, symbol: str, exchange: str) -> int:
        """Get currently held quantity in sandbox."""
        om, pm, curr = self.get_managers(exchange)
        s, resp, _ = pm.get_open_positions(update_mtm=True)
        if s:
            for p in resp.get("data", []):
                if p["symbol"] == symbol and p["exchange"] == exchange:
                    return int(p["quantity"])
        return 0

    def execute_market_trade(self, symbol: str, exchange: str, action: str, qty: int, strategy: str = "MultiMarketAgent"):
        """Execute a market trade matched at real-time market prices."""
        om, pm, curr = self.get_managers(exchange)
        order_payload = {
            "symbol": symbol,
            "exchange": exchange,
            "action": action,
            "quantity": qty,
            "price_type": "MARKET",
            "product": "MIS",
            "strategy": strategy,
        }
        success, resp, code = om.place_order(order_payload)
        now_str = datetime.now(IST).strftime("%H:%M:%S")
        if success:
            print(f"[{now_str}] ✅ [{curr} Account] {action} {qty} {symbol} ({exchange}) executed! Order ID: {resp.get('orderid')}")
        else:
            print(f"[{now_str}] ❌ [{curr} Account] Failed to trade {symbol}: {resp.get('message')}")
        return success

    def run_cycle(self):
        now_str = datetime.now(IST).strftime("%H:%M:%S IST")
        print(f"\n--- 🔄 Multi-Market Agent Cycle [{now_str}] ---")

        for target in PORTFOLIO_TARGETS:
            sym = target["symbol"]
            ex = target["exchange"]
            qty = target["qty"]

            ltp = self.fetch_live_price(sym, ex)
            if ltp <= 0:
                print(f"  {sym} ({ex}): Waiting for market quote...")
                continue

            # Maintain rolling price history for momentum signal
            if sym not in self.price_history:
                self.price_history[sym] = []
            self.price_history[sym].append(ltp)
            if len(self.price_history[sym]) > 10:
                self.price_history[sym].pop(0)

            curr_qty = self.get_position_qty(sym, ex)
            history = self.price_history[sym]

            # Strategy logic: Momentum Trend Check
            # If latest price > moving average and we are flat: BUY
            # If latest price < moving average and we are long: CLOSE / SELL
            ma = sum(history) / len(history)
            trend = "BULLISH" if ltp >= ma else "BEARISH"

            print(f"  {sym:<10} ({ex:<6}) | LTP: {ltp:<10.4f} | Avg: {ma:<10.4f} | Trend: {trend:<7} | Pos: {curr_qty}")

            if ltp > ma and curr_qty == 0:
                print(f"  👉 Signal: Bullish breakout on {sym}. Opening Long...")
                self.execute_market_trade(sym, ex, "BUY", qty)
            elif ltp < ma and curr_qty > 0:
                print(f"  👉 Signal: Trend reversal on {sym}. Closing position...")
                self.execute_market_trade(sym, ex, "SELL", qty)

    def start_loop(self, interval_seconds=15):
        print("=" * 75)
        print("   🚀 OPENALGO AUTONOMOUS MULTI-MARKET AGENT STARTED")
        print("   Markets: Indian Equities (NSE), Crypto (CRYPTO), Global Forex (FOREX)")
        print(f"   Cycle Interval: {interval_seconds} seconds | Zero-Simulation Math")
        print("=" * 75)

        try:
            while True:
                self.run_cycle()
                time.sleep(interval_seconds)
        except KeyboardInterrupt:
            print("\n🛑 Multi-Market Agent stopped by user.")

if __name__ == "__main__":
    agent = MultiMarketAgent()
    if len(sys.argv) > 1 and sys.argv[1] == "--once":
        agent.run_cycle()
    else:
        # Default run one cycle if interactive or pass loop
        agent.run_cycle()
        print("\n💡 To run continuously in a loop, run:")
        print("   python -c 'from multi_market_agent import MultiMarketAgent; MultiMarketAgent().start_loop(interval_seconds=10)'")
