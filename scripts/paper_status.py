#!/usr/bin/env python3
"""Print the paper system's real state: worker, portfolio, strategies, refusals."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.paper import db, jev  # noqa: E402
from services.paper.config import load  # noqa: E402
from services.paper.worker import analyst_outage_state  # noqa: E402


def _ago(ts) -> str:
    try:
        secs = max(0, int(time.time() - float(ts)))
    except (TypeError, ValueError):
        return "not yet"
    if secs < 90:
        return "moments ago"
    if secs < 5400:
        return f"{secs // 60} minutes ago"
    return f"{secs // 3600} hours ago"


def _when(ts) -> str:
    """A plain timestamp, or a sentence saying there is not one yet."""
    try:
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(float(ts)))
    except (TypeError, ValueError):
        return "an unknown time"


def _plain(payload: str) -> str:
    """Turn an internal refusal code into a sentence."""
    try:
        data = json.loads(payload)
    except Exception:
        return "nothing that met its criteria"
    reason = data.get("refusal_reason") or data.get("reason") or ""
    words = {
        "no_edge": "no strategy had a setup worth taking",
        "no_setup": "the strategy had no signal right now",
        "analyst_unavailable": "the AI reviewer could not be reached",
        "insufficient_cash": "not enough virtual cash left",
        "exposure_cap": "already using as much of the portfolio as allowed",
        "daily_loss_limit": "today's losses reached the limit set for the day",
        "symbol_already_open": "a trade in that coin is already open",
        "stale_data": "the price data was too old to trust",
        "kill_switch": "you had stopped it",
        "planner_unavailable": "the planner was not available",
    }
    return words.get(reason, reason.replace("_", " ") or "nothing that met its criteria")


def _net(metrics: str) -> float:
    try:
        return float(json.loads(metrics or "{}").get("net_pnl", float("-inf")))
    except Exception:
        return float("-inf")


def _last_trade(row) -> str:
    """The most recent fill, in a few words: what, which way, and when.

    Shown so the one-trade rule is visible. A command that places a trade and
    exits leaves exactly one new line here, so a user can see that a command did
    one thing and not several.
    """
    if row is None:
        return "none yet"
    verb = "bought" if str(row["side"]).upper() == "BUY" else "sold"
    try:
        qty = float(row["qty"])
        price = float(row["price"])
    except (TypeError, ValueError):
        return f"{row['symbol']} at {row['ts']}"
    stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(float(row["ts"])))
    return (
        f"{verb} {qty:.6f} {row['symbol']} at {price:.4f}, "
        f"{stamp} ({_ago(row['ts'])})"
    )


def main() -> int:
    db.init()
    cfg = db.get_all()
    cash = float(cfg.get("cash", cfg.get("starting_cash", 0)) or 0)
    halted = bool(cfg.get("halted"))
    watchlist = load().symbols

    with db.conn() as c:
        positions = c.execute("SELECT * FROM positions WHERE status='open'").fetchall()
        strategies = c.execute("SELECT id, family, status, metrics FROM strategies ORDER BY id").fetchall()
        experiments = c.execute("SELECT COUNT(*) FROM experiments").fetchone()[0]
        hb_cols = {r[1] for r in c.execute("PRAGMA table_info(heartbeat)").fetchall()}
        hb = c.execute("SELECT * FROM heartbeat WHERE id=1").fetchone()
        skipped = c.execute(
            "SELECT payload FROM decisions WHERE kind IN ('worker_skipped','plan_refused') ORDER BY ts DESC LIMIT 1"
        ).fetchone()
        last_trade = c.execute(
            "SELECT symbol, side, qty, price, ts FROM fills ORDER BY id DESC LIMIT 1"
        ).fetchone()

    equity = None
    try:
        from services.paper import engine

        equity = engine.equity_of(cfg, engine._unrealized())
    except Exception:
        equity = None

    def hbval(name, default="not reported"):
        if hb is None or name not in hb_cols:
            return default
        val = hb[name]
        return default if val in (None, "") else val

    fault = jev.failure_reason()
    # Read through the worker's own reader, so this screen and the worker's
    # ``--status`` cannot disagree about whether trading has fallen back.
    outage = analyst_outage_state()
    stopped = "STOPPED (you asked it to stop)" if halted else "ON, working on its own"
    print("\n  PAPER TRADING - VIRTUAL MONEY (no real money involved)")
    if outage["active"] and not halted:
        print("  AI reviewer        : not answering - it is being asked every cycle")
        print(
            f"  Trading            : ON, but unreviewed since "
            f"{_when(outage['started_at'])} ({_ago(outage['started_at'])})"
        )
        print(
            f"  No-review trades   : {outage['unreviewed_trades']} placed with no AI "
            "reviewing them"
        )
        print(
            "  What happens next  : the next cycle asks the reviewer again, and reviewed "
            "trading"
        )
        print(
            "                       resumes on its own as soon as it answers. Nothing to restart."
        )
    elif halted:
        print(f"  AI reviewer        : {'not answering' if fault else 'ready'}")
        print(f"  Trading            : {stopped}")
    elif fault:
        print(f"  AI reviewer        : unavailable - {fault}")
        print(f"  Trading            : {stopped}")
    elif jev._key():
        print("  AI reviewer        : ready - it reviews each trade when it can be reached")
        print(f"  Trading            : {stopped}")
    else:
        print("  AI reviewer        : not set up, so nothing reviews the trades")
        print(f"  Trading            : {stopped}")
    print("  One trade each time: every ./paper places one trade, then stops")
    print(f"  Last trade placed  : {_last_trade(last_trade)}")
    print(f"  Watching           : {', '.join(s.replace('USDT', '') for s in watchlist)}")
    print(f"  Virtual cash       : {cash:,.2f} USD")
    if equity is not None:
        print(f"  Total value        : {equity:,.2f} USD")
    print(f"  Open trades        : {len(positions)}")
    for p in positions:
        mark = p["mark"] if "mark" in p.keys() else None
        print(f"    {p['symbol']:<10} {p['side']:<5} qty {p['qty']:<10} entry {p['entry']} mark {mark}")
    ranked = sorted(strategies, key=lambda r: _net(r["metrics"]), reverse=True)
    print(f"  Strategies ready   : {len(strategies)} (showing the 5 with the best backtest)")
    for s in ranked[:5]:
        m = json.loads(s["metrics"]) if s["metrics"] else {}
        print(
            f"    {s['id']:<26} {m.get('trades', '?')} backtest trades, "
            f"net {m.get('net_pnl', '?')} USD, worst dip {m.get('max_drawdown', '?')} USD"
        )
    print(f"  strategies tested  : {experiments} in total")
    print(f"  Last check         : {_ago(hbval('ts'))}")
    verdict = hbval("planner_verdict")
    if verdict == "halted":
        print("  Last decision      : stopped, waiting to be started")
    elif verdict == "executed":
        print("  Last decision      : placed a trade")
    else:
        print("  Last decision      : decided not to trade (no good setup right now)")
    if skipped:
        print(f"  Why                : {_plain(skipped['payload'])}")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
