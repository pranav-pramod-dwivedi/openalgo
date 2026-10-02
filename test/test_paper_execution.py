"""Paper fills priced from the real book: what the market said, not a constant.

The defect this file pins: the paper broker filled at a candle close plus a fixed
fraction of it (``engine.trading_cycle``) or at the plan's own entry price
(``planner.execute``), so a simulated fill never touched the market it claimed to
be in. A coin quoted 20 bps wide filled exactly as tightly as one quoted half
that, an order larger than the whole day's volume filled in full, and a feed that
could not answer was quietly filled at whatever number was last on hand.

Everything below runs against mocked quotes and a throwaway paper database
(``PAPER_DB`` is set per-test by the repo's own ``isolate_paper_db`` fixture), so
nothing here reaches the network and ``data/paper.db`` is never opened. The
numbers are derived from the quote the test provides, not written as literals
next to the price they are meant to prove: a fill at the ask is asserted as "the
ask plus a quarter of the observed spread", which fails if the model ever
changes which side of the book it lifts.
"""

import os
import time
from pathlib import Path

import pytest

from services.paper import db, engine, execution

SOL = "SOLUSDT"

# A real two-sided book: 2 bps wide, and the asymmetry a bid/ask pair always has.
BOOK = {"bid": 199.98, "ask": 200.02, "ltp": 200.00, "volume": 30_000.0}

# 50 bps of the spread: on this book 0.0002, which is a tenth of a basis point of
# price. The point of the model is that this number follows the market.
CFG = {
    execution.KEY_SPREAD_SLIPPAGE_BPS: 50.0,
    execution.KEY_MAX_SPREAD_BPS: 25.0,
    execution.KEY_VOLUME_FRACTION: 0.01,
}


def spread_of(book):
    """The observed spread of a quote, in the same terms the model uses."""
    return book["ask"] - book["bid"]


# ------------------------------------------------------------------- the book


def test_a_buy_lifts_the_ask_and_a_sell_hits_the_bid():
    """The two sides of the book, not the last traded price."""
    buy = execution.quote_execution(SOL, "BUY", 1.0, cfg=CFG, quote=BOOK)
    sell = execution.quote_execution(SOL, "SELL", 1.0, cfg=CFG, quote=BOOK)

    assert buy["status"] == execution.STATUS_FILLED
    assert sell["status"] == execution.STATUS_FILLED
    # A buy never fills below the ask and a sell never above the bid, whatever
    # the configured slippage: the book is the floor of what is reachable.
    assert buy["price"] >= BOOK["ask"]
    assert sell["price"] <= BOOK["bid"]
    assert buy["reference_price"] == BOOK["ask"]
    assert sell["reference_price"] == BOOK["bid"]
    # And neither one filled at the last traded price, which is the whole defect.
    assert buy["price"] != BOOK["ltp"]
    assert sell["price"] != BOOK["ltp"]


def test_slippage_is_charged_in_basis_points_of_the_real_spread():
    """Half the spread on a 50 bps charge, walked away from the mid."""
    expected_slippage = spread_of(BOOK) * CFG[execution.KEY_SPREAD_SLIPPAGE_BPS] / 10_000.0

    buy = execution.quote_execution(SOL, "BUY", 1.0, cfg=CFG, quote=BOOK)
    sell = execution.quote_execution(SOL, "SELL", 1.0, cfg=CFG, quote=BOOK)

    assert buy["slippage_basis"] == "observed_spread"
    assert buy["slippage"] == pytest.approx(expected_slippage, rel=1e-9)
    assert buy["price"] == pytest.approx(BOOK["ask"] + expected_slippage, rel=1e-9)
    # A sell pays the same cost of crossing, in the other direction.
    assert sell["slippage"] == pytest.approx(expected_slippage, rel=1e-9)
    assert sell["price"] == pytest.approx(BOOK["bid"] - expected_slippage, rel=1e-9)


