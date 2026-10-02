#!/usr/bin/env python3
"""Three-arm comparison: unfiltered vs Jev-gated vs random-gate control,
plus a threshold sweep across the range Jev actually answers in.

Read-only with respect to services/paper/: strategies, the backtest engine and
jev.ask are imported, never modified. Every Jev response is appended to a JSONL
cache keyed by the sha256 of the exact request body (model + state + questions),
so a re-run makes zero API calls and a richer state string can never be served
from a stale, thinner entry.

Two things this script is careful about, both of which the previous revision got
wrong:

1. The state string is discriminative. The old one carried only symbol, family,
   params, signal and price, and jev-1.13-free answered p(take) = 0.68-0.86 to
   every single one of those, so a 0.30 gate rejected nothing and the gate was
   never actually tested. The state below adds the setup metrics backtest()
   computes for that param set, the realised volatility and range of the recent
   window, the volume trend, and where price sits inside the recent range. Every
   field is a number this script measured on the candles it was given. Nothing is
   invented.

2. setup_* metrics are computed on the *causal prefix* candles[:i+1], not on the
   full window. backtest() is the same function either way, so the fields are
   exactly the ones backtest() reports, but a candidate at candle i is only ever
   shown what a live system would have known at candle i. Computing them over
   the full window would feed the model a summary of the future it is being
   asked to judge, and arm B would look better than any deployable version of
   itself could.

Risk is recorded but never acts. The strategy decides direction; P(risky) is
reported alongside the realised outcome so it can be judged on its own, but it
never flips an accept into a reject.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
import statistics
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from services.paper import jev  # noqa: E402
from services.paper.backtest import backtest  # noqa: E402
from services.paper.strategies import FAMILIES, PARAM_GRID  # noqa: E402

CACHE_PATH = REPO_ROOT / "log" / "jev_cache.jsonl"

MODEL = "jev-1.13-free"

# Both questions go in a single free call, so asking about risk costs nothing
# extra. "0" is the first criterion on each: skip / safe.
QUESTIONS = {
    "trade": {"type": "score", "criteria": ["skip", "take"]},
    "risk": {"type": "score", "criteria": ["safe", "risky"]},
}

# Arm B accepts when p(take) >= this. Swept over the range the model answers in.
THRESHOLDS = (0.30, 0.50, 0.60, 0.70, 0.75, 0.80, 0.85, 0.90)

# Candles of lookback context behind a candidate. 20 matches the donchian
# default and the rsi window, so the context matches what the families react to.
WINDOW = 20

# Arm C: a signal is accepted on a coin flip at this rate, from a fixed seed.
CONTROL_SEED = 42
CONTROL_ACCEPT_RATE = 0.5

# p(risky) at or above this is called "risky" when reporting the coincidence with
# realised losses. Reporting the rate on both sides too, so the cut is visible.
RISKY_CUT = 0.5

# Concurrent HTTP calls. The questions are parallel inside one request; this
# parallelises across requests so the sweep finishes in minutes, not an hour.
# Kept low deliberately: the free endpoint throttles bursts, and a throttled
# request is indistinguishable from an outage once jev.ask() has swallowed it.
MAX_WORKERS = 4

# Retries before a state is treated as unavailable, and the backoff between them.
API_ATTEMPTS = 3
API_BACKOFF = (10.0, 45.0)

# Minimum seconds between two requests, enforced process-wide. The free tier
# answers HTTP 429 FreeUsageLimitError once a burst arrives, and jev.ask() hides
# the status code, so the only defence is to stay under the limit and back off.
API_MIN_INTERVAL = 2.0

# Consecutive failures across all workers before the run gives up. Grinding
# through a quota wall costs a full backoff per state and produces nothing.
ABORT_AFTER_FAILS = 12

_CACHE: dict[str, dict] = {}
_CACHE_LOCK = threading.Lock()
_HASH_LOCKS: dict[str, threading.Lock] = {}
_API_CALLS = 0
_FALLBACKS = 0
_CONSEC_FAILS = 0
_ABORTED = False
_PACE_LOCK = threading.Lock()
_LAST_CALL = 0.0

# (family, params, i) -> setup metrics. Shared across every threshold, so the
# causal backtest prefix runs once per candidate rather than once per sweep point.
_SETUP: dict[tuple, dict] = {}
_SETUP_LOCK = threading.Lock()


def load_cache() -> int:
    """Read log/jev_cache.jsonl so re-runs answer without calling the API."""
    if not CACHE_PATH.exists():
        return 0
    n = 0
    with _CACHE_LOCK:
        for line in CACHE_PATH.read_text().splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            _CACHE[row["hash"]] = row
            n += 1
    return n


def request_hash(state: str, questions: dict) -> str:
    """sha256 of the exact request body.

    The body carries the full state string, so widening the state necessarily
    changes the hash. That is what stops a richer state from being answered out
    of the cache with a thinner state's verdict.
    """
    body = json.dumps(
        {"model": MODEL, "state": state, "questions": questions},
        sort_keys=True,
    )
    return hashlib.sha256(body.encode()).hexdigest()


def _lock_for(h: str) -> threading.Lock:
    with _CACHE_LOCK:
        lock = _HASH_LOCKS.get(h)
        if lock is None:
            lock = _HASH_LOCKS[h] = threading.Lock()
        return lock


def _pace() -> None:
    """Hold the process-wide gap between two API requests."""
    global _LAST_CALL
    with _PACE_LOCK:
        wait = API_MIN_INTERVAL - (time.monotonic() - _LAST_CALL)
        if wait > 0:
            time.sleep(wait)
        _LAST_CALL = time.monotonic()


def _fetch(state: str) -> dict | None:
    """Call the API, retrying a few times before giving up.

    jev.ask() swallows every transport error and returns None, so a burst of
    concurrent requests looks exactly like an outage. Retrying here keeps a
    transient 429 or socket reset from being recorded as a verdict. A failure
    that survives the retries is NOT cached, so the next run retries it for real
    instead of inheriting a fake answer forever.
    """
    global _API_CALLS, _CONSEC_FAILS, _ABORTED
    for attempt in range(API_ATTEMPTS):
        with _CACHE_LOCK:
            if _ABORTED:
                return None
            _API_CALLS += 1
        _pace()
        resp = jev.ask(state, QUESTIONS)
        if _prob(resp, "trade", "1") is not None:
            with _CACHE_LOCK:
                _CONSEC_FAILS = 0
            return resp
        with _CACHE_LOCK:
            _CONSEC_FAILS += 1
            if _CONSEC_FAILS >= ABORT_AFTER_FAILS:
                _ABORTED = True
                return None
        if attempt + 1 < API_ATTEMPTS:
            time.sleep(API_BACKOFF[min(attempt, len(API_BACKOFF) - 1)])
    return None


def jev_ask(state: str) -> dict | None:
    """Cached single call carrying both questions. None means unavailable."""
    h = request_hash(state, QUESTIONS)
    with _CACHE_LOCK:
        hit = _CACHE.get(h)
    if hit is not None:
        return hit["response"]

    # One in-flight request per distinct state, however many workers want it.
    with _lock_for(h):
        with _CACHE_LOCK:
            hit = _CACHE.get(h)
        if hit is not None:
            return hit["response"]
        resp = _fetch(state)
        if resp is None:
            return None
        row = {"hash": h, "state": state, "questions": QUESTIONS, "response": resp}
        with _CACHE_LOCK:
            _CACHE[h] = row
            CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
            with CACHE_PATH.open("a") as f:
                f.write(json.dumps(row, default=str) + "\n")
    return resp


def _prob(resp, question: str, index: str) -> float | None:
    """p for one question's chosen criterion, or None if the answer is unusable."""
    if not isinstance(resp, dict):
        return None
    try:
        return float(resp["answers"][question]["probabilities"][index])
    except (KeyError, TypeError, ValueError):
        return None


