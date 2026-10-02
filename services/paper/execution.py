"""Paper fills priced from the real order book, not from a fixed offset.

The model this replaces
----------------------
The paper system filled a buy at ``last * (1 + slippage_rate)``
(``engine.trading_cycle``) and wrote the plan's own ``entry_price`` into the
fills table (``planner.execute``). Both take a candle close and add a fixed
fraction of it, so a simulated fill never touched the market it claimed to be
in: a coin quoted 0.20% wide filled exactly as tightly as one quoted 0.01%
wide, and an order bigger than everything that traded that day filled in full.
The paper book was therefore systematically kinder than the market, which is the
one thing a paper account must not be.

What this model does
--------------------
:func:`quote_execution` prices one order against a live quote from
``services.foreign_data_service.get_foreign_quote(symbol, "CRYPTO")``:

* a BUY lifts the **ask** and a SELL hits the **bid**, because those are the
  prices the order would actually pay, read out of the real two-sided book;
* slippage is charged in **basis points of the observed spread**, so the cost of
  crossing the market scales with how wide that market actually is;
* a book wider than ``execution_max_spread_bps`` is refused, because no trader
  could have got filled inside it and calling that a fill is a lie told in the
  journal;
* an order larger than ``execution_volume_fraction`` of the observed volume is
  filled **partially** and the unfilled quantity comes back as an open
  remainder, because a paper book is not infinitely deep;
* every refusal is a stable slug plus a sentence naming the number behind it.

Nothing here is invented
------------------------
There is no default price anywhere in this module, and no fallback to a candle
close. Every price comes from the quote's bid or ask; if the quote cannot supply
the field a calculation needs, the answer is a ``price_unavailable`` refusal
rather than a stand-in number, because a stand-in is exactly the defect this
module exists to remove. The only configured constants are *thresholds* (how
wide a spread is too wide, what share of volume may be taken, how many
milliseconds of latency to model) and they are all reported back in the result,
so a caller never has to guess which ones produced a fill.

Deliberately absent: ``qty_below_lot``
-------------------------------------
There is no such rejection, and there must not be one. This engine has no
per-symbol lot size and never had any (``engine.plan_size`` says so at length);
inventing a minimum quantity would put a size in the journal that no exchange
would accept, and would refuse orders the paper account is in fact able to
place. The only quantity floor applied here is ``engine.QTY_DECIMALS``, the same
precision the engine already floors every quantity to.

Slippage when the quote carries no spread
-----------------------------------------
``fetch_crypto_quote`` reads Binance's 24h ticker, which does not publish a
book, and defaults ``bid``/``ask`` to the last price. A quote from that endpoint
therefore routinely has ``bid == ask`` -- one price, no spread to measure. In
that case there is no real spread to take a basis-point fraction *of*, so the
model falls back to the account's own existing convention,
``engine._slip_rate(cfg)`` (bps of price), and says so in
``slippage_basis``. That is not a new constant: it is the number the engine
already charges every paper fill, and the result carries it so the substitution
is visible instead of silent. Set ``execution_require_observed_spread`` to True
to refuse such orders outright instead.

Nothing in this module writes to the database or places an order. Given a quote
it is a pure function of (quote, side, qty, config); given no quote it reads one
live quote, and given no config it reads the config table. Either way it leaves
the decision of how a refusal is journalled to the caller.
"""

from __future__ import annotations

import math

from utils.logging import get_logger

from . import db, engine

logger = get_logger(__name__)


# ------------------------------------------------------------------ vocabulary

STATUS_FILLED = "filled"
STATUS_PARTIAL = "partial"
STATUS_REJECTED = "rejected"

#: A stable slug for every outcome that is not a clean full fill. ``reason`` is
#: ``None`` on a full fill, ``partially_filled`` on a partial, and one of the
#: rejection slugs below otherwise. Callers match on the slug, never on the
#: sentence.
BAD_REQUEST = "bad_request"
PRICE_UNAVAILABLE = "price_unavailable"
CROSSED_QUOTE = "crossed_quote"
SPREAD_TOO_WIDE = "spread_too_wide"
INSUFFICIENT_LIQUIDITY = "insufficient_liquidity"
PARTIALLY_FILLED = "partially_filled"

