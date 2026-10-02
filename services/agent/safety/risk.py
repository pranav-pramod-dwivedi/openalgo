"""The order guard for the `/agent` module.

Pure Python. The guard reads its limits from :mod:`services.agent.settings` and
is **handed** every market number it needs: the caller supplies the last traded
price and the available funds. It never fetches a quote, never calls a broker,
never opens a socket, and reads no prompt, so nothing the model or the user says
can talk past it and the whole thing is exercisable with no platform running::

    guard = RiskGuard(session_id="test", limits=some_limits)
    verdict = guard.check_order(
        symbol="INFY", exchange="NSE", action="BUY", quantity=10,
        product="CNC", ltp=Decimal("1500"), available_funds=Decimal("50000"),
        analyzer_mode=True,
    )

Where it sits
-------------

Agno pauses a mutating tool for human confirmation; the UI approves; **then**
this guard runs inside the tool body, before the service is called. Approval is
not a bypass. The guard is the last thing between an approved intention and a
real order.

Order of checks
---------------

Fixed by the build contract, and the order is the point:

1. kill switch, engaged either by the stored flag or by the kill-switch file
2. trading enabled
3. analyzer mode, when the operator requires it
4. symbol
5. exchange
6. product
7. quantity
8. session order cap, enforced against the persisted daily budget
9. duplicate-order window
10. loss containment: daily realised loss, exit cooldown, max one open
    position per symbol, and the total exposure cap
11. notional and limit-price deviation
12. affordability against available funds

Which way a check fails when it cannot decide
---------------------------------------------

Two directions, chosen per check rather than by habit:

* **Exposure-increasing orders fail closed.** A BUY is the only order kind
  that adds exposure on this account model (equity-style: buys open or add,
  sells reduce or close; a SELL that opens a short is not modelled). Whenever
  the guard cannot verify the market state for a BUY - no last traded price,
  no available funds, no readable risk counter, no reference price - the order
  is blocked, with ``PRICE_UNAVAILABLE``, ``FUNDS_UNAVAILABLE`` or
  ``STATE_UNAVAILABLE`` as the code. The old behaviour of warning and letting
  the order through is gone. The broker performs its own margin check, but
  that is not a substitute: the guard is the last thing between an approved
  intention and a real order, and a guard that waves an uncheckable order
  through is not a guard.
* **Reductions and closes always pass.** A SELL is never blocked by a missing
  price, a missing funds figure, an unavailable counter store, or a tripped
  daily-loss halt: it reduces exposure, and it is the only way back out of a
  bad trade. The old warn-and-continue path remains for it, so the operator
  still sees the warning in ``Verdict.details["warnings"]``.
* **Analyzer mode fails closed.** `require_analyzer_mode` is the operator
  stating in the database that the agent may not reach the live market. If the
  platform toggle cannot be read, allowing the order would send a live order
  under a policy that forbids exactly that. There is no broker-side backstop for
  this one.

State that outlives a restart
-----------------------------

The budget and the exposure accounting are persisted through
:mod:`database.agent_db` (``agent_risk:`` keys in the ``ag_setting`` table,
keyed by UTC date for the counters), not in memory: a process restart, a new
conversation, or a fresh ``RiskGuard`` does not reset them. Losing the
counter store therefore blocks new exposure, because an unverifiable budget
is an unknown budget.

Claiming
--------

`check_order` does not merely opine. When it allows an order it **claims** it:
under one hold of one lock it re-checks the session cap and the duplicate
window, increments the count and records the fingerprint. That is the shape
`services/strategy_module/state.claim_leg_exit` earned the hard way - a check
and a write in separate holds let two rules firing on one leg send two orders.

A claim that is not used must be released. `release(verdict)` rolls back the
count and the fingerprint, because a dispatch that never happened must not
consume the session's budget, and the caller must be able to retry. `commit`
just forgets the bookkeeping and keeps the claim.

Fills are accounted once, at claim time, in the persisted store: a BUY claim
adds its notional to the tracked open position, and a SELL that zeroes the
tracker stamps the symbol's exit for the cooldown. Both are tied to the
order that triggered them; if that dispatch is refused downstream, the
caller must call `release`, which undoes the slot and the accounting.

Where a tracked position goes stale (closed by hand, out of band), the
exposure checks read high rather than low, which is the safe direction: the
blocked BUY can be unblocked with ``agent_db.clear_open_position``.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Any
from uuid import uuid4

from cachetools import TTLCache

from services.agent.settings import RiskLimits, get_risk_limits
from utils import real_threading
from utils.constants import (
    PRICE_TYPE_LIMIT,
    PRICE_TYPE_MARKET,
    PRICE_TYPE_SL,
    PRICE_TYPE_SLM,
    VALID_ACTIONS,
    VALID_PRICE_TYPES,
)
from utils.logging import get_logger

logger = get_logger(__name__)

HUNDRED = Decimal("100")

# Destructive operations that touch one named thing. Permitted whenever trading
# is enabled and the analyzer policy is satisfied, because each is a reduction
# of exposure the operator can point at.
#
# `close_position` belongs here and not below. It takes a symbol, an exchange
# and a product and squares off exactly that contract, which is the same shape
# as cancelling one order: the operator names the thing, and the tool touches
# nothing else. Classifying it as bulk had two costs, and the second is the
# serious one. It refused the agent's only per-position exit by default, and the
# only way to permit it was `allow_bulk_destructive`, which in the same stroke
# unlocks `cancel_all_orders` and `close_all_positions`. An operator who wanted
# to close one leg had to grant account-wide sweep authority to get it, which is
# a wider permission than they asked for. The refusal also told them
# `close_position` "affects the whole account", which is not true.
TARGETED_OPERATIONS: frozenset[str] = frozenset(
    {
        "cancel_order",
        "modify_order",
        "cancel_gtt_order",
        "modify_gtt_order",
        "close_position",
    }
)

# Destructive operations that sweep the whole account. Gated behind
# `allow_bulk_destructive`, off by default: "close everything" is the single
# most expensive sentence a model can produce, and it should take an operator
# turning a switch on rather than a confirmation dialog nobody reads.
#
# The test for membership is whether the operation can be named at one
# instrument. If it cannot, it belongs here.
BULK_OPERATIONS: frozenset[str] = frozenset(
    {
        "cancel_all_orders",
        "close_all_positions",
        "square_off_all",
    }
)

KNOWN_OPERATIONS: frozenset[str] = TARGETED_OPERATIONS | BULK_OPERATIONS

# How long a fingerprint stays in the duplicate registry beyond its window, and
# how many the guard will hold. Both bound the memory of a session that runs all
# day in a worker that never restarts.
_MAX_TRACKED_FINGERPRINTS = 512
_MAX_PENDING_CLAIMS = 256


class RiskCode(StrEnum):
    """Machine-readable outcome of a guard check.

    The string is what reaches the audit row and the model, so the values are
    stable: a UI or a prompt may switch on them.
    """

    OK = "ok"
    KILL_SWITCH = "kill_switch"
    TRADING_DISABLED = "trading_disabled"
    ANALYZER_REQUIRED = "analyzer_required"
    ANALYZER_UNKNOWN = "analyzer_unknown"
    INVALID_ARGUMENTS = "invalid_arguments"
    INVALID_SYMBOL = "invalid_symbol"
    SYMBOL_BLOCKED = "symbol_blocked"
    SYMBOL_NOT_ALLOWED = "symbol_not_allowed"
    EXCHANGE_NOT_ALLOWED = "exchange_not_allowed"
    PRODUCT_NOT_ALLOWED = "product_not_allowed"
    INVALID_ACTION = "invalid_action"
    INVALID_PRICE_TYPE = "invalid_price_type"
    INVALID_PRICE = "invalid_price"
    INVALID_QUANTITY = "invalid_quantity"
    QUANTITY_EXCEEDED = "quantity_exceeded"
    SESSION_CAP_REACHED = "session_cap_reached"
    DUPLICATE_ORDER = "duplicate_order"
    NOTIONAL_EXCEEDED = "notional_exceeded"
    PRICE_DEVIATION = "price_deviation"
    INSUFFICIENT_FUNDS = "insufficient_funds"
    EMPTY_BASKET = "empty_basket"
    UNKNOWN_OPERATION = "unknown_operation"
    BULK_NOT_ALLOWED = "bulk_operation_not_allowed"
    DAILY_LOSS = "daily_loss"
    COOLDOWN = "cooldown"
    MAX_POSITION = "max_position"
    EXPOSURE_CAP = "exposure_cap"
    PRICE_UNAVAILABLE = "price_unavailable"
    FUNDS_UNAVAILABLE = "funds_unavailable"
    STATE_UNAVAILABLE = "state_unavailable"


def _increases_exposure(intent: _Intent) -> bool:
    """Whether the order adds exposure under this account model.

    Equity-style model used throughout this pass: a BUY opens or adds a
    position, and a SELL reduces or closes it. A SELL that would open a
    short is deliberately not modelled; on the account classes this guard
    sizes, that order shapes a position the account already covers.
    """
    return intent.action == "BUY"


def _jsonable(value: Any) -> Any:
    """Render a detail value as a JSON-safe primitive."""
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, frozenset | set):
        return sorted(str(item) for item in value)
    if isinstance(value, tuple | list):
        return [_jsonable(item) for item in value]
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    return value


@dataclass(frozen=True)
class Verdict:
    """The result of a guard check.

    Attributes:
        allowed: Whether the caller may proceed.
        reason: One sentence a human or a model can act on. Written for the
            reader, so it names the value and the limit rather than the rule.
        code: A stable :class:`RiskCode` value.
        details: The numbers behind the decision, JSON-safe once passed through
            :meth:`as_dict`. `warnings` lists every fail-open path taken.
    """

    allowed: bool
    reason: str
    code: str = RiskCode.OK
    details: Mapping[str, Any] = field(default_factory=dict)

    def as_message(self) -> str:
        """One line suitable for a tool result, a notice or a log.

        Returns:
            The reason, prefixed with the code when the order was refused so the
            model cannot mistake a refusal for a transient error and retry it
            unchanged.
        """
        if self.allowed:
            return self.reason or "Allowed by the risk guard."
        return f"Blocked by the risk guard [{self.code}]: {self.reason}"

    def as_dict(self) -> dict[str, Any]:
        """Render the verdict as JSON-safe primitives.

        Returns:
            A dictionary with `allowed`, `code`, `reason` and `details`.
        """
        return {
            "allowed": self.allowed,
            "code": str(self.code),
            "reason": self.reason,
            "details": {key: _jsonable(value) for key, value in self.details.items()},
        }


class _InvalidOrder(ValueError):
    """An order that cannot be normalised into something checkable."""

    def __init__(self, code: str, reason: str, **details: Any) -> None:
        super().__init__(reason)
        self.code = code
        self.reason = reason
        self.details = details


def _blocked(code: str, reason: str, /, **details: Any) -> Verdict:
    """Build a refusing verdict.

    ``code`` and ``reason`` are positional-only so a caller may pass detail keys
    of any name, including ones that collide with these parameters.
    """
    return Verdict(allowed=False, reason=reason, code=code, details=details)


def _allowed(reason: str, /, **details: Any) -> Verdict:
    """Build an allowing verdict."""
    return Verdict(allowed=True, reason=reason, code=RiskCode.OK, details=details)


def _decimal(value: Any) -> Decimal | None:
    """Coerce a caller-supplied number to a finite Decimal, or None.

    Booleans and non-finite values are rejected rather than coerced: `True`
    arriving where a price belongs is a bug, and treating it as 1.0 hides it.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, Decimal):
        return value if value.is_finite() else None
    try:
        parsed = Decimal(str(value).strip())
    except (InvalidOperation, TypeError, ValueError, AttributeError):
        return None
    return parsed if parsed.is_finite() else None