def _f(x: float, places: int) -> str:
    """Fixed-width number so the same measurement always hashes the same."""
    return f"{x:.{places}f}"


def window_metrics(candles: list, i: int) -> dict:
    """Realised volatility, range, volume trend and price position at candle i.

    Everything here is measured on candles[:i+1]; nothing is projected.
    """
    px = float(candles[i]["close"])
    closes = [float(c["close"]) for c in candles[max(0, i - WINDOW) : i + 1]]
    rets = [closes[k] / closes[k - 1] - 1 for k in range(1, len(closes)) if closes[k - 1]]
    stdev = statistics.pstdev(rets) if len(rets) > 1 else 0.0

    cur = candles[max(0, i - WINDOW + 1) : i + 1]
    prev = candles[max(0, i - 2 * WINDOW + 1) : max(0, i - WINDOW + 1)]
    hi = max(float(c["high"]) for c in cur)
    lo = min(float(c["low"]) for c in cur)
    rng = (hi - lo) / px if px else 0.0
    pos = (px - lo) / (hi - lo) if hi > lo else 0.5

    cur_v = [float(c.get("volume") or 0.0) for c in cur]
    prev_v = [float(c.get("volume") or 0.0) for c in prev]
    cur_mean = sum(cur_v) / len(cur_v) if cur_v else 0.0
    prev_mean = sum(prev_v) / len(prev_v) if prev_v else 0.0
    vol_trend = (cur_mean / prev_mean - 1.0) if prev_mean > 0 else 0.0

    return {
        "price": px,
        "stdev": stdev,
        "range": rng,
        "vol_trend": vol_trend,
        "pos": pos,
    }


