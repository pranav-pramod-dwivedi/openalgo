#!/usr/bin/env python3
"""Unreviewed paper trading: the same cycle as the worker, with the analyst OFF.

This runner exists for one situation: the Jev analyst is unavailable or its
free tier is rate-limited, and the operator still wants the paper account
walking instead of idling. It reproduces the worker's research -> plan -> risk ->
paper-execute cycle and changes exactly one thing: **no analyst reviews any
trade it places**.

The analyst is disabled, not merely optional
---------------------------------------------
``services.paper.planner`` always asks Jev for a verdict, and only falls back to
rules-only when the caller passes ``allow_rules_only=True``. This module does
both:

* every cycle calls ``planner.plan(..., allow_rules_only=True)``, so an
  unanswered analyst is a permitted outcome rather than a refusal; and
* every plan call is made inside ``analyst_disabled()``, which swaps the
  planner's analyst hook (``planner._ask_analyst``) for a stub returning
  ``None`` without any network call, and restores it afterwards. So no code path
  in this runner can reach the analyst even by accident, and nothing outside the
  plan call is left changed.

**This module never imports ``services.paper.jev``, never calls ``jev.ask``,
and takes no decision from it.** ``services.paper.engine.trading_cycle`` is
never called either, because that path asks the analyst directly. Every order
placed here is sized, risk-checked and bracketed by rules alone, and every one
of them is stamped ``analyst_bypassed: true`` and journalled as a
``rules_only_execution`` decision carrying the whole plan and its reasoning, so
the record says plainly that nothing reviewed the trade.

What is still enforced
----------------------
The planner's validation bar (backtest trades, net P&L, drawdown), the risk
rules (cash, exposure cap, daily loss limit, position size, risk budget), the
kill switch and the data-freshness check all still apply. A rules-only runner
is a *less* careful trader, not an unchecked one.

Arming
------
This runner is autonomous by definition, so it cannot ask before each trade. It
is instead prevented from being *started* by accident:

* ``--autonomous`` confirms this one run. Nothing is persisted.
* ``--arm`` persists arming in the paper database, so later runs do not need
  the flag. ``--disarm`` clears it.

``--status``, ``--halt`` and ``--resume`` never trade and never need arming.

Modes:

    --once               run exactly one cycle and exit
    --halt               set the persistent kill switch: stop trading
    --resume             clear the kill switch
    --status             print cash, equity, positions and the rules-only tally
    --symbols A,B        set the watchlist (persisted)
    --interval 5m        set the candle timeframe (persisted)
    --max-risk 5         risk budget in USD a plan may commit (persisted)
    --interval-seconds N seconds between cycles, default 300
    --arm / --disarm     persist or clear arming
    --quiet              suppress per-cycle logging

No live orders: every fill is simulated and stays in this database
(``PAPER_DB``, default ``data/paper.db``). See PAPER_TRADING.md.
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.paper import config, db, engine, planner  # noqa: E402
from utils.logging import get_logger  # noqa: E402

logger = get_logger(__name__)

# Journal kinds. ``rules_only_execution`` is the point of this runner: a row
# that says, in the database, that an order went out with nothing reviewing it.
KIND_EXECUTION = "rules_only_execution"
KIND_REFUSED = "rules_only_refused"
KIND_HALTED = "rules_only_halted"
KIND_UNARMED = "rules_only_unarmed"

# Persisted arming key. Separate from the worker so arming the unreviewed
# runner never arms the worker and the other way round.
KEY_ARMED = "rules_only_armed"

DEFAULT_INTERVAL_SECONDS = 300
SLEEP_SLICE = 1.0
MAX_ERROR_LENGTH = 500

VERDICT_EXECUTED = "executed"
VERDICT_REFUSED = "refused"
VERDICT_NOT_TRADED = "not_traded"

ANALYST_OFF_REASON = (
    "the analyst is switched off for this run, so no model reviewed this trade"
)

_banner_shown = False
_analyst_call_attempts = 0


# --------------------------------------------------------------------- analyst


def _disabled_analyst(state: str) -> None:
    """Stand in for the planner's analyst hook. Answers nothing, always."""
    global _analyst_call_attempts
    _analyst_call_attempts += 1
    logger.warning(
        "rules-only runner: the analyst hook was reached and refused to answer, "
        "so this plan carries no model opinion (state withheld: %d chars)",
        len(state or ""),
    )
    return None


