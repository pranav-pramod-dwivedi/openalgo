#!/usr/bin/env python3
"""Persist the agent's reasoning trace and widen the audit verdict.

Adds ``reasoning`` TEXT to ``ag_message`` and widens
``ag_audit.risk_verdict`` to TEXT so the audit API returns the full verdict
rather than a 60-character truncation.

``reasoning`` holds the ``reasoning`` frames from
``services/agent/frames.py`` (mapped from ``reasoning_content`` in
``services/agent/stream.py``), stored alongside ``content`` by
``blueprints/agent.py``. Nullable so rows written before this migration read
back as empty.

``risk_verdict`` was ``String(60)`` with the write path truncating to 60
characters. The column is now ``Text`` and the write path stores the full
summary. On SQLite no rebuild is needed: SQLite does not enforce the old
length, so an existing ``VARCHAR(60)`` declaration already stores longer
values. Fresh installs create the column as TEXT. Other backends are altered
in place.

Idempotent, and safe to run on a database that never had the agent tables
(it refuses and points at migrate_agent.py, which owns that schema).

Usage:
    cd upgrade
    uv run migrate_agent_reasoning.py           # Apply migration
    uv run migrate_agent_reasoning.py --status  # Check status without changing anything
"""

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import _pragmas  # noqa: F401,E402
from dotenv import load_dotenv  # noqa: E402
from sqlalchemy import create_engine, inspect, text  # noqa: E402
from sqlalchemy.pool import NullPool  # noqa: E402

PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)

MESSAGE_TABLE = "ag_message"
REASONING_COLUMN = "reasoning"

AUDIT_TABLE = "ag_audit"
VERDICT_COLUMN = "risk_verdict"


def resolve_sqlite_path(db_url):
    """Make a relative sqlite:/// path absolute against the project root."""
    prefix = "sqlite:///"
    if not db_url.startswith(prefix):
        return db_url
    path = db_url[len(prefix):]
    if os.path.isabs(path):
        return db_url
    return prefix + os.path.join(PROJECT_ROOT, path).replace("\\", "/")


def get_database_url():
    """Read DATABASE_URL from the environment, with the project default."""
    load_dotenv(os.path.join(PROJECT_ROOT, ".env"))
    return resolve_sqlite_path(os.getenv("DATABASE_URL", "sqlite:///db/openalgo.db"))


def sqlite_file(db_url):
    """The filesystem path a sqlite URL points at, or None for other backends."""
    prefix = "sqlite:///"
    if not db_url.startswith(prefix):
        return None
    return db_url[len(prefix):]


def _columns(engine, table):
    """Column names of ``table``, or an empty set when it does not exist."""
    if table not in set(inspect(engine).get_table_names()):
        return set()
    return {col["name"] for col in inspect(engine).get_columns(table)}


def _column_type(engine, table, column):
    """Declared type of one column, or None when the table or column is absent."""
    if table not in set(inspect(engine).get_table_names()):
        return None
    for col in inspect(engine).get_columns(table):
        if col["name"] == column:
            return str(col.get("type") or "")
    return None


def _is_sqlite(engine, db_url):
    """Whether the target backend is SQLite."""
    try:
        return engine.dialect.name == "sqlite"
    except Exception:
        return db_url.startswith("sqlite")


def pending(engine):
    """What this migration would change.

    Args:
        engine: An engine bound to the target database.

    Returns:
        ``(needs_reasoning, needs_verdict_widen)``. The second is True only
        on non-SQLite backends whose verdict column is not already TEXT:
        SQLite does not enforce the old VARCHAR length, so no rebuild is
        needed there.
    """
    cols = _columns(engine, MESSAGE_TABLE)
    if MESSAGE_TABLE not in set(inspect(engine).get_table_names()):
        needs_reasoning = False
    else:
        needs_reasoning = REASONING_COLUMN not in cols

    needs_verdict_widen = False
    if AUDIT_TABLE in set(inspect(engine).get_table_names()):
        vtype = (_column_type(engine, AUDIT_TABLE, VERDICT_COLUMN) or "").upper()
        if VERDICT_COLUMN in _columns(engine, AUDIT_TABLE) and "TEXT" not in vtype:
            # SQLite stores longer values regardless of the VARCHAR(60)
            # declaration, so widening there is a declaration nicety, not a
            # storage fix, and rebuilding an append-only trade audit to rename
            # a type is risk without benefit. Other backends enforce the
            # length, so they do need the ALTER.
            try:
                is_sqlite = engine.dialect.name == "sqlite"
            except Exception:
                is_sqlite = True
            needs_verdict_widen = not is_sqlite
    return needs_reasoning, needs_verdict_widen


def table_present(engine):
    """Whether the agent message table exists yet."""
    return MESSAGE_TABLE in set(inspect(engine).get_table_names())