def _positive(value: Decimal | None) -> Decimal | None:
    """Return the value only when it is a usable, strictly positive price."""
    return value if value is not None and value > 0 else None


def _now() -> datetime:
    """The current time as an aware UTC datetime."""
    return datetime.now(UTC)


@dataclass(frozen=True)
class _Intent:
    """A normalised, checkable order.

    Everything the guard reasons about is on this object, so the checks
    themselves never touch caller-supplied strings.
    """

    symbol: str
    exchange: str
    action: str
    quantity: int
    product: str
    price_type: str
    price: Decimal | None
    trigger_price: Decimal | None
    ltp: Decimal | None
    available_funds: Decimal | None

    @property
    def fingerprint(self) -> str:
        """The identity used by the duplicate-order window."""
        return "|".join(
            [
                self.symbol,
                self.exchange,
                self.action,
                str(self.quantity),
                self.product,
                self.price_type,
                str(self.price if self.price is not None else ""),
                str(self.trigger_price if self.trigger_price is not None else ""),
            ]
        )

    @property
    def reference_price(self) -> Decimal | None:
        """The price the notional is computed from.

        A limit order is worth its limit price; anything else is worth the last
        traded price. A stop-market order carries only a trigger, which is the
        closest thing it has to an execution price.
        """
        if self.price_type in (PRICE_TYPE_LIMIT, PRICE_TYPE_SL) and self.price is not None:
            return self.price
        if self.ltp is not None:
            return self.ltp
        if self.price_type == PRICE_TYPE_SLM:
            return self.trigger_price
        return None

    @property
    def deviation_price(self) -> Decimal | None:
        """The price the deviation check compares against the last traded price."""
        if self.price_type in (PRICE_TYPE_LIMIT, PRICE_TYPE_SL):
            return self.price
        if self.price_type == PRICE_TYPE_SLM:
            return self.trigger_price
        return None

    def describe(self) -> str:
        """A short human description used in verdict reasons."""
        return f"{self.action} {self.quantity} {self.symbol} on {self.exchange}"


_ORDER_KEYS = frozenset(
    {
        "symbol",
        "exchange",
        "action",
        "quantity",
        "product",
        "price_type",
        "price",
        "trigger_price",
        "ltp",
        "available_funds",
    }
)