def disable_analyst() -> None:
    """Make it impossible for this run to reach ``services.paper.jev``.

    Idempotent. The planner itself is untouched on disk; only this process's
    view of its analyst hook is replaced. :func:`analyst_disabled` is the scoped
    form, and is what a cycle uses, so nothing outside the plan call is
    affected -- including other tests sharing this interpreter.
    """
    planner._ask_analyst = _disabled_analyst  # noqa: SLF001


@contextmanager
def analyst_disabled():
    """Swap the planner's analyst hook out for the duration of one plan call.

    Restored on the way out, including on an exception, so a cycle cannot leave
    the planner muted for whoever runs next in this process.
    """
    had_original = hasattr(planner, "_ask_analyst")
    original = getattr(planner, "_ask_analyst", None)
    planner._ask_analyst = _disabled_analyst  # noqa: SLF001
    try:
        yield
    finally:
        if had_original:
            planner._ask_analyst = original  # noqa: SLF001
        else:
            delattr(planner, "_ask_analyst")


def analyst_is_disabled() -> bool:
    """Whether the analyst hook in this process can still answer."""
    return getattr(planner, "_ask_analyst", None) is _disabled_analyst


# ----------------------------------------------------------------------- banner


def banner() -> str:
    """Print the startup warning once per process. Returns what it printed."""
    global _banner_shown
    lines = [
        "=" * 78,
        "  PAPER RUN - THE ANALYST IS OFF",
        "=" * 78,
        "  No analyst is running. The Jev model is not consulted, not called,",
        "  and cannot veto anything. Every trade below is placed on rules alone.",
        "  Nothing reviewed these trades.",
        "",
        "  Every plan is stamped analyst_bypassed=true and journalled as a",
        f"  {KIND_EXECUTION} decision carrying the full plan and its reasoning.",
        "",
        "  THIS IS PAPER MONEY. Simulated fills in the paper database only.",
        "  No broker is contacted and no real order can be placed.",
        "=" * 78,
    ]
    text = "\n".join(lines)
    if not _banner_shown:
        print(text)
        _banner_shown = True
    return text


# ---------------------------------------------------------------------- arming


def is_armed() -> bool:
    """Whether arming is persisted in the paper database."""
    try:
        return bool(db.get(KEY_ARMED, False))
    except Exception:
        return False


def set_armed(value: bool) -> bool:
    """Persist arming so a later run does not need the flag. Returns the value."""
    db.setc(KEY_ARMED, bool(value))
    return bool(value)


# ----------------------------------------------------------------------- cycle


