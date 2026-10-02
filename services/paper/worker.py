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
* the planner is called at most once per cycle, so one tick can never produce
  two jobs;
* a plan carrying no ``refusal_reason`` but no analyst opinion is refused while
  the analyst is healthy: no blind, rules-only trades.

Analyst outage
--------------
A free-tier analyst that answers nothing is not a reason for an account to stand
still for hours, and refusing forever is indistinguishable, to a user, from a
worker that is stuck. So the worker counts the cycles in which the analyst did
not answer:

* the analyst is asked **first on every cycle**, outage or not, so reviewed
  trading resumes on the very next cycle in which the quota has reset -- no
  restart, no manual step, no operator watching for a switch;
* once the analyst has failed ``analyst_outage_cycles`` cycles running (config,
  default 3) the worker stops refusing and asks for that cycle's plan with
  ``allow_rules_only=True``. The plan is still risk-checked, still sized and
  still bracketed exactly as a reviewed one; only the model opinion is missing;
* every trade placed that way is stamped unreviewed on the plan itself
  (``analyst_bypassed``) and again in a ``unreviewed_trade`` decision, so no
  record anywhere can be read as a reviewed trade;
* the switch is journalled **once**: ``analyst_outage_started`` when unreviewed
  trading begins, ``analyst_outage_ended`` when the analyst answers again. One
  row each, however many cycles the outage lasts -- a row per cycle would make
  the outage itself the noise;
* neither switch relaxes anything else. One trade per cycle still holds, and
  halted still means no orders at all, unreviewed trading included.

Journal hygiene
---------------
A refusal that repeats every minute is not information, and a journal nobody can
read is not a decision log. An identical refusal -- same reason, as the
planner's own vocabulary defines it -- is journalled at most once an hour
(:data:`REFUSAL_LOG_INTERVAL_SECONDS`). The throttle state is the journal itself:
a refusal is written only after asking the ``decisions`` table whether this same
reason was already recorded inside the window, on the same connection the row is
then inserted on. Nothing is cached in the worker, so a second process running
the same database honours the same throttle instead of holding a private count
that drifts from what is written.

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

import json
import sqlite3
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
DEFAULT_MAX_RISK_USD = 2.0
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

# Journal kinds. KIND_SKIPPED is a refused cycle, which is the thing that
# repeats. The two outage kinds mark a state change and are written once each,
# never per cycle, so the outage is legible as two rows rather than as noise.
KIND_SKIPPED = "worker_skipped"
KIND_OUTAGE_STARTED = "analyst_outage_started"
KIND_OUTAGE_ENDED = "analyst_outage_ended"
KIND_UNREVIEWED_TRADE = "unreviewed_trade"

# How often one identical refusal may be journalled, in seconds. One hour is the
# journal's resolution: after it the reason is worth writing again, because
# something may have changed in between.
REFUSAL_LOG_INTERVAL_SECONDS = 3600

# The plain-language note stamped on an unreviewed trade. It is the same sentence
# the operator reads in status, kept in one place so the journal and the screen
# cannot drift apart.
UNREVIEWED_NOTE = (
    "the AI analyst was not answering, so this trade was placed on rules alone "
    "and nothing reviewed it"
)

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


# ------------------------------------------------------------ refusal throttle


def _last_refusal_ts(conn, reason: str) -> float | None:
    """When this exact reason was last journalled on ``conn``, if ever.

    Read through SQLite's JSON reader so the filter happens in the database
    rather than by pulling the table into Python. A build without that function
    falls back to a bounded scan instead of losing the throttle entirely.
    """
    try:
        row = conn.execute(
            "SELECT ts FROM decisions WHERE kind=? "
            "AND json_extract(payload, '$.refusal_reason')=? ORDER BY id DESC LIMIT 1",
            (KIND_SKIPPED, reason),
        ).fetchone()
    except sqlite3.Error:
        return _last_refusal_ts_scan(conn, reason)
    return float(row["ts"]) if row is not None else None


