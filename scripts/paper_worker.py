#!/usr/bin/env python3
"""Autonomous permanent paper-trading worker.

Loop: data -> research (backtest grid) -> register -> planner -> paper orders ->
fills -> positions -> equity snapshot -> heartbeat.

Every autonomous cycle goes through ``services.paper.planner``: the worker
asks for a plan and executes it only when the plan carries no refusal reason
and the analyst was available. A refused cycle records a ``worker_skipped``
decision and trades nothing.

State lives in the paper database (``PAPER_DB``, default ``data/paper.db``).

Modes (no flag = the autonomous loop):

    --once              run exactly one cycle and exit
    --halt              set the persistent kill switch: stop trading
    --resume            clear the kill switch
    --status            print worker health from the database
    --symbols A,B       set the watchlist (persisted)
    --interval 5m       set the candle timeframe (persisted)
    --monitor-seconds   monitor cadence, default 60
    --research-seconds  research cadence, default 300
    --max-risk 5        risk budget in USD the planner may commit (persisted)
    --close SYMBOL      close one named open position now, at the live mark

No live orders: every fill is simulated and stays in this database. See
PAPER_TRADING.md.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.paper import config, db, engine, planner, worker  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="paper_worker",
        description="Autonomous paper-trading worker. Simulated only, never live.",
    )
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--once", action="store_true", help="run one cycle and exit")
    mode.add_argument("--halt", action="store_true", help="set the kill switch (no trading)")
    mode.add_argument("--resume", action="store_true", help="clear the kill switch")
    mode.add_argument("--status", action="store_true", help="print worker health and exit")

    p.add_argument("--symbols", help="comma separated watchlist, e.g. BTCUSDT,SOLUSDT")
    p.add_argument(
        "--interval",
        choices=list(engine.ALLOWED_INTERVALS),
        help="candle timeframe, e.g. 1m,5m,15m,1h",
    )
    p.add_argument(
        "--monitor-seconds",
        type=int,
        help=f"monitor cadence in seconds (default {config.DEFAULT_MONITOR_SECONDS})",
    )
    p.add_argument(
        "--research-seconds",
        type=int,
        help=f"research cadence in seconds (default {config.DEFAULT_RESEARCH_SECONDS})",
    )
    p.add_argument(
        "--max-risk",
        type=float,
        help=(
            "risk budget in USD the planner may commit per cycle "
            f"(default {worker.DEFAULT_MAX_RISK_USD}, persisted)"
        ),
    )
    p.add_argument(
        "--close",
        metavar="SYMBOL",
        help="close one named open paper position now, in full, at the live mark",
    )
    p.add_argument("--quiet", action="store_true", help="suppress per-cycle logging")
    return p


def _close(symbol: str) -> int:
    """Close one named position on purpose and report it plainly.

    The same kill switch applies as everywhere else: halted means no orders, so
    this refuses and says the position is still open rather than pretending.
    """
    record = planner.close_position(symbol)
    if record.get("executed"):
        print(
            f"[paper_worker] closed {record['symbol']} {record['side']} {record['qty']} "
            f"at {record['exit_price']:.4f} (entry {record['entry']:.4f}), "
            f"net {record['realized_pnl']:+.4f} USD, cash now {record['cash']:.2f} USD."
        )
        return 0
    if record.get("refusal_reason") == planner.HALTED:
        print(f"[paper_worker] not closed: {record.get('detail')}")
        return 1
    print(
        f"[paper_worker] not closed: {record.get('refusal_reason')} -- "
        f"{record.get('detail', '')}"
    )
    return 1


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    db.init()

    if args.symbols or args.interval or args.monitor_seconds or args.research_seconds:
        saved = config.persist(
            symbols=args.symbols,
            interval=args.interval,
            monitor_seconds=args.monitor_seconds,
            research_seconds=args.research_seconds,
        )
        print(
            f"[paper_worker] config saved: watchlist {saved.symbols_csv} @ {saved.interval}, "
            f"monitor {saved.monitor_seconds}s, research {saved.research_seconds}s"
        )

    if args.max_risk is not None:
        print(f"[paper_worker] max risk saved: {worker.set_max_risk(args.max_risk)} USD per plan")

    if args.status:
        print(worker.format_status(worker.status()))
        return 0

    if args.close:
        return _close(args.close)

    if args.halt:
        config.set_halted(True)
        print("[paper_worker] kill switch set: trading is halted until --resume")
        return 0

    if args.resume:
        config.set_halted(False)
        print("[paper_worker] kill switch cleared: the worker may trade again")
        return 0

    w = worker.PaperWorker(verbose=not args.quiet)
    if args.once:
        result = w.run_cycle()
        if result.get("status") == "halted":
            print("[paper_worker] kill switch is set: cycle refused to trade (use --resume)")
        elif result.get("status") == "skipped_busy":
            print("[paper_worker] a cycle was still running, this tick did nothing")
        elif result.get("planner_verdict") == worker.VERDICT_REFUSED:
            print(
                "[paper_worker] the planner refused this cycle, so nothing was traded. "
                f"Reason: {result.get('refusal_reason')}"
            )
        print(worker.format_status(worker.status()))
        return 0 if result.get("status") != "error" else 1

    w.run_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