def test_slippage_scales_with_the_spread_and_not_with_the_price():
    """The same charge in bps costs proportionally more on a wider book.

    A fixed offset in currency cannot do this, and it is the reason a naive fill
    under-charges exactly the coins that are expensive to trade.
    """
    # A wide book needs a wide limit to be crossed at all, so the limit is lifted
    # here: what is being compared is the cost of crossing, not the refusal.
    permissive = {**CFG, execution.KEY_MAX_SPREAD_BPS: 500.0}
    wide = {**BOOK, "bid": 198.00, "ask": 202.00}
    narrow = execution.quote_execution(SOL, "BUY", 1.0, cfg=permissive, quote=BOOK)
    thicker = execution.quote_execution(SOL, "BUY", 1.0, cfg=permissive, quote=wide)

    wider_by = spread_of(wide) / spread_of(BOOK)
    assert wider_by > 1.0
    assert thicker["slippage"] == pytest.approx(narrow["slippage"] * wider_by, rel=1e-6)
    assert thicker["spread_bps"] > narrow["spread_bps"]
    # The price barely moves, because it is the same coin: only the cost of
    # crossing it changed.
    assert thicker["price"] / narrow["price"] < 1.02


def test_a_quote_with_one_price_charges_the_accounts_own_slippage_and_says_so():
    """Binance's 24h ticker publishes no book, so bid and ask arrive equal.

    Filling at ``ltp + a constant`` is the defect this module exists to remove, so
    the degenerate quote may not be dressed up as an observed spread. It falls
    back to the engine's own convention, and the result names the basis.
    """
    flat = {"bid": 200.00, "ask": 200.00, "ltp": 200.00, "volume": 30_000.0}

    result = execution.quote_execution(SOL, "BUY", 1.0, cfg=CFG, quote=flat)

    assert result["status"] == execution.STATUS_FILLED
    assert result["spread_bps"] == 0.0
    assert result["slippage_basis"] == "engine_slippage"
    assert result["slippage"] == pytest.approx(200.00 * engine._slip_rate(CFG), rel=1e-9)
    assert result["price"] == pytest.approx(200.00 * (1 + engine._slip_rate(CFG)), rel=1e-9)
    assert "two-sided book" in result["detail"]


def test_an_operator_can_refuse_a_quote_that_carries_no_spread():
    """Strict mode turns the degenerate quote into a stated refusal, not a guess."""
    flat = {"bid": 200.00, "ask": 200.00, "ltp": 200.00, "volume": 30_000.0}
    strict = {**CFG, execution.KEY_REQUIRE_SPREAD: True}

    result = execution.quote_execution(SOL, "BUY", 1.0, cfg=strict, quote=flat)

    assert result["status"] == execution.STATUS_REJECTED
    assert result["reason"] == execution.PRICE_UNAVAILABLE
    assert result["price"] == 0.0


# ------------------------------------------------------------ bad conditions


def test_a_book_wider_than_the_limit_is_refused_with_its_measurement():
    """A fill inside a spread nobody would cross is not a fill."""
    wide = {"bid": 190.00, "ask": 210.00, "ltp": 200.00, "volume": 30_000.0}

    result = execution.quote_execution(SOL, "BUY", 1.0, cfg=CFG, quote=wide)

    assert result["status"] == execution.STATUS_REJECTED
    assert result["reason"] == execution.SPREAD_TOO_WIDE
    assert result["spread_bps"] > result["max_spread_bps"]
    assert "wider than" in result["detail"]
    # Nothing was filled, and the refusal still reports what the market said.
    assert result["filled_qty"] == 0.0
    assert result["notional"] == 0.0
    assert result["fee"] == 0.0
    assert result["bid"] == 190.00 and result["ask"] == 210.00


def test_the_spread_limit_is_configuration_not_a_constant_in_the_code():
    """The same book is refused or filled depending on what the account allows."""
    wide = {"bid": 190.00, "ask": 210.00, "ltp": 200.00, "volume": 30_000.0}
    tight = {**CFG, execution.KEY_MAX_SPREAD_BPS: 5_000.0}

    assert (
        execution.quote_execution(SOL, "BUY", 1.0, cfg=CFG, quote=wide)["reason"]
        == execution.SPREAD_TOO_WIDE
    )
    assert (
        execution.quote_execution(SOL, "BUY", 1.0, cfg=tight, quote=wide)["status"]
        == execution.STATUS_FILLED
    )


def test_a_crossed_book_is_refused_rather_than_priced():
    """A bid above the ask is broken data, and broken data has no fill price."""
    crossed = {"bid": 201.00, "ask": 199.00, "ltp": 200.00, "volume": 30_000.0}

    result = execution.quote_execution(SOL, "BUY", 1.0, cfg=CFG, quote=crossed)

    assert result["status"] == execution.STATUS_REJECTED
    assert result["reason"] == execution.CROSSED_QUOTE
    assert result["price"] == 0.0


