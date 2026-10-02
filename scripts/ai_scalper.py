#!/usr/bin/env python3
"""Supervised multi-symbol Binance demo scalper.

Deterministic, audited, and capped by the broker layer:
  - one position per symbol, only while flat
  - pluggable strategies (sma_cross, donchian, rsi_revert)
  - exit: native reduce-only STOP_MARKET / TAKE_PROFIT_MARKET resting on the venue
  - poll: 5 seconds (Binance testnet cadence, not milliseconds)
  - kill: Ctrl+C cancels resting exits and leaves positions for review
  - report: --report summarizes log/ai_scalper.jsonl

Run:
    uv run python scripts/ai_scalper.py --symbols BTCUSDT,SOLUSDT --strategy sma_cross --yes
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


def _candles(symbol: str) -> list[dict]:
    try:
        return get_foreign_history(symbol, "CRYPTO", "1m", "", "")
    except Exception:
        return []


def sig_sma_cross(symbol: str) -> tuple[str, dict]:
    c = _candles(symbol)
    if len(c) < 20:
        return "HOLD", {"reason": "not enough candles"}
    closes = [k["close"] for k in c[-12:]]
    sma = sum(closes[:9]) / 9.0
    last = closes[-1]
    move = (last - sma) / sma
    if move > 0.0008:
        return "LONG", {"reason": f"price {last:.2f} > sma9 {sma:.2f} by {move*100:.3f}%"}
    if move < -0.0008:
        return "SHORT", {"reason": f"price {last:.2f} < sma9 {sma:.2f} by {move*100:.3f}%"}
    return "HOLD", {"reason": f"price within band ({move*100:.3f}%)"}


def sig_donchian(symbol: str) -> tuple[str, dict]:
    c = _candles(symbol)
    if len(c) < 21:
        return "HOLD", {"reason": "not enough candles"}
    highs = [k["high"] for k in c[-21:-1]]
    lows = [k["low"] for k in c[-21:-1]]
    last = c[-1]["close"]
    if last > max(highs):
        return "LONG", {"reason": f"price {last:.2f} broke 20-candle high {max(highs):.2f}"}
    if last < min(lows):
        return "SHORT", {"reason": f"price {last:.2f} broke 20-candle low {min(lows):.2f}"}
    return "HOLD", {"reason": f"inside {min(lows):.2f}-{max(highs):.2f}"}


def _rsi(closes: list[float], period: int = 14) -> float | None:
    if len(closes) < period + 1:
        return None
    gains = losses = 0.0
    for i in range(-period, 0):
        d = closes[i] - closes[i - 1]
        if d > 0:
            gains += d
        else:
            losses -= d
    if losses == 0:
        return 100.0
    rs = (gains / period) / (losses / period)
    return 100 - 100 / (1 + rs)


def sig_rsi_revert(symbol: str) -> tuple[str, dict]:
    c = _candles(symbol)
    closes = [k["close"] for k in c[-20:]]
    r = _rsi(closes)
    if r is None:
        return "HOLD", {"reason": "not enough candles"}
    if r < 30:
        return "LONG", {"reason": f"RSI {r:.1f} oversold"}
    if r > 70:
        return "SHORT", {"reason": f"RSI {r:.1f} overbought"}
    return "HOLD", {"reason": f"RSI {r:.1f} neutral"}


STRATEGIES = {"sma_cross": sig_sma_cross, "donchian": sig_donchian, "rsi_revert": sig_rsi_revert}


def should_exit(strategy: str, symbol: str, position: dict) -> tuple[bool, str]:
    c = _candles(symbol)
    if len(c) < 10:
        return False, ""
    closes = [k["close"] for k in c[-10:]]
    sma = sum(closes[:9]) / 9.0
    last = closes[-1]
    if strategy in ("sma_cross", "donchian"):
        hit = (position["side"] == "LONG" and last < sma) or (position["side"] == "SHORT" and last > sma)
        if hit:
            return True, "sma reversion"
    if strategy == "rsi_revert":
        r = _rsi([k["close"] for k in c[-20:]])
        if r is not None and ((position["side"] == "LONG" and r > 55) or (position["side"] == "SHORT" and r < 45)):
            return True, f"RSI normalized {r:.1f}"
    return False, ""


def report() -> int:
    if not LOG_PATH.exists():
        print("No log yet.")
        return 0
    rows = [json.loads(line) for line in LOG_PATH.read_text().splitlines() if line.strip()]
    entries = [r for r in rows if r.get("event") == "entry"]
    exits = [r for r in rows if r.get("event") == "exit"]
    halts = [r for r in rows if r.get("event") == "halt"]
    errors = [r for r in rows if r.get("event") == "error"]
    print(f"events={len(rows)} entries={len(entries)} exits={len(exits)} halts={len(halts)} errors={len(errors)}")
    for e in entries:
        print(f"  entry {e.get('side')} @ {e.get('ts')} {e.get('result', {}).get('status_code')}")
    for e in exits:
        print(f"  exit {e.get('reason')} @ {e.get('ts')}")
    if halts:
        print(f"  HALTED: {halts[-1].get('reason')} pnl={halts[-1].get('session_pnl')}")
    return 0


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--symbols", default="BTCUSDT", help="comma list, e.g. BTCUSDT,SOLUSDT,ETHUSDT")
    p.add_argument("--strategy", choices=[*STRATEGIES], default="sma_cross")
    p.add_argument("--size", type=float, default=0.001, help="qty per position")
    p.add_argument("--tp-pct", type=float, default=0.004)
    p.add_argument("--sl-pct", type=float, default=0.002)
    p.add_argument("--yes", action="store_true", help="required to trade; otherwise dry-run print only")
    p.add_argument("--report", action="store_true", help="summarize the journal and exit")
    args = p.parse_args()

    if args.report:
        return report()

    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    b = BinanceDemoService()
    session_pnl = 0.0
    entry_ts: dict[str, float | None] = dict.fromkeys(symbols)
    journal({"event": "start", "symbols": symbols, "strategy": args.strategy, "size": args.size, "tp_pct": args.tp_pct, "sl_pct": args.sl_pct, "armed": args.yes})

    while True:
        try:
            positions = b.get_positions()
            for symbol in symbols:
                open_pos = next((p for p in positions if p["symbol"] == symbol and float(p.get("amount", 0)) != 0), None)

                if open_pos is None:
                    if session_pnl <= -DAILY_LOSS_LIMIT_USD:
                        journal({"event": "halt", "reason": "daily loss limit", "session_pnl": round(session_pnl, 4)})
                        return 0
                    entry_ts[symbol] = None
                    signal, meta = STRATEGIES[args.strategy](symbol)
                    journal({"event": "signal", "symbol": symbol, "signal": signal, **meta})
                    if signal in ("LONG", "SHORT") and args.yes:
                        side = "BUY" if signal == "LONG" else "SELL"
                        res = b.place_order(symbol=symbol, side=side, quantity=args.size, order_type="MARKET", is_futures=True)
                        journal({"event": "entry", "symbol": symbol, "side": side, "strategy": args.strategy, "result": res})
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
                            tp_res = b.place_order(symbol=symbol, side=exit_side, quantity=args.size, order_type="TAKE_PROFIT_MARKET", is_futures=True, stop_price=tp, reduce_only=True)
                            sl_res = b.place_order(symbol=symbol, side=exit_side, quantity=args.size, order_type="STOP_MARKET", is_futures=True, stop_price=sl, reduce_only=True)
                            journal({"event": "brackets", "symbol": symbol, "tp": tp, "sl": sl, "tp_result": tp_res, "sl_result": sl_res})
                            entry_ts[symbol] = time.time()
                    continue

                mark = float(open_pos.get("mark_price") or 0)
                upnl = float(open_pos.get("unrealized_pnl") or 0)
                held = time.time() - (entry_ts[symbol] or time.time())
                journal({"event": "tick", "symbol": symbol, "side": open_pos["side"], "mark": mark, "upnl": upnl, "held_s": round(held, 1)})

                hit, why = should_exit(args.strategy, symbol, open_pos)
                if hit or held > MAX_HOLD_SECONDS:
                    reason = why or "max hold"
                    close = b.close_position(symbol=symbol, product="FUTURES")
                    journal({"event": "exit", "symbol": symbol, "reason": reason, "upnl_at_exit": upnl, "result": {"ok": close[0], "message": close[1].get("message")}})
                    session_pnl += upnl
                    time.sleep(1.0)
                    entry_ts[symbol] = None
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