def status(engine, db_url):
    """Report what would change, without changing anything.

    Args:
        engine: An engine bound to the target database, or None.
        db_url: The database URL, for the report.

    Returns:
        True when the schema is already up to date, False when work is pending.
    """
    print("\nAgent reasoning migration status")
    print("-" * 62)

    path = sqlite_file(db_url)
    if path is not None and not os.path.exists(path):
        print("Database file does not exist yet. Not created: --status changes nothing.")
        print(f"Migration needed. Would create {MESSAGE_TABLE}.{REASONING_COLUMN} on first run.")
        return False

    if engine is None:
        print("Could not open the database. Nothing was changed.")
        return False

    try:
        if not table_present(engine):
            print(f"{MESSAGE_TABLE} is missing. Nothing was changed.")
            print("Run migrate_agent.py first; it owns the agent schema.")
            return False
        needs_reasoning, needs_verdict_widen = pending(engine)
    except Exception as exc:
        print("-" * 62)
        print(f"Could not read the agent schema: {type(exc).__name__}: {exc}")
        print("Nothing was changed. Fix the installation and run --status again.")
        return False

    has = REASONING_COLUMN in _columns(engine, MESSAGE_TABLE)
    print(f"  {MESSAGE_TABLE}.{REASONING_COLUMN:<30} {'present' if has else 'MISSING'}")
    if AUDIT_TABLE in set(inspect(engine).get_table_names()):
        vtype = _column_type(engine, AUDIT_TABLE, VERDICT_COLUMN) or "(absent)"
        print(f"  {AUDIT_TABLE}.{VERDICT_COLUMN:<30} {vtype}")
        if "TEXT" not in vtype.upper() and _is_sqlite(engine, db_url):
            print("  SQLite does not enforce the old VARCHAR(60); longer verdicts already store.")
    print("-" * 62)

    if not needs_reasoning and not needs_verdict_widen:
        print("Up to date. Nothing to do.")
        return True

    if needs_reasoning:
        print(f"Migration needed. Would add {MESSAGE_TABLE}.{REASONING_COLUMN} TEXT.")
    if needs_verdict_widen:
        print(f"Migration needed. Would widen {AUDIT_TABLE}.{VERDICT_COLUMN} to TEXT.")
    return False


def apply(engine):
    """Add the reasoning column and widen the verdict column where needed.

    Args:
        engine: An engine bound to the target database.

    Returns:
        True on success, False when the change could not be applied.
    """
    try:
        if not table_present(engine):
            print(f"  [FAIL] {MESSAGE_TABLE} does not exist. Run migrate_agent.py first.")
            return False
        needs_reasoning, needs_verdict_widen = pending(engine)
    except Exception as exc:
        print(f"  [FAIL] Could not read the agent schema: {type(exc).__name__}: {exc}")
        return False

    if not needs_reasoning and not needs_verdict_widen:
        print("  All agent reasoning columns already present. Nothing to do.")
        return True

    if needs_reasoning:
        try:
            with engine.begin() as connection:
                connection.exec_driver_sql(
                    f"ALTER TABLE {MESSAGE_TABLE} ADD COLUMN {REASONING_COLUMN} TEXT"
                )
        except Exception as exc:
            print(f"  [FAIL] Could not add {MESSAGE_TABLE}.{REASONING_COLUMN}: {exc}")
            return False
        print(f"  [OK] Added column {MESSAGE_TABLE}.{REASONING_COLUMN}")

    if needs_verdict_widen:
        try:
            with engine.begin() as connection:
                connection.execute(
                    text(f"ALTER TABLE {AUDIT_TABLE} ALTER COLUMN {VERDICT_COLUMN} TYPE TEXT")
                )
        except Exception as exc:
            print(f"  [FAIL] Could not widen {AUDIT_TABLE}.{VERDICT_COLUMN}: {exc}")
            return False
        print(f"  [OK] Widened {AUDIT_TABLE}.{VERDICT_COLUMN} to TEXT")

    return True


def main():
    """Entry point.

    Returns:
        0 on success, 1 when the migration could not be applied.
    """
    parser = argparse.ArgumentParser(description="Agent reasoning persistence migration")
    parser.add_argument(
        "--status",
        action="store_true",
        help="Report what would change without changing anything",
    )
    args = parser.parse_args()

    db_url = get_database_url()
    shown = db_url if not db_url.startswith("sqlite") else "sqlite://..."
    print("\nAgent Reasoning Migration")
    print("-" * 62)
    print(f"Database: {shown}")

    path = sqlite_file(db_url)
    if args.status and path is not None and not os.path.exists(path):
        status(None, db_url)
        return 0

    engine = create_engine(db_url, poolclass=NullPool)
    try:
        if args.status:
            status(engine, db_url)
            return 0

        print("\nApplying migration...")
        ok = apply(engine)
        print("-" * 62)
        if ok:
            print("Migration complete.")
            return 0
        print("Migration failed. See the messages above.")
        return 1
    finally:
        engine.dispose()


if __name__ == "__main__":
    sys.exit(main())