class RulesOnlyRunner:
    """Research -> plan (rules only) -> paper execute, on a fixed cadence."""

    def __init__(
        self,
        *,
        interval_seconds: int = DEFAULT_INTERVAL_SECONDS,
        max_risk: float | None = None,
        armed: bool = False,
        verbose: bool = True,
    ) -> None:
        self._lock = threading.Lock()
        self._verbose = verbose
        self.interval_seconds = max(1, int(interval_seconds))
        self.max_risk = max_risk
        self.armed = bool(armed) or is_armed()
        self.cycle = 0
        self.trades_taken = 0
        self.plans_refused = 0
        self.last_result: dict = {}
        self.last_execution: dict = {}
        self.last_refusal_reason = ""

    # ---------------------------------------------------------------- helpers

    def _log(self, message: str) -> None:
        if self._verbose:
            print(f"[paper_run] {message}")

    def _heartbeat(self, status: str, error: str = "", **fields) -> None:
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

    def _equity_upkeep(self) -> dict:
        """Mark open positions and append an equity row. No analyst involved."""
        try:
            return engine._record_equity()  # noqa: SLF001
        except Exception as exc:
            logger.warning("rules-only equity snapshot failed: %s", exc)
            return {}

    # ------------------------------------------------------------------ steps

    def _trade_plan(self, watchlist: list[str], interval: str) -> dict:
        """Ask for exactly one plan and execute it exactly once, or refuse.

        The planner is called once here with ``allow_rules_only=True``, so an
        unanswered analyst is a permitted outcome instead of a refusal. There is
        no retry and no second call: one cycle can never place two orders.
        """
        with analyst_disabled():
            plan = planner.plan(
                symbols=watchlist,
                interval=interval,
                max_risk=self.max_risk,
                allow_rules_only=True,
            )
        if not isinstance(plan, dict):
            raise TypeError("planner.plan did not return a mapping")

        refusal = plan.get("refusal_reason")
        if refusal:
            reason = str(refusal)
            self.plans_refused += 1
            self.last_refusal_reason = reason
            db.log(
                KIND_REFUSED,
                {
                    "cycle": self.cycle,
                    "refusal_reason": reason,
                    "detail": plan.get("refusal_detail", ""),
                    "symbols": watchlist,
                    "analyst_bypassed": True,
                    "considered": plan.get("considered", []),
                },
            )
            self._log(f"cycle {self.cycle}: refused ({reason}), nothing traded")
            return {
                "verdict": VERDICT_REFUSED,
                "refusal_reason": reason,
                "detail": plan.get("refusal_detail", ""),
            }

        # Stamp the plan before it reaches execute(), so the order record and
        # the journal both carry it: nothing reviewed this trade.
        plan["analyst_bypassed"] = True
        plan["analyst_available"] = False
        plan["rules_only"] = True
        plan["analyst_note"] = ANALYST_OFF_REASON

        result = planner.execute(plan)
        self.last_execution = result if isinstance(result, dict) else {}

        # The full plan, reasoning included, in the journal. An operator reading
        # this row later must not have to guess whether anything reviewed it.
        db.log(
            KIND_EXECUTION,
            {
                "cycle": self.cycle,
                "analyst_bypassed": True,
                "analyst_available": False,
                "analyst_note": ANALYST_OFF_REASON,
                "plan": plan,
                "execution": result,
            },
        )

        if result.get("status") == "filled":
            self.trades_taken += 1
            self._log(
                f"cycle {self.cycle}: UNREVIEWED {result.get('side')} "
                f"{result.get('symbol')} qty {result.get('qty')} @ {result.get('price')} "
                "(analyst off, no model opinion)"
            )
            return {
                "verdict": VERDICT_EXECUTED,
                "refusal_reason": "",
                "execute": result,
            }

        reason = str(result.get("refusal_reason") or "execute_refused")
        self.plans_refused += 1
        self.last_refusal_reason = reason
        db.log(
            KIND_REFUSED,
            {
                "cycle": self.cycle,
                "refusal_reason": reason,
                "detail": result.get("detail", ""),
                "stage": "execute",
                "symbols": watchlist,
                "analyst_bypassed": True,
            },
        )
        self._log(f"cycle {self.cycle}: execution refused ({reason}), nothing traded")
        return {"verdict": VERDICT_REFUSED, "refusal_reason": reason, "detail": result.get("detail", "")}

    # ------------------------------------------------------------------ cycle

    def run_cycle(self) -> dict:
        """One cycle. Returns a summary dict; never raises."""
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
        db.init()

        cfg = config.load()
        watchlist = cfg.symbols
        interval = cfg.interval

        # The kill switch is read first, every cycle, before any work.
        if cfg.halted:
            db.log(KIND_HALTED, {"cycle": cycle_no, "reason": "kill_switch"})
            self._heartbeat(
                "halted",
                planner_verdict="halted",
                refusal_reason="kill_switch",
                symbols_scanned="",
                duration_s=0.0,
            )
            self._log(f"cycle {cycle_no}: halted, not trading (use --resume)")
            return {"status": "halted", "cycle": cycle_no, "verdict": VERDICT_NOT_TRADED}

        if not self.armed:
            db.log(KIND_UNARMED, {"cycle": cycle_no})
            self._heartbeat(
                "unarmed",
                planner_verdict="unarmed",
                refusal_reason="not_armed",
                symbols_scanned="",
                duration_s=0.0,
            )
            self._log(f"cycle {cycle_no}: not armed, nothing traded (use --arm or --autonomous)")
            return {"status": "unarmed", "cycle": cycle_no, "verdict": VERDICT_NOT_TRADED}

        verdict = ""
        refusal_reason = ""
        error = ""
        status = "ok"
        research_ran = False

        try:
            engine.research_cycle(watchlist, interval)
            research_ran = True
            self._equity_upkeep()
            outcome = self._trade_plan(watchlist, interval)
            verdict = outcome["verdict"]
            refusal_reason = outcome.get("refusal_reason", "")
            status = "refused" if verdict == VERDICT_REFUSED else status
        except Exception as exc:  # one bad cycle must not kill the loop
            status = "error"
            error = f"{type(exc).__name__}: {exc}"
            self._log(f"cycle {cycle_no} error: {error}")
            logger.exception("rules-only runner cycle %s failed", cycle_no)
            db.log("runner_error", {"cycle": cycle_no, "error": error})

        finished = time.time()
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
            interval=interval,
            planner_verdict=verdict or "none",
            last_refusal_reason=refusal_reason,
            symbols_scanned=",".join(watchlist),
            rules_only=True,
            analyst_bypassed=True,
            trades_taken_this_run=self.trades_taken,
            plans_refused_this_run=self.plans_refused,
            last_rules_only_cycle=cycle_no,
            max_risk_usd=self.max_risk if self.max_risk is not None else worker_max_risk(),
            last_cycle_started=started,
            last_cycle_seconds=round(finished - started, 3),
        )
        self._log(
            f"cycle {cycle_no}: {status} in {finished - started:.1f}s "
            f"(research={'yes' if research_ran else 'no'}, analyst=OFF, "
            f"planner={verdict or 'none'}) {','.join(watchlist)}"
        )
        summary = {
            "status": status,
            "cycle": cycle_no,
            "planner_verdict": verdict or "none",
            "refusal_reason": refusal_reason,
            "trades_taken": self.trades_taken,
            "plans_refused": self.plans_refused,
            "analyst_bypassed": True,
            "symbols_scanned": list(watchlist),
            "seconds": round(finished - started, 3),
            "error": error,
        }
        self.last_result = summary
        return summary

    # -------------------------------------------------------------------- loop

    def sleep_until_next(self) -> None:
        """Sleep in slices so a halt lands promptly instead of at cycle end."""
        deadline = time.time() + self.interval_seconds
        while True:
            remaining = deadline - time.time()
            if remaining <= 0:
                return
            time.sleep(min(SLEEP_SLICE, remaining))

    def run_forever(self) -> None:
        """The default mode. Returns only if asked to stop."""
        self._log(
            f"starting rules-only loop: every {self.interval_seconds}s, "
            f"watchlist {','.join(config.load().symbols)} @ {config.load().interval}, "
            "analyst OFF"
        )
        if config.is_halted():
            self._log("kill switch is set, idling without trading until --resume")
        if not self.armed:
            self._log("not armed, idling without trading until --arm or --autonomous")
        while True:
            self.run_cycle()
            self.sleep_until_next()