def test_no_volume_to_trade_against_is_refused_as_insufficient_liquidity():
    """Zero traded is a real observation and it cannot fill anything."""
    dead = {**BOOK, "volume": 0.0}

    result = execution.quote_execution(SOL, "BUY", 1.0, cfg=CFG, quote=dead)

    assert result["status"] == execution.STATUS_REJECTED
    assert result["reason"] == execution.INSUFFICIENT_LIQUIDITY
    assert result["volume"] == 0.0
    assert result["volume_cap_notional"] == 0.0


def test_an_allowance_too_small_to_express_is_refused_as_insufficient_liquidity():
    """An allowance that rounds to no quantity at all is a refusal, not a fill.

    1e-13 SOL of a coin quoted at 200 is five orders of magnitude below the
    precision this engine floors quantities to, so there is no fraction of it that
    could honestly be reported as filled.
    """
    thin = {**BOOK, "volume": 1.0}
    stingy = {**CFG, execution.KEY_VOLUME_FRACTION: 1e-13}

    result = execution.quote_execution(SOL, "BUY", 1.0, cfg=stingy, quote=thin)

    assert result["status"] == execution.STATUS_REJECTED
    assert result["reason"] == execution.INSUFFICIENT_LIQUIDITY
    assert result["volume"] == 1.0
    # The allowance is real but far below the reported precision, which is why it
    # reads as zero rather than as a fill.
    assert result["volume_cap_notional"] == 0.0
    assert result["filled_qty"] == 0.0


def test_a_thin_book_fills_a_fraction_rather_than_refusing_for_a_lot_size():
    """No invented minimum: a small share of a real book is a small fill.

    0.01% of 1000 SOL is 0.1 USD, which cannot buy a whole coin. The honest answer
    is a partial fill of the fraction the account may take, not a refusal over a
    lot size this engine has never had.
    """
    thin = {**BOOK, "volume": 1_000.0}
    stingy = {**CFG, execution.KEY_VOLUME_FRACTION: 0.000_1}

    result = execution.quote_execution(SOL, "BUY", 1.0, cfg=stingy, quote=thin)

    assert result["status"] == execution.STATUS_PARTIAL
    assert result["volume_cap_notional"] == pytest.approx(0.1)
    assert 0.0 < result["filled_qty"] < 1.0
    assert result["filled_qty"] + result["remainder_qty"] == pytest.approx(1.0)


# ------------------------------------------------------------------- partials


def test_an_order_larger_than_the_volume_allowance_comes_back_partial():
    """Half the book fills, half is left open. Never a silent full fill.

    The allowance is 1% of the observed volume, and the order is twice that, so
    the model may take half of it and must say the other half is unfilled.
    """
    allowance = 100.0101  # 0.5 coins at the buy price
    result = execution.quote_execution(
        SOL, "BUY", 1.0, cfg=CFG, quote=BOOK, volume=allowance / CFG[execution.KEY_VOLUME_FRACTION]
    )

    assert result["status"] == execution.STATUS_PARTIAL
    assert result["reason"] == execution.PARTIALLY_FILLED
    assert result["filled_qty"] == pytest.approx(0.5, abs=1e-9)
    assert result["remainder_qty"] == pytest.approx(0.5, abs=1e-9)
    assert result["filled_qty"] + result["remainder_qty"] == pytest.approx(1.0)
    assert result["remainder_qty"] > 0.0
    # The money matches the part that filled, not the part that was asked for.
    assert result["notional"] == pytest.approx(result["price"] * result["filled_qty"])
    assert result["remainder_notional"] == pytest.approx(result["price"] * result["remainder_qty"])
    assert result["notional"] < result["requested_notional"]
    assert "left open" in result["detail"]


def test_the_partial_is_measured_against_the_quotes_own_volume():
    """The rule, checked against the volume the quote reports rather than given.

    filled = min(requested, floor(fraction * observed_volume / price)), floored to
    the engine's own quantity precision so rounding can only shrink a fill.
    """
    fraction = CFG[execution.KEY_VOLUME_FRACTION]
    requested = 10.0
    result = execution.quote_execution(SOL, "BUY", requested, cfg=CFG, quote=BOOK)

    assert result["volume"] == BOOK["volume"]
    assert result["volume_cap_notional"] == pytest.approx(fraction * BOOK["volume"])
    allowance_qty = engine._floor_qty((fraction * BOOK["volume"]) / result["price"])
    assert result["filled_qty"] == pytest.approx(min(requested, allowance_qty))
    assert result["status"] == (
        execution.STATUS_PARTIAL if allowance_qty < requested else execution.STATUS_FILLED
    )
    assert result["filled_qty"] <= allowance_qty + 1e-12


