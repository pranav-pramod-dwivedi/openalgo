"""An independent check on every paper trade, run before it can be placed.

The planner is our own code, and that is not evidence. It builds the entry price
from the last close, the quantity from a risk budget, the risk and reward from
that quantity, and the analyst's opinion from a network call. A bug, a bad
assumption or a silent field rename in any of those steps produces a plan whose
numbers are internally plausible and completely invented. Nothing downstream can
catch that: the engine places the order from the same fields the planner wrote.

So this module re-derives what can be re-derived and refuses the rest:

1. **Price plausibility.** The plan's entry, stop and target must sit within a
   configurable band of a real live quote for that symbol. This is the
   anti-hallucination check: a price nobody can find in the market is not a
   price. The quote comes from
   :func:`services.foreign_data_service.get_foreign_quote`, which returns None
   when the feed cannot be reached. There is **no** default price, no last-known
   fallback, no mid, no average and no cached constant anywhere in this module,
   because one such number would be exactly the thing the check exists to catch.
   No live price means the trade is refused (``data_unavailable``), never
   approved against a guess.
2. **Freshness.** The candles behind the setup are re-fetched and the newest one
   age-checked. A setup read off yesterday's candles is not today's setup. The
   candles come from the provider directly rather than through the planner's own
   helper, so a change in the data path this checks cannot go unnoticed.
3. **Arithmetic.** ``|entry - stop| * qty`` must equal ``risk_usd``,
   ``|target - entry| * qty`` must equal ``reward_usd``, ``rr`` must equal
   reward over risk, and the stop must be on the losing side of the entry for the
   stated side with the target on the winning side. These are exactly the fields
   that could be invented, so each is re-computed rather than trusted.
4. **Sanity.** No ``None``, ``NaN``, infinite, negative or zero where a positive
   number is required; the symbol must be a real Binance USDT pair written the
   way the exchange writes it; the quantity must be positive and inside the
   configured ceiling.
5. **Provenance.** ``strategy_id`` must name a row in the ``strategies`` table,
   that row must be active, and the metrics *recorded on the row* must clear the
   validation bar. The plan's own copy of those metrics is compared against the
   row's, because a plan quoting a better record than the registry holds is
   quoting a record that does not exist.
6. **No fabricated analyst.** A plan that claims the analyst answered must carry
   a verdict with a probability in ``[0, 1]``. A plan that claims no opinion must
   say so honestly and be explicitly bypassed, or it is not a tradeable plan. A
   plan carrying a verdict while claiming no opinion, or stamped unreviewed while
   claiming to be reviewed, is refused: both are lies about the one part of the
   path that was not a backtest.
7. **Not already open.** A second position in a symbol that is already open.

Every check records its own named boolean and, when it fails, a stable reason
slug plus one plain sentence naming the actual numbers involved. The slugs are
constants and never change meaning; the sentences are written for a trader.

**It fails closed.** A verifier that cannot finish denies the trade and says the
checker itself failed. There is no path here that reaches ``allow=True`` because
something went wrong: one outer ``try`` catches every exception, including one
raised inside a check, and returns ``allow=False`` with the ``verifier_error``
slug.

Every verdict, allowed or denied, is written to the ``verdicts`` table so the
dashboard can show what was refused and why. If that write fails the trade is
still judged on its merits and the failure is recorded as an advisory reason:
refusing a sound trade because an audit row could not be written would punish the
trader for a disk problem, and the failure is logged either way.

Wiring it in, from :func:`services.paper.planner.execute`, after the planner's own
refusals and before the position is opened::

    verdict = verifier.verify(plan)
    if not verdict["allow"]:
        return _refuse(verdict["reason_slugs"][0], verdict["reasons"][0]["message"])
"""

from __future__ import annotations

import json
import math
import re
import time

from utils.logging import get_logger

from . import db, engine

logger = get_logger(__name__)

# --------------------------------------------------------------------------- #
# Severity
# --------------------------------------------------------------------------- #

# BLOCKING denies the trade by itself. ADVISORY is worth saying out loud and is
# recorded, but does not stop the order on its own: a claim that cannot be proved
# from the plan is a different thing from a claim that is provably wrong.
BLOCKING = "BLOCKING"
ADVISORY = "ADVISORY"
NONE = "NONE"

# --------------------------------------------------------------------------- #
# Reason slugs
# --------------------------------------------------------------------------- #
#
# Stable strings, read by the worker, the dashboard and the tests. A new check
# gets a new slug; an existing slug never changes meaning, because something is
# already counting it.

PLAN_MALFORMED = "plan_malformed"
ALREADY_REFUSED = "plan_already_refused"
SYMBOL_INVALID = "symbol_invalid"
SIDE_INVALID = "side_invalid"
NUMBERS_INVALID = "numbers_invalid"
QTY_ABOVE_CEILING = "qty_above_ceiling"
QTY_CEILING_UNKNOWN = "qty_ceiling_unavailable"
PRICE_NOT_PLAUSIBLE = "price_not_plausible"
DATA_UNAVAILABLE = "data_unavailable"
DATA_STALE = "data_stale"
DATA_UNVERIFIABLE = "data_unverifiable"
RISK_MISMATCH = "arithmetic_mismatch_risk"
REWARD_MISMATCH = "arithmetic_mismatch_reward"
RR_MISMATCH = "arithmetic_mismatch_rr"
BRACKET_SIDE_WRONG = "stop_or_target_on_wrong_side"
STOP_TOO_FAR = "stop_distance_implausible"
STRATEGY_UNKNOWN = "strategy_unknown"
STRATEGY_INACTIVE = "strategy_inactive"
STRATEGY_UNVALIDATED = "strategy_unvalidated"
STRATEGY_METRICS_MISMATCH = "strategy_metrics_mismatch"
STRATEGY_SETUP_MISMATCH = "strategy_setup_mismatch"
ANALYST_CLAIMED_WITHOUT_VERDICT = "analyst_claimed_without_verdict"
ANALYST_VERDICT_WITHOUT_CLAIM = "analyst_verdict_without_claim"
ANALYST_BYPASS_INCONSISTENT = "analyst_bypass_inconsistent"
ANALYST_UNAVAILABLE = "analyst_unavailable"
SYMBOL_ALREADY_OPEN = "symbol_already_open"
VERIFIER_ERROR = "verifier_error"
VERDICT_NOT_RECORDED = "verdict_not_recorded"
BRACKETS_NOT_RESTING = "brackets_claimed_but_not_resting"
ACCOUNT_MOVED = "account_moved_since_plan"