def worker_max_risk() -> float:
    """The persisted risk budget, without importing the worker's private state."""
    try:
        return float(db.get("max_risk_usd", 5.0))
    except (TypeError, ValueError):
        return 5.0


def run_once(runner: RulesOnlyRunner | None = None) -> dict:
    r = runner or RulesOnlyRunner()
    return r.run_cycle()


# ---------------------------------------------------------------------- status


def _count(c, kind: str) -> int:
    row = c.execute("SELECT COUNT(*) AS n FROM decisions WHERE kind=?", (kind,)).fetchone()
    return int(row["n"]) if row else 0


def _latest_refusal(c) -> tuple[str, str]:
    row = c.execute(
        "SELECT payload FROM decisions WHERE kind IN (?,?) ORDER BY id DESC LIMIT 1",
        (KIND_REFUSED, KIND_HALTED),
    ).fetchone()
    if row is None:
        return "", ""
    try:
        payload = json.loads(row["payload"])
    except (TypeError, ValueError):
        return "", ""
    if not isinstance(payload, dict):
        return "", ""
    return str(payload.get("refusal_reason", "")), str(payload.get("detail", ""))


def status() -> dict:
    """Read the rules-only runner's state straight from the paper database."""
    db.init()
    cfg = config.load()
    with db.conn() as c:
        open_positions = c.execute(
            "SELECT symbol,side,qty,entry FROM positions WHERE status='open'"
        ).fetchall()
        equity_row = c.execute("SELECT ts,equity FROM equity ORDER BY ts DESC LIMIT 1").fetchone()
        executed = _count(c, KIND_EXECUTION)
        refused = _count(c, KIND_REFUSED)
        halted_rows = _count(c, KIND_HALTED)
        reason, detail = _latest_refusal(c)
    raw_cfg = db.get_all()
    cash = float(raw_cfg.get("cash", raw_cfg.get("starting_cash", engine.DEFAULT_CASH)))
    equity = (
        float(equity_row["equity"])
        if equity_row
        else engine.equity_of(raw_cfg, 0.0)
    )
    health = config.read_health()
    return {
        "halted": cfg.halted,
        "armed": is_armed(),
        "analyst_bypassed": True,
        "cash": round(cash, 6),
        "equity": round(equity, 6),
        "open_positions": [dict(r) for r in open_positions],
        "open_position_count": len(open_positions),
        "trades_taken": executed,
        "plans_refused": refused,
        "halted_cycles": halted_rows,
        "last_refusal_reason": reason,
        "last_refusal_detail": detail,
        "symbols": cfg.symbols,
        "interval": cfg.interval,
        "max_risk": worker_max_risk(),
        "health": health,
        "trades_taken_this_run": health.get("trades_taken_this_run", 0),
        "plans_refused_this_run": health.get("plans_refused_this_run", 0),
    }