def _last_refusal_ts_scan(conn, reason: str) -> float | None:
    """The same lookup without ``json_extract``, over the most recent rows."""
    rows = conn.execute(
        "SELECT ts, payload FROM decisions WHERE kind=? ORDER BY id DESC LIMIT 500",
        (KIND_SKIPPED,),
    ).fetchall()
    for row in rows:
        try:
            payload = json.loads(row["payload"])
        except (TypeError, ValueError):
            continue
        if isinstance(payload, dict) and str(payload.get("refusal_reason", "")) == reason:
            return float(row["ts"])
    return None


def _journal_skip(reason: str, payload: dict) -> bool:
    """Write one refused-cycle row, unless this reason was written recently.

    Returns True when a row was appended, False when the hour had not passed and
    the refusal was already on record.

    The throttle is the journal. Whether a reason is due is answered by reading
    ``decisions`` -- the table every process writes -- on the same connection the
    row is then inserted on, rather than from a counter this process keeps in
    memory. That is what makes it hold across processes: a second worker, or the
    ``--status`` shell, cannot disagree with what was written, and restarting
    cannot replay an hour of refusals that were never logged.

    The write lock is taken before the lookup so the check and the insert cannot
    interleave with another process doing the same. If SQLite refuses to start
    that transaction the row is still written, throttled or not: losing one
    duplicate row is preferable to losing the record of a refusal.
    """
    row = dict(payload)
    row["refusal_reason"] = reason
    try:
        with db.conn() as c:
            try:
                c.execute("BEGIN IMMEDIATE")
            except sqlite3.Error:
                pass  # already inside a transaction, or this build objects
            last = _last_refusal_ts(c, reason)
            if last is not None and time.time() - last < REFUSAL_LOG_INTERVAL_SECONDS:
                return False
            db.log(KIND_SKIPPED, row, existing=c)
        return True
    except Exception:
        logger.exception("could not journal the refusal %s; recording it anyway", reason)
        db.log(KIND_SKIPPED, row)
        return True


