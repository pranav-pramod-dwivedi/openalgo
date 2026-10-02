#!/usr/bin/env python3
"""Print the paper system's real state: worker, portfolio, strategies, refusals."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.paper import db, jev  # noqa: E402
from services.paper.config import load  # noqa: E402


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

    print("\n  PAPER TRADING - VIRTUAL MONEY")
    print(f"  analyst            : {jev.MODEL} ({'key found' if jev._key() else 'no key in .env'})")
    print(f"  analyst last fault : {jev.failure_reason() or 'none'}")
    print(f"  kill switch        : {'HALTED, not trading' if halted else 'running'}")
    print(f"  watchlist          : {', '.join(watchlist)}")
    print(f"  virtual cash       : {cash:,.2f}")
    if equity is not None:
        print(f"  equity             : {equity:,.2f}")
    print(f"  open positions     : {len(positions)}")
    for p in positions:
        mark = p["mark"] if "mark" in p.keys() else None
        print(f"    {p['symbol']:<10} {p['side']:<5} qty {p['qty']:<10} entry {p['entry']} mark {mark}")
    print(f"  strategies active  : {len(strategies)}")
    for s in strategies:
        print(f"    {s['id']:<24} {s['status']:<8} {s['metrics']}")
    print(f"  experiments run    : {experiments}")
    print(f"  worker heartbeat   : {hbval('ts')} status={hbval('status')} cycle={hbval('cycle')}")
    print(f"  last planner call  : {hbval('planner_verdict')} ({hbval('refusal_reason')})")
    if skipped:
        print(f"  last refusal       : {skipped['payload'][:160]}")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