def _normalise(
    *,
    symbol: Any,
    exchange: Any,
    action: Any,
    quantity: Any,
    product: Any,
    price_type: Any = PRICE_TYPE_MARKET,
    price: Any = None,
    trigger_price: Any = None,
    ltp: Any = None,
    available_funds: Any = None,
) -> _Intent:
    """Turn caller-supplied order arguments into an :class:`_Intent`.

    Args:
        symbol: OpenAlgo symbol.
        exchange: OpenAlgo exchange code.
        action: BUY or SELL.
        quantity: Whole number of units, strictly positive.
        product: CNC, NRML or MIS.
        price_type: MARKET, LIMIT, SL or SL-M.
        price: Limit price, required for LIMIT and SL.
        trigger_price: Trigger price, required for SL and SL-M.
        ltp: Last traded price, supplied by the caller.
        available_funds: Free cash available, supplied by the caller.

    Returns:
        The normalised intent.

    Raises:
        _InvalidOrder: When an argument is missing, malformed, or inconsistent
            with the price type.
    """
    symbol_text = str(symbol or "").strip().upper()
    if not symbol_text:
        raise _InvalidOrder(RiskCode.INVALID_SYMBOL, "No symbol was supplied.")

    exchange_text = str(exchange or "").strip().upper()
    if not exchange_text:
        raise _InvalidOrder(
            RiskCode.EXCHANGE_NOT_ALLOWED, f"No exchange was supplied for {symbol_text}."
        )

    action_text = str(action or "").strip().upper()
    if action_text not in VALID_ACTIONS:
        raise _InvalidOrder(
            RiskCode.INVALID_ACTION,
            f"Action must be BUY or SELL, not {action!r}.",
            action=str(action),
        )

    product_text = str(product or "").strip().upper()
    if not product_text:
        raise _InvalidOrder(
            RiskCode.PRODUCT_NOT_ALLOWED, f"No product type was supplied for {symbol_text}."
        )

    price_type_text = str(price_type or PRICE_TYPE_MARKET).strip().upper()
    if price_type_text == "SLM":
        price_type_text = PRICE_TYPE_SLM
    if price_type_text not in VALID_PRICE_TYPES:
        raise _InvalidOrder(
            RiskCode.INVALID_PRICE_TYPE,
            f"Price type must be one of {', '.join(VALID_PRICE_TYPES)}, not {price_type!r}.",
            price_type=str(price_type),
        )

    try:
        quantity_value = int(str(quantity).strip())
    except (TypeError, ValueError):
        raise _InvalidOrder(
            RiskCode.INVALID_QUANTITY,
            f"Quantity must be a whole number, not {quantity!r}.",
            quantity=str(quantity),
        ) from None
    if quantity_value <= 0:
        raise _InvalidOrder(
            RiskCode.INVALID_QUANTITY,
            f"Quantity must be greater than zero, not {quantity_value}.",
            quantity=quantity_value,
        )

    price_value = _positive(_decimal(price))
    trigger_value = _positive(_decimal(trigger_price))

    if price_type_text in (PRICE_TYPE_LIMIT, PRICE_TYPE_SL) and price_value is None:
        raise _InvalidOrder(
            RiskCode.INVALID_PRICE,
            f"A {price_type_text} order needs a positive limit price.",
            price=str(price),
        )
    if price_type_text in (PRICE_TYPE_SL, PRICE_TYPE_SLM) and trigger_value is None:
        raise _InvalidOrder(
            RiskCode.INVALID_PRICE,
            f"A {price_type_text} order needs a positive trigger price.",
            trigger_price=str(trigger_price),
        )

    return _Intent(
        symbol=symbol_text,
        exchange=exchange_text,
        action=action_text,
        quantity=quantity_value,
        product=product_text,
        price_type=price_type_text,
        price=price_value,
        trigger_price=trigger_value,
        ltp=_positive(_decimal(ltp)),
        available_funds=_decimal(available_funds),
    )


@dataclass
class _Claim:
    """Bookkeeping for an order the guard has allowed but not yet seen dispatched.

    Attributes:
        fingerprints: One entry per claimed order.
        previous: The duplicate-window timestamp each fingerprint had before the
            claim, so a release restores it instead of erasing a real duplicate.
        created_at: When the claim was made, used only to prune abandoned ones.
    """

    fingerprints: tuple[str, ...]
    previous: dict[str, datetime]
    created_at: datetime
    # What the fill accounting did to the persisted open-position tracker and
    # the exit stamps, so a release can undo it. BUY claims carry a positive
    # delta (notional added); SELL claims a negative one (notional removed).
    position_deltas: tuple[tuple[str, Decimal], ...] = ()
    exit_stamped: tuple[str, ...] = ()


