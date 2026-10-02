from __future__ import annotations

import json
import os
import sqlite3
import time
from pathlib import Path

DATA = Path(os.getenv("PAPER_DB", "data/paper.db"))
DATA.parent.mkdir(parents=True, exist_ok=True)
SCHEMA = """
CREATE TABLE IF NOT EXISTS config (k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS hypotheses (id TEXT PRIMARY KEY, hypothesis TEXT, created REAL);
CREATE TABLE IF NOT EXISTS experiments (id TEXT PRIMARY KEY, hypothesis_id TEXT, params TEXT, result TEXT, created REAL, status TEXT);
CREATE TABLE IF NOT EXISTS strategies (id TEXT PRIMARY KEY, hypothesis_id TEXT, family TEXT, params TEXT, metrics TEXT, status TEXT, created REAL, version INTEGER);
CREATE TABLE IF NOT EXISTS orders (id TEXT PRIMARY KEY, symbol TEXT, side TEXT, qty REAL, type TEXT, status TEXT, created REAL, updated REAL, strategy_id TEXT, reason TEXT);
CREATE TABLE IF NOT EXISTS fills (id INTEGER PRIMARY KEY AUTOINCREMENT, order_id TEXT, symbol TEXT, side TEXT, qty REAL, price REAL, fee REAL, slippage REAL, ts REAL);
CREATE TABLE IF NOT EXISTS positions (symbol TEXT PRIMARY KEY, side TEXT, qty REAL, entry REAL, opened REAL, strategy_id TEXT, sl REAL, tp REAL, status TEXT);
CREATE TABLE IF NOT EXISTS equity (ts REAL PRIMARY KEY, cash REAL, equity REAL, realized REAL, unrealized REAL, fees REAL, slippage REAL, drawdown REAL);
CREATE TABLE IF NOT EXISTS decisions (id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, kind TEXT, payload TEXT);
CREATE TABLE IF NOT EXISTS heartbeat (id INTEGER PRIMARY KEY CHECK(id=1), ts REAL, status TEXT, error TEXT, cycle INTEGER);
"""


def conn():
    c = sqlite3.connect(DATA)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    return c


def init():
    with conn() as c:
        c.executescript(SCHEMA)
        defaults = {
            "starting_cash": "1000",
            "max_exposure_pct": "0.5",
            "max_daily_loss": "20",
            "max_position_qty": "0.01",
            "fee_bps": "4",
            "slippage_bps": "2",
            "active": "true",
        }
        for k, v in defaults.items():
            c.execute("INSERT OR IGNORE INTO config VALUES(?,?)", (k, v))
        # ``cash`` is the live balance and the single source of truth for equity.
        # It is seeded from the configured starting capital the first time only,
        # so an existing installation keeps its real balance rather than being
        # reset by the INSERT OR IGNORE above.
        seeded = c.execute("SELECT v FROM config WHERE k='starting_cash'").fetchone()
        c.execute(
            "INSERT OR IGNORE INTO config VALUES('cash',?)",
            (seeded["v"] if seeded else json.dumps(1000.0),),
        )
        c.execute("INSERT OR IGNORE INTO config VALUES('short_margin_locked','0')")


def get_cash(existing=None) -> float:
    """The live cash balance, falling back to the configured starting capital."""
    cfg = get_all(existing)
    return float(cfg.get("cash", cfg.get("starting_cash", 1000.0)))


def get_margin_locked(existing=None) -> float:
    """Notional currently posted as margin against open short positions."""
    return float(get_all(existing).get("short_margin_locked", 0.0))


def ensure_column(table: str, column: str, decl: str) -> bool:
    """Add a column to an existing table when it is missing. Returns True if added."""
    with conn() as c:
        cols = {r["name"] for r in c.execute(f"PRAGMA table_info({table})")}
        if column in cols:
            return False
        c.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
        return True


def get_all(existing=None) -> dict:
    """Every config key as a plain dict, for callers that read several at once.

    Pass the caller's own connection when it already holds a write transaction:
    a second connection would block against it in SQLite.
    """
    if existing is not None:
        return {r["k"]: json.loads(r["v"]) for r in existing.execute("SELECT k,v FROM config")}
    with conn() as c:
        return {r["k"]: json.loads(r["v"]) for r in c.execute("SELECT k,v FROM config")}


def set_many(values: dict, existing=None) -> None:
    """Write several config keys in one transaction, so a cycle cannot half-persist."""
    if existing is not None:
        _write(existing, values)
        return
    with conn() as c:
        _write(c, values)


def _write(c, values: dict) -> None:
    for k, v in values.items():
        c.execute("INSERT OR REPLACE INTO config VALUES(?,?)", (k, json.dumps(v)))


def get(k, default=None):
    with conn() as c:
        r = c.execute("SELECT v FROM config WHERE k=?", (k,)).fetchone()
        return json.loads(r["v"]) if r else default


def setc(k, v):
    with conn() as c:
        c.execute("INSERT OR REPLACE INTO config VALUES(?,?)", (k, json.dumps(v)))


def move_cash(delta: float, existing=None) -> float:
    """Apply a signed delta to the cash balance and return the new balance.

    Cash is the single source of truth, so every movement goes through here, on
    the caller's own connection, inside the same transaction as the fill. A delta
    that would take the balance below zero is refused rather than applied.
    """
    cfg = get_all(existing)
    current = float(cfg.get("cash", cfg.get("starting_cash", 1000.0)))
    new = round(current + float(delta), 8)
    if new < 0:
        raise ValueError("insufficient cash")
    set_many({"cash": new}, existing)
    return new


def set_margin(locked: float, existing=None) -> None:
    """Persist the notional held as margin against open short positions."""
    set_many({"short_margin_locked": round(max(0.0, float(locked)), 8)}, existing)


def log(kind, payload, existing=None):
    """Append a decision. Pass the caller's connection when one is already open."""
    row = (time.time(), kind, json.dumps(payload, default=str))
    if existing is not None:
        existing.execute("INSERT INTO decisions(ts,kind,payload) VALUES(?,?,?)", row)
        return
    with conn() as c:
        c.execute("INSERT INTO decisions(ts,kind,payload) VALUES(?,?,?)", row)