# Every slug this module can emit. The test suite asserts the constants and this
# tuple stay equal, so a typo in a literal cannot slip past.
REASON_SLUGS = (
    PLAN_MALFORMED,
    ALREADY_REFUSED,
    SYMBOL_INVALID,
    SIDE_INVALID,
    NUMBERS_INVALID,
    QTY_ABOVE_CEILING,

    PRICE_NOT_PLAUSIBLE,
    DATA_UNAVAILABLE,
    DATA_STALE,
    DATA_UNVERIFIABLE,
    RISK_MISMATCH,
    REWARD_MISMATCH,
    RR_MISMATCH,
    BRACKET_SIDE_WRONG,
    STOP_TOO_FAR,
    STRATEGY_UNKNOWN,
    STRATEGY_INACTIVE,
    STRATEGY_UNVALIDATED,
    STRATEGY_METRICS_MISMATCH,
    STRATEGY_SETUP_MISMATCH,
    ANALYST_CLAIMED_WITHOUT_VERDICT,
    ANALYST_VERDICT_WITHOUT_CLAIM,
    ANALYST_BYPASS_INCONSISTENT,
    ANALYST_UNAVAILABLE,
    SYMBOL_ALREADY_OPEN,
    VERIFIER_ERROR,
    VERDICT_NOT_RECORDED,
    BRACKETS_NOT_RESTING,
    ACCOUNT_MOVED,
)

# --------------------------------------------------------------------------- #
# Check names
# --------------------------------------------------------------------------- #
#
# The keys of the returned ``checks`` mapping. Named so a dashboard can show one
# line per check, and so a test can assert that one check failed rather than
# asserting on a whole verdict.

CHECK_PLAN_MAPPING = "plan_is_a_mapping"
CHECK_NOT_REFUSED = "plan_carries_no_refusal"
CHECK_SYMBOL = "symbol_is_a_real_usdt_pair"
CHECK_SIDE = "side_is_buy_or_sell"
CHECK_NUMBERS = "required_numbers_are_real"
CHECK_QTY = "qty_is_positive_and_within_the_ceiling"
CHECK_PRICE = "prices_sit_near_a_live_quote"
CHECK_FRESHNESS = "candles_behind_the_setup_are_recent"
CHECK_RISK = "risk_usd_matches_entry_stop_and_qty"
CHECK_REWARD = "reward_usd_matches_target_and_qty"
CHECK_RR = "rr_matches_reward_over_risk"
CHECK_SIDES = "stop_and_target_are_on_the_right_sides"
CHECK_STOP_DISTANCE = "stop_is_a_reachable_distance_from_entry"
CHECK_STRATEGY_EXISTS = "strategy_row_exists"
CHECK_STRATEGY_ACTIVE = "strategy_row_is_active"
CHECK_STRATEGY_VALIDATED = "recorded_metrics_clear_the_validation_bar"
CHECK_STRATEGY_METRICS = "plan_metrics_match_the_registered_row"
CHECK_STRATEGY_SETUP = "setup_matches_the_registered_row"
CHECK_ANALYST_VERDICT = "analyst_claim_is_carried_by_a_verdict"
CHECK_ANALYST_QUIET = "plan_claims_no_opinion_and_is_bypassed"
CHECK_ANALYST_OVERCLAIM = "plan_carries_no_verdict_it_does_not_claim"
CHECK_ANALYST_BYPASS = "bypass_agrees_with_the_verdict"
CHECK_NOT_OPEN = "symbol_is_not_already_open"
CHECK_BRACKETS = "bracket_claim_is_honest"
CHECK_BALANCE = "account_balance_has_not_moved_since_the_plan"
CHECK_RECORDED = "verdict_was_recorded"
CHECK_COMPLETED = "verifier_ran_to_the_end"

# --------------------------------------------------------------------------- #
# Tolerances
# --------------------------------------------------------------------------- #
#
# None of these is a price. They are how far a plan's own numbers may sit from the
# live market and from each other before the plan is refused. The entry band
# absorbs the gap between the last close the planner used and the live quote it
# never saw, plus ordinary spread; the stop and target sit further out than entry
# by design, so they get a wider band with a hard ceiling on top.

LIMITS: dict = {
    # Fraction of the live price. 0.5% on an entry is roughly a wide spread plus a
    # tick; a plan further from the market than that was not built from it.
    "max_entry_drift_pct": 0.005,
    # Fraction of the live price: the hard ceiling for the stop and the target.
    "max_level_drift_pct": 0.02,
    # Fraction of entry. The planner brackets 0.2% away, so this is a wide margin
    # and anything past it is not a stop on this market at all.
    "max_stop_distance_pct": 0.05,
    # Absolute and relative tolerance on the money figures, and on the ratio.
    "money_atol": 0.01,
    "money_rtol": 1e-6,
    "rr_atol": 0.01,
    "rr_rtol": 1e-6,
    # Newest-candle age: two bar intervals, with a floor for short timeframes.
    "min_candle_age_seconds": 120.0,
    "staleness_slack_intervals": 2,
    # Position ceiling, in CURRENCY. ``None`` means "read
    # max_position_notional_usd from the config", the only ceiling this module
    # accepts. A ceiling in coins is not a guard: 0.01 units is about 840 USD of
    # BTC and about 1.18 USD of SOL, so the engine stopped reading
    # max_position_qty for exactly that reason, and this module must not revive
    # it as a ceiling that would deny every honest trade in the cheap coins.
    "max_notional_usd": None,
    # Candle timeframe. ``None`` means "the plan's own strategy_interval".
    "interval": None,
}

# Candle timestamp keys, in the order they are preferred. Values are unit
# conversions, not data.
TIME_KEYS = ("time", "timestamp", "ts", "open_time", "date")
INTERVAL_SECONDS = {"s": 1.0, "m": 60.0, "h": 3600.0, "d": 86400.0}

# Quote payload keys that carry a price, in the order they are preferred. A
# provider's shape is not assumed, but a price is never invented for one.
LIVE_PRICE_KEYS = ("ltp", "last", "lastPrice", "price", "mark_price", "close")

# A Binance spot pair: a base asset settled in dollars. Anything else is not a
# market this account trades, so it is refused before a quote is asked for.
SYMBOL_RE = re.compile(r"^[A-Z0-9]{1,20}USDT$")

# Numbers a plan must carry, all of them strictly positive, each with the words a
# trader would use for it. A refusal that says "risk_usd is None" has told the
# reader nothing they can act on.
REQUIRED_NUMBERS = {
    "qty": "the amount to trade",
    "entry_price": "the price it buys or sells at",
    "stop_loss": "the price that stops the trade out",
    "take_profit": "the price that takes the profit",
    "risk_usd": "the amount it says it risks",
    "reward_usd": "the amount it says it stands to make",
    "rr": "the reward-to-risk multiple",
}

# The metric fields a plan quotes about the strategy that produced it, with the
# words a trader would use for them. They are compared against the registered row
# one by one, because a plan quoting a better record than the registry holds is
# quoting a record that does not exist.
STRATEGY_METRIC_FIELDS = {
    "trades": "backtest trades",
    "net_pnl": "net profit",
    "max_drawdown": "worst drawdown",
    "fees": "fees",
}

