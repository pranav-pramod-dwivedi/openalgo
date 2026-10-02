#!/usr/bin/env python3
"""One reasoned paper trade, planned first and placed only when armed.

Without ``--yes`` this prints the plan and places nothing. With ``--yes`` it
executes the same plan and reports the order. Every number it prints comes from
``services.paper.planner``: nothing here computes a price, a size or an
outcome.

This is PAPER TRADING -- virtual money in the paper database (``PAPER_DB``,
default ``data/paper.db``). No broker, no real funds, no guarantee of profit.

    uv run python scripts/profitable_trade.py --symbol BTCUSDT --max-risk 25
    uv run python scripts/profitable_trade.py --symbol BTCUSDT --max-risk 25 --json
    uv run python scripts/profitable_trade.py --symbol BTCUSDT --max-risk 25 --yes

A position the planner opened is closed by name, on purpose:

    uv run python scripts/profitable_trade.py --close BTCUSDT
    uv run python scripts/profitable_trade.py --close BTCUSDT --json

``--close`` goes through ``planner.close_position``, which closes the whole
position at the live mark and journals it. Stops, targets and the max-hold limit
are enforced automatically by the worker; this is the deliberate exit. The kill
switch applies to it as it does to everything else, so while trading is halted no
exit is placed either.

Exit code 0 when a plan was produced, 1 when the planner refused.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.paper import config, db, planner  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="profitable_trade",
        description="Plan, and optionally place, one validated paper trade. Virtual money only.",
    )
    p.add_argument("--symbol", help="single symbol, e.g. BTCUSDT (default: the configured watchlist)")
    p.add_argument(
        "--yes",
        action="store_true",
        help="arm the plan: place the paper order instead of only printing it",
    )
    p.add_argument(
        "--max-risk",
        type=float,
        default=None,
        help=f"risk budget in USD the plan may commit (default {planner.DEFAULT_MAX_RISK:g})",
    )
    p.add_argument(
        "--close",
        metavar="SYMBOL",
        help="close one named open paper position now, in full, at the live mark",
    )
    p.add_argument("--json", action="store_true", help="print the plan as JSON")
    p.add_argument(
        "--allow-rules-only",
        action="store_true",
        help="permit a plan built without the analyst (off by default: such a plan is refused)",
    )
    return p


def _fmt(plan: dict) -> str:
    lines = ["Paper trade plan (virtual money, no broker)"]
    if plan.get("refusal_reason"):
        lines.append(f"  refused        : {plan['refusal_reason']}")
        if plan.get("refusal_detail"):
            lines.append(f"  why            : {plan['refusal_detail']}")
        lines.append(
            "  analyst        : "
            + (
                "available"
                if plan.get("analyst_available")
                else "not consulted (no setup reached the analyst)"
                if plan.get("refusal_reason") in ("no_setup", "insufficient_data")
                else "unavailable"
            )
        )
        lines.append("  no order was placed.")
        return "\n".join(lines)

    metrics = plan.get("metrics") or {}
    verdict = plan.get("jev_verdict") or {}
    lines += [
        f"  symbol         : {plan['symbol']}",
        f"  side           : {plan['side']}",
        f"  qty            : {plan['qty']:.6f}",
        f"  entry          : {plan['entry_price']:.4f}",
        f"  stop loss      : {plan['stop_loss']:.4f}",
        f"  take profit    : {plan['take_profit']:.4f}",
        f"  risk (USD)     : {plan['risk_usd']:.2f}",
        f"  reward (USD)   : {plan['reward_usd']:.2f}",
        f"  R:R            : {plan['rr']:.2f}",
        f"  strategy       : {plan['strategy_id']} ({plan.get('setup_family')}) "
        f"{json.dumps(plan.get('strategy_params') or {}, sort_keys=True)}",
        "  backtest       : "
        f"{metrics.get('trades', 0):g} trades, net {metrics.get('net_pnl', 0.0):+.2f} USD, "
        f"max drawdown {metrics.get('max_drawdown', 0.0):.2f} USD, fees {metrics.get('fees', 0.0):.2f} USD",
    ]
    if verdict.get("p_take") is not None:
        lines.append(
            f"  analyst        : p(take) {verdict['p_take']:.2f} against a "
            f"{plan.get('take_threshold'):.2f} gate, setup called {verdict.get('quality_label')}"
        )
    else:
        lines.append("  analyst        : did not answer")
    lines.append(f"  resting exits  : {'attached' if plan.get('brackets_attached') else 'not supported by the paper engine'}")
    lines.append(f"  reasoning      : {plan['reasoning']}")
    if plan.get("analyst_bypassed"):
        lines.append(
            "  WARNING        : the analyst was switched off for this plan. Nothing reviewed "
            "it. It rests on the backtested strategy alone, and the dashboard records it as "
            "unreviewed."
        )
    elif not plan.get("analyst_available"):
        lines.append("  note           : this plan carries no analyst opinion and will be refused on execution.")
    return "\n".join(lines)


def _fmt_close(record: dict) -> str:
    """The deliberate exit, written for a trader: what closed, at what price."""
    if not record.get("executed"):
        return "\n".join(
            [
                "Paper position close (virtual money, no broker)",
                f"  symbol         : {record.get('symbol', '-')}",
                f"  not closed     : {record.get('refusal_reason')}",
                f"  why            : {record.get('detail', '')}",
                "  no order was placed.",
            ]
        )
    lines = [
        "Paper position closed (virtual money, no broker)",
        f"  symbol         : {record['symbol']}",
        f"  position       : {record['side']} {record['qty']}",
        f"  entry          : {record['entry']:.4f}",
        f"  exit price     : {record['exit_price']:.4f}",
        f"  stop / target  : {record.get('stop_loss')} / {record.get('take_profit')}",
        f"  reason         : {record['reason']}",
        f"  p&l (net)      : {record['realized_pnl']:+.4f} USD "
        f"(gross {record['gross_pnl']:+.4f}, fee {record['fee']:.4f})",
        f"  strategy       : {record.get('strategy_id') or 'none recorded'}",
        f"  held           : {record['held_seconds'] / 60.0:.1f} min",
        f"  cash now       : {record['cash']:.2f} USD",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    db.init()

    if args.close:
        record = planner.close_position(args.close)
        if args.json:
            print(json.dumps({"close": record}, indent=2, default=str))
        else:
            print(_fmt_close(record))
        return 0 if record.get("executed") else 1

    symbols = [args.symbol.strip().upper()] if args.symbol else config.load().symbols
    max_risk = args.max_risk if args.max_risk is not None else planner.DEFAULT_MAX_RISK

    plan = planner.plan(
        symbols=symbols,
        mode="execute" if args.yes else "scan",
        max_risk=max_risk,
        allow_rules_only=args.allow_rules_only,
    )

    result = None
    if args.yes:
        result = planner.execute(plan)
        plan = dict(plan)
        plan["execution"] = result

    if args.json:
        print(json.dumps({"plan": plan, "execution": result}, indent=2, default=str))
    else:
        print(_fmt(plan))
        if result is not None:
            if result.get("executed"):
                print(
                    f"\nPaper order {result['order_id']} filled: {result['side']} "
                    f"{result['qty']:.6f} {result['symbol']} at {result['price']:.4f}, "
                    f"stop {result['stop_loss']:.4f}, target {result['take_profit']:.4f}, "
                    f"cash now {result['cash']:.2f} USD."
                )
                print(f"Reasoning: {result['reasoning']}")
                if not result.get("brackets_attached"):
                    print(f"Note: {result['bracket_note']}.")
            else:
                print(f"\nNot placed: {result.get('refusal_reason')} -- {result.get('detail', '')}")

    refused = bool(plan.get("refusal_reason")) or (
        result is not None and not result.get("executed")
    )
    return 1 if refused else 0


if __name__ == "__main__":
    raise SystemExit(main())
