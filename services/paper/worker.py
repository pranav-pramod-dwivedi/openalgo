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

One trade per cycle
-------------------
The loop may run for hours, but a single cycle places at most one trade
(``MAX_TRADES_PER_CYCLE``). That is enforced, not assumed: every order goes
through :meth:`PaperWorker._execute_once`, which counts the orders placed in the
current cycle and refuses a second with ``too_many_trades_in_cycle`` -- recorded
in the journal, never sent -- and the counter is reset at the start of each
cycle. So the next tick can trade again, while no tick can ever trade twice. The
count is reported as ``trades_placed`` in the cycle summary and in status, so a
reader can see that a cycle did one thing.

Exit management
---------------
Opening a position is only half of a trade, so every cycle manages the other half
before it plans anything new:

* ``planner.manage_open_positions`` walks **every** open position -- not only the
  watchlist's -- and closes it when the mark reaches its stop or target, when the
  max-hold limit is reached, or when the strategy that owns it now signals the
  other way. It runs before the planner is asked for a plan, so a stop is acted on
  in the tick it is seen;
* an exit is always the full position quantity. ``engine.plan_size`` is entry
  sizing and shrinks with free cash, so using it here would leave a fraction of
  the position open at exactly the moment the account is least able to fund it;
* the kill switch stays authoritative: halted means no orders at all, exits
  included, so a halted worker's open positions are unmanaged until ``--resume``.
  That is reported every halted cycle (``position_exit_halted`` naming the
  positions) rather than done quietly.

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

# Orders a single cycle may place. One. See the module docstring.
MAX_TRADES_PER_CYCLE = 1
TOO_MANY_TRADES = "too_many_trades_in_cycle"

PLANNER_UNAVAILABLE = "planner_unavailable"
PLANNER_ANALYST_UNAVAILABLE = "analyst_unavailable"
PLANNER_ERROR = "planner_error"