# How many rows ``recent`` returns when asked for nothing sensible.
DEFAULT_RECENT = 50


# --------------------------------------------------------------------------- #
# Public interface
# --------------------------------------------------------------------------- #


def verify(plan, *, live=None, limits=None) -> dict:
    """Check one plan; return ``{"allow", "reasons", "checks", "severity", ...}``.

    ``live`` injects a live price for the test suite and for a caller that has
    already fetched one: a positive number, or a quote mapping carrying one.
    Leave it ``None`` and the price is fetched from
    :func:`services.foreign_data_service.get_foreign_quote`. There is no third
    option -- a price supplied and none reachable is a denial.

    ``limits`` overrides entries of :data:`LIMITS` for one call (the position
    ceiling, the price band, the candle age). An unknown key is a failure, not an
    ignored typo.

    Never raises. Every failure, including a failure inside this module, returns
    ``allow=False``.
    """
    checks: dict = {}
    reasons: list[dict] = []
    now = time.time()
    live_price = None

    try:
        effective = _limits(limits)
        db.ensure_verdicts()
        live_price = _run(plan, live, effective, checks, reasons, now)
        checks[CHECK_COMPLETED] = True
    except Exception:
        # The one place a crash can reach a caller, and it denies. A checker that
        # lets an exception escape is a checker that cannot be trusted to say no.
        logger.exception("paper verifier failed closed; the trade was not placed")
        checks[CHECK_COMPLETED] = False
        reasons.append(
            _reason(
                VERIFIER_ERROR,
                CHECK_COMPLETED,
                BLOCKING,
                "The independent check on this trade could not be finished, so nothing was "
                "placed. The fault is in the checker rather than in your settings, and the "
                "details have been written to the log.",
            )
        )

    blocking = [reason for reason in reasons if reason["severity"] == BLOCKING]
    allow = not blocking
    severity = BLOCKING if blocking else (ADVISORY if reasons else NONE)

    recorded_id = None
    try:
        recorded_id = _persist(plan, allow, severity, reasons, checks, live_price, now)
        checks[CHECK_RECORDED] = True
    except Exception:
        logger.exception("could not record the paper trade verdict")
        checks[CHECK_RECORDED] = False
        reasons.append(
            _reason(
                VERDICT_NOT_RECORDED,
                CHECK_RECORDED,
                ADVISORY,
                "This decision could not be written to the trade log, so it will not appear "
                "on the dashboard. The trade was judged on its own merits and the details "
                "have been written to the log.",
            )
        )
        severity = BLOCKING if blocking else ADVISORY

    return {
        "allow": allow,
        "severity": severity,
        "reasons": reasons,
        "reason_slugs": [reason["slug"] for reason in reasons],
        "checks": checks,
        "failed": [name for name, ok in checks.items() if not ok],
        "symbol": _symbol_of(plan),
        "live_price": live_price,
        "verified_at": now,
        "verdict_id": recorded_id,
    }


def recent(limit: int = DEFAULT_RECENT) -> list[dict]:
    """The most recent verdicts, newest first, for the dashboard.

    Refusals and allowances alike: a verifier that kept only its denials would be
    indistinguishable from one that had stopped looking. A database that cannot be
    read returns an empty list rather than raising, because a status page is not
    the thing that should go down.
    """
    try:
        count = max(1, int(limit))
    except (TypeError, ValueError):
        count = DEFAULT_RECENT
    try:
        db.ensure_verdicts()
        with db.conn() as c:
            rows = c.execute(
                f"SELECT * FROM {db.VERDICTS_TABLE} ORDER BY id DESC LIMIT ?", (count,)
            ).fetchall()
    except Exception:
        logger.exception("could not read the paper trade verdicts")
        return []
    return [_verdict_row(row) for row in rows]


# --------------------------------------------------------------------------- #
# The checks
# --------------------------------------------------------------------------- #


def _run(plan, live, limits, checks, reasons, now) -> float | None:
    """Every check in turn, each recording its own boolean. Returns the live price.

    Checks are not short-circuited on each other: a plan can fail five ways, and
    an operator who has fixed one of them should not have to re-run to discover
    the next. The only skips are where a check genuinely cannot run -- a plan that
    is not a mapping has no symbol to quote.
    """
    if not isinstance(plan, dict):
        _record(
            checks,
            reasons,
            CHECK_PLAN_MAPPING,
            False,
            PLAN_MALFORMED,
            BLOCKING,
            "What reached the checker was not a set of trade instructions at all, so there "
            "was nothing to check and nothing was placed.",
        )
        return None
    checks[CHECK_PLAN_MAPPING] = True

    numbers = {field: _num(plan.get(field)) for field in REQUIRED_NUMBERS}
    ctx = {
        "plan": plan,
        "symbol": _symbol_of(plan),
        "side": _stripped(plan.get("side")),
        "numbers": numbers,
        "limits": limits,
        "now": now,
    }

    _check_refusal(ctx, checks, reasons)
    symbol_ok = _check_symbol(ctx, checks, reasons)
    side_ok = _check_side(ctx, checks, reasons)
    _check_numbers(ctx, checks, reasons)
    _check_qty(ctx, checks, reasons)

    live_price = None
    if symbol_ok:
        live_price = _live_price(ctx["symbol"], live)
        _check_price(ctx, live_price, checks, reasons)
    else:
        _record(
            checks,
            reasons,
            CHECK_PRICE,
            False,
            PRICE_NOT_PLAUSIBLE,
            BLOCKING,
            "This trade names a coin that is not a market this account trades, so no live "
            "price could be asked for and no price on it could be checked. Nothing was "
            "placed.",
        )

    _check_freshness(ctx, checks, reasons)
    _check_arithmetic(ctx, checks, reasons)
    _check_stop_distance(ctx, side_ok, checks, reasons)
    _check_provenance(ctx, checks, reasons)
    _check_analyst(ctx, checks, reasons)
    _check_not_open(ctx, checks, reasons)
    _check_brackets(ctx, checks, reasons)
    _check_balance(ctx, checks, reasons)
    return live_price


def _check_refusal(ctx, checks, reasons) -> None:
    """A plan the planner already refused is not a trade to verify."""
    reason = ctx["plan"].get("refusal_reason")
    if not reason:
        checks[CHECK_NOT_REFUSED] = True
        return
    detail = str(ctx["plan"].get("refusal_detail") or "").strip()
    spoken = str(reason).replace("_", " ").strip()
    message = (
        f"This trade was already turned down before it reached the independent check, because "
        f"{spoken}."
    )
    if detail:
        message += f" It said: {detail}"
    _record(checks, reasons, CHECK_NOT_REFUSED, False, ALREADY_REFUSED, BLOCKING, message)


