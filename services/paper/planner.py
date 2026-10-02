"""The single gate between a strategy signal and a paper order.

Nothing trades on a signal alone. Every order the paper engine places goes
through :func:`plan` first, which builds a full plan from computed facts, has
the Jev analyst score it, checks the risk rules, and either hands back a
tradeable plan or a refusal with a reason. :func:`execute` runs only on a plan
that carries no refusal.

The rules, in the order they are applied:

1. **Validation.** A strategy is only a candidate if the metrics recorded for
   it in the ``strategies`` table clear the same bar research used: at least 3
   backtest trades, positive net P&L, and max drawdown under 50. A strategy
   that fails is never proposed, and the rejection is recorded with the reason.
2. **Facts before opinions.** The state string handed to Jev is assembled from
   values computed here -- volatility, volume trend, where the price sits in
   its recent range, the recent regime -- plus the strategy's own recorded
   metrics. Nothing is invented, and Jev is asked for both the trade call and a
   quality call in one request.
3. **The analyst gate.** ``p(take)`` must clear ``DEFAULT_TAKE_THRESHOLD``. If
   Jev does not answer, the plan is marked ``analyst_available: False`` and the
   trade is refused unless the caller explicitly opts into rules-only. Jev can
   veto a trade; it can never flip the direction, which always comes from the
   strategy function itself.
4. **Risk.** Sizing comes from :func:`services.paper.engine.plan_size`: the
   quantity is chosen so that hitting the stop costs the risk budget
   (``qty = risk_usd / |entry - stop|``), and the configured caps -- the cash
   left, the account's exposure cap, and a per-position notional ceiling -- are
   ceilings on that number rather than the rule itself. ``risk_usd`` and
   ``reward_usd`` are then recomputed from the final quantity, so the plan
   reports the risk the order that gets placed actually carries. The plan is
   refused for insufficient cash, a reached daily loss limit, a hit exposure
   cap, caps that leave a position too small to be worth opening, a symbol that
   is already open, or stale data.

5. **Exits.** A plan that is never closed is not a trade, so this module also
   owns the way out: :func:`manage_open_positions` runs on every monitoring cycle
   and closes every open position whose stop, target, max-hold limit or owning
   strategy has turned against it, and :func:`close_position` is the deliberate
   one an operator asks for by name. Both go through the engine's own close, so
   cash, realised P&L and fees move exactly as an entry does.

Every refusal is a reason string on the returned plan, never an exception. An
exit is likewise a record, never an exception: one bad position row cannot stop
the others from being managed.
"""

from __future__ import annotations

import json
import time
import uuid

from utils.logging import get_logger

from . import config, db, engine, jev
from .strategies import FAMILIES

logger = get_logger(__name__)

# The bar research used to register a strategy (engine.research_cycle). Kept
# here as module constants so the planner states the bar it enforces instead of
# inheriting it silently.
MIN_BACKTEST_TRADES = 3
MIN_NET_PNL = 0.0
MAX_DRAWDOWN = 50.0

# Below this, the analyst's vote is "skip".
DEFAULT_TAKE_THRESHOLD = 0.30

# How stale the newest candle may be before the plan is refused. Two candle
# intervals of slack absorbs a feed that is a bar or two behind.
STALENESS_SLACK_INTERVALS = 2
MIN_STALE_SECONDS = 120.0

# Stop and target, as fractions of entry. These match what engine.trading_cycle
# writes into positions.sl / positions.tp, so a plan and a cycled paper
# position carry the same bracket.
STOP_PCT = 0.002
TARGET_PCT = 0.004

# Candle timestamp keys, in the order they are preferred. Providers differ.
TIME_KEYS = ("time", "timestamp", "ts", "open_time", "date")

REGIME_WINDOW = 20
VOL_WINDOW = 20

DEFAULT_MAX_RISK = 5.0

# Refusal reasons. Named constants so the worker, the CLI and the tests all
# read the same string.
NO_EDGE = "no_edge"
NO_STRATEGIES = "no_validated_strategy"
FAILED_VALIDATION = "strategy_failed_validation"
ANALYST_UNAVAILABLE = "analyst_unavailable"

# Plain-language reason from the analyst client for the most recent failed call,
# so a refusal explains itself instead of reading as "no edge".
_last_analyst_failure = ""
LOW_TAKE_PROBABILITY = "analyst_p_take_below_threshold"
INSUFFICIENT_CASH = "insufficient_cash"
DAILY_LOSS_LIMIT = "daily_loss_limit"
EXPOSURE_CAP = "exposure_cap"
SYMBOL_ALREADY_OPEN = "symbol_already_open"
STALE_DATA = "stale_data"
BAD_REQUEST = "bad_request"
REFUSED_PLAN = "plan_already_refused"
# The caps left a position too small to be worth opening, and the risk budget
# cannot be funded into one. Both are refusals on purpose: a plan that quietly
# traded 0.0024 USD of risk while claiming a 5 USD budget is worse than no trade.
BELOW_MIN_SIZE = "below_min_size"
RISK_BUDGET = "risk_budget"

# Refusal reason for each reason engine.plan_size returns, mapped onto the
# planner's own vocabulary. Anything unmapped is passed through as-is, because the
# engine's reason is more specific than anything invented here.
_SIZE_REFUSALS = {
    engine.SIZE_NO_CASH: INSUFFICIENT_CASH,
    engine.SIZE_INSUFFICIENT_CASH: INSUFFICIENT_CASH,
    engine.SIZE_EXPOSURE_CAP: EXPOSURE_CAP,
    engine.SIZE_CAPPED_BY_CONFIG: "capped_by_config",
    engine.SIZE_INVALID_PRICE: "invalid_price",
    engine.SIZE_INVALID_BUDGET: RISK_BUDGET,
    engine.SIZE_ZERO_RISK_DISTANCE: RISK_BUDGET,
    engine.SIZE_BELOW_MIN_SIZE: BELOW_MIN_SIZE,
}


def _reject(symbol, strategy_id, reason, detail, **extra) -> dict:
    """A candidate that produced no trade, with the reason kept."""
    return {
        "symbol": symbol,
        "strategy_id": strategy_id,
        "reason": reason,
        "detail": detail,
        **extra,
    }


# --------------------------------------------------------------------- metrics