#: Every slug ``quote_execution`` can return. There is deliberately no
#: ``qty_below_lot``: this engine has no lot size, so no minimum quantity can be
#: justified from anything real.
REASONS = (
    BAD_REQUEST,
    PRICE_UNAVAILABLE,
    CROSSED_QUOTE,
    SPREAD_TOO_WIDE,
    INSUFFICIENT_LIQUIDITY,
    PARTIALLY_FILLED,
)

REJECTION_REASONS = tuple(r for r in REASONS if r != PARTIALLY_FILLED)


# --------------------------------------------------------------- configuration
#
# Thresholds, not prices. Every one is settable in the paper ``config`` table
# and every one is echoed in the result, so a fill always says which rule
# produced it.

KEY_SPREAD_SLIPPAGE_BPS = "execution_slippage_bps"
KEY_MAX_SPREAD_BPS = "execution_max_spread_bps"
KEY_VOLUME_FRACTION = "execution_volume_fraction"
KEY_LATENCY_MS = "execution_latency_ms"
KEY_REQUIRE_SPREAD = "execution_require_observed_spread"

#: Slippage as a share of the observed spread. A quarter of the spread is the
#: cost of crossing a book that is already a quarter wide, which is what a
#: market order actually pays.
DEFAULT_SPREAD_SLIPPAGE_BPS = 25.0

#: The widest book this paper account will cross. Binance majors sit inside a
#: couple of basis points, so 25 bps (0.25%) refuses a genuinely bad book while
#: leaving an ordinary one alone. Above this the honest answer is "not now", not
#: a fill at whatever price happened to print.
DEFAULT_MAX_SPREAD_BPS = 25.0
#: Ceiling for the configured limit, so a mistyped 2500 cannot silently disable
#: the check by refusing every order.
MAX_SPREAD_BPS_CEILING = 1000.0

#: The share of the observed volume one paper order may take. A paper position
#: is capped at a fraction of a small account, so this never binds on BTC; it
#: binds exactly where pretending is worst, on a thin coin where the order is a
#: real share of everything that traded.
DEFAULT_VOLUME_FRACTION = 0.01

#: Modelled round-trip latency. Reported, never slept: this is a market model,
#: not a queue, and blocking here would stall the worker cycle that asked.
DEFAULT_LATENCY_MS = 250.0
#: Extra latency for consuming the whole volume allowance. Reaching the far side
#: of an order book takes time even in a model, and it makes the modelled
#: latency a function of the order rather than a constant.
FULL_ALLOWANCE_LATENCY_MS = 1000.0

#: The stable shape of the returned dict, on every outcome including a refusal.
#: The refusal paths keep the same keys with empty or None values so a caller can
#: read ``result["spread_bps"]`` without first checking the status.
RESULT_KEYS = (
    "status",
    "reason",
    "detail",
    "symbol",
    "side",
    "quote_source",
    "requested_qty",
    "filled_qty",
    "remainder_qty",
    "price",
    "reference_price",
    "bid",
    "ask",
    "ltp",
    "spread_bps",
    "max_spread_bps",
    "slippage_bps",
    "slippage_basis",
    "slippage",
    "requested_notional",
    "notional",
    "remainder_notional",
    "volume",
    "volume_fraction",
    "volume_cap_notional",
    "fee",
    "fee_rate",
    "latency_ms",
    "latency_base_ms",
    "latency_impact_ms",
)


# -------------------------------------------------------------------- helpers


def _positive(value) -> float | None:
    """A strictly positive finite number, or ``None``. Never a substitute."""
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number > 0 else None


def _observed_volume(value) -> float | None:
    """A volume observation, or ``None`` when the quote carries none.

    Zero is a real observation (nothing traded) and is kept, because it is a
    refusal the caller should hear about. ``None`` means "not reported", which is
    a different thing and does not refuse anything.
    """
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number >= 0 else None


def _cfg_number(cfg: dict, key: str, default: float, low: float, high: float) -> float:
    """Read a numeric config key, clamped to a range that keeps it meaningful.

    ``engine._config_float`` does the tolerant read; the clamp is this model's,
    because an unclamped threshold is how a configuration value turns a safety
    check off by accident.
    """
    return max(low, min(high, engine._config_float(cfg, key, default)))


def _fetch_quote(symbol: str) -> dict | None:
    """The live quote for a symbol, or ``None`` when there is no data.

    ``get_foreign_quote`` returns ``None`` when the upstream feed could not be
    reached. That is reported as no data and never as a price.
    """
    from services.foreign_data_service import get_foreign_quote

    try:
        quote = get_foreign_quote(symbol, "CRYPTO")
    except Exception:
        logger.warning("Live quote unavailable for %s; no fill price to take", symbol)
        return None
    return quote if isinstance(quote, dict) else None