def _check_symbol(ctx, checks, reasons) -> bool:
    """A real Binance USDT pair, written the way the exchange writes it."""
    symbol = ctx["symbol"]
    if isinstance(symbol, str) and SYMBOL_RE.match(symbol):
        checks[CHECK_SYMBOL] = True
        return True
    stripped = symbol.strip() if isinstance(symbol, str) else ""
    shown = stripped or "nothing at all"
    advice = ""
    if stripped and stripped.upper() != stripped:
        advice = f" The exchange writes it as {stripped.upper()}, not {stripped}."
    _record(
        checks,
        reasons,
        CHECK_SYMBOL,
        False,
        SYMBOL_INVALID,
        BLOCKING,
        f"This trade names {shown} as the coin, which is not a market this account can trade. "
        f"It has to be a real coin pair settled in dollars and written in capitals, such as "
        f"BTCUSDT.{advice} Nothing was placed.",
    )
    return False


def _check_side(ctx, checks, reasons) -> bool:
    """The direction, spelled exactly as the exchange spells it.

    Not normalized on the way in. A plan that had to be corrected to say which way
    it was trading is a plan something already got wrong, and a checker that
    quietly repairs the field it is checking is not checking it.
    """
    side = ctx["side"]
    if side in ("BUY", "SELL"):
        checks[CHECK_SIDE] = True
        return True
    _record(
        checks,
        reasons,
        CHECK_SIDE,
        False,
        SIDE_INVALID,
        BLOCKING,
        f"This trade says it is a {side or 'blank'} trade, and there are only two directions, "
        f"written as BUY or SELL. Nothing was placed.",
    )
    return False


def _check_numbers(ctx, checks, reasons) -> None:
    """No missing, unreadable, infinite or non-positive number where one is required."""
    numbers = ctx["numbers"]
    bad = []
    for field, number in numbers.items():
        raw = ctx["plan"].get(field)
        if number is None:
            bad.append(f"{REQUIRED_NUMBERS[field]} is {'missing' if raw is None else 'unreadable'}")
        elif number <= 0:
            bad.append(f"{REQUIRED_NUMBERS[field]} is {_fmt(number)}, which is not a usable amount")
    if not bad:
        checks[CHECK_NUMBERS] = True
        return
    _record(
        checks,
        reasons,
        CHECK_NUMBERS,
        False,
        NUMBERS_INVALID,
        BLOCKING,
        f"This trade is missing figures it cannot be placed without: {'; '.join(bad)}. Nothing "
        f"was placed.",
    )


def _check_qty(ctx, checks, reasons) -> None:
    """The quantity is positive and inside the configured ceiling."""
    qty = ctx["numbers"]["qty"]
    ceiling_usd = _notional_ceiling(ctx["limits"], ctx["numbers"]["entry_price"])
    if qty is None:
        # Already denied by the numbers check, with the number named.
        checks[CHECK_QTY] = False
        return
    notional = qty * (ctx["numbers"]["entry_price"] or 0.0)
    if ceiling_usd is not None and notional > ceiling_usd:
        _record(
            checks,
            reasons,
            CHECK_QTY,
            False,
            QTY_ABOVE_CEILING,
            BLOCKING,
            f"This trade would be worth {_fmt(notional)} USD, and the largest single position "
            f"this account allows is {_fmt(ceiling_usd)} USD. Nothing was placed.",
        )
        return
    checks[CHECK_QTY] = True


def _check_price(ctx, live_price, checks, reasons) -> None:
    """The anti-hallucination check: every price must sit near the live quote."""
    symbol = ctx["symbol"]
    numbers = ctx["numbers"]
    entry = numbers["entry_price"]
    stop = numbers["stop_loss"]
    target = numbers["take_profit"]

    if live_price is None:
        _record(
            checks,
            reasons,
            CHECK_PRICE,
            False,
            DATA_UNAVAILABLE,
            BLOCKING,
            f"No live price for {symbol} could be read at the moment, so the prices on this "
            f"trade could not be checked. This checker never substitutes a price of its own, "
            f"and nothing was placed.",
        )
        return
    if entry is None:
        _record(
            checks,
            reasons,
            CHECK_PRICE,
            False,
            PRICE_NOT_PLAUSIBLE,
            BLOCKING,
            f"This trade has no entry price, so there was nothing to compare against the live "
            f"price of {_fmt(live_price)} for {symbol}. Nothing was placed.",
        )
        return

    limits = ctx["limits"]
    entry_band = _limit(limits, "max_entry_drift_pct")
    drift = _drift(entry, live_price)
    if drift > entry_band:
        verb = "buy" if ctx["side"] == "BUY" else "sell"
        _record(
            checks,
            reasons,
            CHECK_PRICE,
            False,
            PRICE_NOT_PLAUSIBLE,
            BLOCKING,
            f"This trade wanted to {verb} at {_fmt(entry)}, but {symbol} is trading at "
            f"{_fmt(live_price)} right now, a gap of {drift * 100:.2f}%. That price does not "
            f"exist in the market, so nothing was placed.",
        )
        return

    ceiling = _limit(limits, "max_level_drift_pct")
    for name, level in (("stop-loss", stop), ("take-profit", target)):
        if level is None:
            continue
        # A level cannot sit further from the market than the entry does plus its own
        # distance from the entry, so the band is the tighter of that and a hard
        # ceiling. No second opinion about where a bracket belongs.
        offset = abs(level - entry) / entry if entry else float("inf")
        band = min(ceiling, entry_band + offset)
        level_drift = _drift(level, live_price)
        if level_drift > band:
            _record(
                checks,
                reasons,
                CHECK_PRICE,
                False,
                PRICE_NOT_PLAUSIBLE,
                BLOCKING,
                f"This trade sets its {name} at {_fmt(level)}, while {symbol} is trading at "
                f"{_fmt(live_price)} right now. That level is nowhere near the market, so "
                f"nothing was placed.",
            )
            return

    checks[CHECK_PRICE] = True


def _check_freshness(ctx, checks, reasons) -> None:
    """The candles the setup was read from must be recent, and must carry a clock.

    The candles are re-fetched here rather than read off the plan: a plan carries a
    summary of the data it used, not the data, and trusting the summary is trusting
    the thing being verified.
    """
    symbol = ctx["symbol"]
    limits = ctx["limits"]
    interval = _interval_of(ctx["plan"], limits)
    candles = _candles(symbol, interval)
    if not candles:
        _record(
            checks,
            reasons,
            CHECK_FRESHNESS,
            False,
            DATA_UNVERIFIABLE,
            BLOCKING,
            f"No market history for {symbol} on {interval} candles could be read, so how old "
            f"the data behind this trade is could not be checked. Nothing was placed.",
        )
        return
    stamp = _candle_time(candles)
    if stamp is None:
        _record(
            checks,
            reasons,
            CHECK_FRESHNESS,
            False,
            DATA_UNVERIFIABLE,
            BLOCKING,
            f"The market history for {symbol} carries no timestamp on its last bar, so its age "
            f"cannot be checked. A setup read off data of unknown age is not traded. Nothing "
            f"was placed.",
        )
        return
    limit = _age_limit(interval, limits)
    age = ctx["now"] - stamp
    if age > limit:
        _record(
            checks,
            reasons,
            CHECK_FRESHNESS,
            False,
            DATA_STALE,
            BLOCKING,
            f"The market data behind this trade stopped updating {age / 60.0:.0f} minutes ago. "
            f"A setup read off data older than {limit / 60.0:.0f} minutes is not today's setup, "
            f"so nothing was placed.",
        )
        return
    checks[CHECK_FRESHNESS] = True