def setup_metrics(candles: list, family: str, params: dict, i: int) -> dict:
    """What backtest() says about this family/params on the causal prefix.

    Memoised across thresholds. Returns net_pnl, max_drawdown, trades and fees,
    which is exactly backtest()'s own output shape.
    """
    key = (family, json.dumps(params, sort_keys=True), i)
    with _SETUP_LOCK:
        got = _SETUP.get(key)
    if got is not None:
        return got
    got = backtest(candles[: i + 1], family, params)
    with _SETUP_LOCK:
        _SETUP[key] = got
    return got


def build_state(
    symbol: str,
    interval: str,
    family: str,
    params: dict,
    signal: str,
    candles: list,
    i: int,
) -> str:
    """The exact string handed to the model. Every field was measured above."""
    m = window_metrics(candles, i)
    s = setup_metrics(candles, family, params, i)
    return " ".join(
        [
            f"symbol={symbol}",
            f"interval={interval}",
            f"family={family}",
            f"params={json.dumps(params, sort_keys=True)}",
            f"signal={signal}",
            f"price={_f(m['price'], 2)}",
            f"setup_net_pnl={_f(s['net_pnl'], 4)}",
            f"setup_max_dd={_f(s['max_drawdown'], 4)}",
            f"setup_trades={int(s['trades'])}",
            f"setup_fees={_f(s['fees'], 4)}",
            f"ret_stdev_{WINDOW}={_f(m['stdev'], 6)}",
            f"range_{WINDOW}={_f(m['range'], 6)}",
            f"volume_trend_{WINDOW}={_f(m['vol_trend'], 6)}",
            f"price_pos_{WINDOW}={_f(m['pos'], 4)}",
        ]
    )


def make_jev_gate(symbol: str, interval: str, family: str, params: dict, threshold: float):
    """Gate for arm B. Returns (accept, take_prob, risk_prob).

    A missing or malformed answer accepts, and is counted: the failure mode is
    "trade as before", never "silently stop trading". p(risky) rides along for
    reporting only and cannot change the accept decision.
    """

    def gate(sig, i, candles):
        global _FALLBACKS
        state = build_state(symbol, interval, family, params, sig, candles, i)
        resp = jev_ask(state)
        take = _prob(resp, "trade", "1")
        risk = _prob(resp, "risk", "1")
        if take is None:
            _FALLBACKS += 1
            return True, None, risk
        return take >= threshold, take, risk

    return gate