def _result(symbol: str, side: str, requested_qty: float | None, *, reason: str) -> dict:
    """A result carrying every key of :data:`RESULT_KEYS`, nothing filled.

    Money and quantity keys are ``0.0`` and observation keys are ``None``, so a
    caller can read a refusal the same way it reads a fill and a missing price is
    never mistaken for a price of zero.
    """
    out = dict.fromkeys(RESULT_KEYS)
    out.update(
        {
            "status": STATUS_REJECTED,
            "reason": reason,
            "detail": "",
            "symbol": symbol,
            "side": side,
            "quote_source": None,
            "requested_qty": requested_qty,
            "requested_notional": 0.0,
            "filled_qty": 0.0,
            "remainder_qty": 0.0,
            "price": 0.0,
            "reference_price": 0.0,
            "slippage": 0.0,
            "notional": 0.0,
            "remainder_notional": 0.0,
            "fee": 0.0,
            "latency_ms": 0.0,
            "latency_impact_ms": 0.0,
        }
    )
    return out


def _thresholds(cfg: dict) -> dict:
    """The configured limits, read once and clamped so none can be switched off.

    These come from the config table, not from the market, so they are reported
    on every outcome including a refusal: a refusal that does not say which limit
    it applied leaves the reader guessing at the number that turned the order
    away.
    """
    return {
        "slippage_bps": _cfg_number(
            cfg,
            KEY_SPREAD_SLIPPAGE_BPS,
            engine._config_float(cfg, "slippage_bps", DEFAULT_SPREAD_SLIPPAGE_BPS),
            0.0,
            10_000.0,
        ),
        "max_spread_bps": _cfg_number(
            cfg, KEY_MAX_SPREAD_BPS, DEFAULT_MAX_SPREAD_BPS, 0.0, MAX_SPREAD_BPS_CEILING
        ),
        "volume_fraction": _cfg_number(cfg, KEY_VOLUME_FRACTION, DEFAULT_VOLUME_FRACTION, 0.0, 1.0),
        "fee_rate": engine._fee_rate(cfg),
        "latency_base_ms": _latency(cfg, None)[1],
    }


def _latency(cfg: dict, share_of_allowance: float | None) -> tuple[float, float, float]:
    """Modelled latency in ms as (total, base, impact). Never slept.

    ``share_of_allowance`` is the order's notional over the volume allowance it
    was measured against: below 1 for a full fill, above 1 for a partial, and
    ``None`` when no volume was reported and nothing can be said about the share.
    """
    base = _cfg_number(cfg, KEY_LATENCY_MS, DEFAULT_LATENCY_MS, 0.0, 60_000.0)
    if share_of_allowance is None:
        return base, base, 0.0
    impact = FULL_ALLOWANCE_LATENCY_MS * max(0.0, min(1.0, share_of_allowance))
    return base + impact, base, impact


def _px(value: float) -> str:
    """A price or an amount at the precision it was computed, without float noise.

    ``:g`` would print a 200.0202 fill as ``200.02`` -- the exact price the model
    chose, rounded back to the number it was trying to beat.
    """
    return f"{value:,.8f}".rstrip("0").rstrip(".") or "0"


def _qty(value: float) -> str:
    """A quantity at the engine's own ``QTY_DECIMALS`` precision."""
    return f"{value:,.12f}".rstrip("0").rstrip(".") or "0"


def _pct(fraction: float) -> str:
    """A share as a percentage a trader can read, never ``0.00%`` for a real one."""
    percent = fraction * 100.0
    return f"{percent:.2f}%" if percent >= 0.01 else f"{percent:.0e}%"


# --------------------------------------------------------------- public model