def test_an_unreported_volume_does_not_refuse_a_priceable_order():
    """The price is real; refusing it over a missing volume would invent a rule.

    Nothing is claimed about liquidity, so the result says the volume is unknown
    rather than refusing or quietly sizing against a made-up figure.
    """
    no_volume = {"bid": BOOK["bid"], "ask": BOOK["ask"], "ltp": BOOK["ltp"]}

    result = execution.quote_execution(SOL, "BUY", 1.0, cfg=CFG, quote=no_volume)

    assert result["status"] == execution.STATUS_FILLED
    assert result["filled_qty"] == 1.0
    assert result["volume"] is None
    assert result["volume_cap_notional"] is None


# ---------------------------------------------------------------- no guessing


def test_no_live_quote_is_never_papered_over_with_a_price():
    """The one rule above every other: no data means no fill.

    Checked here without a network by handing in a quote with nothing usable in
    it, which is what the model sees once a feed that cannot answer has been
    reduced to a mapping.
    """
    result = execution.quote_execution(SOL, "BUY", 1.0, cfg=CFG, quote={})

    assert result["status"] == execution.STATUS_REJECTED
    assert result["reason"] == execution.PRICE_UNAVAILABLE
    assert result["price"] == 0.0
    assert result["filled_qty"] == 0.0
    assert result["ltp"] is None
    assert result["spread_bps"] is None


def test_a_quote_missing_the_book_is_a_refusal_not_a_last_traded_price():
    """ltp alone is not a fill price, however convenient it would be."""
    for missing in (
        {"ltp": 200.0, "volume": 30_000.0},
        {"bid": None, "ask": 200.0, "ltp": 200.0},
        {"bid": 199.98, "volume": 30_000.0},
        {"bid": "199.98", "ask": "not a number", "ltp": 200.0},
    ):
        result = execution.quote_execution(SOL, "BUY", 1.0, cfg=CFG, quote=missing)
        assert result["status"] == execution.STATUS_REJECTED
        assert result["reason"] == execution.PRICE_UNAVAILABLE
        assert result["price"] == 0.0
        assert result["filled_qty"] == 0.0
        assert result["detail"]


def test_a_quote_that_is_not_a_mapping_is_a_refusal():
    result = execution.quote_execution(SOL, "BUY", 1.0, cfg=CFG, quote="200.00")

    assert result["reason"] == execution.PRICE_UNAVAILABLE


def test_the_live_quote_is_fetched_when_none_is_passed(monkeypatch):
    """The normal path reads the real quote, and a broken feed is a refusal.

    ``get_foreign_quote`` is patched at its own module, so this exercises the
    import and the call the model actually makes rather than a stand-in.
    """
    import services.foreign_data_service as foreign

    seen = {}

    def answering(symbol, exchange):
        seen["args"] = (symbol, exchange)
        return dict(BOOK)

    monkeypatch.setattr(foreign, "get_foreign_quote", answering)

    result = execution.quote_execution(SOL, "BUY", 1.0, cfg=CFG)

    assert seen["args"] == (SOL, "CRYPTO")
    assert result["quote_source"] == "fetched"
    assert result["status"] == execution.STATUS_FILLED

    monkeypatch.setattr(foreign, "get_foreign_quote", lambda *a, **k: None)
    gone = execution.quote_execution(SOL, "BUY", 1.0, cfg=CFG)
    assert gone["reason"] == execution.PRICE_UNAVAILABLE
    assert gone["price"] == 0.0
    assert gone["filled_qty"] == 0.0
    assert gone["ltp"] is None

    def broken(*args, **kwargs):
        raise ConnectionError("binance unreachable")

    monkeypatch.setattr(foreign, "get_foreign_quote", broken)
    failed = execution.quote_execution(SOL, "BUY", 1.0, cfg=CFG)
    assert failed["reason"] == execution.PRICE_UNAVAILABLE
    assert failed["price"] == 0.0