def analyst_outage_state() -> dict:
    """Whether trading has fallen back to unreviewed, since when, and for how much.

    Read from the journal rather than from the worker's memory, so this is the
    same answer in every process and survives the worker being restarted: the
    last ``analyst_outage_started`` or ``analyst_outage_ended`` row says which
    side of the switch we are on, and the unreviewed trades placed since that row
    are counted by scanning forward from it.
    """
    try:
        with db.conn() as c:
            marker = c.execute(
                "SELECT id, ts, kind FROM decisions WHERE kind IN (?,?) "
                "ORDER BY id DESC LIMIT 1",
                (KIND_OUTAGE_STARTED, KIND_OUTAGE_ENDED),
            ).fetchone()
            if marker is None:
                return {
                    "active": False,
                    "started_at": None,
                    "unreviewed_trades": 0,
                    "last_unreviewed_trade_at": None,
                }
            unreviewed = 0
            last_at = None
            if marker["kind"] == KIND_OUTAGE_STARTED:
                rows = c.execute(
                    "SELECT ts FROM decisions WHERE kind=? AND id>=? ORDER BY id",
                    (KIND_UNREVIEWED_TRADE, marker["id"]),
                ).fetchall()
                unreviewed = len(rows)
                last_at = float(rows[-1]["ts"]) if rows else None
            return {
                "active": marker["kind"] == KIND_OUTAGE_STARTED,
                "started_at": float(marker["ts"]),
                "unreviewed_trades": unreviewed,
                "last_unreviewed_trade_at": last_at,
            }
    except Exception:
        logger.warning("could not read the analyst outage state from the journal")
        return {
            "active": False,
            "started_at": None,
            "unreviewed_trades": 0,
            "last_unreviewed_trade_at": None,
        }


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
        # Analyst outage state. Seeded from the last cycle's health snapshot, so
        # a worker restarted in the middle of an outage picks the streak back up
        # instead of spending three more cycles rediscovering it. The analyst is
        # still asked on the very first cycle after a restart, so an outage that
        # has actually ended ends immediately either way.
        health = config.read_health()
        self._analyst_fail_streak = max(0, int(health.get("analyst_fail_streak") or 0))
        self._outage_active = bool(health.get("analyst_outage"))
        self._outage_started_at = health.get("analyst_outage_started_at") or None
        self._unreviewed_trades = max(0, int(health.get("unreviewed_trades_this_outage") or 0))

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

    def _skip(self, reason: str, cycle_no: int, symbols: list[str], **extra) -> dict:
        """Record a refused cycle: no order, and one row unless it is a repeat.

        The row is throttled per reason, so a refusal that repeats every cycle
        fills the journal once an hour instead of once a minute. The console line
        is printed every cycle either way: the log is cheap and local, the
        journal is the record someone reads later.
        """
        written = _journal_skip(
            reason, {"cycle": cycle_no, "symbols": symbols, **extra}
        )
        self._log(f"cycle {cycle_no}: planner refused to trade ({reason})")
        if not written:
            self._log(
                f"cycle {cycle_no}: same refusal already on record, not journalled again "
                f"({reason})"
            )
        return {
            "verdict": VERDICT_REFUSED,
            "refusal_reason": reason,
            "refusal_logged": written,
        }

    def _rules_only_this_cycle(self) -> bool:
        """Whether this cycle's plan may be built without the analyst.

        The analyst is asked for first on every cycle regardless of this answer:
        ``planner.plan`` always consults it, and only the *outcome* may be a
        rules-only plan. So a healthy analyst still vetoes, and a recovered one
        puts the very next cycle back on reviewed trading.

        Two ways in. An outage already in progress stays open until the analyst
        answers. Otherwise this cycle is the one that would reach the limit, so
        the fallback is permitted now rather than the cycle refusing and then
        trading on the next tick -- which would mean asking the planner twice in
        one cycle and burning the quota we are already out of.
        """
        if self._outage_active:
            return True
        return self._analyst_fail_streak + 1 >= self.cfg.analyst_outage_cycles

    def _note_analyst_outcome(self, available: bool, cycle_no: int) -> None:
        """Update the outage from what this cycle's plan says about the analyst.

        Each transition is journalled exactly once, on the cycle it happens. An
        outage that lasts a hundred cycles therefore costs two rows, and a
        thousand failed ones are refused and throttled rather than narrated.

        A cycle refused for some other reason -- no setup, stale candles -- says
        nothing about the analyst, so it neither extends nor clears the streak:
        the count is of cycles in which the analyst did not answer, not of
        consecutive refusals.
        """
        if available:
            self._analyst_fail_streak = 0
            if self._outage_active:
                self._outage_active = False
                db.log(
                    KIND_OUTAGE_ENDED,
                    {
                        "cycle": cycle_no,
                        "outage_started_at": self._outage_started_at,
                        "outage_seconds": round(time.time() - float(self._outage_started_at or 0), 3),
                        "unreviewed_trades": self._unreviewed_trades,
                        "detail": (
                            "the analyst answered again, so reviewed trading has resumed by "
                            "itself: no restart and no manual step were needed"
                        ),
                    },
                )
                self._log(
                    f"cycle {cycle_no}: the analyst answered, so trading is reviewed again "
                    f"({self._unreviewed_trades} unreviewed trade(s) during the outage)"
                )
            return

        self._analyst_fail_streak += 1
        limit = self.cfg.analyst_outage_cycles
        if not self._outage_active and self._analyst_fail_streak >= limit:
            self._outage_active = True
            self._outage_started_at = time.time()
            self._unreviewed_trades = 0
            db.log(
                KIND_OUTAGE_STARTED,
                {
                    "cycle": cycle_no,
                    "outage_cycles": limit,
                    "failed_cycles": self._analyst_fail_streak,
                    "detail": (
                        f"the analyst did not answer for {self._analyst_fail_streak} cycles "
                        "running, so this cycle trades on rules alone; every trade it places "
                        "is recorded as unreviewed, and it is tried again next cycle"
                    ),
                },
            )
            self._log(
                f"cycle {cycle_no}: the analyst has not answered for "
                f"{self._analyst_fail_streak} cycles, so trading continues unreviewed "
                "(every trade is stamped as unreviewed; it is tried again each cycle)"
            )

    def _execute_once(
        self,
        planner_module,
        plan: dict,
        cycle_no: int,
        symbols: list[str],
        *,
        unreviewed: bool = False,
    ) -> dict:
        """The one order this cycle may place.

        Every order the worker places comes through here, and the count is per
        cycle. A second attempt in the same cycle is refused here, in the
        journal and in the log, without reaching ``planner.execute`` -- so a
        cycle cannot place two orders even if a later edit asks it to try again.
        The next cycle starts with the allowance back.

        Unreviewed mode changes nothing here. An outage trade is stamped and
        journalled in addition to the ordinary record; it spends the cycle's one
        allowance exactly like a reviewed one, and a halted worker never reaches
        this method at all.
        """
        if self._cycle_trades >= MAX_TRADES_PER_CYCLE:
            self._log(
                f"cycle {cycle_no}: refused, this cycle already placed its one trade "
                "and will not place another"
            )
            _journal_skip(
                TOO_MANY_TRADES,
                {
                    "cycle": cycle_no,
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
            if unreviewed:
                self._unreviewed_trades += 1
                db.log(
                    KIND_UNREVIEWED_TRADE,
                    {
                        "cycle": cycle_no,
                        "symbol": result.get("symbol"),
                        "side": result.get("side"),
                        "qty": result.get("qty"),
                        "price": result.get("price"),
                        "strategy_id": result.get("strategy_id"),
                        "analyst_available": False,
                        "analyst_bypassed": True,
                        "outage_started_at": self._outage_started_at,
                        "unreviewed_in_this_outage": self._unreviewed_trades,
                        "detail": UNREVIEWED_NOTE,
                    },
                )
            self._log(
                f"cycle {cycle_no}: placed 1 "
                f"{'UNREVIEWED ' if unreviewed else ''}trade: {result.get('side')} "
                f"{result.get('symbol')} qty {result.get('qty')} @ {result.get('price')}"
            )
        return result

    def _ask_planner(
        self, planner_module, symbols: list[str], interval: str, max_risk: float, rules_only: bool
    ):
        """One planner call for this cycle.

        Returns the plan the planner produced, or a refusal reason as a string
        when the call itself failed. A string is not something ``plan`` can
        return, so the two are never confused.

        ``allow_rules_only`` is passed only when this cycle may fall back. The
        planner asks the analyst either way, so this never decides to skip the
        analyst -- it only decides whether an unanswered analyst ends the cycle
        or is an allowed outcome.
        """
        kwargs = {"symbols": symbols, "max_risk": max_risk, "interval": interval}
        if rules_only:
            kwargs["allow_rules_only"] = True
        try:
            return planner_module.plan(**kwargs)
        except TypeError:
            # Tolerate a planner that does not take the optional keywords yet.
            try:
                return planner_module.plan(symbols=symbols)
            except Exception as exc:
                logger.exception("paper planner failed on cycle %s", self.cycle)
                return f"{PLANNER_ERROR}: {type(exc).__name__}"
        except Exception as exc:
            logger.exception("paper planner failed on cycle %s", self.cycle)
            return f"{PLANNER_ERROR}: {type(exc).__name__}: {exc}"

    def _run_planner_cycle(
        self, symbols: list[str], interval: str, max_risk: float, cycle_no: int
    ) -> dict:
        """Ask the planner for a plan and trade it only if it is clean.

        The planner is called exactly once here, in every outcome: a refusal is
        final for this cycle, so one tick cannot produce two jobs and a quota
        that is already spent is not hammered twice for the price of one answer.
        """
        planner_module = _load_planner()
        if planner_module is None:
            # No planner means no risk rules at all, which is a different fault
            # from a silent analyst and is not something unreviewed trading
            # forgives: there would be nothing checking size, bracket or cash.
            return self._skip(PLANNER_UNAVAILABLE, cycle_no, symbols)

        rules_only = self._rules_only_this_cycle()
        plan = self._ask_planner(planner_module, symbols, interval, max_risk, rules_only)

        if isinstance(plan, str):
            # _ask_planner returns a reason string rather than raising, so one
            # bad cycle cannot cost the next one.
            return self._skip(plan, cycle_no, symbols)

        if not isinstance(plan, dict):
            return self._skip(f"{PLANNER_ERROR}: plan was not a mapping", cycle_no, symbols)

        refusal = plan.get("refusal_reason")
        analyst_available = bool(plan.get("analyst_available"))

        if refusal:
            if str(refusal) == PLANNER_ANALYST_UNAVAILABLE:
                # The planner is reporting the analyst's silence itself. That is
                # the same fact the ``analyst_available`` check below reads, and
                # it must be counted the same way.
                self._note_analyst_outcome(False, cycle_no)
            return self._skip(str(refusal), cycle_no, symbols, outage=self._outage_active)

        if not analyst_available:
            # A clean plan with no analyst opinion. Refused while the analyst is
            # healthy; in an outage this is the outcome the fallback exists for.
            self._note_analyst_outcome(False, cycle_no)
            if not rules_only or not self._outage_active:
                # Either the outage has not been declared yet, or this cycle's
                # plan was asked for without the fallback and came back without
                # an analyst. Both are refusals.
                return self._skip(PLANNER_ANALYST_UNAVAILABLE, cycle_no, symbols)
            unreviewed = True
        else:
            # The analyst answered, which is also what ends an outage in progress.
            self._note_analyst_outcome(True, cycle_no)
            unreviewed = False

        if unreviewed:
            # Stamped on the plan itself, so the order record and the journal row
            # the planner writes both carry it: no record can be read as reviewed.
            plan["analyst_available"] = False
            plan["analyst_bypassed"] = True
            plan["analyst_outage"] = True
            plan["analyst_outage_started_at"] = self._outage_started_at
            plan["unreviewed_reason"] = UNREVIEWED_NOTE

        try:
            result = self._execute_once(
                planner_module, plan, cycle_no, symbols, unreviewed=unreviewed
            )
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

        return {
            "verdict": VERDICT_EXECUTED,
            "refusal_reason": "",
            "execute": result,
            "analyst_available": analyst_available,
            "unreviewed": unreviewed,
            "outage": self._outage_active,
        }

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
                # Carried through a halted cycle unchanged, so a halted worker
                # cannot make an outage look like it ended while nothing ran.
                analyst_fail_streak=self._analyst_fail_streak,
                analyst_outage=self._outage_active,
                analyst_outage_started_at=self._outage_started_at,
                unreviewed_trades_this_outage=self._unreviewed_trades,
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
                "analyst_outage": self._outage_active,
                "unreviewed": False,
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
        refusal_logged = None
        exits: list[dict] = []
        analyst_available: bool | None = None
        unreviewed = False

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
            refusal_logged = outcome.get("refusal_logged")
            analyst_available = outcome.get("analyst_available")
            unreviewed = bool(outcome.get("unreviewed"))
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
            # The analyst's state as of this cycle, so ``--status`` in another
            # shell can answer "is it answering, and since when" from the
            # database rather than from a process that may not be running.
            analyst_available=analyst_available,
            analyst_fail_streak=self._analyst_fail_streak,
            analyst_outage=self._outage_active,
            analyst_outage_started_at=self._outage_started_at,
            analyst_outage_cycles=self.cfg.analyst_outage_cycles,
            unreviewed_trades_this_outage=self._unreviewed_trades,
            last_cycle_unreviewed=unreviewed,
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
            f"planner={verdict or 'none'}"
            f"{', UNREVIEWED' if unreviewed else ''}) {','.join(watchlist)}"
        )
        return {
            "status": status,
            "cycle": cycle_no,
            "planner_verdict": verdict or "none",
            "refusal_reason": refusal_reason,
            # False when the refusal was already on record inside the throttle
            # window, so a reader can tell "not journalled" from "not refused".
            "refusal_logged": refusal_logged,
            "symbols_scanned": list(watchlist),
            "max_risk": max_risk,
            # Counted, not inferred: 0 or 1, never more, for this cycle.
            "trades_placed": self._cycle_trades,
            "analyst_available": analyst_available,
            "analyst_outage": self._outage_active,
            "unreviewed": unreviewed,
            "unreviewed_trades_this_outage": self._unreviewed_trades,
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
    outage = analyst_outage_state()
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
        # Whether the analyst answered on the last cycle, whether trading has
        # fallen back to unreviewed, since when, and how much has traded that
        # way. Taken from the journal rather than from the worker's memory, so a
        # status printed while no worker is running is still true.
        "analyst_available": health.get("analyst_available"),
        "analyst_fail_streak": health.get("analyst_fail_streak", 0),
        "analyst_outage": outage["active"],
        "analyst_outage_started_at": outage["started_at"],
        "analyst_outage_cycles": cfg.analyst_outage_cycles,
        "unreviewed_trades_this_outage": outage["unreviewed_trades"],
    }


def _fmt_ts(value) -> str:
    if not value:
        return "never"
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(float(value)))