def quote_execution(
    symbol: str,
    side: str,
    qty: float,
    *,
    cfg: dict | None = None,
    quote: dict | None = None,
    volume: float | None = None,
) -> dict:
    """Price one paper order against the real market and report what it got.

    A BUY fills at the ask and a SELL at the bid, plus slippage charged in basis
    points of the observed spread. A book wider than
    ``execution_max_spread_bps`` is refused; an order larger than
    ``execution_volume_fraction`` of the observed volume is filled partially and
    the rest is returned as an open remainder. Every refusal names the number
    that caused it.

    Args:
        symbol: Binance pair, e.g. ``BTCUSDT``.
        side: ``BUY`` or ``SELL``. Case and surrounding space are ignored; any
            other value is a ``bad_request`` refusal.
        qty: Quantity in units of the coin, as the planner sized it.
        cfg: Paper config mapping. ``None`` reads the config table, so the
            caller can stay on its own connection and inside its transaction.
        quote: A quote already in hand. ``None`` fetches the live one, which is
            the normal path; pass a quote to price several orders against the
            same book without re-fetching it.
        volume: Volume to measure the order against, overriding the quote's own
            ``volume``. Pass a candle's volume here to size against a bar rather
            than against the rolling 24h figure the quote reports.

    Returns:
        A dict with exactly the keys in :data:`RESULT_KEYS`. ``status`` is
        ``filled``, ``partial`` or ``rejected``; ``reason`` is ``None`` on a
        full fill, ``partially_filled`` on a partial, and one of
        :data:`REJECTION_REASONS` on a refusal. ``price`` and ``fee`` are the
        numbers to book against -- recomputing either is how a caller ends up
        with two fee conventions -- and are ``0.0`` on a refusal, where
        ``bid``/``ask`` and ``spread_bps`` still carry what the market said and
        the configured limits are still reported.

    This never raises for a bad market and never writes anything.
    """
    cfg = db.get_all() if cfg is None else cfg
    symbol = str(symbol or "").strip().upper()
    side = str(side or "").strip().upper()
    requested = _positive(qty)
    result = _result(symbol, side, requested, reason=BAD_REQUEST)
    limits = _thresholds(cfg)
    result["slippage_bps"] = limits["slippage_bps"]
    result["max_spread_bps"] = limits["max_spread_bps"]
    result["volume_fraction"] = limits["volume_fraction"]
    result["fee_rate"] = limits["fee_rate"]
    result["latency_base_ms"] = round(limits["latency_base_ms"], 3)
    result["latency_ms"] = result["latency_base_ms"]

    if not symbol:
        result["detail"] = "No symbol was given, so there is no market to price against."
        return result
    if side not in ("BUY", "SELL"):
        result["detail"] = (
            f"{side or 'No side'} is not a side this paper account can take; it buys or it sells."
        )
        return result
    if requested is None:
        result["detail"] = (
            f"A quantity of {qty!r} is not a positive number of coins, so there is nothing to fill."
        )
        return result

    # ---- the book ----------------------------------------------------------
    #
    # ``quote`` and a fetch are the same thing to the model: a real two-sided
    # quote, or nothing. A feed that cannot answer is a refusal with a reason,
    # not a reason to substitute the last candle close.
    if quote is None:
        quote_source = "fetched"
        book = _fetch_quote(symbol)
    else:
        quote_source = "provided"
        book = quote if isinstance(quote, dict) else None
    result["quote_source"] = quote_source

    bid = _positive(book.get("bid")) if book is not None else None
    ask = _positive(book.get("ask")) if book is not None else None
    ltp = _positive(book.get("ltp")) if book is not None else None
    result["bid"], result["ask"], result["ltp"] = bid, ask, ltp

    if book is None:
        result["reason"] = PRICE_UNAVAILABLE
        result["detail"] = (
            f"No live price is available for {symbol} right now, so there is no "
            "real book to fill against."
        )
        return result
    if bid is None or ask is None:
        missing = (
            "bid and ask" if bid is None and ask is None else ("bid" if bid is None else "ask")
        )
        result["reason"] = PRICE_UNAVAILABLE
        result["detail"] = (
            f"The {symbol} quote carries no usable {missing}, so the price this "
            "order would pay cannot be read from the book."
        )
        return result
    if ask < bid:
        result["reason"] = CROSSED_QUOTE
        result["detail"] = (
            f"The {symbol} quote shows a bid of {_px(bid)} above its ask of "
            f"{_px(ask)}, which is not a book any fill could be priced from."
        )
        return result

    # ---- spread, and the price that side of it costs -----------------------
    #
    # The spread is measured off the real book, in basis points of the mid. Every
    # fill decision below is a statement about this number.
    spread = ask - bid
    mid = (ask + bid) / 2.0
    spread_bps = spread / mid * 10_000.0
    max_spread_bps = limits["max_spread_bps"]
    result["spread_bps"] = round(spread_bps, 4)

    if spread_bps > max_spread_bps:
        result["reason"] = SPREAD_TOO_WIDE
        result["detail"] = (
            f"{symbol} is quoted {spread_bps:.1f} bps wide, wider than the "
            f"{max_spread_bps:.1f} bps this paper account will cross."
        )
        return result

    require_spread = bool(cfg.get(KEY_REQUIRE_SPREAD, False))
    if spread <= 0.0 and require_spread:
        result["reason"] = PRICE_UNAVAILABLE
        result["detail"] = (
            f"The {symbol} quote carries one price rather than a two-sided book, "
            "so there is no spread to measure a fill against."
        )
        return result

    # Slippage is a share of the spread, because that is what the spread is. When
    # the quote has no spread to measure (see the module docstring), the engine's
    # own convention stands in and the result says which basis was used.
    slippage_bps = limits["slippage_bps"]
    if spread > 0.0:
        basis = "observed_spread"
        slippage = spread * slippage_bps / 10_000.0
    else:
        basis = "engine_slippage"
        slippage = mid * engine._slip_rate(cfg)
    result["slippage_basis"] = basis

    # A buy lifts the ask, a sell hits the bid, and slippage is walked away from
    # the mid either way. Nothing is filled at the last traded price.
    reference = ask if side == "BUY" else bid
    price = reference + slippage if side == "BUY" else reference - slippage
    price = round(price, 8)
    slippage = round(abs(price - reference), 8)
    result["price"] = price
    result["reference_price"] = round(reference, 8)
    result["slippage"] = slippage

    requested_notional = price * requested
    result["requested_notional"] = round(requested_notional, 6)

    # ---- liquidity ---------------------------------------------------------
    #
    # The allowance is a share of the volume actually observed, so an order that
    # is large against what traded comes back partial with the remainder open.
    # An unreported volume is not a refusal: the price is already real, and
    # inventing a volume to refuse against would be the same defect as inventing
    # a price to fill against.
    observed = _observed_volume(
        volume if volume is not None else (book.get("volume") if book is not None else None)
    )
    fraction = limits["volume_fraction"]

    if observed is not None:
        result["volume"] = observed
        allowance = fraction * observed
        result["volume_cap_notional"] = round(allowance, 6)
        cap_qty = engine._floor_qty(allowance / price)
        if cap_qty <= 0.0:
            result["reason"] = INSUFFICIENT_LIQUIDITY
            result["detail"] = (
                f"Only {_qty(observed)} {symbol} traded in the last day, and this "
                f"account may take only {_pct(fraction)} of it, which is worth "
                f"{allowance:.2f} USD and cannot fill any part of "
                f"{_qty(requested)} {symbol}."
            )
            return result
        filled = min(requested, cap_qty)
    else:
        filled = requested

    filled = round(engine._floor_qty(filled), 12)
    remainder = round(requested - filled, 12)
    notional = price * filled
    result["filled_qty"] = filled
    result["remainder_qty"] = remainder
    result["notional"] = round(notional, 6)
    result["remainder_notional"] = round(price * remainder, 6)
    result["fee"] = round(notional * limits["fee_rate"], 8)

    share = requested_notional / allowance if observed is not None else None
    total_ms, base_ms, impact_ms = _latency(cfg, share)
    result["latency_base_ms"] = round(base_ms, 3)
    result["latency_impact_ms"] = round(impact_ms, 3)
    result["latency_ms"] = round(total_ms, 3)

    if remainder > 0.0:
        result["status"] = STATUS_PARTIAL
        result["reason"] = PARTIALLY_FILLED
        detail = (
            f"{_qty(filled)} of {_qty(requested)} {symbol} filled at {_px(price)}; "
            f"the other {_qty(remainder)} is left open because "
            f"{requested_notional:.2f} USD is more than the {allowance:.2f} USD "
            f"this account may take out of the {_qty(observed)} that traded"
        )
    else:
        result["status"] = STATUS_FILLED
        result["reason"] = None
        detail = (
            f"{_qty(filled)} {symbol} filled at {_px(price)}, the "
            f"{side.lower()} side of a {spread_bps:.1f} bps book"
        )
    if basis == "engine_slippage":
        # Said every time, because it is the one place this model does not use the
        # market's own spread and a reader is entitled to know which basis paid.
        detail += (
            ". The quote carried one price rather than a two-sided book, so the "
            "account's own slippage convention was charged instead of a share of "
            "the spread"
        )
    result["detail"] = f"{detail}."
    return result
