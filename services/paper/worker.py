"""Autonomous worker loop for paper trading.

Two cadences:

* **monitor** (default 60s) - signals, paper orders, fills, positions, equity.
* **research** (default 300s) - the backtest grid and strategy registration.

The worker owns no order placement of its own. Every write goes through
``services.paper.engine``, which is simulated end to end. There is no live-order
path in this module or anywhere it calls.

Planner gate
------------
Autonomous trading no longer reads raw strategy signals. Every cycle asks
``services.paper.planner`` for a plan and trades only what the planner approves:

* the planner is imported lazily, inside the cycle, so a missing or half-written
  planner module cannot stop the worker from starting or from reporting;
* ``execute`` runs only when the plan carries no ``refusal_reason`` **and** the
  planner reports ``analyst_available``. A plan made without the analyst is
  rules-only and is refused: no blind, rules-only trades;
* a refused cycle writes one ``worker_skipped`` decision carrying the reason and
  places no order;
* the planner is called at most once per cycle and a refusal is never retried, so
  one tick can never produce two jobs.

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

# The risk budget a single plan may commit, in USD. Persisted as its own config
# key (not part of ``PaperConfig``) so it survives a restart without touching the
# engine's own risk settings.
KEY_MAX_RISK = "max_risk_usd"
DEFAULT_MAX_RISK_USD = 5.0
MIN_MAX_RISK_USD = 0.01
MAX_MAX_RISK_USD = 1_000_000.0

VERDICT_EXECUTED = "executed"
VERDICT_REFUSED = "refused"

PLANNER_UNAVAILABLE = "planner_unavailable"
PLANNER_ANALYST_UNAVAILABLE = "analyst_unavailable"
PLANNER_ERROR = "planner_error"


def normalize_max_risk(value) -> float:
    """Clamp a risk budget into the range the planner will accept."""
    try:
        amount = float(value)
    except (TypeError, ValueError):
        return DEFAULT_MAX_RISK_USD
    return max(MIN_MAX_RISK_USD, min(MAX_MAX_RISK_USD, amount))


def load_max_risk() -> float:
    return normalize_max_risk(db.get(KEY_MAX_RISK, DEFAULT_MAX_RISK_USD))


def set_max_risk(value) -> float:
    """Persist the risk budget so a restart keeps it. Returns the stored value."""
    amount = normalize_max_risk(value)
    db.setc(KEY_MAX_RISK, amount)
    return amount


def _load_planner():
    """Import the planner lazily.

    The planner is being built alongside this worker, so it may not exist yet.
    Importing here rather than at module scope keeps the worker runnable (and
    loudly refusing to trade) instead of failing to start.
    """
    try:
        from . import planner  # noqa: PLC0415

        return planner
    except Exception as exc:  # missing module, or one that does not import yet
        logger.warning("paper planner unavailable: %s", exc)
        return None


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

    def _heartbeat(self, status: str, error: str = "", **fields) -> None:
        """Record the cycle heartbeat, planner verdict included.

        ``heartbeat`` carries fixed columns, so the planner fields are added once
        with ``db.ensure_column`` and written on every cycle afterwards.
        """
        db.ensure_column("heartbeat", "planner_verdict", "TEXT")
        db.ensure_column("heartbeat", "refusal_reason", "TEXT")
        db.ensure_column("heartbeat", "symbols_scanned", "TEXT")
        db.ensure_column("heartbeat", "duration_s", "REAL")
        row = (
            1,
            time.time(),
            status,
            error[:MAX_ERROR_LENGTH],
            self.cycle,
            fields.get("planner_verdict"),
            fields.get("refusal_reason"),
            fields.get("symbols_scanned"),
            fields.get("duration_s"),
        )
        with db.conn() as c:
            c.execute(
                "INSERT OR REPLACE INTO heartbeat"
                "(id,ts,status,error,cycle,planner_verdict,refusal_reason,"
                "symbols_scanned,duration_s) VALUES(?,?,?,?,?,?,?,?,?)",
                row,
            )

    def _research_due(self, now: float) -> bool:
        if self.last_research_at is None:
            return True
        return now - self.last_research_at >= self.cfg.research_seconds

    # ---------------------------------------------------------------- planner

    def _skip(self, reason: str, cycle_no: int, symbols: list[str]) -> dict:
        """Record a refused cycle: one ``worker_skipped`` row, no order."""
        db.log(
            "worker_skipped",
            {
                "cycle": cycle_no,
                "refusal_reason": reason,
                "symbols": symbols,
            },
        )
        self._log(f"cycle {cycle_no}: planner refused to trade ({reason})")
        return {"verdict": VERDICT_REFUSED, "refusal_reason": reason}

    def _run_planner_cycle(
        self, symbols: list[str], interval: str, max_risk: float, cycle_no: int
    ) -> dict:
        """Ask the planner for a plan and trade it only if it is clean.

        The planner is called exactly once here. A refusal is final for this
        cycle: no second plan, no retry, so one tick cannot produce two jobs.
        """
        planner = _load_planner()
        if planner is None:
            # No planner means no analyst gate. Refuse rather than trade raw
            # signals: a blind rules-only trade is exactly what this gate exists
            # to prevent.
            return self._skip(PLANNER_UNAVAILABLE, cycle_no, symbols)

        try:
            plan = planner.plan(symbols=symbols, max_risk=max_risk, interval=interval)
        except TypeError:
            # Tolerate a planner that does not take the optional keywords yet.
            try:
                plan = planner.plan(symbols=symbols)
            except Exception as exc:
                logger.exception("paper planner failed on cycle %s", cycle_no)
                return self._skip(f"{PLANNER_ERROR}: {type(exc).__name__}", cycle_no, symbols)
        except Exception as exc:
            logger.exception("paper planner failed on cycle %s", cycle_no)
            return self._skip(f"{PLANNER_ERROR}: {type(exc).__name__}: {exc}", cycle_no, symbols)

        if not isinstance(plan, dict):
            return self._skip(f"{PLANNER_ERROR}: plan was not a mapping", cycle_no, symbols)

        refusal = plan.get("refusal_reason")
        if refusal:
            return self._skip(str(refusal), cycle_no, symbols)

        if not plan.get("analyst_available"):
            # The plan was built without the analyst. Refusing it is the point
            # of the gate: rules-only trades stay out.
            return self._skip(PLANNER_ANALYST_UNAVAILABLE, cycle_no, symbols)

        try:
            result = planner.execute(plan)
        except Exception as exc:
            logger.exception("paper planner execute failed on cycle %s", cycle_no)
            return self._skip(f"{PLANNER_ERROR}: {type(exc).__name__}: {exc}", cycle_no, symbols)

        self._log(
            f"cycle {cycle_no}: planner executed "
            f"{plan.get('mode', 'scan')} for {','.join(symbols)}"
        )
        return {"verdict": VERDICT_EXECUTED, "refusal_reason": "", "execute": result}

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
            self._heartbeat(
                "halted",
                planner_verdict="halted",
                refusal_reason="kill_switch",
                symbols_scanned="",
                duration_s=0.0,
            )
            config.write_health(
                status="halted",
                cycle=cycle_no,
                last_error="",
                planner_verdict="halted",
                last_refusal_reason="kill_switch",
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

        watchlist = self.cfg.symbols
        interval = self.cfg.interval
        research_ran = False
        research_seconds = 0.0
        monitor_seconds = 0.0
        error = ""
        status = "ok"

        max_risk = load_max_risk()
        verdict = ""
        refusal_reason = ""

        try:
            if self._research_due(started):
                t0 = time.time()
                engine.research_cycle(watchlist, interval)
                research_seconds = time.time() - t0
                self.last_research_at = time.time()
                research_ran = True
                self.next_research_at = self.last_research_at + self.cfg.research_seconds
            t0 = time.time()
            # The planner is the only path to an order now. engine.trading_cycle
            # is no longer called directly: a raw signal must never be traded.
            outcome = self._run_planner_cycle(watchlist, interval, max_risk, cycle_no)
            monitor_seconds = time.time() - t0
            verdict = outcome["verdict"]
            refusal_reason = outcome.get("refusal_reason", "")
            self.last_monitor_at = time.time()
            self.last_success = time.time()
            status = "refused" if verdict == VERDICT_REFUSED else status
        except Exception as exc:  # one bad cycle must not kill the loop
            status = "error"
            error = f"{type(exc).__name__}: {exc}"
            self._log(f"cycle {cycle_no} error: {error}")
            logger.exception("paper worker cycle %s failed", cycle_no)
            db.log("worker_error", {"cycle": cycle_no, "error": error})

        finished = time.time()
        self.next_cycle_at = finished + self.cfg.monitor_seconds
        self._heartbeat(
            status,
            error,
            planner_verdict=verdict or "none",
            refusal_reason=refusal_reason,
            symbols_scanned=",".join(watchlist),
            duration_s=round(finished - started, 3),
        )
        config.write_health(
            status=status,
            cycle=cycle_no,
            last_error=error,
            symbols=watchlist,
            interval=self.cfg.interval,
            planner_verdict=verdict or "none",
            last_refusal_reason=refusal_reason,
            symbols_scanned=",".join(watchlist),
            max_risk_usd=max_risk,
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
            f"(research={'yes' if research_ran else 'no'}, planner={verdict or 'none'}) "
            f"{','.join(watchlist)}"
        )
        return {
            "status": status,
            "cycle": cycle_no,
            "planner_verdict": verdict or "none",
            "refusal_reason": refusal_reason,
            "symbols_scanned": list(watchlist),
            "max_risk": max_risk,
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


def _hb_field(hb, name: str):
    """Read a heartbeat column, tolerating a database written before it existed."""
    if hb is None or name not in hb.keys():
        return None
    return hb[name]


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
        "max_risk": load_max_risk(),
        "heartbeat": dict(hb) if hb else None,
        "health": health,
        "planner_verdict": _hb_field(hb, "planner_verdict")
        or health.get("planner_verdict"),
        "refusal_reason": _hb_field(hb, "refusal_reason")
        or health.get("last_refusal_reason"),
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
        f"  max risk         : {state['max_risk']} USD per plan",
        f"  heartbeat        : {hb.get('status', 'unknown')} at {_fmt_ts(hb.get('ts'))}",
        f"  planner verdict  : {state.get('planner_verdict') or 'none yet'}",
        f"  refusal reason   : {state.get('refusal_reason') or 'none'}",
        f"  symbols scanned  : {health.get('symbols_scanned') or '-'}",
        f"  cycle seconds    : {_hb_field(state['heartbeat'], 'duration_s') or '-'}",
        f"  cycle number     : {hb.get('cycle', health.get('cycle', 0))}",
        f"  last successful  : {_fmt_ts(state['last_successful_cycle'])}",
        f"  next cycle       : {_fmt_ts(state['next_cycle_at'])}",
        f"  next research    : {_fmt_ts(state['next_research_at'])}",
        f"  last error       : {health.get('last_error') or 'none'}",
    ]
    return "\n".join(lines)
