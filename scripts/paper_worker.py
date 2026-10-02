#!/usr/bin/env python3
"""Autonomous permanent paper-trading worker.

Loop: data -> validate -> research (backtest grid) -> register -> signals ->
paper orders -> fills -> positions -> equity snapshot -> heartbeat.
State persisted in data/paper.db. No live orders anywhere.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.paper import db, engine  # noqa: E402

CYCLE_SECONDS = 300  # 5 minutes per full cycle


def main() -> int:
    db.init()
    cycle = 0
    while True:
        cycle += 1
        try:
            engine.research_cycle()
            engine.trading_cycle()
            with db.conn() as c:
                c.execute(
                    "INSERT OR REPLACE INTO heartbeat VALUES(?,?,?,?)",
                    (1, time.time(), "ok", "", cycle),
                )
        except Exception as exc:
            with db.conn() as c:
                c.execute(
                    "INSERT OR REPLACE INTO heartbeat VALUES(?,?,?,?)",
                    (1, time.time(), "error", str(exc), cycle),
                )
            print(f"[paper_worker] cycle {cycle} error: {exc}")
        time.sleep(CYCLE_SECONDS)


if __name__ == "__main__":
    raise SystemExit(main())
