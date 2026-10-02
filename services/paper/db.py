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


def get(k, default=None):
    with conn() as c:
        r = c.execute("SELECT v FROM config WHERE k=?", (k,)).fetchone()
        return json.loads(r["v"]) if r else default


def setc(k, v):
    with conn() as c:
        c.execute("INSERT OR REPLACE INTO config VALUES(?,?)", (k, json.dumps(v)))


def log(kind, payload):
    with conn() as c:
        c.execute(
            "INSERT INTO decisions(ts,kind,payload) VALUES(?,?,?)",
            (time.time(), kind, json.dumps(payload, default=str)),
        )