def _check_arithmetic(ctx, checks, reasons) -> None:
    """Re-derive the money figures. Every one of these could be invented."""
    numbers = ctx["numbers"]
    entry = numbers["entry_price"]
    stop = numbers["stop_loss"]
    target = numbers["take_profit"]
    qty = numbers["qty"]
    limits = ctx["limits"]

    if any(numbers[field] is None for field in ("entry_price", "stop_loss", "take_profit", "qty")):
        for name, slug in (
            (CHECK_RISK, RISK_MISMATCH),
            (CHECK_REWARD, REWARD_MISMATCH),
            (CHECK_RR, RR_MISMATCH),
            (CHECK_SIDES, BRACKET_SIDE_WRONG),
        ):
            _record(
                checks,
                reasons,
                name,
                False,
                slug,
                BLOCKING,
                "This trade is missing the prices and size its own arithmetic has to be checked "
                "against, so the amounts on it could not be confirmed. Nothing was placed.",
            )
        return

    money_atol = _limit(limits, "money_atol")
    money_rtol = _limit(limits, "money_rtol")

    # Risk and reward are magnitudes, so the absolute difference is used: a short's
    # target sits below its entry and the money is still a positive distance.
    risk = abs(entry - stop) * qty
    reward = abs(target - entry) * qty

    claimed_risk = numbers["risk_usd"]
    if claimed_risk is not None and math.isclose(
        risk, claimed_risk, rel_tol=money_rtol, abs_tol=money_atol
    ):
        checks[CHECK_RISK] = True
    else:
        _record(
            checks,
            reasons,
            CHECK_RISK,
            False,
            RISK_MISMATCH,
            BLOCKING,
            f"This trade says it risks {_fmt(claimed_risk)}, but trading {_fmt(qty)} between "
            f"{_fmt(entry)} and a stop at {_fmt(stop)} risks {_fmt(risk)}. Those are different "
            f"numbers, so nothing was placed.",
        )

    claimed_reward = numbers["reward_usd"]
    if claimed_reward is not None and math.isclose(
        reward, claimed_reward, rel_tol=money_rtol, abs_tol=money_atol
    ):
        checks[CHECK_REWARD] = True
    else:
        _record(
            checks,
            reasons,
            CHECK_REWARD,
            False,
            REWARD_MISMATCH,
            BLOCKING,
            f"This trade says it stands to make {_fmt(claimed_reward)}, but the distance from "
            f"{_fmt(entry)} to its target of {_fmt(target)} on {_fmt(qty)} is worth "
            f"{_fmt(reward)}. Those are different numbers, so nothing was placed.",
        )

    claimed_rr = numbers["rr"]
    if risk <= 0:
        _record(
            checks,
            reasons,
            CHECK_RR,
            False,
            RR_MISMATCH,
            BLOCKING,
            "This trade puts its stop and its entry at the same price, so there is no risk to "
            "divide anything by. Nothing was placed.",
        )
    elif claimed_rr is not None and math.isclose(
        reward / risk,
        claimed_rr,
        rel_tol=_limit(limits, "rr_rtol"),
        abs_tol=_limit(limits, "rr_atol"),
    ):
        checks[CHECK_RR] = True
    else:
        _record(
            checks,
            reasons,
            CHECK_RR,
            False,
            RR_MISMATCH,
            BLOCKING,
            f"This trade claims to risk {_fmt(risk)} to make {_fmt(reward)}, which is "
            f"{reward / risk:.2f} times the risk, but it reports {_fmt(claimed_rr)}. Nothing "
            f"was placed.",
        )

    _check_sides(ctx, checks, reasons)


def _check_sides(ctx, checks, reasons) -> None:
    """The stop must lose and the target must win, for the direction the plan states."""
    numbers = ctx["numbers"]
    entry = numbers["entry_price"]
    stop = numbers["stop_loss"]
    target = numbers["take_profit"]
    side = ctx["side"]

    if entry is None or stop is None or target is None or side not in ("BUY", "SELL"):
        _record(
            checks,
            reasons,
            CHECK_SIDES,
            False,
            BRACKET_SIDE_WRONG,
            BLOCKING,
            "This trade does not say which way it is trading, or is missing its entry, stop or "
            "target, so there was nothing to check the stop and target against. Nothing was "
            "placed.",
        )
        return

    if side == "BUY" and stop < entry < target:
        checks[CHECK_SIDES] = True
        return
    if side == "SELL" and target < entry < stop:
        checks[CHECK_SIDES] = True
        return

    _record(
        checks,
        reasons,
        CHECK_SIDES,
        False,
        BRACKET_SIDE_WRONG,
        BLOCKING,
        f"This trade is a {side.lower()}, so its stop at {_fmt(stop)} has to sit below its entry "
        f"at {_fmt(entry)} and its target at {_fmt(target)} above it. It does not, so a trade "
        f"that is meant to lose could never lose and one meant to win could never win. Nothing "
        f"was placed.",
    )


def _check_stop_distance(ctx, side_ok, checks, reasons) -> None:
    """A stop has to be somewhere the market can actually reach."""
    numbers = ctx["numbers"]
    entry = numbers["entry_price"]
    stop = numbers["stop_loss"]
    if not side_ok or entry is None or stop is None or entry <= 0:
        _record(
            checks,
            reasons,
            CHECK_STOP_DISTANCE,
            False,
            STOP_TOO_FAR,
            BLOCKING,
            "This trade's entry or stop is missing or unreadable, so how far the stop sits from "
            "the entry could not be checked. Nothing was placed.",
        )
        return
    ceiling = _limit(ctx["limits"], "max_stop_distance_pct")
    distance = abs(entry - stop) / entry
    if distance <= ceiling:
        checks[CHECK_STOP_DISTANCE] = True
        return
    _record(
        checks,
        reasons,
        CHECK_STOP_DISTANCE,
        False,
        STOP_TOO_FAR,
        BLOCKING,
        f"This trade's stop at {_fmt(stop)} sits {distance * 100:.1f}% away from its entry at "
        f"{_fmt(entry)}, further than this account's {ceiling * 100:.0f}% limit and further than "
        f"the market moves on this timeframe. Nothing was placed.",
    )