def _metrics_of(raw) -> dict:
    if isinstance(raw, dict):
        return raw
    try:
        loaded = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _as_float(value, default=0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def validate_metrics(metrics: dict) -> tuple[bool, str]:
    """Return ``(passed, reason)`` for a strategy's recorded backtest metrics.

    The reason names the specific bar that failed, so a rejection is
    actionable rather than a bare "invalid".
    """
    trades = _as_float(metrics.get("trades"), 0.0)
    net_pnl = _as_float(metrics.get("net_pnl"), 0.0)
    drawdown = _as_float(metrics.get("max_drawdown"), 0.0)
    if trades < MIN_BACKTEST_TRADES:
        return False, f"trades {trades:g} below the minimum {MIN_BACKTEST_TRADES:g}"
    if net_pnl <= MIN_NET_PNL:
        return False, f"net_pnl {net_pnl:g} is not positive"
    if drawdown >= MAX_DRAWDOWN:
        return False, f"max_drawdown {drawdown:g} is at or above the {MAX_DRAWDOWN:g} limit"
    return True, "ok"


# ------------------------------------------------------------------ market facts


def _closes(candles) -> list[float]:
    return [float(k["close"]) for k in candles]


def _volatility(closes: list[float]) -> float:
    """Standard deviation of bar-over-bar returns, as a fraction."""
    if len(closes) < 3:
        return 0.0
    window = closes[-(VOL_WINDOW + 1) :]
    rets = [(window[i] - window[i - 1]) / window[i - 1] for i in range(1, len(window)) if window[i - 1]]
    if len(rets) < 2:
        return 0.0
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
    return var**0.5


def _volume_trend(candles) -> float | None:
    """Ratio of recent average volume to the prior window's, or None if absent."""
    vols = [k.get("volume") for k in candles]
    if any(v is None for v in vols[-2 * VOL_WINDOW :]):
        return None
    vols = [float(v) for v in vols if v is not None]
    if len(vols) < 2 * VOL_WINDOW:
        return None
    recent = sum(vols[-VOL_WINDOW:]) / VOL_WINDOW
    prior = sum(vols[-2 * VOL_WINDOW : -VOL_WINDOW]) / VOL_WINDOW
    if prior <= 0:
        return None
    return recent / prior


def _range_position(candles) -> float | None:
    """Where the last close sits between the recent low and high, 0 to 1."""
    window = candles[-REGIME_WINDOW:]
    if len(window) < 2:
        return None
    highs = [_as_float(k.get("high"), _as_float(k.get("close"), 0.0)) for k in window]
    lows = [_as_float(k.get("low"), _as_float(k.get("close"), 0.0)) for k in window]
    top, bottom = max(highs), min(lows)
    if top <= bottom:
        return None
    return (float(window[-1]["close"]) - bottom) / (top - bottom)


def _regime(candles) -> str:
    """A one-word read of the recent drift, from closes only."""
    window = _closes(candles)[-(REGIME_WINDOW + 1) :]
    if len(window) < 2 or window[0] <= 0:
        return "unknown"
    change = (window[-1] - window[0]) / window[0]
    if change > 0.002:
        return "rising"
    if change < -0.002:
        return "falling"
    return "flat"


def _candle_time(candles):
    for key in TIME_KEYS:
        value = candles[-1].get(key)
        if value is None:
            continue
        try:
            seconds = float(value)
        except (TypeError, ValueError):
            continue
        # Milliseconds are common from crypto feeds.
        return seconds / 1000.0 if seconds > 1e11 else seconds
    return None


def _staleness(candles, interval: str, now: float) -> tuple[bool, str]:
    """Return ``(is_stale, detail)`` for the newest candle.

    A candle with no timestamp cannot be age-checked; that is reported rather
    than passed off as fresh.
    """
    stamp = _candle_time(candles)
    if stamp is None:
        return False, "candle carries no timestamp, freshness not verifiable"
    per_bar = _interval_seconds(interval)
    limit = max(MIN_STALE_SECONDS, per_bar * STALENESS_SLACK_INTERVALS)
    age = now - stamp
    if age > limit:
        return True, f"newest candle is {age:.0f}s old, limit {limit:.0f}s"
    return False, f"newest candle is {age:.0f}s old"


def _interval_seconds(interval: str) -> float:
    unit = str(interval or "")[-1:].lower()
    amount = str(interval or "")[:-1]
    try:
        amount = float(amount)
    except ValueError:
        return 300.0
    return amount * {"s": 1, "m": 60, "h": 3600, "d": 86400}.get(unit, 60)


# --------------------------------------------------------------------- account


def _daily_loss(limit: float) -> tuple[bool, str]:
    """Whether today's paper equity loss has reached ``limit`` USD."""
    if limit <= 0:
        return False, ""
    day_start = time.time() - (time.time() % 86400)
    with db.conn() as c:
        rows = c.execute(
            "SELECT equity FROM equity WHERE ts >= ? ORDER BY ts ASC", (day_start,)
        ).fetchall()
    if len(rows) < 2:
        return False, ""
    first, last = float(rows[0]["equity"]), float(rows[-1]["equity"])
    loss = first - last
    if loss >= limit:
        return True, f"today's paper loss {loss:.2f} USD reached the {limit:.2f} USD limit"
    return False, f"today's paper loss {loss:.2f} USD against a {limit:.2f} USD limit"


def _open_symbols() -> set[str]:
    with db.conn() as c:
        return {r["symbol"] for r in c.execute("SELECT symbol FROM positions WHERE status='open'")}


def _exposure_notional() -> float:
    with db.conn() as c:
        rows = c.execute("SELECT side,qty,entry FROM positions WHERE status='open'").fetchall()
    return sum(float(r["qty"]) * float(r["entry"]) for r in rows)


# ------------------------------------------------------------------- reasoning


def _reasoning(symbol, side, setup, facts, verdict, p_take, threshold, plan):
    """Plain English built only from the numbers that were actually computed.

    Three parts: what the backtest says should happen, what this market's data
    actually shows, and what would prove the trade wrong. It is given the finished
    plan rather than loose arguments, so the money quoted here is the money the
    order is placed with -- there is no second place for a quantity to be
    recomputed and drift.
    """
    entry = plan["entry_price"]
    stop = plan["stop_loss"]
    target = plan["take_profit"]
    qty = plan["qty"]
    expected = (
        f"The {setup['family']} setup on {symbol} has backtested {setup['metrics']['trades']:g} "
        f"trades for a net {setup['metrics']['net_pnl']:+.2f} USD with a worst drawdown of "
        f"{setup['metrics']['max_drawdown']:.2f} USD and {setup['metrics'].get('fees', 0.0):.2f} USD "
        f"of fees, so over that sample it was expected to make money "
        f"{'long' if side == 'BUY' else 'short'}."
    )

    observations = []
    observations.append(
        f"On the {facts['interval']} candles right now it wants to go {side} at "
        f"{entry:.4f}, with the market in a {facts['regime']} regime."
    )
    observations.append(
        f"Bar-to-bar volatility is {facts['volatility'] * 100:.2f}% and the last close sits "
        f"{_pct_of_range(facts['range_position'])} of the way through the last "
        f"{facts['range_window']}-bar range."
    )
    if facts.get("volume_trend") is None:
        observations.append("The candles carry no usable volume, so volume could not confirm it.")
    elif facts["volume_trend"] >= 1.0:
        observations.append(
            f"Volume is running at {facts['volume_trend']:.2f}x its prior "
            f"{facts['volume_window']}-bar average, so participation backs the direction."
        )
    else:
        observations.append(
            f"Volume is running at only {facts['volume_trend']:.2f}x its prior "
            f"{facts['volume_window']}-bar average, which is thin support for the direction."
        )

    model = []
    if verdict and verdict.get("p_take") is not None:
        model.append(
            f"The analyst scored this a {verdict['p_take']:.2f} probability of 'take' "
            f"against a {threshold:.2f} gate, and called the setup "
            f"{verdict['quality_label'].lower()}."
        )
    else:
        model.append(
            "The analyst did not answer, so this plan carries no model opinion at all."
        )
    model.append(
        f"Sizing it at {qty:.6f} unit(s) is what makes the stop cost "
        f"{plan['risk_usd']:.2f} USD against a target worth {plan['reward_usd']:.2f} USD, "
        f"for {plan['rr']:.2f} times the risk."
    )

    invalidation = (
        f"It is wrong if the price reaches {stop:.4f}, which is "
        f"{abs(entry - stop) / entry * 100:.2f}% away and costs the full risk; "
        f"it works as planned if it reaches {target:.4f}. "
        f"A move back against the {side} direction that takes the last close out of the "
        f"{facts['interval']} {facts['regime']} range, or volume staying this far below "
        f"its prior average, would mean the setup no longer matches the sample it was "
        f"validated on."
    )

    return " ".join([expected, *observations, *model, invalidation])


def _pct_of_range(value) -> str:
    if value is None:
        return "an unknown position"
    return f"{value * 100:.0f}%"


# ------------------------------------------------------------------- jev gate


def _questions() -> dict:
    """Both questions in a single request, as the analyst is meant to be used."""
    return {
        "trade": {"type": "score", "criteria": ["skip", "take"]},
        "quality": {"type": "score", "criteria": ["weak", "strong"]},
    }


def _prob(answer) -> float | None:
    if not isinstance(answer, dict):
        return None
    probs = answer.get("probabilities")
    if not isinstance(probs, dict):
        return None
    value = probs.get("1")
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _ask_analyst(state: str) -> dict | None:
    """Ask Jev once. Returns the parsed verdict summary, or None.

    A failure here is never an exception and never a guess: the caller marks
    the plan ``analyst_available: False`` and refuses.
    """
    try:
        raw = jev.ask(state, _questions())
    except Exception:
        logger.warning("jev analyst call failed; plan will be refused")
        return None
    if raw is None:
        # Say why in the operator's words, so a refusal never reads as "no edge".
        _last_analyst_failure = jev.failure_reason()
    if not isinstance(raw, dict):
        return None
    answers = raw.get("answers")
    if not isinstance(answers, dict):
        return None
    p_take = _prob(answers.get("trade"))
    p_quality = _prob(answers.get("quality"))
    if p_take is None:
        return None
    return {
        "available": True,
        "p_take": p_take,
        "p_quality": p_quality,
        "quality_label": "strong" if (p_quality or 0.0) >= 0.5 else "weak",
        "raw": raw,
    }


# ----------------------------------------------------------------------- plan


def _blank(symbol=None, **extra) -> dict:
    plan = {
        "symbol": symbol,
        "side": None,
        "qty": 0.0,
        "entry_price": None,
        "stop_loss": None,
        "take_profit": None,
        "risk_usd": 0.0,
        "reward_usd": 0.0,
        "rr": 0.0,
        "strategy_id": None,
        "metrics": {},
        "jev_verdict": None,
        "reasoning": "",
        "refusal_reason": None,
        "created_at": time.time(),
    }
    plan.update(extra)
    return plan


def plan(
    symbols=None,
    mode: str = "scan",
    *,
    interval: str | None = None,
    max_risk: float | None = None,
    take_threshold: float = DEFAULT_TAKE_THRESHOLD,
    allow_rules_only: bool = False,
) -> dict:
    """Build one reasoned, risk-checked plan. Never raises for a bad setup.

    Returns the best candidate found, or a refusal. ``considered`` carries every
    candidate that was passed over and why, so a refusal is never a bare "no".
    """
    db.init()
    considered: list[dict] = []
    interval = interval or engine.resolve_interval()
    symbols = [str(s).strip().upper() for s in symbols] if symbols else engine.resolve_watchlist()
    budget = DEFAULT_MAX_RISK if max_risk is None else _as_float(max_risk, DEFAULT_MAX_RISK)
    threshold = _as_float(take_threshold, DEFAULT_TAKE_THRESHOLD)

    cfg = db.get_all()
    cash = engine.available_cash(cfg)
    unrealized = _unrealized()
    equity = engine.equity_of(cfg, unrealized)
    open_symbols = _open_symbols()
    exposure_pct = _as_float(cfg.get("max_exposure_pct"), 0.5)
    exposure_open = _exposure_notional()
    daily_limit = _as_float(cfg.get("max_daily_loss"), 0.0)
    fee_rate = engine._fee_rate(cfg)
    slip_rate = engine._slip_rate(cfg)

    blocked, blocked_detail = _daily_loss(daily_limit)
    best: dict | None = None

    for symbol in symbols:
        if blocked:
            considered.append(_reject(symbol, None, DAILY_LOSS_LIMIT, blocked_detail))
            continue
        if symbol in open_symbols:
            considered.append(
                _reject(symbol, None, SYMBOL_ALREADY_OPEN, f"{symbol} already holds an open position")
            )
            continue

        candles = engine._candles(symbol, interval)
        if len(candles) < 30:
            considered.append(
                _reject(
                    symbol,
                    None,
                    "insufficient_data",
                    f"{len(candles)} candles, at least 30 are needed",
                )
            )
            continue
        stale, stale_detail = _staleness(candles, interval, time.time())
        if stale:
            considered.append(_reject(symbol, None, STALE_DATA, stale_detail))
            continue

        for row in _strategies_for(symbol, interval):
            candidate, rejection = _candidate(
                row,
                symbol,
                candles,
                interval,
                cfg,
                cash,
                equity,
                exposure_pct,
                exposure_open,
                budget,
                threshold,
                allow_rules_only,
                fee_rate,
                slip_rate,
            )
            if rejection is not None:
                considered.append(rejection)
                continue
            # Best by the analyst's own score, so the choice is not invented
            # either. Ties fall to the earlier symbol, which keeps the result
            # deterministic.
            if best is None or _score_of(candidate) > _score_of(best):
                best = candidate

    if best is not None:
        best.update(
            {
                "mode": mode,
                "considered": considered,
                "analyst_available": bool((best["jev_verdict"] or {}).get("available")),
                # Set only by an explicit operator override. The plan still
                # carries every number and its reasoning, but nothing here
                # reviewed it, so the journal and the dashboard must say so.
                "analyst_bypassed": bool(allow_rules_only and not (best["jev_verdict"] or {}).get("available")),
                "brackets_attached": _brackets_supported(),
                "cash": cash,
                "equity": equity,
                # Carried so execute() can restate equity from the balance that
                # actually exists after the fill, rather than reporting the
                # pre-trade figure next to a post-trade one.
                "unrealized": unrealized,
                "risk_budget": budget,
                "take_threshold": threshold,
            }
        )
        return best

    reason = _overall_refusal(considered, blocked)
    refused = _blank(
        refusal_reason=reason,
        refusal_detail=_detail_for(considered, reason),
        mode=mode,
        considered=considered,
        analyst_available=False,
        analyst_bypassed=False,
        brackets_attached=_brackets_supported(),
        cash=cash,
        equity=equity,
        unrealized=unrealized,
        risk_budget=budget,
        take_threshold=threshold,
    )
    db.log("plan_refused", {"reason": reason, "considered": considered})
    return refused


def _score_of(candidate: dict) -> float:
    verdict = candidate.get("jev_verdict") or {}
    return float(verdict.get("p_take") or 0.0)


def _strategies_for(symbol: str, interval: str | None = None) -> list:
    """Every active strategy registered for ``symbol``, whatever its id looks like.

    A strategy id is ``<family>-<SYMBOL>-<interval>`` and the interval is part of
    it, not a note beside it. Matching a strategy to a symbol by taking everything
    after the first dash could therefore only ever see the legacy
    ``<family>-<SYMBOL>`` shape and was blind to every strategy registered with
    the timeframe it was validated on -- which research has written for a long
    time. Research papered over that by also registering a duplicate "mirror" row
    under the legacy id, so each symbol had two planable strategies and research
    was maintaining rows that existed only to be visible. The rows are gone now
    (``engine.purge_mirror_strategies``); the id is parsed the way the engine
    parses it, in one place, so there is a single definition of what an id means.

    Rows validated on the timeframe being planned are returned first. That is a
    preference, not a filter: a strategy validated on another timeframe is still
    returned rather than silently dropped, and the plan carries the interval it
    was validated on so a reader can see which is which.
    """
    with db.conn() as c:
        rows = c.execute("SELECT * FROM strategies WHERE status='active'").fetchall()
    wanted = str(symbol or "").strip().upper()
    ranked = []
    for row in rows:
        _family, parsed_symbol, parsed_interval = engine.parse_strategy_id(str(row["id"]))
        if str(parsed_symbol or "").strip().upper() != wanted:
            continue
        if parsed_interval == interval:
            rank = 0  # validated on the timeframe being planned
        elif parsed_interval is None:
            rank = 1  # registered before ids carried a timeframe: no claim to match
        else:
            rank = 2
        ranked.append((rank, row))
    return [row for _rank, row in sorted(ranked, key=lambda pair: pair[0])]


def _candidate(
    row,
    symbol,
    candles,
    interval,
    cfg,
    cash,
    equity,
    exposure_pct,
    exposure_open,
    budget,
    threshold,
    allow_rules_only,
    fee_rate,
    slip_rate,
):
    """Turn one strategy row into a plan, or a rejection. Exactly one is not None."""
    strategy_id = row["id"]
    family = row["family"]
    metrics = _metrics_of(row["metrics"])

    passed, why = validate_metrics(metrics)
    if not passed:
        return None, _reject(
            symbol, strategy_id, FAILED_VALIDATION, why, metrics=metrics, strategy_validated=False
        )

    try:
        params = json.loads(row["params"] or "{}")
    except (TypeError, ValueError):
        params = {}
    if family not in FAMILIES:
        return None, _reject(symbol, strategy_id, "unknown_family", f"family '{family}' is not implemented")

    try:
        signal = FAMILIES[family](candles, **params)
    except TypeError:
        return None, _reject(symbol, strategy_id, "bad_params", f"params {params} are not accepted by {family}")
    if signal not in ("BUY", "SELL"):
        return None, _reject(
            symbol, strategy_id, "no_setup", f"{family} has no signal on the latest candles"
        )

    # The direction is the strategy's, full stop. Nothing below can change it.
    side = signal

    last = float(candles[-1]["close"])
    entry = last * (1 + slip_rate)
    stop = entry * (1 - STOP_PCT) if side == "BUY" else entry * (1 + STOP_PCT)
    target = entry * (1 + TARGET_PCT) if side == "BUY" else entry * (1 - TARGET_PCT)

    facts = {
        "interval": interval,
        "regime": _regime(candles),
        "volatility": _volatility(_closes(candles)),
        "volume_trend": _volume_trend(candles),
        "range_position": _range_position(candles),
        "range_window": REGIME_WINDOW,
        "volume_window": VOL_WINDOW,
    }

    setup = {
        "strategy_id": strategy_id,
        "family": family,
        "params": params,
        "metrics": {
            "trades": _as_float(metrics.get("trades")),
            "net_pnl": _as_float(metrics.get("net_pnl")),
            "max_drawdown": _as_float(metrics.get("max_drawdown")),
            "fees": _as_float(metrics.get("fees")),
        },
    }
    state = _state_string(symbol, side, setup, facts, entry, stop, target)
    verdict = _ask_analyst(state)
    db.log(
        "jev",
        {"symbol": symbol, "state": state, "available": bool(verdict), "p_take": (verdict or {}).get("p_take")},
    )

    if verdict is None:
        if not allow_rules_only:
            return None, _reject(
                symbol,
                strategy_id,
                ANALYST_UNAVAILABLE,
                (
                    _last_analyst_failure
                    or "the analyst did not answer, so there is no reasoned opinion to trade on"
                ),
                metrics=setup["metrics"],
                strategy_validated=True,
            )
        verdict = {"available": False, "p_take": None, "p_quality": None, "quality_label": "unknown"}
    elif verdict["p_take"] < threshold:
        return None, _reject(
            symbol,
            strategy_id,
            LOW_TAKE_PROBABILITY,
            f"p(take) {verdict['p_take']:.2f} is below the {threshold:.2f} gate",
            metrics=setup["metrics"],
            p_take=verdict["p_take"],
            strategy_validated=True,
        )

    risk_per_unit = abs(entry - stop)
    if risk_per_unit <= 0 or entry <= 0:
        return None, _reject(symbol, strategy_id, "invalid_price", "entry or stop resolved to zero")

    # The exposure room is a ceiling the engine sizes inside, and a refusal in its
    # own right: an account already at its cap cannot open anything, whatever the
    # risk budget says it could afford.
    room = equity * exposure_pct - exposure_open
    if room <= 0:
        return None, _reject(
            symbol,
            strategy_id,
            EXPOSURE_CAP,
            f"open exposure {exposure_open:.2f} USD already uses the "
            f"{exposure_pct * 100:.0f}% cap on {equity:.2f} USD equity",
            metrics=setup["metrics"],
        )

    # One sizing rule: the quantity whose stop costs the risk budget, capped by the
    # per-position notional ceiling, the exposure room and the cash actually left.
    # The budget is the intent; a cap may make the position smaller, and if what is
    # left is too small to be a trade it is refused with the reason rather than
    # filled as dust.
    size = engine.plan_size(
        cfg,
        entry,
        side,
        risk_budget=budget,
        stop_price=stop,
        exposure_room=room,
    )
    qty = size.qty
    if qty <= 0:
        return None, _reject(
            symbol,
            strategy_id,
            _SIZE_REFUSALS.get(size.reason, size.reason),
            f"sizing refused: {size.detail}",
            metrics=setup["metrics"],
            cash=cash,
            risk_budget=budget,
        )

    # The engine sized the position; these two checks stay as the planner's own
    # backstop, so a cap added later cannot quietly let an over-cap or unfunded
    # position through.
    notional = qty * entry
    if notional > room:
        return None, _reject(
            symbol,
            strategy_id,
            EXPOSURE_CAP,
            f"{notional:.2f} USD position exceeds the {room:.2f} USD of exposure room left",
            metrics=setup["metrics"],
        )
    # A buy has to pay notional plus fee out of the balance; a short only pays the
    # fee out of it, because the notional is posted as margin -- and plan_size
    # already refused unless that whole notional was free.
    cost = entry * qty * ((1 + fee_rate) if side == "BUY" else fee_rate)
    if cost > cash:
        return None, _reject(
            symbol,
            strategy_id,
            INSUFFICIENT_CASH,
            f"{cash:.2f} USD available cannot fund {cost:.2f} USD",
            metrics=setup["metrics"],
        )

    # Every figure below is recomputed from the FINAL quantity, after every cap,
    # so the plan reports the risk the placed order actually carries. risk_usd is
    # therefore at most the budget and never rounds away to 0.00, and rr is the
    # multiple the stop and target distances actually imply rather than a ratio of
    # two rounded dollar figures.
    risk_usd = risk_per_unit * qty
    reward_usd = abs(target - entry) * qty
    rr = abs(target - entry) / risk_per_unit
    built = _blank(
        symbol=symbol,
        side=side,
        qty=qty,
        entry_price=entry,
        stop_loss=stop,
        take_profit=target,
        risk_usd=round(risk_usd, 6),
        reward_usd=round(reward_usd, 6),
        rr=round(rr, 4),
        strategy_id=strategy_id,
        strategy_interval=_strategy_interval(strategy_id),
        metrics=setup["metrics"],
        jev_verdict={k: v for k, v in verdict.items() if k != "raw"},
        strategy_params=setup["params"],
        setup_family=family,
        facts=dict(facts),
    )
    built["reasoning"] = _reasoning(
        symbol,
        side,
        setup,
        facts,
        built["jev_verdict"],
        built["jev_verdict"].get("p_take"),
        threshold,
        built,
    )
    return built, None


def _strategy_interval(strategy_id) -> str | None:
    """The timeframe a strategy was validated on, or None for a legacy row."""
    return engine.parse_strategy_id(str(strategy_id))[2]


def _state_string(symbol, side, setup, facts, entry, stop, target) -> str:
    """The analyst's state string: only values computed in this process."""
    metrics = setup["metrics"]
    volume = facts["volume_trend"]
    return (
        f"Symbol: {symbol}\n"
        f"Setup: {setup['family']} params={json.dumps(setup['params'], sort_keys=True)}\n"
        f"Direction the strategy produced: {side} (not negotiable)\n"
        f"Entry {entry:.4f}, stop {stop:.4f}, target {target:.4f}\n"
        f"Backtest on this setup: {metrics['trades']:g} trades, net_pnl "
        f"{metrics['net_pnl']:+.2f} USD, max_drawdown {metrics['max_drawdown']:.2f} USD, "
        f"fees {metrics['fees']:.2f} USD\n"
        f"Timeframe: {facts['interval']} candles\n"
        f"Recent regime: {facts['regime']}\n"
        f"Bar-to-bar volatility: {facts['volatility'] * 100:.2f}%\n"
        f"Volume trend: {'unknown (no volume in candles)' if volume is None else f'{volume:.2f}x the prior {facts['volume_window']} bars'}\n"
        f"Price position in the last {facts['range_window']} bars: "
        f"{_pct_of_range(facts['range_position'])}\n"
        "Question: is this a trade worth taking, and is the setup strong or weak?"
    )


def _unrealized() -> float:
    """Open P&L, reusing the engine's own marking so the numbers agree."""
    try:
        _, unrealized = engine.mark_open_positions()
    except Exception:
        logger.warning("could not mark open positions; treating unrealized as zero")
        return 0.0
    return _as_float(unrealized)


def _brackets_supported() -> bool:
    """Whether the engine can rest reduce-only paper exits.

    It cannot today: the paper engine writes the stop and target onto the
    position row and the worker enforces them on the next cycle, but there is
    no resting-order path. The plan says so rather than claiming brackets.
    """
    return hasattr(engine, "attach_paper_exits")


def _overall_refusal(considered: list[dict], blocked: bool) -> str:
    if not considered:
        return NO_EDGE
    reasons = {r["reason"] for r in considered}
    for candidate in (DAILY_LOSS_LIMIT, STALE_DATA, EXPOSURE_CAP, INSUFFICIENT_CASH,
                      SYMBOL_ALREADY_OPEN, FAILED_VALIDATION, NO_STRATEGIES):
        if candidate in reasons:
            return candidate
    if reasons and reasons <= {ANALYST_UNAVAILABLE, LOW_TAKE_PROBABILITY, "no_setup",
                               "insufficient_data", "bad_params", "unknown_family",
                               "invalid_price", RISK_BUDGET, BELOW_MIN_SIZE,
                               "capped_by_config"}:
        return sorted(reasons)[0]
    return NO_EDGE


def _detail_for(considered: list[dict], reason: str) -> str:
    parts = [f"{r['strategy_id'] or r['symbol']}: {r['detail']}" for r in considered if r["reason"] == reason]
    if not parts:
        parts = [f"{r['strategy_id'] or r['symbol']}: {r['detail']}" for r in considered]
    return "; ".join(parts[:5]) if parts else reason


# -------------------------------------------------------------------- execute


def execute(plan: dict) -> dict:
    """Place the paper order behind a clean plan. Refusals return, never raise."""
    if not isinstance(plan, dict):
        return {"status": "refused", "refusal_reason": BAD_REQUEST, "detail": "plan was not a mapping"}
    if plan.get("refusal_reason"):
        return {
            "status": "refused",
            "refusal_reason": plan["refusal_reason"],
            "detail": plan.get("refusal_detail", ""),
            "executed": False,
        }
    if not plan.get("analyst_available") and not plan.get("analyst_bypassed"):
        return {
            "status": "refused",
            "refusal_reason": ANALYST_UNAVAILABLE,
            "detail": "the plan carries no analyst opinion, so it is not executed",
            "executed": False,
        }
    if not plan.get("qty") or not plan.get("entry_price"):
        return {
            "status": "refused",
            "refusal_reason": BAD_REQUEST,
            "detail": "the plan carries no size or entry price",
            "executed": False,
        }

    db.init()
    db.ensure_column("positions", "mark", "REAL")
    symbol = plan["symbol"]
    side = plan["side"]
    qty = float(plan["qty"])
    entry = float(plan["entry_price"])
    stop = float(plan["stop_loss"])
    target = float(plan["take_profit"])
    strategy_id = plan.get("strategy_id")

    try:
        with db.conn() as c:
            already = c.execute(
                "SELECT symbol FROM positions WHERE symbol=? AND status='open'", (symbol,)
            ).fetchone()
            if already:
                return _refuse(SYMBOL_ALREADY_OPEN, f"{symbol} already holds an open position")
            cfg = db.get_all(c)
            cash = engine.available_cash(cfg)
            fee = entry * qty * engine._fee_rate(cfg)
            cost = entry * qty + fee if side == "BUY" else fee
            if cost > cash:
                return _refuse(
                    INSUFFICIENT_CASH,
                    f"{cash:.2f} USD available cannot fund {cost:.2f} USD",
                )
            # Cash moves on the fill, inside this transaction, exactly as the
            # engine's own signal path does it: a buy debits notional plus fee,
            # a short entry credits the proceeds and posts the notional as
            # margin. Both go through db.move_cash, so there is one balance.
            delta = -(entry * qty + fee) if side == "BUY" else (entry * qty - fee)
            new_cash = db.move_cash(delta, c)
            locked = float(cfg.get("short_margin_locked", 0.0))
            if side == "SELL":
                locked = locked + entry * qty
                db.set_margin(locked, c)
            order_id = "plan-" + uuid.uuid4().hex[:8]
            now = time.time()
            c.execute(
                "INSERT INTO orders VALUES(?,?,?,?,?,?,?,?,?,?)",
                (order_id, symbol, side, qty, "MARKET", "filled", now, now, strategy_id,
                 plan.get("reasoning", "")[:200] or "planner"),
            )
            c.execute(
                "INSERT INTO fills(order_id,symbol,side,qty,price,fee,slippage,ts) VALUES(?,?,?,?,?,?,?,?)",
                (order_id, symbol, side, qty, entry, fee, entry * engine._slip_rate(cfg), now),
            )
            c.execute(
                "INSERT OR REPLACE INTO positions"
                "(symbol,side,qty,entry,opened,strategy_id,sl,tp,status,mark) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (symbol, side, qty, entry, now, strategy_id, stop, target, "open", entry),
            )
    except Exception as exc:
        logger.exception("planner execute failed for %s", symbol)
        return _refuse("execution_error", f"{type(exc).__name__}: {exc}")

    # Equity after the fill, on the account's own convention:
    #     equity = cash + unrealized - short_margin_locked
    # The new position is written at its entry mark, so it contributes exactly
    # zero unrealized and the open P&L the plan measured carries over untouched.
    # Reporting the plan's pre-trade equity next to the post-trade cash is what
    # made a filled position read as "equity 1000, cash 499.80".
    open_pnl = _as_float(plan.get("unrealized"), 0.0)
    equity_after = new_cash + open_pnl - locked
    _record_equity_after_entry()

    result = {
        "status": "filled",
        "executed": True,
        "order_id": order_id,
        "symbol": symbol,
        "side": side,
        "qty": qty,
        "price": entry,
        "fee": round(fee, 8),
        "cash": round(new_cash, 6),
        "equity": round(equity_after, 6),
        "short_margin_locked": round(locked, 6),
        "risk_usd": plan.get("risk_usd"),
        "reward_usd": plan.get("reward_usd"),
        "stop_loss": stop,
        "take_profit": target,
        "strategy_id": strategy_id,
        "reasoning": plan.get("reasoning", ""),
        # True only when real resting reduce-only exits were attached. They are
        # not, so this stays False and the plan says so rather than implying a
        # bracket is watching the position.
        "brackets_attached": False,
        "bracket_note": (
            "the paper engine cannot rest reduce-only exits, so the stop and target "
            "live on the position row and are checked by manage_open_positions on "
            "every worker cycle"
        ),
    }
    recorded = dict(plan)
    # The journal row carries the balance as it stands after the fill, so the
    # numbers in one record describe one moment.
    recorded["cash"] = round(new_cash, 6)
    recorded["equity"] = round(equity_after, 6)
    recorded["unrealized"] = round(open_pnl, 6)
    recorded["executed"] = True
    recorded["execution"] = {k: v for k, v in result.items() if k != "reasoning"}
    db.log("plan_executed", recorded)
    return result


def _record_equity_after_entry() -> None:
    """Append the equity row for a fresh entry, best effort.

    Only ``engine.trading_cycle`` used to do this, and the worker no longer runs
    that path: every entry goes through the planner. Without it a filled position
    left no equity row at all, so the equity curve stalled at the previous value,
    peak equity never advanced, and the daily-loss guard -- which reads that same
    table -- stopped seeing the loss it exists to stop. A failure here must never
    cost a position that is already open, so it is logged and swallowed.
    """
    try:
        engine._record_equity()
    except Exception:
        logger.exception("could not record equity after an entry; the position is open")


def _refuse(reason: str, detail: str) -> dict:
    db.log("plan_execute_refused", {"reason": reason, "detail": detail})
    return {"status": "refused", "refusal_reason": reason, "detail": detail, "executed": False}


# ---------------------------------------------------------------------- exits
#
# Exit accounting convention
# --------------------------
# An exit is closed through ``engine._close``, the same function the engine's own
# signal path uses, rather than through a second implementation written here. That
# function already holds the one convention this account has:
#
#   * closing a long sells, so cash is credited ``price*qty - fee``; covering a
#     short buys, so cash is debited ``price*qty + fee`` and the entry notional
#     comes off ``short_margin_locked``;
#   * ``realized_total`` carries the gross P&L and ``fees_total`` the exit fee, and
#     the *net* of the two is what the journal and the caller are told;
#   * an order and a fill row are written for the exit, the fill on the side that
#     closes the position, and the position row is marked closed.
#
# Two things are deliberately NOT done here:
#
#   * ``engine.plan_size`` is never used on the way out. It is entry sizing: it
#     shrinks with the free cash and refuses outright when the balance cannot
#     fund the configured cap. Running it on an exit would leave a fraction of
#     the position open (or skip the exit entirely) exactly when the account is
#     most likely to be short of cash. An exit is always the full position qty.
#   * no slippage is applied to the exit price. ``engine._close`` takes the price
#     it is given, and the engine's own caller passes the observed price; so a
#     stop or target fill happens at that level and everything else at the mark.
#     The record carries the mark alongside the fill price so a gap is visible.
#
# The kill switch is authoritative. A halted account places no orders at all, and
# an exit is an order, so while the switch is set no open position is managed:
# stops, targets and the max-hold limit are all left alone until ``--resume``.
# That is a deliberate choice -- the switch is the operator's "hands off", and an
# exit that ignores it would be trading against the one instruction that must
# never be second-guessed -- and it is reported rather than done quietly: a
# halted cycle writes a decision naming the positions it is not managing, so an
# open position with nothing watching it is visible instead of merely forgotten.

# Why a position is closed. Named so the worker, the CLI and the tests all read
# the same word.
EXIT_STOP = "stop"
EXIT_TARGET = "target"
EXIT_MAX_HOLD = "max_hold"
EXIT_STRATEGY_SIGNAL = "strategy_signal"
EXIT_MANUAL = "manual"
EXIT_REASONS = (EXIT_STOP, EXIT_TARGET, EXIT_MAX_HOLD, EXIT_STRATEGY_SIGNAL, EXIT_MANUAL)

# Journal kinds. ``position_exited`` is the record of an exit; the other two are
# the outcomes that are not an exit and would otherwise be invisible.
KIND_EXITED = "position_exited"
KIND_EXIT_SKIPPED = "position_exit_skipped"
KIND_EXIT_REFUSED = "position_exit_refused"
KIND_EXIT_HALTED = "position_exit_halted"

# The max-hold limit, in minutes, as a config key of its own so an operator can
# change it without touching the brackets a plan writes. 15 minutes is roughly
# three candles on the default 5m timeframe: a position that has not resolved by
# then is not the trade the backtest described.
KEY_MAX_HOLD_MINUTES = "max_hold_minutes"
DEFAULT_MAX_HOLD_MINUTES = 15.0

# Refusal reasons for an exit that was asked for but could not happen.
SYMBOL_NOT_OPEN = "symbol_not_open"
HALTED = "kill_switch"
NO_MARK = "no_mark"

# Below this, a strategy is not re-run for an exit decision: a signal from a
# handful of bars is noise, and a wrong exit is worse than a late one.
MIN_SIGNAL_CANDLES = 30


def max_hold_seconds(cfg: dict) -> float:
    """The max-hold limit in seconds, read from the config table."""
    minutes = _as_float(cfg.get(KEY_MAX_HOLD_MINUTES), DEFAULT_MAX_HOLD_MINUTES)
    if minutes <= 0:
        minutes = DEFAULT_MAX_HOLD_MINUTES
    return minutes * 60.0


def _halted() -> bool:
    """Whether the kill switch is set. Tolerant: a config read failure is not a halt."""
    try:
        return bool(config.is_halted())
    except Exception:
        logger.warning("could not read the kill switch; treating it as set (no exits)")
        return True


def _open_rows(symbols=None) -> list:
    """Every open position, not only the watchlist's.

    A symbol that leaves the watchlist while a position is open is still held, so
    it is still managed: otherwise the position would be orphaned, with a stop on
    its row and nothing that ever reads it. ``symbols`` narrows the set only when
    a caller asks for it.
    """
    with db.conn() as c:
        rows = c.execute("SELECT * FROM positions WHERE status='open'").fetchall()
    if not symbols:
        return list(rows)
    wanted = {str(s).strip().upper() for s in symbols}
    return [r for r in rows if str(r["symbol"]).upper() in wanted]


def _live_marks() -> dict:
    """Marks for every open position, keyed by symbol.

    ``engine.mark_open_positions`` is the engine's own marking pass, so the price
    an exit decides on is the price the dashboard shows. A failure here is not
    fatal: the caller falls back to the last mark stored on the row.
    """
    try:
        marked, _unrealized_total = engine.mark_open_positions()
        return {str(m["symbol"]).upper(): m for m in marked if m.get("symbol")}
    except Exception:
        logger.warning("could not mark open positions for exit management")
        return {}


def _mark_for(row, marks: dict) -> tuple[float | None, bool]:
    """The price to judge this position against, and whether it is live.

    Falls back to the mark stored on the row (or the entry) so a provider outage
    still lets a bracket be enforced on the last real price, labelled as not live.
    """
    key = str(row["symbol"]).upper()
    marked = marks.get(key)
    if marked and marked.get("mark") is not None:
        return float(marked["mark"]), bool(marked.get("mark_live"))
    fallback = None
    if "mark" in row.keys() and row["mark"] is not None:
        fallback = float(row["mark"])
    if fallback is None:
        fallback = float(row["entry"])
    return fallback, False


def _opposite_signal(row) -> tuple[str | None, str]:
    """Whether the position's own strategy now points the other way.

    Returns ``(signal, detail)``. ``signal`` is ``None`` whenever the answer
    cannot be trusted -- no strategy row, an unknown family, no usable candles --
    because an exit fired on a guess is worse than no signal exit at all. The
    strategy id carries the timeframe it was validated on, and it is run on that
    one: a setup tuned for 15m candles is a different strategy fed 5m ones.
    """
    sid = row["strategy_id"]
    if not sid:
        return None, "the position records no owning strategy"
    family, symbol, interval = engine.parse_strategy_id(str(sid))
    if not family or family not in FAMILIES:
        return None, f"strategy {sid!r} names no implemented family"
    with db.conn() as c:
        registered = c.execute(
            "SELECT params FROM strategies WHERE id=?", (str(sid),)
        ).fetchone()
    if registered is None:
        return None, f"strategy {sid!r} is no longer registered"
    try:
        params = json.loads(registered["params"] or "{}")
    except (TypeError, ValueError):
        params = {}
    candles = engine._candles(symbol or row["symbol"], interval or engine.resolve_interval())
    if len(candles) < MIN_SIGNAL_CANDLES:
        return None, f"only {len(candles)} candles for {sid}, so no signal is trusted"
    try:
        signal = FAMILIES[family](candles, **params)
    except Exception as exc:
        logger.warning("strategy %s could not be re-run for an exit: %s", sid, exc)
        return None, f"strategy {sid!r} could not be re-run"
    if signal not in ("BUY", "SELL"):
        return None, f"{sid} has no signal on the latest candles"
    return signal, f"{sid} now signals {signal}"


def _exit_reason(row, mark: float, cfg: dict, now: float) -> tuple[str | None, float | None, str]:
    """Why this position should close, and at what price.

    Returns ``(reason, price, detail)``. ``reason is None`` means hold. The order
    is fixed and deliberate: a stop and a target are the levels the position was
    opened with and are checked first, then the clock, then the strategy. A
    bracket the operator can see outranks a judgement the engine makes up.
    """
    side = str(row["side"])
    stop = float(row["sl"]) if row["sl"] is not None else 0.0
    target = float(row["tp"]) if row["tp"] is not None else 0.0

    # A long is stopped from below and targeted from above; a short is the mirror.
    if stop > 0 and ((side == "BUY" and mark <= stop) or (side == "SELL" and mark >= stop)):
        return EXIT_STOP, stop, f"mark {mark:.6f} reached the stop at {stop:.6f}"
    if target > 0 and ((side == "BUY" and mark >= target) or (side == "SELL" and mark <= target)):
        return EXIT_TARGET, target, f"mark {mark:.6f} reached the target at {target:.6f}"

    opened = float(row["opened"] or 0.0)
    limit = max_hold_seconds(cfg)
    held = now - opened if opened > 0 else 0.0
    if limit > 0 and held >= limit:
        return EXIT_MAX_HOLD, mark, f"held {held / 60.0:.1f} min against a {limit / 60.0:.1f} min limit"

    signal, detail = _opposite_signal(row)
    if signal and signal != side:
        return EXIT_STRATEGY_SIGNAL, mark, f"{detail}, the position is {side}"

    return None, None, ""


def _close_row(row, price: float, reason: str, mark: float, mark_live: bool, detail: str) -> dict:
    """Close one open position in full and return the record of it.

    The whole row quantity goes out -- never a fraction sized off the free cash
    -- and the close itself is ``engine._close``, so cash, the realised and fee
    totals, the margin release, the order and the fill all move by exactly the
    convention an entry used. This function adds the journal row and the record
    the caller gets back; it does not reimplement the accounting.
    """
    symbol = str(row["symbol"])
    side = str(row["side"])
    qty = float(row["qty"])
    entry = float(row["entry"])
    strategy_id = row["strategy_id"]
    held = time.time() - float(row["opened"] or 0.0)
    gross = (price - entry) * qty if side == "BUY" else (entry - price) * qty

    try:
        with db.conn() as c:
            net = engine._close(c, row, price, strategy_id)
    except Exception as exc:
        logger.exception("exit of %s failed", symbol)
        db.log(KIND_EXIT_REFUSED, {"symbol": symbol, "reason": reason, "error": f"{type(exc).__name__}: {exc}"})
        return {}

    if net is None:
        # engine._close refused: covering a short would take the balance below
        # zero. It logged paper_refused itself, so this only names the exit.
        db.log(
            KIND_EXIT_REFUSED,
            {
                "symbol": symbol,
                "side": side,
                "qty": qty,
                "exit_price": round(price, 6),
                "reason": reason,
                "detail": "the close was refused, so the position is still open and still managed",
                "strategy_id": strategy_id,
            },
        )
        return {}

    cfg = db.get_all()
    fee = price * qty * engine._fee_rate(cfg)
    cash, _locked = engine._cash_state(cfg)
    record = {
        "status": "closed",
        "executed": True,
        "symbol": symbol,
        "side": side,
        "qty": qty,
        "entry": entry,
        "exit_price": round(price, 6),
        "mark": round(mark, 6),
        "mark_live": mark_live,
        "stop_loss": row["sl"],
        "take_profit": row["tp"],
        "reason": reason,
        "detail": detail,
        "gross_pnl": round(gross, 6),
        "fee": round(fee, 8),
        # Net of the exit fee, which is the figure a reader wants. The engine's
        # own totals keep gross P&L and fees apart, as they always have.
        "realized_pnl": round(net, 6),
        "strategy_id": strategy_id,
        "held_seconds": round(held, 3),
        "cash": round(cash, 6),
    }
    db.log(KIND_EXITED, dict(record))
    return record


def manage_open_positions(symbols=None) -> list[dict]:
    """Close every open position whose exit has triggered. Returns one record each.

    This is the other half of the planner and it runs every monitoring cycle,
    before any new trade is considered: a stop that is only acted on once
    something else has been planned is not a stop. It walks every open position
    rather than the watchlist's, so a position whose symbol has been dropped from
    the watchlist is still managed.

    One bad position -- a missing bracket, an unpriceable mark, a strategy that
    no longer runs -- is skipped and journalled, never raised: the remaining
    positions must still be managed.
    """
    db.init()
    db.ensure_column("positions", "mark", "REAL")
    now = time.time()
    cfg = db.get_all()

    rows = _open_rows(symbols)
    if not rows:
        return []

    if _halted():
        # Authoritative: no orders at all while halted, exits included. Said out
        # loud, because it means an open position is unmanaged until --resume.
        open_names = [str(r["symbol"]) for r in rows]
        db.log(
            KIND_EXIT_HALTED,
            {
                "reason": HALTED,
                "open_positions": open_names,
                "detail": (
                    "the kill switch is set, so no exit is placed either; these positions "
                    "are unmanaged until the worker is resumed"
                ),
            },
        )
        logger.warning(
            "kill switch is set: %d open position(s) are not being managed until --resume",
            len(open_names),
        )
        return []

    marks = _live_marks()
    closed: list[dict] = []

    for row in rows:
        try:
            mark, mark_live = _mark_for(row, marks)
            if mark is None or mark <= 0:
                db.log(
                    KIND_EXIT_SKIPPED,
                    {"symbol": row["symbol"], "reason": NO_MARK, "detail": "no price to judge it against"},
                )
                continue
            reason, price, detail = _exit_reason(row, mark, cfg, now)
            if reason is None:
                continue
            record = _close_row(row, float(price), reason, mark, mark_live, detail)
            if record:
                closed.append(record)
        except Exception:
            # One position must never stop the others from being managed.
            logger.exception("could not manage the open position %s", row["symbol"])
            db.log(KIND_EXIT_SKIPPED, {"symbol": row["symbol"], "detail": "managing this position failed"})
    return closed


def close_position(symbol: str, reason: str = EXIT_MANUAL) -> dict:
    """Close one named position in full, on purpose. Returns the exit record.

    This is the operator's way out and the agent's: a deliberate exit by name,
    judged by nothing but the live mark, journalled with the reason it was asked
    for. The kill switch applies here exactly as it does in
    :func:`manage_open_positions` -- halted means no orders, so this refuses and
    says so.
    """
    db.init()
    db.ensure_column("positions", "mark", "REAL")
    wanted = str(symbol or "").strip().upper()
    if not wanted:
        return {"status": "refused", "refusal_reason": BAD_REQUEST, "detail": "no symbol was named", "executed": False}

    if _halted():
        db.log(
            KIND_EXIT_HALTED,
            {"symbol": wanted, "reason": HALTED, "detail": "the kill switch is set, so no exit was placed"},
        )
        return {
            "status": "refused",
            "symbol": wanted,
            "refusal_reason": HALTED,
            "executed": False,
            "detail": (
                "trading is halted, so no exit was placed either. The position is still "
                "open and unmanaged; resume the worker first."
            ),
        }

    matches = [r for r in _open_rows([wanted]) if str(r["symbol"]).upper() == wanted]
    if not matches:
        db.log(
            KIND_EXIT_REFUSED,
            {"symbol": wanted, "reason": SYMBOL_NOT_OPEN, "detail": "no open position carries that symbol"},
        )
        return {
            "status": "refused",
            "symbol": wanted,
            "refusal_reason": SYMBOL_NOT_OPEN,
            "executed": False,
            "detail": f"{wanted} does not hold an open paper position",
        }

    asked = str(reason or EXIT_MANUAL).strip().lower() or EXIT_MANUAL
    row = matches[0]
    mark, mark_live = _mark_for(row, _live_marks())
    if mark is None or mark <= 0:
        return {
            "status": "refused",
            "symbol": wanted,
            "refusal_reason": NO_MARK,
            "executed": False,
            "detail": f"there is no price for {wanted}, so it was not closed",
        }
    record = _close_row(
        row,
        mark,
        asked if asked in EXIT_REASONS else EXIT_MANUAL,
        mark,
        mark_live,
        f"{wanted} was closed by request",
    )
    if not record:
        return {
            "status": "refused",
            "symbol": wanted,
            "refusal_reason": "exit_failed",
            "executed": False,
            "detail": f"the close of {wanted} could not be completed, so it is still open",
        }
    return record