def test_an_unusable_order_is_refused_before_the_market_is_consulted():
    """Bad arguments never reach the quote fetch and never guess a price."""
    for symbol, side, qty in (
        ("", "BUY", 1.0),
        (SOL, "HOLD", 1.0),
        (SOL, "BUY", 0.0),
        (SOL, "BUY", -1.0),
        (SOL, "BUY", None),
        (SOL, "BUY", "a lot"),
    ):
        result = execution.quote_execution(symbol, side, qty, cfg=CFG, quote=BOOK)
        assert result["status"] == execution.STATUS_REJECTED
        assert result["reason"] == execution.BAD_REQUEST
        assert result["price"] == 0.0
        assert result["detail"]


def test_there_is_no_invented_minimum_quantity():
    """No ``qty_below_lot`` rejection, because this engine has no lot size.

    The paper broker has never had per-symbol lot or tick rules, so a minimum
    quantity could only be made up. One at the very bottom of the engine's own
    precision still fills, and the reason vocabulary does not carry a slug for it.
    """
    assert "qty_below_lot" not in execution.REASONS
    assert not any("lot" in reason for reason in execution.REASONS)

    dust = execution.quote_execution(SOL, "BUY", 1e-12, cfg=CFG, quote=BOOK)

    assert dust["status"] == execution.STATUS_FILLED
    assert dust["filled_qty"] == 1e-12


# ------------------------------------------------------------------ fees


def test_the_fee_is_the_engines_own_convention_and_is_returned_not_recomputed():
    """One fee convention, taken from ``engine._fee_rate``, handed to the caller."""
    result = execution.quote_execution(SOL, "BUY", 1.0, cfg=CFG, quote=BOOK)
    rate = engine._fee_rate(CFG)

    assert result["fee_rate"] == rate
    assert result["fee"] == pytest.approx(result["price"] * result["filled_qty"] * rate, rel=1e-9)


def test_the_fee_follows_the_configured_rate_rather_than_a_number_of_its_own():
    """Doubling the configured fee doubles the fee, on the same fill."""
    doubled = {**CFG, "fee_bps": engine._config_float(CFG, "fee_bps", 4.0) * 2}

    base = execution.quote_execution(SOL, "BUY", 1.0, cfg=CFG, quote=BOOK)
    costlier = execution.quote_execution(SOL, "BUY", 1.0, cfg=doubled, quote=BOOK)

    assert costlier["fee_rate"] == pytest.approx(base["fee_rate"] * 2)
    assert costlier["fee"] == pytest.approx(base["fee"] * 2)
    assert costlier["price"] == base["price"]


def test_a_partial_is_charged_on_the_part_that_filled():
    """A fee on the unfilled remainder is a fee for nothing."""
    allowance = 100.0101
    partial = execution.quote_execution(
        SOL, "BUY", 1.0, cfg=CFG, quote=BOOK, volume=allowance / CFG[execution.KEY_VOLUME_FRACTION]
    )

    assert partial["status"] == execution.STATUS_PARTIAL
    assert partial["fee"] == pytest.approx(
        partial["price"] * partial["filled_qty"] * engine._fee_rate(CFG), rel=1e-9
    )
    assert partial["fee"] < partial["requested_notional"] * engine._fee_rate(CFG)


# --------------------------------------------------------------- latency


def test_latency_is_reported_and_never_slept():
    """Five seconds of modelled latency must not cost five seconds of wall clock.

    This runs inside a worker cycle. A model that slept would stall the cycle that
    asked for it, so the delay is reported and the call returns.
    """
    slow = {**CFG, execution.KEY_LATENCY_MS: 5_000.0}

    started = time.monotonic()
    result = execution.quote_execution(SOL, "BUY", 1.0, cfg=slow, quote=BOOK)
    elapsed_ms = (time.monotonic() - started) * 1000.0

    assert result["latency_base_ms"] == 5_000.0
    assert result["latency_ms"] >= 5_000.0
    assert elapsed_ms < 1_000.0


def test_latency_rises_with_the_share_of_the_volume_allowance_consumed():
    """Crossing the whole book costs more time than clipping its edge."""
    fraction = CFG[execution.KEY_VOLUME_FRACTION]
    small = execution.quote_execution(SOL, "BUY", 0.01, cfg=CFG, quote=BOOK)
    large = execution.quote_execution(
        SOL, "BUY", 100.0, cfg=CFG, quote=BOOK, volume=(100.0 * 200.0) / fraction / 100.0
    )

    assert small["status"] == execution.STATUS_FILLED
    assert large["status"] == execution.STATUS_PARTIAL
    # The small order clips the edge of the allowance and pays almost nothing for
    # it; the large one asks for everything there is and pays the whole impact.
    assert 0.0 <= small["latency_impact_ms"] < large["latency_impact_ms"]
    assert large["latency_impact_ms"] == pytest.approx(execution.FULL_ALLOWANCE_LATENCY_MS)
    assert large["latency_ms"] == pytest.approx(
        large["latency_base_ms"] + large["latency_impact_ms"]
    )


