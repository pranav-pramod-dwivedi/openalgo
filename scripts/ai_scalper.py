#!/usr/bin/env python3
"""Supervised Binance demo scalper.

Deterministic, audited, and capped by the broker layer:
  - one position at a time, only while flat
  - entry: last 1m close crosses the 9-period SMA by >= MOMENTUM_PCT
  - exit: native reduce-only STOP_MARKET / TAKE_PROFIT_MARKET resting on the venue
  - poll: 5 seconds (Binance testnet cadence, not milliseconds)
  - kill: Ctrl+C cancels resting exits and closes any open position

Run:
    uv run python scripts/ai_scalper.py --symbol BTCUSDT --size 0.001 --yes
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.binance_demo_service import BinanceDemoService  # noqa: E402
from services.foreign_data_service import get_foreign_history  # noqa: E402

POLL_SECONDS = 5.0
MAX_HOLD_SECONDS = 300.0
DAILY_LOSS_LIMIT_USD = 5.0

LOG_PATH = Path("log/ai_scalper.jsonl")
LOG_PATH.parent.mkdir(exist_ok=True)


def journal(entry: dict) -> None:
    entry["ts"] = datetime.now(UTC).isoformat()
    with LOG_PATH.open("a") as f:
        f.write(json.dumps(entry, default=str) + "\n")
    print(json.dumps(entry, default=str))


def trend_signal(symbol: str) -> tuple[str, dict]:
    candles = get_foreign_history(symbol, "CRYPTO", "1m", "", "")
    if len(candles) < 20:
        return "HOLD", {"reason": "not enough candles"}
    closes = [c["close"] for c in candles[-12:]]
    sma = sum(closes[:9]) / 9.0
    last = closes[-1]
    move = (last - sma) / sma
    if move > 0.0008:
        return "LONG", {"reason": f"price {last:.2f} > sma9 {sma:.2f} by {move*100:.3f}%", "sma9": round(sma, 2), "last": last}
    if move < -0.0008:
        return "SHORT", {"reason": f"price {last:.2f} < sma9 {sma:.2f} by {move*100:.3f}%", "sma9": round(sma, 2), "last": last}
    return "HOLD", {"reason": f"price within band ({move*100:.3f}%)", "last": last}


def sma_reversion(symbol: str, position: dict) -> bool:
    candles = get_foreign_history(symbol, "CRYPTO", "1m", "", "")
    if len(candles) < 10:
        return False
    closes = [c["close"] for c in candles[-10:]]
    sma = sum(closes[:9]) / 9.0
    last = closes[-1]
    return (position["side"] == "LONG" and last < sma) or (position["side"] == "SHORT" and last > sma)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--symbol", default="BTCUSDT")
    p.add_argument("--size", type=float, default=0.001)
    p.add_argument("--leverage", type=int, default=1)
    p.add_argument("--tp-pct", type=float, default=0.004)
    p.add_argument("--sl-pct", type=float, default=0.002)
    p.add_argument("--yes", action="store_true", help="required to trade; otherwise dry-run print only")
    args = p.parse_args()

    b = BinanceDemoService()
    symbol = args.symbol
    qty = args.size
    session_pnl = 0.0
    entry_ts = [None]
    journal({"event": "start", "symbol": symbol, "size": qty, "tp_pct": args.tp_pct, "sl_pct": args.sl_pct, "armed": args.yes})

    while True:
        try:
            positions = b.get_positions()
            open_pos = next((p for p in positions if p["symbol"] == symbol and float(p.get("amount", 0)) != 0), None)

            if open_pos is None:
                if session_pnl <= -DAILY_LOSS_LIMIT_USD:
                    journal({"event": "halt", "reason": "daily loss limit", "session_pnl": round(session_pnl, 4)})
                    return 0
                signal, meta = trend_signal(symbol)
                entry_ts[0] = None
                journal({"event": "signal", "signal": signal, **meta})
                if signal in ("LONG", "SHORT") and args.yes:
                    side = "BUY" if signal == "LONG" else "SELL"
                    res = b.place_order(symbol=symbol, side=side, quantity=qty, order_type="MARKET", is_futures=True)
                    journal({"event": "entry", "side": side, "result": res})
                    time.sleep(1.0)
                    positions = b.get_positions()
                    pos = next((p for p in positions if p["symbol"] == symbol and float(p.get("amount", 0)) != 0), None)
                    if pos:
                        entry = float(pos["entry_price"])
                        if side == "BUY":
                            tp = round(entry * (1 + args.tp_pct), 2)
                            sl = round(entry * (1 - args.sl_pct), 2)
                            exit_side = "SELL"
                        else:
                            tp = round(entry * (1 - args.tp_pct), 2)
                            sl = round(entry * (1 + args.sl_pct), 2)
                            exit_side = "BUY"
                        tp_res = b.place_order(symbol=symbol, side=exit_side, quantity=qty, order_type="TAKE_PROFIT_MARKET", is_futures=True, stop_price=tp, reduce_only=True)
                        sl_res = b.place_order(symbol=symbol, side=exit_side, quantity=qty, order_type="STOP_MARKET", is_futures=True, stop_price=sl, reduce_only=True)
                        journal({"event": "brackets", "tp": tp, "sl": sl, "tp_result": tp_res, "sl_result": sl_res})
                        entry_ts[0] = time.time()
                time.sleep(POLL_SECONDS)
                continue

            # In a position
            mark = float(open_pos.get("ltp") or open_pos.get("mark_price") or 0)
            upnl = float(open_pos.get("unrealized_pnl") or 0)
            held = time.time() - (entry_ts[0] or time.time())
            journal({"event": "tick", "side": open_pos["side"], "mark": mark, "upnl": upnl, "held_s": round(held, 1)})

            if sma_reversion(symbol, open_pos) or held > MAX_HOLD_SECONDS:
                reason = "sma reversion" if sma_reversion(symbol, open_pos) else "max hold"
                close = b.close_position(symbol=symbol, product="FUTURES")
                journal({"event": "exit", "reason": reason, "result": close})
                time.sleep(1.0)
                after = next((p for p in b.get_positions() if p["symbol"] == symbol and float(p.get("amount", 0)) != 0), None)
                if after is None:
                    journal({"event": "flat", "note": "position closed"})
            time.sleep(POLL_SECONDS)
        except KeyboardInterrupt:
            journal({"event": "stop", "reason": "user interrupt"})
            try:
                b.cancel_all_orders()
            except Exception:
                pass
            return 0
        except Exception as exc:
            journal({"event": "error", "error": str(exc)})
            time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    raise SystemExit(main())