def _check_provenance(ctx, checks, reasons) -> None:
    """The strategy must exist, be active, and be the one the registry validated.

    The metrics judged here are the ones *recorded on the row*, not the ones the
    plan carries, and the two are then compared: a plan quoting a better record
    than the registry holds is quoting a record that does not exist.
    """
    strategy_id = _text(ctx["plan"].get("strategy_id"))
    if not strategy_id:
        message = (
            "This trade does not name the strategy it came from, so there is no recorded "
            "backtest behind it to check. Nothing was placed."
        )
        for name, slug in (
            (CHECK_STRATEGY_EXISTS, STRATEGY_UNKNOWN),
            (CHECK_STRATEGY_ACTIVE, STRATEGY_INACTIVE),
            (CHECK_STRATEGY_VALIDATED, STRATEGY_UNVALIDATED),
            (CHECK_STRATEGY_METRICS, STRATEGY_METRICS_MISMATCH),
            (CHECK_STRATEGY_SETUP, STRATEGY_SETUP_MISMATCH),
        ):
            _record(checks, reasons, name, False, slug, BLOCKING, message)
        return

    with db.conn() as c:
        row = c.execute("SELECT * FROM strategies WHERE id=?", (strategy_id,)).fetchone()

    if row is None:
        message = (
            f"This trade claims to come from the strategy {strategy_id}, but there is no such "
            f"strategy on file. A trade with no recorded backtest behind it is not a trade, so "
            f"nothing was placed."
        )
        for name, slug in (
            (CHECK_STRATEGY_EXISTS, STRATEGY_UNKNOWN),
            (CHECK_STRATEGY_ACTIVE, STRATEGY_INACTIVE),
            (CHECK_STRATEGY_VALIDATED, STRATEGY_UNVALIDATED),
            (CHECK_STRATEGY_METRICS, STRATEGY_METRICS_MISMATCH),
            (CHECK_STRATEGY_SETUP, STRATEGY_SETUP_MISMATCH),
        ):
            _record(checks, reasons, name, False, slug, BLOCKING, message)
        return
    checks[CHECK_STRATEGY_EXISTS] = True

    status = str(row["status"] or "").strip().lower()
    if status == "active":
        checks[CHECK_STRATEGY_ACTIVE] = True
    else:
        _record(
            checks,
            reasons,
            CHECK_STRATEGY_ACTIVE,
            False,
            STRATEGY_INACTIVE,
            BLOCKING,
            f"This trade comes from the strategy {strategy_id}, which is not active on this "
            f"account any more. Nothing was placed.",
        )

    recorded = _metrics_of(row["metrics"])
    passed, why = engine.passes_validation(recorded, engine.validation_bar())
    if passed:
        checks[CHECK_STRATEGY_VALIDATED] = True
    else:
        _record(
            checks,
            reasons,
            CHECK_STRATEGY_VALIDATED,
            False,
            STRATEGY_UNVALIDATED,
            BLOCKING,
            f"This trade comes from the strategy {strategy_id}, and its own recorded backtest "
            f"does not clear the bar this account requires, because {why}. Nothing was placed.",
        )

    _compare_strategy_metrics(ctx, recorded, strategy_id, checks, reasons)
    _compare_strategy_setup(ctx, row, strategy_id, checks, reasons)


def _compare_strategy_metrics(ctx, recorded, strategy_id, checks, reasons) -> None:
    claimed = ctx["plan"].get("metrics")
    if not isinstance(claimed, dict):
        checks[CHECK_STRATEGY_METRICS] = False
        return
    mismatched = [
        field
        for field in STRATEGY_METRIC_FIELDS
        if not _same_number(_num(claimed.get(field)), _num(recorded.get(field)))
    ]
    if not mismatched:
        checks[CHECK_STRATEGY_METRICS] = True
        return
    said = ", ".join(f"{STRATEGY_METRIC_FIELDS[f]} {_fmt(claimed.get(f))}" for f in mismatched)
    truth = ", ".join(f"{STRATEGY_METRIC_FIELDS[f]} {_fmt(recorded.get(f))}" for f in mismatched)
    _record(
        checks,
        reasons,
        CHECK_STRATEGY_METRICS,
        False,
        STRATEGY_METRICS_MISMATCH,
        BLOCKING,
        f"This trade reports a backtest record for {strategy_id} of {said}, but the record "
        f"actually on file is {truth}. A plan is not allowed to describe a backtest better "
        f"than it happened, so nothing was placed.",
    )


def _compare_strategy_setup(ctx, row, strategy_id, checks, reasons) -> None:
    family = str(row["family"] or "")
    planned = ctx["plan"].get("setup_family")
    family_ok = planned is None or str(planned) == family
    if family_ok and _params_match(ctx["plan"].get("strategy_params"), row["params"]):
        checks[CHECK_STRATEGY_SETUP] = True
        return
    _record(
        checks,
        reasons,
        CHECK_STRATEGY_SETUP,
        False,
        STRATEGY_SETUP_MISMATCH,
        BLOCKING,
        f"This trade describes its setup as {planned or 'an unnamed one'}, but {strategy_id} is "
        f"registered as {family or 'an unnamed one'}. A trade built on a different setup than "
        f"the one that was tested is not that tested strategy, so nothing was placed.",
    )


def _check_analyst(ctx, checks, reasons) -> None:
    """The plan may not assert an opinion it does not have.

    The analyst is the one part of this path that was not a backtest, so whether it
    spoke is the one claim a reader is entitled to take at face value. A plan that
    says it was reviewed and carries no review, or that carries a review and says
    it was not reviewed, is refused.
    """
    plan = ctx["plan"]
    available = _truthy(plan.get("analyst_available"))
    bypassed = _truthy(plan.get("analyst_bypassed"))
    verdict = plan.get("jev_verdict")
    verdict = verdict if isinstance(verdict, dict) else None
    p_take = _num(verdict.get("p_take")) if verdict else None
    has_opinion = p_take is not None

    if not available:
        # Nothing claimed, so there is no verdict owed. The claims themselves are
        # judged by the three checks below.
        checks[CHECK_ANALYST_VERDICT] = True
    elif has_opinion and 0.0 <= p_take <= 1.0:
        checks[CHECK_ANALYST_VERDICT] = True
    else:
        _record(
            checks,
            reasons,
            CHECK_ANALYST_VERDICT,
            False,
            ANALYST_CLAIMED_WITHOUT_VERDICT,
            BLOCKING,
            f"This trade says an analyst reviewed it, but it carries no verdict from one: "
            f"{_fmt(p_take)} is not a probability between 0 and 1. A trade nobody reviewed but "
            f"reported as reviewed is worse than no trade at all, so nothing was placed.",
        )

    if not available and has_opinion:
        _record(
            checks,
            reasons,
            CHECK_ANALYST_OVERCLAIM,
            False,
            ANALYST_VERDICT_WITHOUT_CLAIM,
            BLOCKING,
            f"This trade says no analyst reviewed it, yet it carries a verdict of {p_take:.2f}. "
            f"A plan cannot claim an opinion and deny having one at the same time, so nothing "
            f"was placed.",
        )
    else:
        checks[CHECK_ANALYST_OVERCLAIM] = True

    if bypassed and available:
        _record(
            checks,
            reasons,
            CHECK_ANALYST_BYPASS,
            False,
            ANALYST_BYPASS_INCONSISTENT,
            BLOCKING,
            "This trade is stamped as unreviewed while also claiming an analyst reviewed it. One "
            "of the two is wrong and this checker will not guess which, so nothing was placed.",
        )
    else:
        checks[CHECK_ANALYST_BYPASS] = True

    if available or bypassed:
        checks[CHECK_ANALYST_QUIET] = True
    else:
        _record(
            checks,
            reasons,
            CHECK_ANALYST_QUIET,
            False,
            ANALYST_UNAVAILABLE,
            BLOCKING,
            "This trade carries no opinion from the analyst and has not been marked as "
            "deliberately trading without one, so there is nothing to place. Nothing was "
            "placed.",
        )