def _fmt_ago(value) -> str:
    """How long ago something happened, in words a trader reads at a glance."""
    if not value:
        return "never"
    try:
        seconds = max(0, int(time.time() - float(value)))
    except (TypeError, ValueError):
        return "never"
    if seconds < 90:
        return "moments ago"
    if seconds < 5400:
        return f"{seconds // 60} minutes ago"
    if seconds < 172800:
        return f"{seconds // 3600} hours ago"
    return f"{seconds // 86400} days ago"


def _analyst_lines(state: dict) -> list[str]:
    """Two or three lines saying whether anything is reviewing these trades.

    Plain words on purpose. The reader is looking at a status screen wondering
    why nothing is trading, and the answer has to survive being read by someone
    who does not know what a planner is.
    """
    available = state.get("analyst_available")
    streak = int(state.get("analyst_fail_streak") or 0)
    limit = state.get("analyst_outage_cycles") or config.DEFAULT_ANALYST_OUTAGE_CYCLES
    if state.get("analyst_outage"):
        since = state.get("analyst_outage_started_at")
        return [
            "  AI reviewer      : NOT ANSWERING - it is being asked every cycle and is not replying",
            "  trading mode     : UNREVIEWED - rules only, and it is checked again every cycle",
            f"  unreviewed since : {_fmt_ts(since)} ({_fmt_ago(since)})",
            f"  unreviewed trades: {state.get('unreviewed_trades_this_outage', 0)} placed with no AI review",
            "  what to do       : wait - the next cycle tries the reviewer again and goes back to",
            "                     reviewed trading by itself as soon as it answers. Nothing to restart.",
        ]
    if available:
        return [
            "  AI reviewer      : answering - it reviews each trade before it is placed",
            "  trading mode     : reviewed - every trade has an AI opinion behind it",
        ]
    if streak:
        remaining = max(0, int(limit) - streak)
        return [
            f"  AI reviewer      : not answering - {streak} cycle(s) in a row so far",
            "  trading mode     : still waiting for it; nothing is placed without a review",
            f"  gives up waiting : after {limit} cycles in a row, and then trades on rules alone",
            f"  cycles to wait   : {remaining} more",
        ]
    return [
        "  AI reviewer      : no answer yet this run",
        "  trading mode     : waiting for its first opinion before it places anything",
    ]


def format_status(state: dict) -> str:
    hb = state["heartbeat"] or {}
    health = state["health"]
    lines = [
        "Paper worker status",
        f"  kill switch      : {'HALTED (not trading)' if state['halted'] else 'running'}",
        *_analyst_lines(state),
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
