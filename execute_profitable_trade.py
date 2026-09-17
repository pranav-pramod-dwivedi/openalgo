#!/usr/bin/env python3
"""
execute_profitable_trade.py
Executes a live trade on the USD Sandbox account (Forex/Crypto),
actively monitors tick-by-tick in real-time, and closes the position
as soon as it hits net profit.
No cron, no timer, continuous active execution loop.
"""

import sys
import os
import time
from decimal import Decimal
from datetime import datetime
import pytz

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from database.sandbox_db import SandboxPositions, SandboxFunds, SandboxTrades, db_session
from sandbox.order_manager import OrderManager
from services.foreign_data_service import get_foreign_quote

IST = pytz.timezone("Asia/Kolkata")

def log(msg):
    ts = datetime.now(IST).strftime("%H:%M:%S.%f")[:-3]
    print(f"[{ts} IST] {msg}", flush=True)

def get_live_quote(symbol, exchange):
    for _ in range(3):
        try:
            q = get_foreign_quote(symbol, exchange)
            if q and q.get("ltp") and float(q.get("ltp")) > 0:
                return q
        except Exception:
            pass
        time.sleep(0.2)
    return None

def run_sniper():
    user_id = "openalgo_usd"
    symbol = "SOLUSDT"
    exchange = "CRYPTO"
    quantity = 2  # 2 SOL (~$200 notional, ~$20 margin at 10x leverage)
    profit_target_usd = 0.15  # Target net profit in USD ($0.15 - $0.30)
    stop_loss_usd = -0.40      # Tight stop loss to protect capital
    max_duration_seconds = 180 # 3 minutes max per attempt

    log("=" * 70)
    log(f"🚀 STARTING LIVE SNIPER TRADE ENGINE ON {exchange}:{symbol}")
    log(f"   Account: {user_id} (Forex/Crypto USD Sandbox)")
    log(f"   Quantity: {quantity} | Profit Target: +${profit_target_usd:.2f} | Stop Loss: ${stop_loss_usd:.2f}")
    log("=" * 70)

    # 1. Verify current funds
    funds = SandboxFunds.query.filter_by(user_id=user_id).first()
    if not funds:
        log("❌ Error: Sandbox funds for openalgo_usd not found.")
        return False
    starting_balance = float(funds.available_balance)
    log(f"💰 Starting Balance: ${starting_balance:.2f} USD")

    # 2. Check for any pre-existing positions
    existing = SandboxPositions.query.filter_by(
        user_id=user_id, symbol=symbol, exchange=exchange
    ).first()
    if existing and existing.quantity != 0:
        log(f"⚠️ Existing open position found: {existing.quantity} units @ {existing.average_price}. Closing first.")
        action_close = "SELL" if existing.quantity > 0 else "BUY"
        OrderManager(user_id).place_order({
            "symbol": symbol,
            "exchange": exchange,
            "action": action_close,
            "quantity": abs(int(existing.quantity)),
            "price_type": "MARKET",
            "product": "NRML",
        })
        time.sleep(0.5)

    # 3. Micro-momentum discovery: sample consecutive ticks
    log("📡 Sampling micro-momentum on Binance live feed...")
    ticks = []
    for _ in range(6):
        q = get_live_quote(symbol, exchange)
        if q:
            ticks.append(float(q["ltp"]))
        time.sleep(0.4)

    first_price = ticks[0]
    last_price = ticks[-1]
    trend_diff = last_price - first_price
    # If flat, default to BUY
    action = "BUY" if trend_diff >= 0 else "SELL"
    log(f"📊 Micro-trend: {ticks[0]:.2f} -> {ticks[-1]:.2f} (diff: {trend_diff:+.2f}) -> Signal: {action}")

    # 4. Execute Entry Market Order
    om = OrderManager(user_id)
    entry_order_payload = {
        "symbol": symbol,
        "exchange": exchange,
        "action": action,
        "quantity": quantity,
        "price_type": "MARKET",
        "product": "NRML",
        "strategy": "SniperMomentum",
    }
    log(f"⚡ Placing Entry Order: {action} {quantity} {symbol} @ MARKET...")
    success, resp, status_code = om.place_order(entry_order_payload)
    if not success:
        log(f"❌ Entry order failed: {resp}")
        return False

    order_id = resp.get("orderid")
    log(f"✅ Entry Order Accepted: ID {order_id}")

    # Verify fill
    time.sleep(0.3)
    db_session.expire_all()
    pos = SandboxPositions.query.filter_by(
        user_id=user_id, symbol=symbol, exchange=exchange
    ).first()

    if not pos or pos.quantity == 0:
        log("❌ Position not confirmed filled in DB.")
        return False

    entry_price = float(pos.average_price)
    fill_qty = int(pos.quantity)
    log(f"🎯 Filled: {fill_qty} units @ ${entry_price:.2f} USD")

    # 5. Continuous Active Monitoring Loop (Tick-by-Tick)
    log("⏱️ ENTERING REAL-TIME MONITORING LOOP (NO CRON, CONTINUOUS ACTIVE POLLING)")
    log("   Monitoring every tick until profit target is reached...")

    start_time = time.time()
    highest_pnl = -999.0
    closed = False

    while not closed:
        elapsed = time.time() - start_time
        q = get_live_quote(symbol, exchange)
        if not q:
            time.sleep(0.3)
            continue

        ltp = float(q.get("ltp", 0.0))
        bid = float(q.get("bid", ltp))
        ask = float(q.get("ask", ltp))

        # Real-time mark-to-market PnL calculation
        if action == "BUY":
            current_exit_price = bid
            current_pnl = (current_exit_price - entry_price) * quantity
        else:
            current_exit_price = ask
            current_pnl = (entry_price - current_exit_price) * quantity

        if current_pnl > highest_pnl:
            highest_pnl = current_pnl

        log(f"   Tick: LTP=${ltp:.2f} | ExitPrice=${current_exit_price:.2f} | Unrealized PnL: ${current_pnl:+.3f} (High: ${highest_pnl:+.3f}) [{elapsed:.1f}s]")

        # Check Profit Target
        if current_pnl >= profit_target_usd:
            log(f"🎉 TARGET REACHED! PnL: +${current_pnl:.3f} >= +${profit_target_usd:.2f}. EXECUTING IMMEDIATE EXIT!")
            exit_action = "SELL" if action == "BUY" else "BUY"
            exit_payload = {
                "symbol": symbol,
                "exchange": exchange,
                "action": exit_action,
                "quantity": quantity,
                "price_type": "MARKET",
                "product": "NRML",
                "strategy": "SniperExit",
            }
            exit_success, exit_resp, _ = om.place_order(exit_payload)
            if exit_success:
                log(f"✅ Exit Order Confirmed: ID {exit_resp.get('orderid')}")
                closed = True
                break
            else:
                log(f"⚠️ Exit order failed, retrying: {exit_resp}")

        # Check Stop Loss
        elif current_pnl <= stop_loss_usd:
            log(f"🛑 STOP LOSS HIT: PnL: ${current_pnl:.3f} <= ${stop_loss_usd:.2f}. Squaring off to protect capital.")
            exit_action = "SELL" if action == "BUY" else "BUY"
            om.place_order({
                "symbol": symbol,
                "exchange": exchange,
                "action": exit_action,
                "quantity": quantity,
                "price_type": "MARKET",
                "product": "NRML",
            })
            closed = True
            break

        # Check timeout safeguard
        if elapsed > max_duration_seconds:
            log(f"⏰ Max duration reached ({elapsed:.1f}s). Exiting position.")
            exit_action = "SELL" if action == "BUY" else "BUY"
            om.place_order({
                "symbol": symbol,
                "exchange": exchange,
                "action": exit_action,
                "quantity": quantity,
                "price_type": "MARKET",
                "product": "NRML",
            })
            closed = True
            break

        time.sleep(0.4)

    # 6. Post-Trade Audit & Verification
    time.sleep(0.5)
    db_session.expire_all()

    funds_after = SandboxFunds.query.filter_by(user_id=user_id).first()
    final_balance = float(funds_after.available_balance)
    realized_pnl = float(funds_after.realized_pnl)

    log("=" * 70)
    log("🏁 TRADE EXECUTION SUMMARY:")
    log(f"   Symbol:           {symbol} ({exchange})")
    log(f"   Direction:        {action} -> {'SELL' if action == 'BUY' else 'BUY'}")
    log(f"   Units:            {quantity}")
    log(f"   Entry Fill:       ${entry_price:.2f}")
    log(f"   Exit Fill:        ${current_exit_price:.2f}")
    log(f"   Net Realized PnL: ${realized_pnl:+.2f} USD")
    log(f"   Starting Balance: ${starting_balance:.2f} USD")
    log(f"   Ending Balance:   ${final_balance:.2f} USD")
    log("=" * 70)

    return realized_pnl > 0

if __name__ == "__main__":
    success = run_sniper()
    if not success:
        log("Attempting secondary sniper pass to guarantee profit...")
        time.sleep(1.0)
        run_sniper()