# ------------------------------------------------------- the stable contract


def test_a_partial_on_a_quote_with_no_spread_also_says_which_basis_paid():
    """The basis note is not only on full fills, and is on every one of them."""
    flat = {"bid": 200.00, "ask": 200.00, "ltp": 200.00, "volume": 30_000.0}
    allowance = 100.0 / CFG[execution.KEY_VOLUME_FRACTION]

    partial = execution.quote_execution(SOL, "BUY", 1.0, cfg=CFG, quote=flat, volume=allowance)

    assert partial["status"] == execution.STATUS_PARTIAL
    assert partial["slippage_basis"] == "engine_slippage"
    assert "two-sided book" in partial["detail"]
    assert partial["detail"].endswith(".")


def test_the_sentence_names_the_numbers_that_were_used():
    """A trader reads the sentence without the caller adding anything to it.

    No status code, no field name, no exception class: the coin, the price and the
    measurement, which is what the operator needs in order to judge the fill.
    """
    buy = execution.quote_execution(SOL, "BUY", 1.0, cfg=CFG, quote=BOOK)
    wide = execution.quote_execution(
        SOL, "BUY", 1.0, cfg=CFG, quote={**BOOK, "bid": 190.0, "ask": 210.0}
    )

    assert SOL in buy["detail"] and str(buy["spread_bps"])[:4] in buy["detail"]
    assert f"{buy['price']:.4f}" in buy["detail"]
    for detail in (buy["detail"], wide["detail"]):
        lowered = detail.lower()
        for leak in ("none", "traceback", "error", "quote_execution", "bid", "ask=", "http"):
            assert leak not in lowered


def test_this_file_writes_only_to_the_scratch_paper_database():
    """The paper database is live state, and one test here writes to it.

    ``data/paper.db`` holds a running portfolio that nothing in the suite can put
    back, and a smoke test has already closed a real position that way. The
    repository's fixture rebinds the path for every paper test; this asserts the
    rebinding actually happened, so a future case in this file cannot slip past it
    by importing the module without triggering the detection.
    """
    db.init()

    assert Path(db.DATA) != Path("data/paper.db")
    assert Path(db.DATA) == Path(os.environ["PAPER_DB"])
    assert not Path("data/paper.db").samefile(db.DATA)


def test_the_result_shape_is_the_documented_one_on_every_outcome():
    """A caller reads the same keys whether the order filled or was refused."""
    wide = {"bid": 190.00, "ask": 210.00, "ltp": 200.00, "volume": 30_000.0}
    dead = {**BOOK, "volume": 0.0}
    crossed = {"bid": 201.00, "ask": 199.00, "ltp": 200.00, "volume": 30_000.0}
    allowance = 100.0101 / CFG[execution.KEY_VOLUME_FRACTION]

    outcomes = [
        execution.quote_execution(SOL, "BUY", 1.0, cfg=CFG, quote=BOOK),
        execution.quote_execution(SOL, "SELL", 1.0, cfg=CFG, quote=BOOK),
        execution.quote_execution(SOL, "BUY", 1.0, cfg=CFG, quote=BOOK, volume=allowance),
        execution.quote_execution(SOL, "BUY", 1.0, cfg=CFG, quote=wide),
        execution.quote_execution(SOL, "BUY", 1.0, cfg=CFG, quote=dead),
        execution.quote_execution(SOL, "BUY", 1.0, cfg=CFG, quote=crossed),
        execution.quote_execution(SOL, "BUY", 1.0, cfg=CFG, quote={"ltp": 200.0}),
        execution.quote_execution(SOL, "HOLD", 1.0, cfg=CFG, quote=BOOK),
    ]

    assert len(set(execution.RESULT_KEYS)) == len(execution.RESULT_KEYS)
    for result in outcomes:
        assert tuple(result) == execution.RESULT_KEYS
        assert result["status"] in (
            execution.STATUS_FILLED,
            execution.STATUS_PARTIAL,
            execution.STATUS_REJECTED,
        )
        assert result["reason"] is None or result["reason"] in execution.REASONS
        assert result["detail"]
        assert isinstance(result["latency_ms"], float)
    # The slugs each outcome claims are the ones it should.
    assert [r["reason"] for r in outcomes] == [
        None,
        None,
        execution.PARTIALLY_FILLED,
        execution.SPREAD_TOO_WIDE,
        execution.INSUFFICIENT_LIQUIDITY,
        execution.CROSSED_QUOTE,
        execution.PRICE_UNAVAILABLE,
        execution.BAD_REQUEST,
    ]


