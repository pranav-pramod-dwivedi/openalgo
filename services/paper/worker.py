"""Autonomous worker loop for paper trading.

Two cadences:

* **monitor** (default 60s) - signals, paper orders, fills, positions, equity.
* **research** (default 300s) - the backtest grid and strategy registration.

The worker owns no order placement of its own. Every write goes through
``services.paper.engine``, which is simulated end to end. There is no live-order
path in this module or anywhere it calls.

Concurrency guard
-----------------
A research cycle can take much longer than the monitor cadence, so a tick that
arrives while one is still running must not start a second one. The guard is a
single ``threading.Lock`` acquired non-blocking: if it is already held the tick
is skipped and recorded as ``skipped_busy`` rather than queued. The loop is
single-threaded today, so the lock is belt-and-braces, but it keeps a future
threaded scheduler from double-running a cycle. It does not protect against a
second *process* running the worker at the same time.
"""

from __future__ import annotations

import threading
import time

from utils.logging import get_logger

from . import config, db, engine

logger = get_logger(__name__)
from .config import PaperConfig, read_health

SLEEP_SLICE = 1.0
MAX_ERROR_LENGTH = 500


class PaperWorker:
    """Runs monitor and research cycles on their own cadences."""

    def __init__(self, cfg: PaperConfig | None = None, verbose: bool = True) -> None:
        self._lock = threading.Lock()
        self._verbose = verbose
        self.cfg = cfg or config.load()
        self.cycle = 0
        self.last_success: float | None = None
        self.last_monitor_at: float | None = None
        self.last_research_at: float | None = None
        self.next_cycle_at: float | None = None
        self.next_research_at: float | None = None

    # ---------------------------------------------------------------- helpers

    def refresh(self) -> PaperConfig:
        self.cfg = config.load()
        return self.cfg

    def _log(self, message: str) -> None:
        if self._verbose:
            print(f"[paper_worker] {message}")

    def _heartbeat(self, status: str, error: str = "") -> None:
        with db.conn() as c:
            c.execute(
                "INSERT OR REPLACE INTO heartbeat(id,ts,status,error,cycle) VALUES(?,?,?,?,?)",
                (1, time.time(), status, error[:MAX_ERROR_LENGTH], self.cycle),
            )

    def _research_due(self, now: float) -> bool:
        if self.last_research_at is None:
            return True
        return now - self.last_research_at >= self.cfg.research_seconds

    # ------------------------------------------------------------------ cycle

    def run_cycle(self) -> dict:
        """One monitor tick. Returns a summary dict; never raises."""
        if not self._lock.acquire(blocking=False):
            summary = {"status": "skipped_busy", "cycle": self.cycle}
            self._log("previous cycle still running, skipping this tick")
            return summary
        try:
            return self._run_cycle_locked()
        finally:
            self._lock.release()

    def _run_cycle_locked(self) -> dict:
        started = time.time()
        self.cycle += 1
        cycle_no = self.cycle

        # The kill switch is read first, every cycle, before any work.
        self.refresh()
        if self.cfg.halted:
            self.next_cycle_at = started + self.cfg.monitor_seconds
            self._heartbeat("halted")
            config.write_health(
                status="halted",
                cycle=cycle_no,
                last_error="",
                symbols=self.cfg.symbols,
                interval=self.cfg.interval,
                last_cycle_started=started,
                last_cycle_seconds=round(time.time() - started, 3),
                last_success=self.last_success or read_health().get("last_success"),
                next_cycle_at=self.next_cycle_at,
                next_research_at=self.next_research_at,
                skipped=True,
            )
            self._log(f"cycle {cycle_no}: halted, not trading")
            return {"status": "halted", "cycle": cycle_no}

        # The configured watchlist is persisted, but engine.WATCHLIST is a module
        # constant with no setter. See PAPER_TRADING.md - "Watchlist limitation".
        watchlist = self.cfg.symbols
        research_ran = False
        research_seconds = 0.0
        monitor_seconds = 0.0
        error = ""
        status = "ok"

        try:
            if self._research_due(started):
                t0 = time.time()
                engine.research_cycle()
                research_seconds = time.time() - t0
                self.last_research_at = time.time()
                research_ran = True
            t0 = time.time()
            engine.trading_cycle()
            monitor_seconds = time.time() - t0
            self.last_monitor_at = time.time()
            self.last_success = time.time()
            self.next_research_at = self.last_research_at + self.cfg.research_seconds
        except Exception as exc:  # one bad cycle must not kill the loop
            status = "error"
            error = f"{type(exc).__name__}: {exc}"
            self._log(f"cycle {cycle_no} error: {error}")
            logger.exception("paper worker cycle %s failed", cycle_no)
            db.log("worker_error", {"cycle": cycle_no, "error": error})

        finished = time.time()
        self.next_cycle_at = finished + self.cfg.monitor_seconds
        self._heartbeat(status, error)
        config.write_health(
            status=status,
            cycle=cycle_no,
            last_error=error,
            symbols=watchlist,
            interval=self.cfg.interval,
            last_cycle_started=started,
            last_cycle_seconds=round(finished - started, 3),
            last_monitor_seconds=round(monitor_seconds, 3),
            last_research_seconds=round(research_seconds, 3) if research_ran else None,
            research_ran=research_ran,
            last_success=self.last_success,
            last_monitor_at=self.last_monitor_at,
            last_research_at=self.last_research_at,
            next_cycle_at=self.next_cycle_at,
            next_research_at=self.next_research_at,
            skipped=False,
        )
        self._log(
            f"cycle {cycle_no}: {status} in {finished - started:.1f}s "
            f"(research={'yes' if research_ran else 'no'}) {','.join(watchlist)}"
        )
        return {
            "status": status,
            "cycle": cycle_no,
            "seconds": round(finished - started, 3),
            "research_ran": research_ran,
            "error": error,
        }

    # -------------------------------------------------------------------- loop

    def sleep_until_next(self) -> None:
        """Sleep in slices so a halt lands promptly instead of at cycle end."""
        deadline = time.time() + self.cfg.monitor_seconds
        while True:
            remaining = deadline - time.time()
            if remaining <= 0:
                return
            time.sleep(min(SLEEP_SLICE, remaining))

    def run_forever(self) -> None:
        """The default autonomous mode. Returns only if asked to stop."""
        self._log(
            f"starting autonomous loop: every {self.cfg.monitor_seconds}s monitor, "
            f"every {self.cfg.research_seconds}s research, "
            f"watchlist {','.join(self.cfg.symbols)} @ {self.cfg.interval}"
        )
        self.refresh()
        if self.cfg.halted:
            self._log("kill switch is set, idling without trading until --resume")
        while True:
            self.run_cycle()
            self.sleep_until_next()