def format_status(state: dict) -> str:
    positions = state["open_positions"]
    lines = [
        "Paper rules-only runner status (analyst OFF, unreviewed trades)",
        f"  kill switch      : {'HALTED (not trading)' if state['halted'] else 'running'}",
        f"  armed            : {'yes' if state['armed'] else 'no'}",
        "  analyst          : OFF - nothing reviews these trades",
        f"  watchlist        : {','.join(state['symbols'])} @ {state['interval']}",
        f"  max risk         : {state['max_risk']} USD per plan",
        f"  cash             : {state['cash']:.2f} USD",
        f"  equity           : {state['equity']:.2f} USD",
        f"  open positions   : {state['open_position_count']}",
    ]
    for pos in positions:
        lines.append(
            f"    {pos['symbol']} {pos['side']} {pos['qty']} @ {pos['entry']}"
        )
    lines.extend(
        [
            f"  trades taken     : {state['trades_taken']} (journalled as {KIND_EXECUTION})",
            f"  this run         : {state['trades_taken_this_run']} traded, "
            f"{state['plans_refused_this_run']} refused",
            f"  plans refused    : {state['plans_refused']} (journalled as {KIND_REFUSED})",
            f"  halted cycles    : {state['halted_cycles']}",
            f"  last refusal     : {state['last_refusal_reason'] or 'none'}",
            f"  last detail      : {state['last_refusal_detail'] or 'none'}",
        ]
    )
    return "\n".join(lines)