def run_arm(candles, family, params, gate):
    """Backtest one family/param pair under a signal gate.

    Mirrors services.paper.backtest.backtest() exactly; the only difference is
    that a signal must clear `gate` to open a position. Arm A passes a gate that
    always accepts, so it reproduces the reference engine exactly (asserted).

    gate(signal, i, candles) -> (accept, take_prob, risk_prob). Every completed
    trade is recorded with the p(take) and p(risky) of the signal that opened
    it, so the sweep can report risk against realised outcomes without re-asking.
    """
    fn = FAMILIES[family]
    cash = 1000.0
    pos = None
    entry_ctx = None
    trades = 0
    peak = cash
    maxdd = 0.0
    fees = 0.0
    candidates = 0
    accepted = 0
    trade_log: list[dict] = []
    takes: list[float] = []
    for i in range(len(candles)):
        w = candles[: i + 1]
        px = float(w[-1]["close"])
        if pos is None:
            sig = fn(w, **params)
            if sig:
                candidates += 1
                ok, take, risk = gate(sig, i, candles)
                if take is not None:
                    takes.append(take)
                if not ok:
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
                entry_ctx = (take, risk)
        else:
            hit_sl = px <= pos["sl"] if pos["side"] == "BUY" else px >= pos["sl"]
            hit_tp = px >= pos["tp"] if pos["side"] == "BUY" else px <= pos["tp"]
            opp = fn(w, **params)
            exit_now = (
                hit_sl
                or hit_tp
                or (
                    opp
                    and (
                        (opp == "SELL" and pos["side"] == "BUY")
                        or (opp == "BUY" and pos["side"] == "SELL")
                    )
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
                trade_log.append(
                    {
                        "pnl": round(pnl - fee, 6),
                        "take": entry_ctx[0] if entry_ctx else None,
                        "risk": entry_ctx[1] if entry_ctx else None,
                    }
                )
                pos = None
                entry_ctx = None
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
        "trade_log": trade_log,
        "takes": takes,
    }


def always(_sig, _i, _candles):
    return True, None, None


def coin_flip(rng):
    def gate(_sig, _i, _candles):
        return rng.random() < CONTROL_ACCEPT_RATE, None, None

    return gate


def blank():
    return {
        "trades": 0,
        "net_pnl": 0.0,
        "max_drawdown": 0.0,
        "candidates": 0,
        "accepted": 0,
        "trade_log": [],
        "takes": [],
    }


def merge(dst, src):
    """Sum a run into an accumulator. max_drawdown is a max, not a sum."""
    for key in ("trades", "net_pnl", "candidates", "accepted"):
        dst[key] += src[key]
    dst["max_drawdown"] = max(dst["max_drawdown"], src["max_drawdown"])
    dst["trade_log"].extend(src["trade_log"])
    dst["takes"].extend(src["takes"])
    return dst


def summarise(take_runs) -> dict:
    """Aggregate the sweep: P&L, stand-aside rate, and risk vs realised loss."""
    acc = blank()
    for r in take_runs:
        merge(acc, r)
    cand = acc["candidates"]
    risky = [t for t in acc["trade_log"] if t["risk"] is not None]
    risky_hi = [t for t in risky if t["risk"] >= RISKY_CUT]
    risky_lo = [t for t in risky if t["risk"] < RISKY_CUT]
    losses = [t for t in acc["trade_log"] if t["pnl"] < 0]
    return {
        **acc,
        "stand_aside": (cand - acc["accepted"]) / cand if cand else 0.0,
        "losses": len(losses),
        "risk_scored": len(risky),
        "risky_n": len(risky_hi),
        "risky_losses": sum(1 for t in risky_hi if t["pnl"] < 0),
        "safe_n": len(risky_lo),
        "safe_losses": sum(1 for t in risky_lo if t["pnl"] < 0),
        "win_rate": (
            (len(acc["trade_log"]) - len(losses)) / len(acc["trade_log"])
            if acc["trade_log"]
            else 0.0
        ),
    }


def two_proportion_z(w1, n1, w2, n2):
    """Two-sided z on the difference of two win rates, or None if undefined."""
    if n1 == 0 or n2 == 0:
        return None
    p1, p2 = w1 / n1, w2 / n2
    p = (w1 + w2) / (n1 + n2)
    se = (p * (1 - p) * (1 / n1 + 1 / n2)) ** 0.5
    if se == 0:
        return None
    return (p1 - p2) / se


def p_from_z(z: float) -> float:
    """Two-sided normal tail. No scipy dependency for one number."""
    return max(0.0, min(1.0, math.erfc(abs(z) / math.sqrt(2.0))))


def print_sweep(summaries):
    head = (
        f"{'thr':>5} {'accepted':>9} {'stand_aside':>12} {'net_pnl':>9} "
        f"{'max_dd':>8} {'trades':>7} {'win%':>6} {'risky':>6} {'risky&loss':>11} "
        f"{'safe&loss':>10}"
    )
    print(head)
    print("-" * len(head))
    for thr, s in summaries:
        print(
            f"{thr:>5.2f} {s['accepted']:>9} {s['stand_aside'] * 100:>11.1f}% "
            f"{s['net_pnl']:>9.2f} {s['max_drawdown']:>8.2f} {s['trades']:>7} "
            f"{s['win_rate'] * 100:>5.1f}% {s['risky_n']:>6} {s['risky_losses']:>11} "
            f"{s['safe_losses']:>10}"
        )


def print_arms(rows):
    head = f"{'arm':<5} {'gate':<34} {'accepted':>9} {'net_pnl':>9} {'max_dd':>8} {'win%':>6}"
    print(head)
    print("-" * len(head))
    for label, gate, s in rows:
        print(
            f"{label:<5} {gate:<34} {s['accepted']:>9} {s['net_pnl']:>9.2f} "
            f"{s['max_drawdown']:>8.2f} {s['win_rate'] * 100:>5.1f}%"
        )


def run_symbol(symbol: str, interval: str, limit: int) -> int:
    global API_MIN_INTERVAL
    from services.foreign_data_service import get_foreign_history

    candles = get_foreign_history(symbol, "CRYPTO", interval, "", "")
    candles = candles[-limit:] if limit else candles
    if len(candles) < 60:
        print(f"Not enough candles for {symbol} ({interval}): {len(candles)}")
        return 1

    # Jobs are keyed by (arm, family, params-as-json, threshold): params is a dict
    # and cannot go in a dict key, and this key is also what selects a run later.
    jobs: list[tuple] = []
    for family, grid in PARAM_GRID.items():
        for params in grid:
            pk = json.dumps(params, sort_keys=True)
            jobs.append(("A", family, pk, None))
            jobs.append(("C", family, pk, None))
            for thr in THRESHOLDS:
                jobs.append(("B", family, pk, thr))

    results: dict[tuple, dict] = {}
    parity: dict[tuple, bool] = {}

    def work(job):
        arm, family, pk, thr = job
        params = json.loads(pk)
        if arm == "A":
            r = run_arm(candles, family, params, always)
            ref = backtest(candles, family, params)
            parity[(family, pk)] = (
                r["net_pnl"],
                r["max_drawdown"],
            ) == (ref["net_pnl"], ref["max_drawdown"])
            return job, r
        if arm == "C":
            rng = random.Random(CONTROL_SEED)
            return job, run_arm(candles, family, params, coin_flip(rng))
        gate = make_jev_gate(symbol, interval, family, params, thr)
        return job, run_arm(candles, family, params, gate)

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        for job, r in pool.map(work, jobs):
            results[job] = r

    all_takes = [t for job, r in results.items() if job[0] == "B" for t in r["takes"]]
    parity_ok = all(parity.values())

    sweep = []
    for thr in THRESHOLDS:
        runs = [
            results[("B", f, json.dumps(p, sort_keys=True), thr)]
            for f, g in PARAM_GRID.items()
            for p in g
        ]
        sweep.append((thr, summarise(runs)))

    print(
        f"\n=== {symbol} {interval} candles={len(candles)} "
        f"api_calls={_API_CALLS} cache_entries={len(_CACHE)} "
        f"armA_matches_backtest={parity_ok} jev_fallbacks={_FALLBACKS} ==="
    )
    if all_takes:
        print(
            f"p(take) observed range: {min(all_takes):.2f} - {max(all_takes):.2f} "
            f"over {len(all_takes)} scored candidates"
        )

    if _ABORTED:
        print(
            "\nSTOPPED EARLY. The Jev endpoint refused repeated requests "
            f"(consecutive failures: {_CONSEC_FAILS}). Every candidate Jev did not\n"
            "answer was accepted by default, so the numbers below are NOT a measurement "
            "of the gate.\nRe-run later: answered states are cached, so the next run only "
            "pays for what is still missing."
        )
    elif _FALLBACKS:
        print(
            f"\nWARNING: {_FALLBACKS} candidates got no usable Jev answer and were "
            "accepted by default.\nArm B is partly arm A; read the stand-aside column as "
            "an upper bound."
        )

    print("\nArm B threshold sweep (all families and params summed)")
    print_sweep(sweep)

    best_thr, best = max(sweep, key=lambda x: x[1]["net_pnl"])
    a_runs = [
        results[("A", f, json.dumps(p, sort_keys=True), None)]
        for f, g in PARAM_GRID.items()
        for p in g
    ]
    c_runs = [
        results[("C", f, json.dumps(p, sort_keys=True), None)]
        for f, g in PARAM_GRID.items()
        for p in g
    ]
    sum_a, sum_c = summarise(a_runs), summarise(c_runs)

    print(f"\nA / B / C at the best-looking threshold (p(take) >= {best_thr:.2f})")
    print_arms(
        [
            ("A", "none (reference backtest())", sum_a),
            ("B", f"Jev, accept p(take) >= {best_thr:.2f}", best),
            ("C", f"random, seed {CONTROL_SEED}, 50%", sum_c),
        ]
    )

    wins_b = best["trades"] - best["losses"]
    wins_a = sum_a["trades"] - sum_a["losses"]
    z_b = two_proportion_z(wins_b, best["trades"], wins_a, sum_a["trades"])
    z_c = two_proportion_z(
        wins_b, best["trades"], sum_c["trades"] - sum_c["losses"], sum_c["trades"]
    )

    print("\nIs the difference real?")
    print(f"  arm B took {best['trades']} trades at the best threshold, arm A {sum_a['trades']}")
    if z_b is None:
        print("  too few completed trades to compare")
    else:
        print(f"  B vs A win rate: z={z_b:+.2f} (two-sided p={p_from_z(z_b):.2f})")
    if z_c is None:
        print("  too few completed trades in the control to compare")
    else:
        print(f"  B vs C win rate: z={z_c:+.2f} (two-sided p={p_from_z(z_c):.2f})")
    n = best["trades"]
    print(
        f"  n={n} completed trades at that threshold. "
        + (
            "A difference of this size is not distinguishable from noise at this n."
            if n < 100
            else "Treat the p-values as weak evidence, not proof."
        )
    )

    print("\nPer family at the best-looking threshold")
    head = f"{'family':<11} {'arm':<4} {'accepted':>9} {'net_pnl':>9} {'max_dd':>8} {'trades':>7}"
    print(head)
    print("-" * len(head))
    for family in PARAM_GRID:
        for arm, thr in (("A", None), ("B", best_thr), ("C", None)):
            runs = [
                results[(arm, family, json.dumps(params, sort_keys=True), thr)]
                for params in PARAM_GRID[family]
            ]
            s = summarise(runs)
            print(
                f"{family:<11} {arm:<4} {s['accepted']:>9} {s['net_pnl']:>9.2f} "
                f"{s['max_drawdown']:>8.2f} {s['trades']:>7}"
            )
    return 0


def main() -> int:
    global API_MIN_INTERVAL
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--symbol", default="BTCUSDT")
    p.add_argument("--limit", type=int, default=300)
    p.add_argument("--interval", default="5m")
    p.add_argument(
        "--min-interval",
        type=float,
        default=API_MIN_INTERVAL,
        help="minimum seconds between two API requests; raise it if the free "
        "tier starts answering 429",
    )
    args = p.parse_args()

    entries = load_cache()
    print(f"cache: {entries} entries loaded from {CACHE_PATH}")

    API_MIN_INTERVAL = args.min_interval
    rc = run_symbol(args.symbol, args.interval, args.limit)
    if rc:
        return rc
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
