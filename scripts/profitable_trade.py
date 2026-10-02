#!/usr/bin/env python3
"""One reasoned paper trade, planned once, placed once, then it stops.

This script places AT MOST ONE trade per invocation, and that is a property of
the code rather than of how carefully it is read:

* :class:`OneTradeGuard` is the only way a plan is built and the only way an
  order is placed. It counts both. A second call to either is refused by
  :class:`TooManyTrades` before ``planner.execute`` is ever reached, so a second
  order cannot exist even if a future edit adds a retry, a fallback or a second
  symbol above this call;
* there is no loop over the watchlist here. The whole watchlist goes into a
  single ``planner.plan`` call, which returns at most one plan -- the best
  candidate, or a refusal. When nothing qualifies the refusal is reported and
  the script stops. It never tries the next symbol;
* a second invocation is a new, independent single trade. Nothing carries over
  between runs, so running it twice places two trades, one per run, and never a
  continuation of the first.

Without ``--yes`` this prints the plan and places nothing. With ``--yes`` it
executes that one plan and reports the order. Every number it prints comes from
``services.paper.planner``: nothing here computes a price, a size or an
outcome.

This is PAPER TRADING -- virtual money in the paper database (``PAPER_DB``,
default ``data/paper.db``). No broker, no real funds, no guarantee of profit.

    uv run python scripts/profitable_trade.py --symbol BTCUSDT --max-risk 25
    uv run python scripts/profitable_trade.py --symbol BTCUSDT --max-risk 25 --json
    uv run python scripts/profitable_trade.py --symbol BTCUSDT --max-risk 25 --yes
    uv run python scripts/profitable_trade.py --symbol BTCUSDT --yes --plain

``--plain`` writes the same single trade in plain words, for someone who is not
reading code. ``./paper`` uses it.

A position the planner opened is closed by name, on purpose:

    uv run python scripts/profitable_trade.py --close BTCUSDT
    uv run python scripts/profitable_trade.py --close BTCUSDT --json

``--close`` goes through ``planner.close_position``, which closes the whole
position at the live mark and journals it. It is a single close, not a loop over
positions. Stops, targets and the max-hold limit are enforced automatically by
the worker; this is the deliberate exit. The kill switch applies to it as it
does to everything else, so while trading is halted no exit is placed either.

Exit code 0 when a plan was produced, 1 when the planner refused.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.paper import config, db, planner  # noqa: E402

# One invocation, one trade. Not a default: the guard below enforces it.
MAX_TRADES_PER_RUN = 1


class TooManyTrades(RuntimeError):
    """Refused: this run was asked for a second plan or a second order."""


class OneTradeGuard:
    """The only way this script plans, and the only way it places an order.

    Every plan goes through :meth:`plan` and every order through :meth:`execute`.
    Each counts its own calls and refuses the second one with
    :class:`TooManyTrades`, before the planner is reached. That is what makes
    "at most one trade per invocation" true in the code and not merely in the
    control flow as it happens to be written today.

    One guard belongs to one invocation. :func:`main` builds a fresh guard per
    run, so a second run is a new single trade rather than a continuation that
    has already spent its allowance.
    """

    def __init__(self, max_trades: int = MAX_TRADES_PER_RUN) -> None:
        self.max_trades = max_trades
        self.plan_calls = 0
        self.execute_calls = 0
        self.placed: list[str] = []

    def plan(self, **kwargs) -> dict:
        """Build the single plan for this run, or refuse a second one."""
        if self.plan_calls >= 1:
            raise TooManyTrades(
                "this command builds one plan and then stops. It does not go "
                "looking for a second coin."
            )
        self.plan_calls += 1
        return planner.plan(**kwargs)

    def execute(self, plan: dict) -> dict:
        """Place the single order for this run, or refuse a second one."""
        if self.execute_calls >= self.max_trades:
            raise TooManyTrades(
                "this command places one trade at most. It has already had its "
                "one attempt, so no second order was sent."
            )
        self.execute_calls += 1
        result = planner.execute(plan)
        if isinstance(result, dict) and result.get("executed"):
            self.placed.append(str(result.get("symbol", "")))
        return result


def attempt(
    guard: OneTradeGuard,
    *,
    symbols: list[str],
    armed: bool,
    max_risk: float,
    allow_rules_only: bool,
) -> tuple[dict, dict | None]:
    """One plan attempt and, when armed, one execution attempt. Nothing else.

    The watchlist is handed to the planner as a single list, so one call returns
    at most one plan. A refusal is final for this run: it is reported as it
    stands, with no second symbol tried after it.
    """
    plan = guard.plan(
        symbols=symbols,
        mode="execute" if armed else "scan",
        max_risk=max_risk,
        allow_rules_only=allow_rules_only,
    )
    result = guard.execute(plan) if armed else None
    return plan, result


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
        "--plain",
        action="store_true",
        help="print the one trade in plain words, for someone who is not reading code",
    )
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


# A refusal code is an internal word. These are the sentences a trader reads.
PLAIN_REFUSALS = {
    "no_edge": "nothing on the list was worth trading right now",
    "no_setup": "no coin on the list had a signal right now",
    "no_validated_strategy": "no strategy on the list has a good enough track record",
    "strategy_failed_validation": "no strategy on the list has a good enough track record",
    "analyst_unavailable": "the AI reviewer could not be reached",
    "analyst_p_take_below_threshold": "the AI reviewer did not think this one was worth taking",
    "insufficient_cash": "there is not enough virtual cash left to open it",
    "exposure_cap": "as much of the virtual money is already in use as this account allows",
    "daily_loss_limit": "today's losses have reached the limit set for the day",
    "symbol_already_open": "a trade in that coin is already open",
    "stale_data": "the price data was too old to trust",
    "kill_switch": "trading has been stopped",
    "planner_unavailable": "the trade planner could not be reached",
    "plan_already_refused": "the plan was already refused",
    "bad_request": "the plan was not usable",
    "execution_error": "the paper order could not be completed",
}


def _plain_reason(reason, detail: str = "") -> str:
    """An internal refusal code, said out loud, with the planner's own detail.

    The detail is dropped when it merely repeats the code, so a reader is never
    shown an internal word they cannot act on.
    """
    words = PLAIN_REFUSALS.get(str(reason), str(reason).replace("_", " "))
    detail = str(detail or "").strip()
    if detail and detail != str(reason) and detail.lower() not in words.lower():
        return f"{words}. {detail}"
    return words


def _fmt_plain(plan: dict, result: dict | None) -> str:
    """The one trade, in the words a trader would use. No field names, no jargon."""
    if result is None and not plan.get("refusal_reason"):
        return "\n".join(
            [
                "One trade was worked out and nothing was sent.",
                f"  {_plain_reason('no_edge')}.",
                "  Add --yes and this command places it instead.",
                "  Either way it places one trade at most, then stops.",
            ]
        )

    refusal = plan.get("refusal_reason") or (
        result or {}
    ).get("refusal_reason")
    if refusal or not (result or {}).get("executed"):
        reason = refusal or "no_edge"
        detail = plan.get("refusal_detail") or (result or {}).get("detail", "")
        return "\n".join(
            [
                "No trade was made. Nothing here is real money.",
                f"  Why: {_plain_reason(reason, detail)}",
                "  That is the whole answer. This command looks once and stops;",
                "  it does not try the next coin.",
                "",
                "  Dashboard: http://127.0.0.1:5001/paper",
            ]
        )

    verb = "bought" if result["side"] == "BUY" else "sold"
    lines = [
        "One paper trade placed. This is virtual money - nothing here is real.",
        "",
        f"  You {verb} {result['qty']:.6f} {result['symbol']} at {result['price']:.4f}.",
        f"  If the price falls to {result['stop_loss']:.4f} it closes and you lose about "
        f"{plan.get('risk_usd', 0.0):.2f} USD.",
        f"  If the price rises to {result['take_profit']:.4f} it closes and you gain about "
        f"{plan.get('reward_usd', 0.0):.2f} USD.",
        f"  Virtual cash left: {result['cash']:,.2f} USD.",
    ]
    if plan.get("analyst_bypassed") or not plan.get("analyst_available"):
        lines.append("  No AI reviewed this trade. It rests on the tested rules alone.")
    else:
        lines.append("  An AI reviewed this trade before it was placed.")
    lines += [
        "",
        "  That is the one trade this command may make, and it is finished.",
        "  Run ./paper again when you want another.",
        "",
        "  Dashboard: http://127.0.0.1:5001/paper",
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

    # One guard per invocation, and every plan and every order goes through it.
    guard = OneTradeGuard()
    try:
        plan, result = attempt(
            guard,
            symbols=symbols,
            armed=args.yes,
            max_risk=max_risk,
            allow_rules_only=args.allow_rules_only,
        )
    except TooManyTrades as exc:
        # Unreachable while the only caller is the block above. That is the
        # point of the guard: if a future edit ever asks for a second plan or a
        # second order, the refusal happens here, before any order is sent.
        print(f"\n  No trade was placed. {exc}\n", file=sys.stderr)
        return 1

    if result is not None:
        plan = dict(plan)
        plan["execution"] = result

    if args.json:
        print(json.dumps({"plan": plan, "execution": result}, indent=2, default=str))
    elif args.plain:
        print(_fmt_plain(plan, result))
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
