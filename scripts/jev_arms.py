#!/usr/bin/env python3
"""Three-arm comparison: unfiltered vs Jev-gated vs random-gate control.

Read-only with respect to services/paper/: strategies, the backtest engine and
jev.ask are imported, never modified. Every Jev response is cached on disk so a
re-run makes zero API calls.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from services.paper import jev  # noqa: E402
from services.paper.backtest import backtest  # noqa: E402
from services.paper.strategies import FAMILIES, PARAM_GRID  # noqa: E402

CACHE_PATH = REPO_ROOT / "log" / "jev_cache.jsonl"

# Arm B accepts a signal when Jev scores "take" at or above this probability.
TAKE_THRESHOLD = 0.30

# Arm C: a signal is accepted on a coin flip at this rate, from a fixed seed.
CONTROL_SEED = 42
CONTROL_ACCEPT_RATE = 0.5

QUESTIONS = {"trade": {"type": "score", "criteria": ["skip", "take"]}}

_cache: dict[str, dict] = {}
_api_calls = 0
_fallbacks = 0


def load_cache() -> None:
    """Read log/jev_cache.jsonl so re-runs answer without calling the API."""
    if not CACHE_PATH.exists():
        return
    for line in CACHE_PATH.read_text().splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        _cache[row["hash"]] = row


def request_hash(state: str, questions: dict) -> str:
    """sha256 of the exact request body, so an identical ask is never repeated."""
    body = json.dumps(
        {"model": "jev-1.13-free", "state": state, "questions": questions},
        sort_keys=True,
    )
    return hashlib.sha256(body.encode()).hexdigest()


def jev_gate(state: str, questions: dict) -> bool:
    """True when Jev says take. None/unusable answers fall back to accept."""
    global _api_calls, _fallbacks
    h = request_hash(state, questions)
    if h in _cache:
        resp = _cache[h]["response"]
    else:
        _api_calls += 1
        resp = jev.ask(state, questions)
        _cache[h] = {
            "hash": h,
            "state": state,
            "questions": questions,
            "response": resp,
        }
        CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        with CACHE_PATH.open("a") as f:
            f.write(json.dumps(_cache[h], default=str) + "\n")
    return accept_from_response(resp)


def accept_from_response(resp) -> bool:
    """Apply the 0.30 threshold. A missing or malformed answer accepts."""
    global _fallbacks
    if resp is None:
        _fallbacks += 1
        return True
    probs = resp.get("answers", {}).get("trade", {}).get("probabilities", {})
    try:
        return float(probs["1"]) >= TAKE_THRESHOLD
    except (KeyError, TypeError, ValueError):
        _fallbacks += 1
        return True


def run_arm(candles, family, params, gate):
    """Backtest one family/param pair under a signal gate.

    Mirrors services.paper.backtest.backtest() exactly; the only difference is
    that a signal must clear `gate` to open a position. Arm A passes a gate
    that always accepts, so it reproduces the reference engine exactly.
    """
    fn = FAMILIES[family]
    cash = 1000.0
    pos = None
    trades = 0
    peak = cash
    maxdd = 0.0
    fees = 0.0
    candidates = 0
    accepted = 0
    for i in range(len(candles)):
        w = candles[: i + 1]
        px = float(w[-1]["close"])
        if pos is None:
            sig = fn(w, **params)
            if sig:
                candidates += 1
                if not gate(sig, px):
                    continue
                accepted += 1
                entry = px * (1 + 0.0002)
                qty = min(0.01, cash * 0.25 / px)
                # Mirrors backtest(): the entry fee is accumulated for reporting
                # and is NOT debited from cash. Debiting it here would make arm A
                # disagree with the reference engine (checked by the parity assert).
                fees += entry * qty * 0.0004
                pos = {
                    "side": sig,
                    "entry": entry,
                    "qty": qty,
                    "sl": entry * 0.998 if sig == "BUY" else entry * 1.002,
                    "tp": entry * 1.004 if sig == "BUY" else entry * 0.996,
                }
        else:
            hit_sl = px <= pos["sl"] if pos["side"] == "BUY" else px >= pos["sl"]
            hit_tp = px >= pos["tp"] if pos["side"] == "BUY" else px <= pos["tp"]
            opp = fn(w, **params)
            exit_now = hit_sl or hit_tp or (
                opp
                and (
                    (opp == "SELL" and pos["side"] == "BUY")
                    or (opp == "BUY" and pos["side"] == "SELL")
                )
            )
            if exit_now:
                exit_px = pos["tp"] if hit_tp else (pos["sl"] if hit_sl else px)
                exit_px *= 0.9998 if pos["side"] == "BUY" else 1.0002
                pnl = (
                    (exit_px - pos["entry"]) * pos["qty"]
                    if pos["side"] == "BUY"
                    else (pos["entry"] - exit_px) * pos["qty"]
                )
                fee = exit_px * pos["qty"] * 0.0004
                fees += fee
                cash += pnl - fee
                trades += 1
                pos = None
        eq = cash
        if pos:
            eq += (
                (px - pos["entry"]) * pos["qty"]
                if pos["side"] == "BUY"
                else (pos["entry"] - px) * pos["qty"]
            )
        peak = max(peak, eq)
        maxdd = max(maxdd, peak - eq)
    return {
        "trades": trades,
        "net_pnl": round(cash - 1000, 4),
        "max_drawdown": round(maxdd, 4),
        "fees": round(fees, 4),
        "candidates": candidates,
        "accepted": accepted,
    }


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--symbol", default="BTCUSDT")
    p.add_argument("--limit", type=int, default=500)
    p.add_argument("--interval", default="5m")
    args = p.parse_args()

    from services.foreign_data_service import get_foreign_history

    candles = get_foreign_history(args.symbol, "CRYPTO", args.interval, "", "")
    candles = candles[-args.limit :] if args.limit else candles
    if len(candles) < 60:
        print(f"Not enough candles for {args.symbol} ({args.interval}): {len(candles)}")
        return 1

    load_cache()

    agg: dict[tuple[str, str], dict] = {}
    parity_ok = True

    for family, grid in PARAM_GRID.items():
        for params in grid:
            rng = random.Random(CONTROL_SEED)

            arm_a = run_arm(candles, family, params, lambda sig, px: True)
            ref = backtest(candles, family, params)
            if (arm_a["net_pnl"], arm_a["max_drawdown"]) != (
                ref["net_pnl"],
                ref["max_drawdown"],
            ):
                parity_ok = False

            def gate_b(sig, px, _f=family, _p=params):
                state = (
                    f"symbol={args.symbol} interval={args.interval} "
                    f"family={_f} params={json.dumps(_p, sort_keys=True)} "
                    f"signal={sig} price={px:.2f}"
                )
                return jev_gate(state, QUESTIONS)

            arm_b = run_arm(candles, family, params, gate_b)
            arm_c = run_arm(
                candles,
                family,
                params,
                lambda sig, px, _r=rng: _r.random() < CONTROL_ACCEPT_RATE,
            )

            for arm, r in (("A", arm_a), ("B", arm_b), ("C", arm_c)):
                acc = agg.setdefault(
                    (family, arm),
                    {"cand": 0, "acc": 0, "pnl": 0.0, "dd": 0.0},
                )
                acc["cand"] += r["candidates"]
                acc["acc"] += r["accepted"]
                acc["pnl"] += r["net_pnl"]
                acc["dd"] = max(acc["dd"], r["max_drawdown"])

    header = (
        f"{'family':<11} {'arm':<4} {'candidates':>10} {'accepted':>9} "
        f"{'net_pnl':>9} {'max_dd':>8} {'stand_aside':>12} {'jev_fallbacks':>14}"
    )
    print(
        f"\n{args.symbol} {args.interval} candles={len(candles)} "
        f"api_calls={_api_calls} cache_entries={len(_cache)} "
        f"threshold={TAKE_THRESHOLD} armA_matches_backtest={parity_ok}\n"
    )
    print(header)
    print("-" * len(header))
    for (family, arm), acc in sorted(agg.items()):
        rate = (acc["cand"] - acc["acc"]) / acc["cand"] if acc["cand"] else 0.0
        fallbacks = _fallbacks if arm == "B" else "-"
        print(
            f"{family:<11} {arm:<4} {acc['cand']:>10} {acc['acc']:>9} "
            f"{acc['pnl']:>9.2f} {acc['dd']:>8.2f} {rate * 100:>11.1f}% {str(fallbacks):>14}"
        )
    print(
        "\nA = unfiltered (reference backtest())  "
        "B = Jev-gated (accept when P(take) >= 0.30)  "
        f"C = control (random, seed {CONTROL_SEED}, "
        f"{int(CONTROL_ACCEPT_RATE * 100)}% accept)"
    )
    print(f"Jev fallbacks in arm B (unavailable/unusable answer, accepted): {_fallbacks}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