def run_once(worker: PaperWorker | None = None) -> dict:
    w = worker or PaperWorker()
    return w.run_cycle()


def status(cfg: PaperConfig | None = None) -> dict:
    """Read worker health straight from the database."""
    cfg = cfg or config.load()
    health = config.read_health()
    with db.conn() as c:
        hb = c.execute("SELECT * FROM heartbeat WHERE id=1").fetchone()
    return {
        "halted": cfg.halted,
        "symbols": cfg.symbols,
        "interval": cfg.interval,
        "monitor_seconds": cfg.monitor_seconds,
        "research_seconds": cfg.research_seconds,
        "heartbeat": dict(hb) if hb else None,
        "health": health,
        "last_successful_cycle": health.get("last_success"),
        "next_cycle_at": health.get("next_cycle_at"),
        "next_research_at": health.get("next_research_at"),
    }


def _fmt_ts(value) -> str:
    if not value:
        return "never"
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(float(value)))


def format_status(state: dict) -> str:
    hb = state["heartbeat"] or {}
    health = state["health"]
    lines = [
        "Paper worker status",
        f"  kill switch      : {'HALTED (not trading)' if state['halted'] else 'running'}",
        f"  watchlist        : {','.join(state['symbols'])} @ {state['interval']}",
        f"  monitor cadence  : {state['monitor_seconds']}s",
        f"  research cadence : {state['research_seconds']}s",
        f"  heartbeat        : {hb.get('status', 'unknown')} at {_fmt_ts(hb.get('ts'))}",
        f"  cycle number     : {hb.get('cycle', health.get('cycle', 0))}",
        f"  last successful  : {_fmt_ts(state['last_successful_cycle'])}",
        f"  next cycle       : {_fmt_ts(state['next_cycle_at'])}",
        f"  next research    : {_fmt_ts(state['next_research_at'])}",
        f"  last cycle secs  : {health.get('last_cycle_seconds', '-')}",
        f"  last error       : {health.get('last_error') or 'none'}",
    ]
    return "\n".join(lines)