def _check_not_open(ctx, checks, reasons) -> None:
    """One open position per symbol: a second one cannot be tracked or managed."""
    symbol = ctx["symbol"]
    if not isinstance(symbol, str) or not symbol.strip():
        # Already denied as a symbol that is not a market.
        checks[CHECK_NOT_OPEN] = False
        return
    with db.conn() as c:
        row = c.execute(
            "SELECT side,qty FROM positions WHERE symbol=? AND status='open'", (symbol,)
        ).fetchone()
    if row is None:
        checks[CHECK_NOT_OPEN] = True
        return
    _record(
        checks,
        reasons,
        CHECK_NOT_OPEN,
        False,
        SYMBOL_ALREADY_OPEN,
        BLOCKING,
        f"This account is already holding {symbol}, {str(row['side'] or '').lower()} "
        f"{_fmt(row['qty'])}, and a second position in the same coin cannot be watched or "
        f"closed properly. Nothing was placed.",
    )


def _check_brackets(ctx, checks, reasons) -> None:
    """A plan may not claim resting exits the paper engine cannot actually place.

    Advisory rather than blocking: the stop and target are on the position row and
    are enforced every cycle either way, so an overclaim misleads a reader rather
    than leaving a position unwatched.
    """
    if not _truthy(ctx["plan"].get("brackets_attached")) or hasattr(engine, "attach_paper_exits"):
        checks[CHECK_BRACKETS] = True
        return
    _record(
        checks,
        reasons,
        CHECK_BRACKETS,
        False,
        BRACKETS_NOT_RESTING,
        ADVISORY,
        "This trade says its stop and target are already resting with the exchange. This "
        "account cannot place those, so they are kept on the position and checked once a cycle "
        "instead. The trade can still go ahead, but a fast move will not be caught the instant "
        "it happens.",
    )


def _check_balance(ctx, checks, reasons) -> None:
    """The balance the plan was sized against, next to the balance that exists now.

    Advisory: a balance that moved between planning and placing is the normal case
    (another cycle closed something, or a fee settled), and the placement path
    re-checks affordability itself.
    """
    claimed = _num(ctx["plan"].get("cash"))
    if claimed is None:
        checks[CHECK_BALANCE] = True
        return
    try:
        with db.conn() as c:
            current = db.get_cash(c)
    except Exception:
        logger.exception("could not read the balance for the advisory check")
        checks[CHECK_BALANCE] = True
        return
    if math.isclose(claimed, current, rel_tol=1e-9, abs_tol=0.01):
        checks[CHECK_BALANCE] = True
        return
    _record(
        checks,
        reasons,
        CHECK_BALANCE,
        False,
        ACCOUNT_MOVED,
        ADVISORY,
        f"This trade was sized when the balance was {_fmt(claimed)}, and it is now "
        f"{_fmt(current)}. The balance is re-checked when the trade is placed, so this is a "
        f"note rather than a stop.",
    )


# --------------------------------------------------------------------------- #
# Data sources
# --------------------------------------------------------------------------- #


def _live_price(symbol, live) -> float | None:
    """The one real price for this symbol, or None when there is none.

    ``live`` is the injected value; otherwise the quote comes from the live feed.
    There is no third source. A module able to answer from a constant would be the
    exact failure it exists to catch, so "no price" is the only fallback, and the
    caller turns that into a denial.
    """
    injected = _price_of(live)
    if injected is not None:
        return injected
    if not isinstance(symbol, str) or not SYMBOL_RE.match(symbol):
        return None
    from services.foreign_data_service import get_foreign_quote

    try:
        quote = get_foreign_quote(symbol, "CRYPTO")
    except Exception:
        logger.warning("no live quote for %s; the plan will be refused", symbol)
        return None
    return _price_of(quote)


def _price_of(value) -> float | None:
    """A positive, finite price from a number or a quote mapping, else None."""
    if value is None:
        return None
    if isinstance(value, dict):
        for key in LIVE_PRICE_KEYS:
            if key in value:
                price = _price_of(value[key])
                if price is not None:
                    return price
        return None
    price = _num(value)
    return price if price is not None and price > 0 else None


def _candles(symbol, interval) -> list[dict]:
    """The market history itself, read fresh rather than taken from the plan.

    The provider is called directly rather than through the planner's own helper,
    so a change in the data path being checked cannot go unnoticed. A provider
    failure is a denial, never an empty list that reads as "no stale data found".
    """
    if not isinstance(symbol, str) or not symbol.strip():
        return []
    from services.foreign_data_service import get_foreign_history

    try:
        raw = get_foreign_history(symbol, "CRYPTO", interval, "", "")
    except Exception:
        logger.warning("no market history for %s on %s; the plan will be refused", symbol, interval)
        return []
    if not isinstance(raw, list):
        return []
    return [bar for bar in raw if isinstance(bar, dict)]


def _candle_time(candles) -> float | None:
    """The newest bar's time in seconds, or None when it carries no usable clock."""
    if not candles:
        return None
    for key in TIME_KEYS:
        if key not in candles[-1]:
            continue
        seconds = _num(candles[-1].get(key))
        if seconds is None:
            continue
        # Crypto feeds are usually in milliseconds.
        return seconds / 1000.0 if seconds > 1e11 else seconds
    return None


def _interval_seconds(interval) -> float:
    text = str(interval or "")
    amount = _num(text[:-1])
    if amount is None:
        return _limit(LIMITS, "min_candle_age_seconds")
    return amount * INTERVAL_SECONDS.get(text[-1:].lower(), 60.0)


def _age_limit(interval, limits) -> float:
    """How old the newest bar may be: two intervals, with a floor."""
    floor = _limit(limits, "min_candle_age_seconds")
    slack = _limit(limits, "staleness_slack_intervals")
    return max(floor, _interval_seconds(interval) * slack)


