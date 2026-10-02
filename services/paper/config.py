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
``research_intervals``
    The timeframes research backtests each symbol on, not just the one the
    worker trades.
``research_max_experiments``
    The per-cycle cap on backtests, so a wider grid cannot make one cycle run
    without bound. Work left out is logged, never silently dropped.
``min_trades`` / ``min_net_pnl`` / ``max_drawdown_pct``
    The bar a backtest has to clear before its strategy may be registered
    active. Research reads these; the engine additionally refuses any candidate
    with a negative net P&L regardless of what is configured.
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
KEY_RESEARCH_INTERVALS = "research_intervals"
KEY_RESEARCH_MAX_EXPERIMENTS = "research_max_experiments"
KEY_MIN_TRADES = "min_trades"
KEY_MIN_NET_PNL = "min_net_pnl"
KEY_MAX_DRAWDOWN_PCT = "max_drawdown_pct"
KEY_MONITOR = "monitor_seconds"
KEY_RESEARCH = "research_seconds"
KEY_HEALTH = "worker_health"

# Liquid Binance pairs with real 1m/5m history. Chosen for depth and history, not
# for a story: a thin pair cannot be backtested honestly.
DEFAULT_SYMBOLS = [
    "BTCUSDT",
    "ETHUSDT",
    "SOLUSDT",
    "BNBUSDT",
    "XRPUSDT",
    "ADAUSDT",
    "DOGEUSDT",
    "LINKUSDT",
    "AVAXUSDT",
]
DEFAULT_INTERVAL = "5m"
# Research validates each symbol on several timeframes. A strategy tuned on 5m
# noise is not a strategy on 1h, so the three are validated separately.
DEFAULT_RESEARCH_INTERVALS = ["5m", "15m", "1h"]
# Nine symbols x three timeframes x seven families is far more work than one
# cycle should hold, so the grid is capped and the rest is picked up next cycle.
DEFAULT_RESEARCH_MAX_EXPERIMENTS = 300
DEFAULT_MONITOR_SECONDS = 60
DEFAULT_RESEARCH_SECONDS = 300

# The validation bar. Trades 2 rather than 3, so a setup that fires twice is
# still evidence, but a net loss of any size is never a candidate: a losing
# backtest is the clearest signal there is, and it does not become a strategy
# because the bar was lowered.
DEFAULT_MIN_TRADES = 2
DEFAULT_MIN_NET_PNL = 0.0
DEFAULT_MAX_DRAWDOWN_PCT = 5.0