# The journal kind a halted cycle writes, kept in step with
# ``planner.KIND_EXIT_HALTED``. Spelled here so a halted cycle can be recorded
# without importing the planner, which may be missing.
EXIT_HALTED_KIND = "position_exit_halted"


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
        # Orders placed by this cycle and by this process, so both the one-trade
        # rule and the totals are counted rather than assumed.
        self._cycle_trades = 0
        self.trades_placed = 0
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

    def _execute_once(self, planner_module, plan: dict, cycle_no: int, symbols: list[str]) -> dict:
        """The one order this cycle may place.

        Every order the worker places comes through here, and the count is per
        cycle. A second attempt in the same cycle is refused here, in the
        journal and in the log, without reaching ``planner.execute`` -- so a
        cycle cannot place two orders even if a later edit asks it to try again.
        The next cycle starts with the allowance back.
        """
        if self._cycle_trades >= MAX_TRADES_PER_CYCLE:
            self._log(
                f"cycle {cycle_no}: refused, this cycle already placed its one trade "
                "and will not place another"
            )
            db.log(
                "worker_skipped",
                {
                    "cycle": cycle_no,
                    "refusal_reason": TOO_MANY_TRADES,
                    "symbols": symbols,
                    "detail": (
                        f"a cycle places at most {MAX_TRADES_PER_CYCLE} trade; this one "
                        "had already placed it, so nothing further was sent"
                    ),
                },
            )
            return {
                "status": "refused",
                "executed": False,
                "refusal_reason": TOO_MANY_TRADES,
                "detail": "this cycle has already placed its one trade",
            }

        self._cycle_trades += 1
        result = planner_module.execute(plan)
        if isinstance(result, dict) and result.get("executed"):
            self.trades_placed += 1
            self._log(
                f"cycle {cycle_no}: placed 1 trade: {result.get('side')} "
                f"{result.get('symbol')} qty {result.get('qty')} @ {result.get('price')}"
            )
        return result

    def _run_planner_cycle(
        self, symbols: list[str], interval: str, max_risk: float, cycle_no: int
    ) -> dict:
        """Ask the planner for a plan and trade it only if it is clean.

        The planner is called exactly once here. A refusal is final for this
        cycle: no second plan, no retry, so one tick cannot produce two jobs.
        """
        planner_module = _load_planner()
        if planner_module is None:
            # No planner means no analyst gate. Refuse rather than trade raw
            # signals: a blind rules-only trade is exactly what this gate exists
            # to prevent.
            return self._skip(PLANNER_UNAVAILABLE, cycle_no, symbols)

        try:
            plan = planner_module.plan(symbols=symbols, max_risk=max_risk, interval=interval)
        except TypeError:
            # Tolerate a planner that does not take the optional keywords yet.
            try:
                plan = planner_module.plan(symbols=symbols)
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
            result = self._execute_once(planner_module, plan, cycle_no, symbols)
        except Exception as exc:
            logger.exception("paper planner execute failed on cycle %s", cycle_no)
            return self._skip(f"{PLANNER_ERROR}: {type(exc).__name__}: {exc}", cycle_no, symbols)

        if isinstance(result, dict) and not result.get("executed"):
            # The guarded execute refused (its one trade is already spent) or the
            # planner refused the order. Either way this cycle placed nothing and
            # the next cycle starts over.
            return self._skip(
                str(result.get("refusal_reason") or PLANNER_ERROR), cycle_no, symbols
            )

        return {"verdict": VERDICT_EXECUTED, "refusal_reason": "", "execute": result}

    def _manage_exits(self, cycle_no: int) -> list[dict]:
        """Act on every open position's stop, target, max hold or signal flip.

        Runs before the planner is asked for anything, so a stop is acted on in
        the same tick it is seen rather than after the next entry has been
        considered. The kill switch is checked in the cycle above this, and the
        planner checks it again: halted means no orders at all, exits included,
        and it says so in the journal rather than leaving a position silently
        unmanaged.
        """
        planner = _load_planner()
        if planner is None:
            self._log(f"cycle {cycle_no}: no planner, so open positions are not managed")
            return []
        try:
            exits = planner.manage_open_positions()
        except Exception as exc:
            # Exit management failing must not cost the cycle, and must not stop
            # the planner from being asked for a new trade either.
            logger.exception("exit management failed on cycle %s", cycle_no)
            db.log("exit_management_error", {"cycle": cycle_no, "error": f"{type(exc).__name__}: {exc}"})
            return []
        for record in exits:
            self._log(
                f"cycle {cycle_no}: closed {record.get('symbol')} {record.get('side')} "
                f"{record.get('qty')} at {record.get('exit_price')} "
                f"({record.get('reason')}), net {record.get('realized_pnl'):+.4f} USD"
            )
        if exits:
            db.log("positions_closed", {"cycle": cycle_no, "count": len(exits)})
        return list(exits)

    def _report_unmanaged(self, cycle_no: int) -> list[str]:
        """Name the positions a halted cycle is leaving alone.

        The kill switch is authoritative: halted means no orders, and an exit is
        an order. So a halted worker does not manage its open positions -- their
        stops, targets and max-hold limits all wait -- and this says which
        positions are waiting, so "halted" never reads as "nothing is open".
        """
        try:
            with db.conn() as c:
                names = [
                    str(r["symbol"])
                    for r in c.execute("SELECT symbol FROM positions WHERE status='open'")
                ]
        except Exception:
            return []
        if names:
            db.log(
                EXIT_HALTED_KIND,
                {
                    "cycle": cycle_no,
                    "reason": "kill_switch",
                    "open_positions": names,
                    "detail": (
                        "trading is halted, so no exit is placed either; these positions are "
                        "unmanaged until the worker is resumed"
                    ),
                },
            )
            self._log(
                f"cycle {cycle_no}: halted, so {len(names)} open position(s) are unmanaged "
                f"until --resume: {','.join(names)}"
            )
        return names

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
        # The one-trade allowance is per cycle. A long-running worker gets it
        # back every tick, and never twice in one tick.
        self._cycle_trades = 0

        # The kill switch is read first, every cycle, before any work.
        self.refresh()
        if self.cfg.halted:
            self.next_cycle_at = started + self.cfg.monitor_seconds
            unmanaged = self._report_unmanaged(cycle_no)
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
                positions_closed=0,
                unmanaged_positions=unmanaged,
                last_success=self.last_success or read_health().get("last_success"),
                next_cycle_at=self.next_cycle_at,
                next_research_at=self.next_research_at,
                skipped=True,
            )
            self._log(f"cycle {cycle_no}: halted, not trading")
            return {
                "status": "halted",
                "cycle": cycle_no,
                "exits": [],
                "positions_closed": 0,
                "trades_placed": 0,
                # Named rather than hidden: while halted, no position is managed.
                "unmanaged_positions": unmanaged,
            }

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
        exits: list[dict] = []

        try:
            if self._research_due(started):
                t0 = time.time()
                engine.research_cycle(watchlist, interval)
                research_seconds = time.time() - t0
                self.last_research_at = time.time()
                research_ran = True
                self.next_research_at = self.last_research_at + self.cfg.research_seconds
            t0 = time.time()
            # Exits first, entries second: an open position's stop is acted on
            # before anything new is planned, so the bracket is never late.
            exits = self._manage_exits(cycle_no)
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
            positions_closed=len(exits),
            trades_placed=self._cycle_trades,
            trades_placed_this_run=self.trades_placed,
            last_exits=[e["symbol"] for e in exits],
            last_success=self.last_success,
            last_monitor_at=self.last_monitor_at,
            last_research_at=self.last_research_at,
            next_cycle_at=self.next_cycle_at,
            next_research_at=self.next_research_at,
            skipped=False,
        )
        self._log(
            f"cycle {cycle_no}: {status} in {finished - started:.1f}s "
            f"(research={'yes' if research_ran else 'no'}, closed={len(exits)}, "
            f"planner={verdict or 'none'}) {','.join(watchlist)}"
        )
        return {
            "status": status,
            "cycle": cycle_no,
            "planner_verdict": verdict or "none",
            "refusal_reason": refusal_reason,
            "symbols_scanned": list(watchlist),
            "max_risk": max_risk,
            # Counted, not inferred: 0 or 1, never more, for this cycle.
            "trades_placed": self._cycle_trades,
            "seconds": round(finished - started, 3),
            "research_ran": research_ran,
            "exits": exits,
            "positions_closed": len(exits),
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


def last_trade() -> dict | None:
    """The most recent paper fill: symbol, side, quantity, price and time.

    Read from ``fills``, so it is an order that actually happened rather than an
    intention. Shown by status so the one-trade-per-cycle rule is visible: each
    cycle adds at most one of these.
    """
    try:
        with db.conn() as c:
            row = c.execute(
                "SELECT symbol, side, qty, price, ts FROM fills ORDER BY id DESC LIMIT 1"
            ).fetchone()
    except Exception:
        return None
    return dict(row) if row is not None else None


def _fmt_trade(row: dict | None) -> str:
    """One sentence: what was traded, which way, and when."""
    if not row:
        return "none yet"
    verb = "bought" if str(row.get("side", "")).upper() == "BUY" else "sold"
    return (
        f"{verb} {float(row.get('qty', 0.0)):.6f} {row.get('symbol')} "
        f"at {float(row.get('price', 0.0)):.4f}, {_fmt_ts(row.get('ts'))}"
    )


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
        "positions_closed": health.get("positions_closed", 0),
        "trades_placed_last_cycle": health.get("trades_placed", 0),
        "trades_placed_this_run": health.get("trades_placed_this_run", 0),
        "last_trade": last_trade(),
        "unmanaged_positions": health.get("unmanaged_positions", []) or [],
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
        f"  trades per cycle : at most {MAX_TRADES_PER_CYCLE} - "
        f"{state.get('trades_placed_last_cycle', 0)} placed last cycle",
        f"  last trade placed: {_fmt_trade(state.get('last_trade'))}",
        f"  positions closed : {state.get('positions_closed', 0)} last cycle",
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