# ------------------------------------------------------------------------- cli


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="paper_run",
        description=(
            "Unreviewed paper trading: the worker's cycle with the analyst off. "
            "Simulated only, never live."
        ),
    )
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--once", action="store_true", help="run one cycle and exit")
    mode.add_argument("--halt", action="store_true", help="set the kill switch (no trading)")
    mode.add_argument("--resume", action="store_true", help="clear the kill switch")
    mode.add_argument("--status", action="store_true", help="print status and exit")
    mode.add_argument("--arm", action="store_true", help="persist arming for later runs")
    mode.add_argument("--disarm", action="store_true", help="clear persisted arming")

    p.add_argument("--symbols", help="comma separated watchlist, e.g. BTCUSDT,SOLUSDT")
    p.add_argument(
        "--interval",
        choices=list(engine.ALLOWED_INTERVALS),
        help="candle timeframe, e.g. 1m,5m,15m,1h",
    )
    p.add_argument(
        "--max-risk",
        type=float,
        help="risk budget in USD a plan may commit per cycle (persisted)",
    )
    p.add_argument(
        "--interval-seconds",
        type=int,
        default=DEFAULT_INTERVAL_SECONDS,
        help=f"seconds between cycles (default {DEFAULT_INTERVAL_SECONDS})",
    )
    p.add_argument(
        "--autonomous",
        action="store_true",
        help=(
            "confirm this run may place unreviewed paper trades without asking "
            "again (not persisted; --arm is)"
        ),
    )
    p.add_argument("--quiet", action="store_true", help="suppress per-cycle logging")
    return p


def _report_cycle(result: dict, runner: RulesOnlyRunner) -> None:
    """Say what happened, honestly, without dressing a refusal up as a trade."""
    status_value = result.get("status")
    if status_value == "halted":
        print("[paper_run] kill switch is set: nothing was traded (use --resume)")
    elif status_value == "unarmed":
        print(
            "[paper_run] this runner is not armed, so nothing was traded. "
            "Pass --autonomous to confirm this run or --arm to persist it."
        )
    elif status_value == "skipped_busy":
        print("[paper_run] a cycle was still running, this tick did nothing")
    elif status_value == "error":
        print(f"[paper_run] the cycle failed and nothing was traded: {result.get('error')}")
    elif result.get("planner_verdict") == VERDICT_REFUSED:
        print(
            "[paper_run] the planner refused this cycle, so nothing was traded. "
            f"Reason: {result.get('refusal_reason') or 'unstated'}"
        )
    elif result.get("planner_verdict") == VERDICT_EXECUTED:
        execution = runner.last_execution or {}
        print(
            "[paper_run] an UNREVIEWED paper trade was placed: "
            f"{execution.get('side')} {execution.get('symbol')} qty {execution.get('qty')} "
            f"@ {execution.get('price')}. No analyst saw it."
        )
    print(f"[paper_run] this run: {runner.trades_taken} traded, {runner.plans_refused} refused")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    db.init()

    if args.symbols or args.interval:
        saved = config.persist(symbols=args.symbols, interval=args.interval)
        print(
            f"[paper_run] config saved: watchlist {saved.symbols_csv} @ {saved.interval}"
        )

    if args.max_risk is not None:
        amount = max(0.01, min(1_000_000.0, float(args.max_risk)))
        db.setc("max_risk_usd", amount)
        print(f"[paper_run] max risk saved: {amount} USD per plan")

    if args.arm:
        set_armed(True)
        print("[paper_run] armed: this runner may now place unreviewed paper trades")

    if args.disarm:
        set_armed(False)
        print("[paper_run] disarmed: the runner must be armed again before it trades")

    if args.status:
        print(format_status(status()))
        return 0

    if args.halt:
        config.set_halted(True)
        print("[paper_run] kill switch set: trading is halted until --resume")
        return 0

    if args.resume:
        config.set_halted(False)
        print("[paper_run] kill switch cleared: the runner may trade again once armed")
        return 0

    banner()

    armed = bool(args.autonomous) or is_armed()
    if not armed:
        print(
            "[paper_run] not armed, so nothing will be traded. This runner places "
            "trades no analyst reviewed, so it refuses to start unattended. Pass "
            "--autonomous to confirm this run, or --arm to persist that choice."
        )
        return 2

    runner = RulesOnlyRunner(
        interval_seconds=args.interval_seconds,
        max_risk=args.max_risk,
        armed=True,
        verbose=not args.quiet,
    )

    if args.once:
        result = runner.run_cycle()
        _report_cycle(result, runner)
        print(format_status(status()))
        return 1 if result.get("status") == "error" else 0

    runner.run_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