class RiskGuard:
    """Stateful order guard for one agent session.

    One guard holds one session's order count and duplicate registry, so the
    session cap means what it says across a multi-turn conversation. Use
    :func:`get_guard` to obtain the guard for a session rather than constructing
    one per turn, which would reset the count on every message.

    The instance is safe to share between the agent's real OS thread and a
    greenlet: every mutation happens under a real lock whose critical section is
    in-memory bookkeeping only, never a database read.
    """

    def __init__(
        self,
        *,
        session_id: str = "default",
        limits: RiskLimits | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        """Create a guard.

        Args:
            session_id: Identifier used only in messages and logs.
            limits: A fixed limit snapshot. When None the limits are read from
                the settings store on every check, so an operator's change
                applies to the next order rather than the next session. Pass an
                explicit snapshot in tests to run with no database.
            clock: Returns the current time. Injected for tests; a naive value
                is assumed to be UTC.
        """
        self.session_id = session_id
        self._fixed_limits = limits
        self._clock = clock or _now
        self._lock = real_threading.RLock()
        self._orders_claimed = 0
        self._recent: dict[str, datetime] = {}
        self._claims: dict[str, _Claim] = {}

    @property
    def orders_claimed(self) -> int:
        """How many orders this session has claimed so far.

        The count is the persisted daily budget for today's UTC date, so the
        cap applies across restarts and new conversations. When the store
        cannot be read, the in-memory count is returned and the claim path
        will already have refused the order.
        """
        try:
            from database import agent_db

            today = self._as_utc(self._clock()).date().isoformat()
            return agent_db.get_daily_order_count(today)
        except Exception:
            return self._orders_claimed

    def reset(self) -> None:
        """Forget the session's order count, duplicates and pending claims."""
        with self._lock:
            self._orders_claimed = 0
            self._recent.clear()
            self._claims.clear()

    def limits(self) -> RiskLimits:
        """The limits this guard is enforcing right now.

        Returns:
            The fixed snapshot given to the constructor, or a fresh read of the
            settings store.
        """
        return self._fixed_limits if self._fixed_limits is not None else get_risk_limits()

    def check_order(
        self,
        *,
        symbol: Any,
        exchange: Any,
        action: Any,
        quantity: Any,
        product: Any,
        price_type: Any = PRICE_TYPE_MARKET,
        price: Any = None,
        trigger_price: Any = None,
        ltp: Any = None,
        available_funds: Any = None,
        analyzer_mode: bool | None = None,
        claim: bool = True,
    ) -> Verdict:
        """Decide whether one order may be sent.

        Args:
            symbol: OpenAlgo symbol.
            exchange: OpenAlgo exchange code.
            action: BUY or SELL.
            quantity: Whole number of units.
            product: CNC, NRML or MIS.
            price_type: MARKET, LIMIT, SL or SL-M.
            price: Limit price, required for LIMIT and SL.
            trigger_price: Trigger price, required for SL and SL-M.
            ltp: Last traded price. The guard never fetches this; supplying it
                enables the notional, deviation and affordability checks, and
                omitting it makes them fail open with a warning.
            available_funds: Free cash available for a buy. Same fail-open rule.
            analyzer_mode: Whether the platform is in analyzer (sandbox) mode.
                When None the guard reads the platform toggle itself, and
                refuses the order if it cannot while the analyzer policy is on.
            claim: Whether an allowed order consumes a slot in the session cap
                and registers in the duplicate window. Pass False for a dry run.

        Returns:
            A :class:`Verdict`. When it allows and ``claim`` was set,
            ``details["claim_token"]`` identifies the claim for :meth:`release`
            or :meth:`commit`.
        """
        limits = self.limits()

        gate = self._gate(limits, analyzer_mode)
        if not gate.allowed:
            return gate

        try:
            intent = _normalise(
                symbol=symbol,
                exchange=exchange,
                action=action,
                quantity=quantity,
                product=product,
                price_type=price_type,
                price=price,
                trigger_price=trigger_price,
                ltp=ltp,
                available_funds=available_funds,
            )
        except _InvalidOrder as exc:
            return _blocked(exc.code, exc.reason, **exc.details)

        instrument = self._check_instrument(intent, limits)
        if not instrument.allowed:
            return instrument

        warnings: list[str] = []

        # Checks 8 and 9 and the claim happen in one hold of one lock, so a
        # second approval racing this one cannot pass the same cap or slip
        # through the same duplicate window.
        claimed = self._claim([intent], limits, claim=claim)
        if not claimed.allowed:
            return claimed
        token = claimed.details.get("claim_token")

        loss_gate = self._check_loss_containment(intent, limits)
        if not loss_gate.allowed:
            self._rollback(token)
            return loss_gate

        pricing = self._check_pricing(intent, limits, warnings)
        if not pricing.allowed:
            self._rollback(token)
            return pricing

        required = self._cash_required(intent)
        funds = self._check_affordability(
            required,
            intent.available_funds,
            limits,
            warnings,
            label=intent.describe(),
            increases_exposure=_increases_exposure(intent),
        )
        if not funds.allowed:
            self._rollback(token)
            return funds

        deltas, stamps, accounting_error = self._record_fill_accounting([intent])
        if accounting_error is not None:
            self._undo_accounting(deltas, stamps)
            self._rollback(token)
            return _blocked(
                RiskCode.STATE_UNAVAILABLE,
                "The exposure accounting could not be persisted, so this order is refused: "
                "an unverifiable exposure is an unknown exposure.",
            )
        self._attach_accounting(token, deltas, stamps)

        details: dict[str, Any] = {
            "symbol": intent.symbol,
            "exchange": intent.exchange,
            "action": intent.action,
            "quantity": intent.quantity,
            "product": intent.product,
            "price_type": intent.price_type,
            "orders_claimed": self.orders_claimed,
            "orders_allowed": limits.max_orders_per_session,
        }
        if token is not None:
            details["claim_token"] = token
        if warnings:
            details["warnings"] = warnings
        return _allowed(f"{intent.describe()} passed every risk check.", **details)

    def check_basket(
        self,
        legs: Sequence[Mapping[str, Any]],
        *,
        available_funds: Any = None,
        analyzer_mode: bool | None = None,
        claim: bool = True,
    ) -> Verdict:
        """Decide whether a multi-leg basket may be sent.

        The per-leg rules run leg by leg in the documented order; the aggregate
        rules run once, because a basket shares one session cap and one pool of
        cash. The whole basket is claimed atomically or not at all: a partial
        claim would leave the session's budget consumed by legs that were never
        sent.

        Args:
            legs: One mapping per leg, carrying the same keys
                :meth:`check_order` accepts (`symbol`, `exchange`, `action`,
                `quantity`, `product` and optionally `price_type`, `price`,
                `trigger_price`, `ltp`, `available_funds`). An unrecognised key
                is an error rather than an ignored typo.
            available_funds: Free cash available for the whole basket. Falls
                back to the largest per-leg value when the legs carry their own.
            analyzer_mode: As for :meth:`check_order`.
            claim: Whether an allowed basket consumes session-cap slots.

        Returns:
            A :class:`Verdict` covering the whole basket. A refusal names the
            leg that caused it.
        """
        limits = self.limits()

        gate = self._gate(limits, analyzer_mode)
        if not gate.allowed:
            return gate

        if not legs:
            return _blocked(RiskCode.EMPTY_BASKET, "The basket contains no legs.")

        intents: list[_Intent] = []
        for index, leg in enumerate(legs, start=1):
            if not isinstance(leg, Mapping):
                return _blocked(
                    RiskCode.INVALID_ARGUMENTS,
                    f"Leg {index} is not a mapping of order fields.",
                    leg=index,
                )
            unknown = sorted(set(leg) - _ORDER_KEYS)
            if unknown:
                return _blocked(
                    RiskCode.INVALID_ARGUMENTS,
                    f"Leg {index} carries unrecognised field(s): {', '.join(unknown)}.",
                    leg=index,
                    unknown=unknown,
                )
            try:
                intents.append(_normalise(**leg))
            except _InvalidOrder as exc:
                return _blocked(exc.code, f"Leg {index}: {exc.reason}", leg=index, **exc.details)
            except TypeError as exc:
                return _blocked(RiskCode.INVALID_ARGUMENTS, f"Leg {index}: {exc}", leg=index)

        for index, intent in enumerate(intents, start=1):
            instrument = self._check_instrument(intent, limits)
            if not instrument.allowed:
                return _blocked(
                    instrument.code,
                    f"Leg {index}: {instrument.reason}",
                    leg=index,
                    **instrument.details,
                )

        warnings: list[str] = []

        claimed = self._claim(intents, limits, claim=claim)
        if not claimed.allowed:
            return claimed
        token = claimed.details.get("claim_token")

        for index, intent in enumerate(intents, start=1):
            loss_gate = self._check_loss_containment(intent, limits)
            if not loss_gate.allowed:
                self._rollback(token)
                return _blocked(
                    loss_gate.code,
                    f"Leg {index}: {loss_gate.reason}",
                    leg=index,
                    **loss_gate.details,
                )

        # The legs share one account, so the cap applies to their combined
        # new notional, not each leg in isolation.
        try:
            from database import agent_db

            open_total = agent_db.total_open_exposure()
        except Exception:
            logger.exception("Agent risk guard could not read the tracked open positions")
            self._rollback(token)
            return _blocked(
                RiskCode.STATE_UNAVAILABLE,
                "The tracked open positions could not be read, so the exposure cap cannot "
                "be verified for this basket.",
            )
        basket_new = sum(
            (
                intent.reference_price * intent.quantity
                for intent in intents
                if _increases_exposure(intent) and intent.reference_price is not None
            ),
            Decimal("0"),
        )
        if open_total + basket_new > limits.max_exposure_usd:
            self._rollback(token)
            return _blocked(
                RiskCode.EXPOSURE_CAP,
                f"Tracked open exposure is {open_total:.2f} and this basket adds {basket_new:.2f}, "
                f"which would take the total past the {limits.max_exposure_usd} cap.",
                open_exposure=open_total,
                new_notional=basket_new,
                limit=limits.max_exposure_usd,
            )

        for index, intent in enumerate(intents, start=1):
            pricing = self._check_pricing(intent, limits, warnings)
            if not pricing.allowed:
                self._rollback(token)
                return _blocked(
                    pricing.code, f"Leg {index}: {pricing.reason}", leg=index, **pricing.details
                )

        basket_funds = _decimal(available_funds)
        if basket_funds is None:
            leg_funds = [leg.available_funds for leg in intents if leg.available_funds is not None]
            basket_funds = max(leg_funds) if leg_funds else None

        required = sum((self._cash_required(leg) for leg in intents), Decimal("0"))
        funds = self._check_affordability(
            required,
            basket_funds,
            limits,
            warnings,
            label=f"this basket of {len(intents)} legs",
            increases_exposure=any(_increases_exposure(leg) for leg in intents),
        )
        if not funds.allowed:
            self._rollback(token)
            return funds

        deltas, stamps, accounting_error = self._record_fill_accounting(intents)
        if accounting_error is not None:
            self._undo_accounting(deltas, stamps)
            self._rollback(token)
            return _blocked(
                RiskCode.STATE_UNAVAILABLE,
                "The exposure accounting could not be persisted, so this basket is refused: "
                "an unverifiable exposure is an unknown exposure.",
            )
        self._attach_accounting(token, deltas, stamps)

        details: dict[str, Any] = {
            "legs": len(intents),
            "symbols": [leg.symbol for leg in intents],
            "orders_claimed": self.orders_claimed,
            "orders_allowed": limits.max_orders_per_session,
        }
        if token is not None:
            details["claim_token"] = token
        if warnings:
            details["warnings"] = warnings
        return _allowed(f"All {len(intents)} legs passed every risk check.", **details)

    def check_destructive(
        self,
        operation: Any,
        *,
        analyzer_mode: bool | None = None,
        **details: Any,
    ) -> Verdict:
        """Decide whether a destructive, non-placing operation may run.

        Args:
            operation: One of :data:`TARGETED_OPERATIONS` or
                :data:`BULK_OPERATIONS`. An operation this guard does not
                recognise is refused: a destructive verb nobody has classified
                must not be waved through on the strength of being unfamiliar.
            analyzer_mode: As for :meth:`check_order`.
            **details: Anything worth recording on the verdict, such as the
                order id or the symbol being closed.

        Returns:
            A :class:`Verdict`. Destructive operations do not consume the
            session order cap: cancelling is how a session gets out of trouble,
            and a cap that blocks the exit is worse than no cap.
        """
        limits = self.limits()

        gate = self._gate(limits, analyzer_mode)
        if not gate.allowed:
            return gate

        name = str(operation or "").strip().lower()
        if name not in KNOWN_OPERATIONS:
            return _blocked(
                RiskCode.UNKNOWN_OPERATION,
                f"{operation!r} is not a destructive operation this guard knows. "
                f"Known operations: {', '.join(sorted(KNOWN_OPERATIONS))}.",
                operation=str(operation),
                **details,
            )

        if name in BULK_OPERATIONS and not limits.allow_bulk_destructive:
            return _blocked(
                RiskCode.BULK_NOT_ALLOWED,
                f"{name} affects the whole account and account-wide operations are "
                "switched off. Enable allow_bulk_destructive in the agent settings "
                "to permit it.",
                operation=name,
                **details,
            )

        if name == "close_position":
            symbol = details.get("symbol")
            if symbol:
                try:
                    from database import agent_db

                    agent_db.clear_open_position(str(symbol).strip().upper())
                    agent_db.record_symbol_exit(str(symbol).strip().upper())
                except Exception:
                    # The close itself must proceed (it reduces exposure), but
                    # the cooldown stamp may have been lost, so the operator
                    # must see it. Losing the stamp is fail-open for the
                    # cooldown only; no new exposure is trapped, and no budget
                    # is consumed.
                    logger.exception(
                        "Agent risk guard could not persist the exit stamp for %s", symbol
                    )

        return _allowed(f"{name} passed every risk check.", operation=name, **details)

    def release(self, verdict: Verdict) -> bool:
        """Give back a claim whose order was never sent.

        A dispatch that was refused downstream must return its slot, or the
        session's budget is consumed by orders that do not exist and the caller
        cannot retry inside the duplicate window.

        Args:
            verdict: The allowing verdict returned by :meth:`check_order` or
                :meth:`check_basket`.

        Returns:
            True when a claim was rolled back, False when there was nothing to
            roll back (already released, already committed, or a dry run).
        """
        return self._rollback(verdict.details.get("claim_token"))

    def commit(self, verdict: Verdict) -> bool:
        """Confirm that a claimed order was dispatched.

        The count and the duplicate fingerprint stay; only the rollback record
        is dropped. Not calling this is harmless - unreleased claims are pruned
        - but calling it keeps the guard's bookkeeping honest.

        Args:
            verdict: The allowing verdict returned by :meth:`check_order` or
                :meth:`check_basket`.

        Returns:
            True when a pending claim was found and finalised.
        """
        token = verdict.details.get("claim_token")
        if not token:
            return False
        with self._lock:
            return self._claims.pop(str(token), None) is not None

    def _gate(self, limits: RiskLimits, analyzer_mode: bool | None) -> Verdict:
        """Checks 1 to 3: kill switch, trading enabled, analyzer mode."""
        if limits.kill_switch_engaged:
            return _blocked(
                RiskCode.KILL_SWITCH,
                "The agent kill switch is engaged. Release it in the agent "
                "settings to resume trading.",
                source="setting",
            )

        if self._kill_switch_file_present(limits):
            return _blocked(
                RiskCode.KILL_SWITCH,
                f"The agent kill switch file {limits.kill_switch_path} exists. "
                "Delete it to resume trading.",
                source="file",
                kill_switch_file=str(limits.kill_switch_path),
            )

        if not limits.trading_enabled:
            return _blocked(
                RiskCode.TRADING_DISABLED,
                "Agent trading is switched off. Enable it in the agent settings first.",
            )

        if limits.require_analyzer_mode:
            resolved = self._resolve_analyzer_mode(analyzer_mode)
            if resolved is None:
                return _blocked(
                    RiskCode.ANALYZER_UNKNOWN,
                    "Analyzer mode is required but the platform's analyzer setting "
                    "could not be read, so this order cannot be confirmed as a "
                    "sandbox order.",
                )
            if not resolved:
                return _blocked(
                    RiskCode.ANALYZER_REQUIRED,
                    "The agent is restricted to analyzer mode and the platform is in "
                    "live mode. Switch the platform to analyzer mode, or turn off "
                    "require_analyzer_mode in the agent settings.",
                )

        return _allowed("Session gate passed.")

    def _kill_switch_file_present(self, limits: RiskLimits) -> bool:
        """Whether the kill-switch file exists.

        The file is the half of the switch that works when the database does
        not: an operator can `touch` it from a shell with the UI unreachable,
        and it survives a restart. An unreadable path counts as absent, which is
        what `Path.exists()` does anyway.
        """
        try:
            return limits.kill_switch_path.exists()
        except Exception:
            logger.exception(
                "Could not test the agent kill switch at %s; treating it as absent",
                limits.kill_switch_file,
            )
            return False

    def _resolve_analyzer_mode(self, provided: bool | None) -> bool | None:
        """Resolve the platform analyzer toggle.

        Args:
            provided: The caller's value, used as-is when given so the guard
                stays runnable with no platform.

        Returns:
            True in analyzer mode, False in live mode, None when it could not be
            determined.
        """
        if provided is not None:
            return bool(provided)
        try:
            from database.settings_db import get_analyze_mode

            return bool(get_analyze_mode())
        except Exception:
            logger.exception("Agent risk guard could not read the platform analyzer setting")
            return None

    def _check_instrument(self, intent: _Intent, limits: RiskLimits) -> Verdict:
        """Checks 4 to 7: symbol, exchange, product, quantity."""
        if intent.symbol in limits.symbol_blocklist:
            return _blocked(
                RiskCode.SYMBOL_BLOCKED,
                f"{intent.symbol} is on the agent's symbol blocklist.",
                symbol=intent.symbol,
            )
        if limits.symbol_allowlist and intent.symbol not in limits.symbol_allowlist:
            return _blocked(
                RiskCode.SYMBOL_NOT_ALLOWED,
                f"{intent.symbol} is not on the agent's symbol allowlist.",
                symbol=intent.symbol,
                allowed=sorted(limits.symbol_allowlist),
            )
        if intent.exchange not in limits.allowed_exchanges:
            return _blocked(
                RiskCode.EXCHANGE_NOT_ALLOWED,
                f"The agent may not trade on {intent.exchange}. "
                f"Allowed: {', '.join(sorted(limits.allowed_exchanges))}.",
                exchange=intent.exchange,
                allowed=sorted(limits.allowed_exchanges),
            )
        if intent.product not in limits.allowed_products:
            return _blocked(
                RiskCode.PRODUCT_NOT_ALLOWED,
                f"The agent may not use product {intent.product}. "
                f"Allowed: {', '.join(sorted(limits.allowed_products))}.",
                product=intent.product,
                allowed=sorted(limits.allowed_products),
            )
        if intent.quantity > limits.max_order_quantity:
            return _blocked(
                RiskCode.QUANTITY_EXCEEDED,
                f"Quantity {intent.quantity} exceeds the per-order limit of "
                f"{limits.max_order_quantity}.",
                quantity=intent.quantity,
                limit=limits.max_order_quantity,
            )
        return _allowed("Instrument checks passed.")

    def _claim(self, intents: Sequence[_Intent], limits: RiskLimits, *, claim: bool) -> Verdict:
        """Checks 8 and 9, and the claim itself, under one hold of the lock.

        Everything inside the lock is in-memory bookkeeping, so a greenlet
        waiting on it is never waiting on a database or a broker.
        """
        now = self._as_utc(self._clock())
        window = limits.duplicate_order_window_seconds
        needed = len(intents)

        try:
            from database import agent_db

            today = now.date().isoformat()
            claimed_today = agent_db.get_daily_order_count(today)
        except Exception:
            logger.exception("Agent risk guard could not read the persisted order budget")
            return _blocked(
                RiskCode.STATE_UNAVAILABLE,
                "The persisted order budget could not be read, so this order cannot be "
                "verified against the session cap. No new exposure is allowed until it "
                "is readable again.",
            )

        with self._lock:
            if claimed_today + needed > limits.max_orders_per_session:
                return _blocked(
                    RiskCode.SESSION_CAP_REACHED,
                    f"The daily order budget is {claimed_today} of {limits.max_orders_per_session} "
                    f"claimed orders and this request needs {needed} more. The budget is "
                    "persisted, so starting a new conversation does not reset it. "
                    "Start over tomorrow (UTC) or raise max_orders_per_session.",
                    orders_claimed=claimed_today,
                    orders_allowed=limits.max_orders_per_session,
                    requested=needed,
                )

            if window > 0:
                seen: dict[str, int] = {}
                for index, intent in enumerate(intents, start=1):
                    fingerprint = intent.fingerprint
                    earlier = seen.get(fingerprint)
                    if earlier is not None:
                        return _blocked(
                            RiskCode.DUPLICATE_ORDER,
                            f"Leg {index} repeats leg {earlier} exactly "
                            f"({intent.describe()}). Send it once with the combined "
                            "quantity if that is what you meant.",
                            symbol=intent.symbol,
                            leg=index,
                            duplicate_of=earlier,
                        )
                    seen[fingerprint] = index

                    previous = self._recent.get(fingerprint)
                    if previous is not None:
                        age = (now - previous).total_seconds()
                        if 0 <= age < window:
                            return _blocked(
                                RiskCode.DUPLICATE_ORDER,
                                f"An identical order ({intent.describe()}) was allowed "
                                f"{age:.1f}s ago, inside the {window}s duplicate window. "
                                "Wait, or change the order.",
                                symbol=intent.symbol,
                                seconds_ago=round(age, 1),
                                window_seconds=window,
                            )

            if not claim:
                return _allowed("Session cap and duplicate window passed (not claimed).")

            fingerprints = tuple(intent.fingerprint for intent in intents)
            previous_stamps = {
                fingerprint: self._recent[fingerprint]
                for fingerprint in fingerprints
                if fingerprint in self._recent
            }
            for fingerprint in fingerprints:
                self._recent[fingerprint] = now

            try:
                for _ in range(needed):
                    agent_db.increment_daily_order_count(today)
            except Exception:
                logger.exception("Agent risk guard could not persist the order budget")
                for fingerprint in fingerprints:
                    restored = previous_stamps.get(fingerprint)
                    if restored is None:
                        self._recent.pop(fingerprint, None)
                    else:
                        self._recent[fingerprint] = restored
                return _blocked(
                    RiskCode.STATE_UNAVAILABLE,
                    "The order budget could not be persisted, so this order is refused: "
                    "an unverifiable budget is an unknown budget.",
                )

            self._orders_claimed += needed
            token = uuid4().hex
            self._claims[token] = _Claim(
                fingerprints=fingerprints, previous=previous_stamps, created_at=now
            )
            self._prune(now, window)

        return _allowed("Session cap and duplicate window passed.", claim_token=token)

    def _rollback(self, token: Any) -> bool:
        """Undo a claim, restoring the count and the duplicate registry."""
        if not token:
            return False
        with self._lock:
            record = self._claims.pop(str(token), None)
            if record is None:
                return False
            self._orders_claimed = max(0, self._orders_claimed - len(record.fingerprints))
            for fingerprint in record.fingerprints:
                restored = record.previous.get(fingerprint)
                if restored is None:
                    self._recent.pop(fingerprint, None)
                else:
                    self._recent[fingerprint] = restored
            try:
                from database import agent_db

                today = self._as_utc(record.created_at).date().isoformat()
                for _ in range(len(record.fingerprints)):
                    agent_db.decrement_daily_order_count(today)
            except Exception:
                logger.exception(
                    "Agent risk guard could not release the persisted daily order budget"
                )
            self._undo_accounting(record.position_deltas, record.exit_stamped)
        return True

    def _prune(self, now: datetime, window: int) -> None:
        """Bound the duplicate registry and the pending-claim table.

        Called with the lock held. A worker that never restarts must not grow a
        dictionary for the life of the process, so entries leave on age first
        and on size second.
        """
        if window > 0:
            expired = [
                fingerprint
                for fingerprint, stamp in self._recent.items()
                if (now - stamp).total_seconds() >= window
            ]
            for fingerprint in expired:
                self._recent.pop(fingerprint, None)

        if len(self._recent) > _MAX_TRACKED_FINGERPRINTS:
            oldest = sorted(self._recent.items(), key=lambda item: item[1])
            for fingerprint, _stamp in oldest[: len(self._recent) - _MAX_TRACKED_FINGERPRINTS]:
                self._recent.pop(fingerprint, None)

        if len(self._claims) > _MAX_PENDING_CLAIMS:
            oldest_claims = sorted(self._claims.items(), key=lambda item: item[1].created_at)
            for token, _record in oldest_claims[: len(self._claims) - _MAX_PENDING_CLAIMS]:
                self._claims.pop(token, None)

    def _attach_accounting(
        self,
        token: Any,
        deltas: tuple[tuple[str, Decimal], ...],
        exit_stamped: tuple[str, ...],
    ) -> None:
        """Record the accounting applied for this claim, for a later release."""
        if not token:
            return
        with self._lock:
            record = self._claims.get(str(token))
            if record is not None:
                record.position_deltas = tuple(deltas)
                record.exit_stamped = tuple(exit_stamped)

    def _undo_accounting(
        self, deltas: Sequence[tuple[str, Decimal]], exit_stamped: Sequence[str]
    ) -> None:
        """Give back persisted open-position accounting after a failed claim."""
        try:
            from database import agent_db

            for symbol, delta in deltas:
                if delta >= 0:
                    agent_db.reduce_open_position(symbol, delta)
                else:
                    agent_db.add_open_position(symbol, -delta)
            for symbol in exit_stamped:
                agent_db.clear_symbol_exit(symbol)
        except Exception:
            logger.exception("Agent risk guard could not undo the exposure accounting")

    def _record_fill_accounting(
        self, intents: Sequence[_Intent]
    ) -> tuple[tuple[tuple[str, Decimal], ...], tuple[str, ...], Exception | None]:
        """Apply the claim to the persisted open-position tracker.

        A BUY adds its notional to the tracker; a SELL reduces it, and when
        the tracked position reaches zero the symbol's exit is stamped for the
        cooldown. Returns the deltas and the stamps actually applied (even on
        failure, for rollback), plus the exception if the store failed.
        """
        deltas: list[tuple[str, Decimal]] = []
        stamps: list[str] = []
        try:
            from database import agent_db

            for intent in intents:
                reference = intent.reference_price
                if intent.action == "BUY":
                    if reference is not None:
                        added = reference * intent.quantity
                        agent_db.add_open_position(intent.symbol, added)
                        deltas.append((intent.symbol, added))
                    continue
                existing = agent_db.get_open_position(intent.symbol)
                if existing is not None and existing > 0:
                    removed = reference * intent.quantity if reference is not None else existing
                    agent_db.reduce_open_position(intent.symbol, removed)
                    deltas.append((intent.symbol, -removed))
                    if existing - removed <= 0:
                        agent_db.record_symbol_exit(intent.symbol, self._as_utc(self._clock()))
                        stamps.append(intent.symbol)
                    continue
                # A SELL that finds no tracked position never stamps an exit,
                # so a short or a manual position never fabricates a cooldown.
        except Exception as exc:
            return tuple(deltas), tuple(stamps), exc
        return tuple(deltas), tuple(stamps), None

    def _check_loss_containment(self, intent: _Intent, limits: RiskLimits) -> Verdict:
        """Check 10: the four persisted-counter rules for BUY orders.

        Read-only: it never writes. Enforced for exposure-increasing orders
        only, and it fails closed when the counter store cannot be read,
        because an unverifiable budget or exposure is an unknown one.
        """
        if not _increases_exposure(intent):
            return _allowed("Exposure checks apply to orders that add exposure.")

        today = self._as_utc(self._clock()).date().isoformat()
        try:
            from database import agent_db

            realised = agent_db.get_daily_realised_pnl(today)
        except Exception:
            logger.exception("Agent risk guard could not read the daily realised P&L")
            return _blocked(
                RiskCode.STATE_UNAVAILABLE,
                "The daily realised-P&L counter could not be read, so it cannot be verified "
                "against the daily loss limit. New exposure is blocked until it is readable.",
            )
        if realised <= -limits.max_daily_loss_usd:
            return _blocked(
                RiskCode.DAILY_LOSS,
                f"Realised P&L for the day is {realised:.2f} against a {limits.max_daily_loss_usd} "
                "loss limit; new exposure is halted for the rest of the UTC day. Reductions "
                "and closes still pass.",
                realised_pnl=realised,
                limit=limits.max_daily_loss_usd,
            )

        try:
            from database import agent_db

            last_exit = agent_db.get_symbol_last_exit_at(intent.symbol)
        except Exception:
            logger.exception("Agent risk guard could not read the symbol exit stamp")
            return _blocked(
                RiskCode.STATE_UNAVAILABLE,
                "The symbol's last-exit stamp could not be read, so the cooldown cannot be "
                "verified. New exposure is blocked until it is readable.",
            )
        if last_exit is not None and limits.cooldown_minutes > 0:
            age = self._as_utc(self._clock()) - last_exit
            if age.total_seconds() < limits.cooldown_minutes * 60:
                return _blocked(
                    RiskCode.COOLDOWN,
                    f"{intent.symbol} exited {age.total_seconds() / 60:.1f} minutes ago; "
                    f"re-entry is blocked for {limits.cooldown_minutes} minutes after an exit.",
                    symbol=intent.symbol,
                    cooldown_minutes=limits.cooldown_minutes,
                    minutes_since_exit=round(age.total_seconds() / 60, 1),
                )

        try:
            from database import agent_db

            existing = agent_db.get_open_position(intent.symbol)
            open_total = agent_db.total_open_exposure()
        except Exception:
            logger.exception("Agent risk guard could not read the tracked open positions")
            return _blocked(
                RiskCode.STATE_UNAVAILABLE,
                "The tracked open positions could not be read, so the position and "
                "exposure limits cannot be verified. New exposure is blocked until it is readable.",
            )

        if existing is not None and existing > 0 and not limits.allow_multiple_positions_per_symbol:
            return _blocked(
                RiskCode.MAX_POSITION,
                f"{intent.symbol} already has a tracked open position of {existing:.2f}; "
                "at most one open position per symbol is allowed unless "
                "allow_multiple_positions_per_symbol is set.",
                symbol=intent.symbol,
                open_notional=existing,
            )

        reference = intent.reference_price
        if reference is not None:
            new_notional = reference * intent.quantity
            if open_total + new_notional > limits.max_exposure_usd:
                return _blocked(
                    RiskCode.EXPOSURE_CAP,
                    f"Tracked open exposure is {open_total:.2f} and this order adds "
                    f"{new_notional:.2f}, which would take the total past the {limits.max_exposure_usd} "
                    "cap.",
                    open_exposure=open_total,
                    new_notional=new_notional,
                    limit=limits.max_exposure_usd,
                )
        return _allowed("Loss-containment checks passed.")

    def _check_pricing(self, intent: _Intent, limits: RiskLimits, warnings: list[str]) -> Verdict:
        """Check 11: notional cap and limit-price deviation."""
        reference = intent.reference_price
        if reference is None:
            if _increases_exposure(intent):
                return _blocked(
                    RiskCode.PRICE_UNAVAILABLE,
                    f"No reference price is available for {intent.symbol}, so the notional "
                    "cap cannot be verified. New exposure is blocked until a price arrives.",
                    symbol=intent.symbol,
                )
            message = (
                f"No reference price for {intent.symbol}, so the notional cap and the "
                "price-deviation check were skipped."
            )
            logger.warning("Agent risk guard: %s", message)
            warnings.append(message)
            return _allowed("Pricing checks skipped.")

        notional = reference * intent.quantity
        if notional > limits.max_order_value:
            return _blocked(
                RiskCode.NOTIONAL_EXCEEDED,
                f"The order is worth about {notional:.2f}, above the per-order limit of "
                f"{limits.max_order_value}.",
                notional=notional,
                limit=limits.max_order_value,
                reference_price=reference,
                quantity=intent.quantity,
            )

        deviation_price = intent.deviation_price
        if deviation_price is not None and intent.ltp is None:
            if _increases_exposure(intent):
                return _blocked(
                    RiskCode.PRICE_UNAVAILABLE,
                    f"The last traded price for {intent.symbol} could not be fetched, so "
                    "the price-deviation check cannot be verified. New exposure is blocked "
                    "until a price arrives.",
                    symbol=intent.symbol,
                )
            message = (
                f"No last traded price for {intent.symbol}, so the price-deviation "
                "check was skipped."
            )
            logger.warning("Agent risk guard: %s", message)
            warnings.append(message)
        elif deviation_price is not None and intent.ltp is not None:
            deviation = abs(deviation_price - intent.ltp) / intent.ltp * HUNDRED
            if deviation > limits.max_price_deviation_pct:
                return _blocked(
                    RiskCode.PRICE_DEVIATION,
                    f"The order price {deviation_price} is {deviation:.2f}% away from the "
                    f"last traded price {intent.ltp}, beyond the "
                    f"{limits.max_price_deviation_pct}% limit.",
                    order_price=deviation_price,
                    ltp=intent.ltp,
                    deviation_pct=deviation.quantize(Decimal("0.01")),
                    limit_pct=limits.max_price_deviation_pct,
                )

        return _allowed("Pricing checks passed.", notional=notional)

    def _cash_required(self, intent: _Intent) -> Decimal:
        """Approximate the cash a leg needs.

        Only a buy consumes cash here. A sell either reduces an existing
        position or opens a short whose requirement is broker-computed margin,
        which this guard has no way to know and deliberately does not guess:
        inventing a margin number would either block legitimate orders or give
        false comfort. The broker performs the real check either way.

        Args:
            intent: The normalised order.

        Returns:
            The approximate rupee requirement, or zero when it cannot be
            computed or the leg is a sell.
        """
        if intent.action != "BUY":
            return Decimal("0")
        reference = intent.reference_price
        if reference is None:
            return Decimal("0")
        return reference * intent.quantity

    def _check_affordability(
        self,
        required: Decimal,
        available: Decimal | None,
        limits: RiskLimits,
        warnings: list[str],
        *,
        label: str,
        increases_exposure: bool,
    ) -> Verdict:
        """Check 12: affordability against available funds.

        New exposure fails closed when the funds figure is missing: the broker's
        margin check is not a control the guard may skip. Reductions always
        pass.
        """
        if required <= 0:
            return _allowed("Affordability check not applicable.")

        if available is None:
            if increases_exposure:
                return _blocked(
                    RiskCode.FUNDS_UNAVAILABLE,
                    f"The available-funds figure for {label} could not be fetched, so "
                    "affordability cannot be verified. New exposure is blocked until the "
                    "funds figure arrives.",
                )
            message = (
                f"Available funds are unknown, so the affordability check for {label} was skipped."
            )
            logger.warning("Agent risk guard: %s", message)
            warnings.append(message)
            return _allowed("Affordability check skipped.")

        usable = available * limits.max_funds_utilization_pct / HUNDRED
        if required > usable:
            return _blocked(
                RiskCode.INSUFFICIENT_FUNDS,
                f"{label} needs about {required:.2f} but only {usable:.2f} of the "
                f"{available:.2f} available may be used "
                f"({limits.max_funds_utilization_pct}% of funds).",
                required=required.quantize(Decimal("0.01")),
                usable=usable.quantize(Decimal("0.01")),
                available=available.quantize(Decimal("0.01")),
                utilization_pct=limits.max_funds_utilization_pct,
            )
        return _allowed("Affordability check passed.", required=required.quantize(Decimal("0.01")))

    @staticmethod
    def _as_utc(value: datetime) -> datetime:
        """Treat a naive datetime as UTC so an injected clock cannot break arithmetic."""
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


# One guard per agent session, bounded in both size and age. A plain dictionary
# here would grow for the life of a Gunicorn worker that never restarts; the TTL
# also means an abandoned conversation eventually releases its order budget.
_GUARD_TTL_SECONDS = 6 * 60 * 60
_guards: TTLCache = TTLCache(maxsize=128, ttl=_GUARD_TTL_SECONDS, timer=time.monotonic)
_guards_lock = real_threading.RLock()


def get_guard(session_id: str, *, limits: RiskLimits | None = None) -> RiskGuard:
    """Return the guard for a session, creating it on first use.

    The session cap and the duplicate window are only meaningful across turns,
    so every tool call in one conversation must reach the same guard.

    Args:
        session_id: The agent session or conversation identifier.
        limits: A fixed limit snapshot for a newly created guard. Ignored when
            the guard already exists.

    Returns:
        The shared :class:`RiskGuard` for that session.
    """
    key = str(session_id or "default")
    with _guards_lock:
        guard = _guards.get(key)
        if guard is None:
            guard = RiskGuard(session_id=key, limits=limits)
            _guards[key] = guard
        return guard


def reset_guard(session_id: str) -> None:
    """Drop one session's guard, resetting its cap and duplicate registry.

    Args:
        session_id: The agent session or conversation identifier.
    """
    with _guards_lock:
        _guards.pop(str(session_id or "default"), None)


def clear_guards() -> None:
    """Drop every session guard. Used at shutdown and by tests."""
    with _guards_lock:
        _guards.clear()