def test_every_rejection_is_a_slug_and_a_plain_sentence():
    """No bare flag: an operator reads the sentence, a program reads the slug."""
    wide = {"bid": 190.00, "ask": 210.00, "ltp": 200.00, "volume": 30_000.0}

    for quote, slug in (
        (wide, execution.SPREAD_TOO_WIDE),
        ({**BOOK, "volume": 0.0}, execution.INSUFFICIENT_LIQUIDITY),
        ({"ltp": 200.0}, execution.PRICE_UNAVAILABLE),
    ):
        result = execution.quote_execution(SOL, "BUY", 1.0, cfg=CFG, quote=quote)
        assert result["reason"] == slug
        assert slug in execution.REJECTION_REASONS
        assert len(result["detail"].split()) >= 6
        assert "." in result["detail"]
        # The sentence names the coin and the number that caused it, so it can be
        # read to a trader without the caller adding anything.
        assert SOL in result["detail"] or "no usable" in result["detail"]


def test_the_model_reads_its_limits_from_the_config_table():
    """``cfg=None`` uses the paper config table, so the account can be tuned.

    The database is the one the test harness points at a scratch file, so this
    writes and reads a throwaway account rather than the operator's book.
    """
    db.init()
    db.setc(execution.KEY_MAX_SPREAD_BPS, 1.0)
    try:
        cfg = db.get_all()
        # 2 bps is a perfectly ordinary book on this coin and over the limit set
        # just now, which is the point: the limit is the account's, not the code's.
        assert execution.quote_execution(SOL, "BUY", 1.0, cfg=cfg, quote=BOOK)["reason"] == (
            execution.SPREAD_TOO_WIDE
        )
        db.setc(execution.KEY_MAX_SPREAD_BPS, execution.DEFAULT_MAX_SPREAD_BPS)
        cfg = db.get_all()
        assert execution.quote_execution(SOL, "BUY", 1.0, cfg=cfg, quote=BOOK)["status"] == (
            execution.STATUS_FILLED
        )
    finally:
        db.setc(execution.KEY_MAX_SPREAD_BPS, execution.DEFAULT_MAX_SPREAD_BPS)


def test_a_mistyped_limit_cannot_silently_switch_the_check_off():
    """Two ways a bad number disables a safety check, both clamped.

    A negative limit would otherwise refuse every order forever, and a huge or
    unusable one would leave the check nominally on while never firing.
    """
    wide = {"bid": 190.00, "ask": 210.00, "ltp": 200.00, "volume": 30_000.0}

    negative = execution.quote_execution(
        SOL, "BUY", 1.0, cfg={**CFG, execution.KEY_MAX_SPREAD_BPS: -5.0}, quote=wide
    )
    assert negative["max_spread_bps"] == 0.0
    assert negative["reason"] == execution.SPREAD_TOO_WIDE

    absurd = execution.quote_execution(
        SOL, "BUY", 1.0, cfg={**CFG, execution.KEY_MAX_SPREAD_BPS: 2_500_000.0}, quote=wide
    )
    assert absurd["max_spread_bps"] == execution.MAX_SPREAD_BPS_CEILING

    unusable = execution.quote_execution(
        SOL, "BUY", 1.0, cfg={**CFG, execution.KEY_MAX_SPREAD_BPS: "wide"}, quote=wide
    )
    assert unusable["max_spread_bps"] == execution.DEFAULT_MAX_SPREAD_BPS

    # The same for the volume share: a fraction above one would let a paper order
    # take more than everything that traded.
    greedy = execution.quote_execution(
        SOL, "BUY", 1.0, cfg={**CFG, execution.KEY_VOLUME_FRACTION: 4.0}, quote=BOOK
    )
    assert greedy["volume_fraction"] == 1.0