VALID_INTERVALS = {"1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "1d"}

MIN_INTERVAL_SECONDS = 5
MAX_INTERVAL_SECONDS = 86_400
MIN_RESEARCH_MAX_EXPERIMENTS = 1
MAX_RESEARCH_MAX_EXPERIMENTS = 10_000

DEFAULTS = {
    KEY_HALTED: False,
    KEY_SYMBOLS: DEFAULT_SYMBOLS,
    KEY_INTERVAL: DEFAULT_INTERVAL,
    KEY_RESEARCH_INTERVALS: DEFAULT_RESEARCH_INTERVALS,
    KEY_RESEARCH_MAX_EXPERIMENTS: DEFAULT_RESEARCH_MAX_EXPERIMENTS,
    KEY_MIN_TRADES: DEFAULT_MIN_TRADES,
    KEY_MIN_NET_PNL: DEFAULT_MIN_NET_PNL,
    KEY_MAX_DRAWDOWN_PCT: DEFAULT_MAX_DRAWDOWN_PCT,
    KEY_MONITOR: DEFAULT_MONITOR_SECONDS,
    KEY_RESEARCH: DEFAULT_RESEARCH_SECONDS,
}


def normalize_intervals(value) -> list[str]:
    """Accept a list or a comma separated string; keep only allowed timeframes."""
    if isinstance(value, str):
        parts = value.replace(";", ",").split(",")
    elif isinstance(value, (list, tuple)):
        parts = list(value)
    else:
        parts = []
    out: list[str] = []
    for part in parts:
        iv = str(part).strip()
        if iv in VALID_INTERVALS and iv not in out:
            out.append(iv)
    return out or list(DEFAULT_RESEARCH_INTERVALS)


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


def _normalize_bounded_int(value, default: int, low: int, high: int) -> int:
    try:
        number = int(float(value))
    except (TypeError, ValueError):
        return default
    return max(low, min(high, number))


def _normalize_float(value, default: float, low: float, high: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if number != number:  # NaN
        return default
    return max(low, min(high, number))


@dataclass
class PaperConfig:
    """The worker's view of the config table, already validated."""

    halted: bool
    symbols: list[str]
    interval: str
    research_intervals: list[str]
    research_max_experiments: int
    min_trades: int
    min_net_pnl: float
    max_drawdown_pct: float
    monitor_seconds: int
    research_seconds: int

    @property
    def symbols_csv(self) -> str:
        return ",".join(self.symbols)

    @property
    def research_intervals_csv(self) -> str:
        return ",".join(self.research_intervals)

    def validation_bar(self) -> dict:
        """The bar a backtest must clear to become a registered strategy."""
        return {
            KEY_MIN_TRADES: self.min_trades,
            KEY_MIN_NET_PNL: self.min_net_pnl,
            KEY_MAX_DRAWDOWN_PCT: self.max_drawdown_pct,
        }

    def as_dict(self) -> dict:
        return {
            KEY_HALTED: self.halted,
            KEY_SYMBOLS: self.symbols,
            KEY_INTERVAL: self.interval,
            KEY_RESEARCH_INTERVALS: self.research_intervals,
            KEY_RESEARCH_MAX_EXPERIMENTS: self.research_max_experiments,
            KEY_MIN_TRADES: self.min_trades,
            KEY_MIN_NET_PNL: self.min_net_pnl,
            KEY_MAX_DRAWDOWN_PCT: self.max_drawdown_pct,
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
        research_intervals=normalize_intervals(
            db.get(KEY_RESEARCH_INTERVALS, DEFAULT_RESEARCH_INTERVALS)
        ),
        research_max_experiments=_normalize_bounded_int(
            db.get(KEY_RESEARCH_MAX_EXPERIMENTS, DEFAULT_RESEARCH_MAX_EXPERIMENTS),
            DEFAULT_RESEARCH_MAX_EXPERIMENTS,
            MIN_RESEARCH_MAX_EXPERIMENTS,
            MAX_RESEARCH_MAX_EXPERIMENTS,
        ),
        min_trades=_normalize_bounded_int(
            db.get(KEY_MIN_TRADES, DEFAULT_MIN_TRADES), DEFAULT_MIN_TRADES, 1, 10_000
        ),
        min_net_pnl=_normalize_float(
            db.get(KEY_MIN_NET_PNL, DEFAULT_MIN_NET_PNL), DEFAULT_MIN_NET_PNL, -1e9, 1e9
        ),
        max_drawdown_pct=_normalize_float(
            db.get(KEY_MAX_DRAWDOWN_PCT, DEFAULT_MAX_DRAWDOWN_PCT),
            DEFAULT_MAX_DRAWDOWN_PCT,
            0.0,
            100.0,
        ),
        monitor_seconds=monitor,
        research_seconds=research,
    )


def persist(
    *,
    symbols=None,
    interval=None,
    research_intervals=None,
    research_max_experiments=None,
    min_trades=None,
    min_net_pnl=None,
    max_drawdown_pct=None,
    monitor_seconds=None,
    research_seconds=None,
) -> PaperConfig:
    """Write only the values that were explicitly supplied, then re-read."""
    if symbols is not None:
        db.setc(KEY_SYMBOLS, normalize_symbols(symbols))
    if interval is not None:
        db.setc(KEY_INTERVAL, normalize_interval(interval))
    if research_intervals is not None:
        db.setc(KEY_RESEARCH_INTERVALS, normalize_intervals(research_intervals))
    if research_max_experiments is not None:
        db.setc(
            KEY_RESEARCH_MAX_EXPERIMENTS,
            _normalize_bounded_int(
                research_max_experiments,
                DEFAULT_RESEARCH_MAX_EXPERIMENTS,
                MIN_RESEARCH_MAX_EXPERIMENTS,
                MAX_RESEARCH_MAX_EXPERIMENTS,
            ),
        )
    if min_trades is not None:
        db.setc(
            KEY_MIN_TRADES,
            _normalize_bounded_int(min_trades, DEFAULT_MIN_TRADES, 1, 10_000),
        )
    if min_net_pnl is not None:
        db.setc(
            KEY_MIN_NET_PNL,
            _normalize_float(min_net_pnl, DEFAULT_MIN_NET_PNL, -1e9, 1e9),
        )
    if max_drawdown_pct is not None:
        db.setc(
            KEY_MAX_DRAWDOWN_PCT,
            _normalize_float(max_drawdown_pct, DEFAULT_MAX_DRAWDOWN_PCT, 0.0, 100.0),
        )
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