def _interval_of(plan, limits) -> str:
    """The timeframe to age-check on: what the caller asked for, else the plan's own."""
    override = _limit(limits, "interval")
    if isinstance(override, str) and override:
        return override
    planned = plan.get("strategy_interval")
    if isinstance(planned, str) and planned in engine.ALLOWED_INTERVALS:
        return planned
    return engine.resolve_interval()


def _notional_ceiling(limits, price) -> float | None:
    """The largest single position allowed, in USD, from an override or config.

    Only those two sources. A ceiling invented here would be a cap nobody set.
    ``None`` means unlimited, which the engine's own exposure cap still bounds.
    """
    override = _limit(limits, "max_notional_usd")
    raw = override if override is not None else db.get("max_position_notional_usd", None)
    ceiling = _num(raw)
    if ceiling is None:
        # Config says nothing, so fall back to the engine's own default rather
        # than treating the position as unbounded. Silently unlimited is not a
        # default anyone chose.
        return float(engine.DEFAULT_MAX_POSITION_NOTIONAL_USD)
    if not ceiling or ceiling <= 0:
        # A configured 0 disables the ceiling, which is the engine's convention.
        return None
    if ceiling <= 0:
        # Engine convention: 0 disables the notional ceiling.
        return None
    return ceiling


def _qty_ceiling(limits, price) -> float | None:
    """The currency ceiling expressed in the coin, for comparison with a qty."""
    ceiling = _notional_ceiling(limits, price)
    if ceiling is None or price is None:
        return None
    px = _num(price)
    if not px or px <= 0:
        return None
    return ceiling / px


# --------------------------------------------------------------------------- #
# Persistence
# --------------------------------------------------------------------------- #


def _persist(plan, allow, severity, reasons, checks, live_price, now):
    """Write the verdict, allowed or denied, and return its row id.

    A plan that is not a mapping at all is recorded as exactly that: every summary
    column is null and the reasons carry the story. A verifier that could not log
    the worst inputs it was handed is the one input worth logging.
    """
    fields = plan if isinstance(plan, dict) else {}
    row = (
        now,
        1 if allow else 0,
        severity,
        _symbol_of(plan),
        _upper(fields.get("side")),
        _text(fields.get("strategy_id")),
        _num(fields.get("entry_price")),
        _num(fields.get("stop_loss")),
        _num(fields.get("take_profit")),
        _num(fields.get("qty")),
        _num(fields.get("risk_usd")),
        _num(fields.get("reward_usd")),
        _num(fields.get("rr")),
        live_price,
        json.dumps([reason["slug"] for reason in reasons]),
        json.dumps(reasons, default=str),
        json.dumps(checks, default=str),
    )
    placeholders = ",".join("?" * len(row))
    with db.conn() as c:
        cursor = c.execute(
            f"INSERT INTO {db.VERDICTS_TABLE}(ts,allow,severity,symbol,side,strategy_id,"
            f"entry_price,stop_loss,take_profit,qty,risk_usd,reward_usd,rr,live_price,"
            f"slugs,reasons,checks) VALUES({placeholders})",
            row,
        )
        return cursor.lastrowid


def _verdict_row(row) -> dict:
    """One stored verdict as a plain dict, with its JSON columns decoded."""
    return {
        "id": row["id"],
        "ts": row["ts"],
        "allow": bool(row["allow"]),
        "severity": row["severity"],
        "symbol": row["symbol"],
        "side": row["side"],
        "strategy_id": row["strategy_id"],
        "entry_price": row["entry_price"],
        "stop_loss": row["stop_loss"],
        "take_profit": row["take_profit"],
        "qty": row["qty"],
        "risk_usd": row["risk_usd"],
        "reward_usd": row["reward_usd"],
        "rr": row["rr"],
        "live_price": row["live_price"],
        "slugs": _loads(row["slugs"], []),
        "reasons": _loads(row["reasons"], []),
        "checks": _loads(row["checks"], {}),
    }


# --------------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------------- #


def _limits(overrides) -> dict:
    """LIMITS with a call's overrides applied. An unknown key is a failure.

    Ignoring a typo in a tolerance would leave a caller believing a band had been
    widened when it had not, which is the wrong way round for a check whose job is
    to stop things.
    """
    merged = dict(LIMITS)
    if not overrides:
        return merged
    if not isinstance(overrides, dict):
        raise TypeError("verifier limits must be a mapping of limit names to values")
    unknown = sorted(set(overrides) - set(merged))
    if unknown:
        raise ValueError(f"unknown verifier limits: {', '.join(unknown)}")
    merged.update(overrides)
    return merged


def _limit(limits, key):
    return limits.get(key, LIMITS.get(key))


def _reason(slug: str, check: str, severity: str, message: str) -> dict:
    return {"slug": slug, "check": check, "severity": severity, "message": message}


def _record(checks, reasons, name, ok, slug, severity, message) -> bool:
    """Record one check's outcome, with its reason when it failed."""
    checks[name] = bool(ok)
    if not ok:
        reasons.append(_reason(slug, name, severity, message))
    return bool(ok)


def _num(value):
    """A finite float, or None. ``None``, ``""`` and NaN all come back as None."""
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _same_number(said, truth) -> bool:
    """Whether two numbers mean the same thing, treating missing as equal to missing."""
    if said is None or truth is None:
        return said is None and truth is None
    return math.isclose(said, truth, rel_tol=1e-9, abs_tol=1e-9)


def _drift(level, live_price) -> float:
    return abs(level - live_price) / live_price if live_price else float("inf")


def _symbol_of(plan):
    if not isinstance(plan, dict):
        return None
    symbol = plan.get("symbol")
    return symbol if isinstance(symbol, str) else None


def _stripped(value):
    return value.strip() if isinstance(value, str) else None


def _upper(value):
    return value.strip().upper() if isinstance(value, str) else None


def _text(value):
    return value.strip() if isinstance(value, str) else None


def _truthy(value) -> bool:
    """A flag as a plan writes it: a bool, or something a bool-ish field held."""
    return bool(value) if isinstance(value, (bool, int, float, str)) else False


def _metrics_of(raw) -> dict:
    """A strategy row's metrics, whether stored as JSON text or already a dict."""
    if isinstance(raw, dict):
        return raw
    loaded = _loads(raw, {})
    return loaded if isinstance(loaded, dict) else {}


def _params_match(claimed, stored) -> bool:
    """Whether a plan's params are the ones its strategy was registered with."""
    if claimed is None:
        return True
    if not isinstance(claimed, dict):
        return False
    recorded = _loads(stored, {})
    return isinstance(recorded, dict) and recorded == claimed


def _loads(raw, default):
    if raw is None or raw == "":
        return default
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return default


def _fmt(value) -> str:
    """A number for a sentence: grouped, never an exponent, no trailing zeros."""
    number = _num(value)
    if number is None:
        return "an unknown amount"
    text = f"{number:,.8f}".rstrip("0").rstrip(".")
    return text or "0"
