"""Persistent configuration and kill switch for the autonomous paper worker.

Everything lives in the ``config`` table of the paper database so it survives a
restart and is readable by any other process (including ``--status`` from a
second shell). Nothing here talks to a broker or places an order.

Keys owned by this module:

``halted``
    The kill switch. When true the worker idles instead of trading. It is read
    at the top of every cycle, so ``--halt`` takes effect on the next tick even
    if the worker is mid-sleep.
``symbols`` / ``interval``
    The configured watchlist and candle timeframe. See PAPER_TRADING.md for the
    engine-side limitation.
``monitor_seconds`` / ``research_seconds``
    The two cadences.
``worker_health``
    A JSON snapshot the worker rewrites every cycle and ``--status`` reads.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass

from . import db

KEY_HALTED = "halted"
KEY_SYMBOLS = "symbols"
KEY_INTERVAL = "interval"
KEY_MONITOR = "monitor_seconds"
KEY_RESEARCH = "research_seconds"
KEY_HEALTH = "worker_health"

DEFAULT_SYMBOLS = ["BTCUSDT", "SOLUSDT", "ETHUSDT"]
DEFAULT_INTERVAL = "5m"
DEFAULT_MONITOR_SECONDS = 60
DEFAULT_RESEARCH_SECONDS = 300

VALID_INTERVALS = {"1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "1d"}

MIN_INTERVAL_SECONDS = 5
MAX_INTERVAL_SECONDS = 86_400

DEFAULTS = {
    KEY_HALTED: False,
    KEY_SYMBOLS: DEFAULT_SYMBOLS,
    KEY_INTERVAL: DEFAULT_INTERVAL,
    KEY_MONITOR: DEFAULT_MONITOR_SECONDS,
    KEY_RESEARCH: DEFAULT_RESEARCH_SECONDS,
}


def normalize_symbols(value) -> list[str]:
    """Accept a list or a comma separated string; return uppercase, de-duped."""
    if isinstance(value, str):
        parts = value.replace(";", ",").split(",")
    elif isinstance(value, (list, tuple)):
        parts = list(value)
    else:
        parts = []
    out: list[str] = []
    for part in parts:
        sym = str(part).strip().upper()
        if sym and sym not in out:
            out.append(sym)
    return out or list(DEFAULT_SYMBOLS)


def normalize_interval(value) -> str:
    iv = str(value or "").strip()
    return iv if iv in VALID_INTERVALS else DEFAULT_INTERVAL


def _normalize_seconds(value, default: int) -> int:
    try:
        seconds = int(float(value))
    except (TypeError, ValueError):
        return default
    return max(MIN_INTERVAL_SECONDS, min(MAX_INTERVAL_SECONDS, seconds))


@dataclass
class PaperConfig:
    """The worker's view of the config table, already validated."""

    halted: bool
    symbols: list[str]
    interval: str
    monitor_seconds: int
    research_seconds: int

    @property
    def symbols_csv(self) -> str:
        return ",".join(self.symbols)

    def as_dict(self) -> dict:
        return {
            KEY_HALTED: self.halted,
            KEY_SYMBOLS: self.symbols,
            KEY_INTERVAL: self.interval,
            KEY_MONITOR: self.monitor_seconds,
            KEY_RESEARCH: self.research_seconds,
        }


def load() -> PaperConfig:
    """Read the config table, falling back to defaults for anything unusable."""
    try:
        halted = bool(db.get(KEY_HALTED, DEFAULTS[KEY_HALTED]))
    except Exception:
        halted = DEFAULTS[KEY_HALTED]
    research = _normalize_seconds(
        db.get(KEY_RESEARCH, DEFAULTS[KEY_RESEARCH]), DEFAULT_RESEARCH_SECONDS
    )
    monitor = _normalize_seconds(db.get(KEY_MONITOR, DEFAULTS[KEY_MONITOR]), DEFAULT_MONITOR_SECONDS)
    if monitor > research:
        # A monitor cadence slower than research would invert the intent.
        monitor = min(monitor, research)
    return PaperConfig(
        halted=halted,
        symbols=normalize_symbols(db.get(KEY_SYMBOLS, DEFAULT_SYMBOLS)),
        interval=normalize_interval(db.get(KEY_INTERVAL, DEFAULT_INTERVAL)),
        monitor_seconds=monitor,
        research_seconds=research,
    )


def persist(
    *,
    symbols=None,
    interval=None,
    monitor_seconds=None,
    research_seconds=None,
) -> PaperConfig:
    """Write only the values that were explicitly supplied, then re-read."""
    if symbols is not None:
        db.setc(KEY_SYMBOLS, normalize_symbols(symbols))
    if interval is not None:
        db.setc(KEY_INTERVAL, normalize_interval(interval))
    if monitor_seconds is not None:
        db.setc(KEY_MONITOR, _normalize_seconds(monitor_seconds, DEFAULT_MONITOR_SECONDS))
    if research_seconds is not None:
        db.setc(KEY_RESEARCH, _normalize_seconds(research_seconds, DEFAULT_RESEARCH_SECONDS))
    return load()


def is_halted() -> bool:
    return load().halted


def set_halted(halted: bool) -> bool:
    """Flip the persistent kill switch. Returns the value now stored."""
    db.setc(KEY_HALTED, bool(halted))
    return bool(halted)


def read_health() -> dict:
    raw = db.get(KEY_HEALTH, None)
    if not isinstance(raw, dict):
        return {}
    return raw


def write_health(**fields) -> dict:
    """Merge fields into the health snapshot and persist it."""
    health = read_health()
    health.update(fields)
    health["updated"] = time.time()
    db.setc(KEY_HEALTH, health)
    return health
