#!/usr/bin/env python3
"""
One-off cleanup: remove the paper engine's legacy "mirror" strategy rows.

Research used to register each validated strategy twice: once under its real id
(`donchian-BTCUSDT-15m`, timeframe included) and once again under a bare
`donchian-BTCUSDT`, because the planner matched a strategy to a symbol by taking
everything after the first dash of its id and so could only see the bare shape.
The duplicate rows were a workaround for that parser, not data.

The planner now parses the timeframe out of the id the same way the engine does
(`services.paper.engine.parse_strategy_id`), so the duplicates are dead weight:
each one is a second strategy the planner can plan with, under an id that says
nothing about the resolution it was validated on. `research_cycle` now deletes
them on every cycle, so this script is only for clearing them out immediately at
upgrade time instead of waiting for the next research cycle.

Notes:
  * It touches `data/paper.db`, which is the paper account -- virtual money, no
    broker -- and not the main OpenAlgo database.
  * Only rows whose `hypothesis_id` is the research-symbol-mirror marker are
    deleted. A strategy research registered is never at risk.
  * Idempotent: safe to run repeatedly, and a database with no mirror rows is left
    exactly as it was found.
  * Deliberately NOT in `migrate_all.py`. Every migration in that list is a schema
    change to the main database, and one that fails unattended on someone's paper
    account is not something to hand to `git pull`. An operator who wants the rows
    gone before the next research cycle runs this by hand.

Usage:
    cd upgrade
    uv run migrate_paper_mirror_strategies.py --status  # report, change nothing
    uv run migrate_paper_mirror_strategies.py           # delete the mirror rows
"""

import argparse
import os
import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# The marker research stamped on every mirror row it wrote.
MIRROR_HYPOTHESIS = "research-symbol-mirror"


def paper_db_path() -> Path:
    """Where the paper account lives, honouring PAPER_DB like the services do."""
    return Path(os.getenv("PAPER_DB") or PROJECT_ROOT / "data" / "paper.db")


def find_mirror_rows(path: Path) -> list[tuple[str, str]]:
    """Every mirror row, as (id, family). Empty when there is nothing to remove."""
    if not path.exists():
        return []
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        names = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        if "strategies" not in names:
            return []
        return [
            (str(row[0]), str(row[1]))
            for row in conn.execute(
                "SELECT id, family FROM strategies WHERE hypothesis_id=?", (MIRROR_HYPOTHESIS,)
            )
        ]
    finally:
        conn.close()


def purge(path: Path) -> int:
    """Delete the mirror rows. Returns how many went."""
    conn = sqlite3.connect(path)
    try:
        with conn:
            removed = conn.execute(
                "DELETE FROM strategies WHERE hypothesis_id=?", (MIRROR_HYPOTHESIS,)
            ).rowcount
    finally:
        conn.close()
    return removed or 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Remove the paper engine's legacy mirror strategy rows."
    )
    parser.add_argument(
        "--status", action="store_true", help="report what would be removed and change nothing"
    )
    args = parser.parse_args()

    path = paper_db_path()
    found = find_mirror_rows(path)

    print()
    print("Paper strategy mirror cleanup")
    print("-" * 46)
    print(f"  paper database              {path}")

    if not path.exists():
        print("  no paper database yet, so there is nothing to clean up")
        return 0
    if not found:
        print("  mirror rows                 none")
        return 0

    for strategy_id, family in found:
        print(f"  would remove                {strategy_id} ({family})")

    if args.status:
        print(f"  {len(found)} row(s) would be removed; nothing was changed")
        return 0

    removed = purge(path)
    print(f"  removed                     {removed} row(s)")
    print()
    print("The next research cycle writes the same strategies under their real,")
    print("timeframe-carrying ids. The planner reads those directly.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
