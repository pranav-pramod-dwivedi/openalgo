"""Equity is invariant to opening and closing a position. Pinned, in both directions.

The defect this file exists for: ``equity_of`` was ``cash + unrealized -
short_margin_locked``, which is right for a short and wrong for a long. A long pays
its notional out of the balance and owns an asset instead, and that asset was in no
term of the identity, so the position's own size read as a permanent loss. The live
ledger showed it exactly: a 100 USD account opened a 42 USD long, equity went to
57.73 and stayed there. The daily-loss guard, which measured a day as first-row-
minus-last-row of that same number, then declared a 50 USD loss on a flat account
and halted it for the day.

So the identity is

    equity = cash + long_capital + unrealized - short_margin_locked

and every case below is a consequence of it rather than a restatement of it:

* opening a long, or a short, at a price leaves equity changed only by the fee;
* closing either at the same price leaves it changed only by the second fee;
* a mark-to-market move changes equity by exactly the P&L, up and down, both sides;
* a partial fill is the same arithmetic on fewer units, so it is also invariant;
* the equity row and ``get_state`` publish every term, so the identity is checkable
  from the reported numbers alone;
* the daily-loss guard measures the live ledger against the day's pinned opening
  equity, so neither a freshly opened account nor one bad row in the chart's table
  can halt it on a loss that was never made.

Everything runs against a throwaway paper database with mocked candles, a mocked
two-sided book and a mocked analyst, so nothing here touches the network and the
repo's own ``data/paper.db`` is never opened. The account is never named: every
figure is read back from the account or derived from the constant that sets it.
"""

import json
import time

import pytest

from services.paper import config, db, engine, planner

BTC = "BTCUSDT"

#: The fee this account pays, as a rate. Every cost assertion below is written
#: against it rather than against a literal, so a change to the default shows up as
#: a failing expectation instead of as a silently wrong number.
FEE_BPS = 4.0


# ------------------------------------------------------------------- fixtures


def series(start, step, count=40):
    """Rising candles, so ``momentum`` signals long on every one of them."""
    out = []
    for i in range(count):
        close = start + i * step
        out.append(
            {
                "open": close,
                "high": close * 1.002,
                "low": close * 0.998,
                "close": close,
                "volume": 1000.0 + i,
            }
        )
    return out


RISING = series(84000.0, 100.0)
FALLING = series(84000.0, -100.0)

GOOD_METRICS = {"trades": 24, "net_pnl": 41.5, "max_drawdown": 12.25, "fees": 3.1}

#: A real spread, so a fill is priced off a two-sided book the way it is in
#: production: a buy lifts the ask, a sell hits the bid.
SPREAD_BPS = 3.0


def stamped(ladder, *, age=0.0, interval=300.0):
    """The ladder with a clock on every bar, newest ``age`` seconds behind now.

    Written when the market is installed rather than when the ladder is built, so a
    ladder constructed at import time is not an hour stale by the time a long suite
    reaches the case that uses it. Milliseconds, as crypto feeds send them; both the
    planner's staleness check and the verifier's refuse a bar with no clock on it.
    """
    now = time.time()
    out = []
    for i, bar in enumerate(ladder):
        copied = dict(bar)
        copied["time"] = (now - age - (len(ladder) - 1 - i) * interval) * 1000.0
        out.append(copied)
    return out


def book(mid, volume=None):
    half = SPREAD_BPS / 20_000.0
    return {
        "ltp": mid,
        "bid": round(mid * (1.0 - half), 8),
        "ask": round(mid * (1.0 + half), 8),
        "open": mid,
        "high": mid,
        "low": mid,
        "prev_close": mid,
        "volume": 40780.0 if volume is None else float(volume),
        "oi": 0.0,
    }


@pytest.fixture
def paper(tmp_path, monkeypatch):
    """An isolated paper account with one coin, one strategy and a real book.

    ``mark`` is the knob the cases below need: it is the price the engine marks
    every open position at, so moving it is a mark-to-market move and nothing else.
    ``throttle`` publishes a 24h volume small enough that an order cannot take its
    whole share of it, which is how a partial fill is produced.
    """
    monkeypatch.setattr(db, "DATA", tmp_path / "paper.db")
    db.init()
    seeded = float(db.get("starting_cash"))
    db.set_many(
        {
            "starting_cash": seeded,
            "cash": seeded,
            "short_margin_locked": 0.0,
            "max_exposure_pct": 0.9,
            "fee_bps": FEE_BPS,
            "slippage_bps": 2.0,
            "max_position_qty": 1.0,
            "max_position_notional_usd": engine.DEFAULT_MAX_POSITION_NOTIONAL_USD,
            "max_daily_loss": seeded * 0.2,
        }
    )
    config.set_halted(False)
    db.ensure_column("positions", "mark", "REAL")
    with db.conn() as c:
        c.execute(
            "INSERT OR REPLACE INTO strategies VALUES(?,?,?,?,?,?,?,?)",
            (
                f"momentum-{BTC}",
                "research",
                "momentum",
                '{"n": 5}',
                json.dumps(GOOD_METRICS),
                "active",
                time.time(),
                1,
            ),
        )

    world = {"bars": stamped(RISING), "volume": None}

    def install(ladder):
        world["bars"] = stamped(ladder)
        world["volume"] = None

    def quote(symbol, exchange):
        if str(symbol).upper() != BTC:
            return None
        return book(float(world["bars"][-1]["close"]), world["volume"])

    def history(symbol, exchange, interval="5m", start_date="", end_date=""):
        return list(world["bars"]) if str(symbol).upper() == BTC else []

    monkeypatch.setattr(engine, "_candles", lambda s, interval="5m": list(world["bars"]))
    monkeypatch.setattr("services.foreign_data_service.get_foreign_quote", quote)
    monkeypatch.setattr("services.foreign_data_service.get_foreign_history", history)

    # Marks come from the position row, never the network, so a position the
    # planner has just written is marked at the price it was actually filled at and
    # carries no open P&L until a case moves it. The book above is quoted at the
    # newest close, which is not the fill price -- a buy lifts the ask and pays slip
    # on it -- so leaving this to the live quote would mark every fresh position down
    # by the spread and quietly make every assertion here about the spread.
    def mark_row():
        """Mark each position at the price on its own row: no open P&L yet."""
        monkeypatch.setattr(engine, "_mark", lambda symbol, fallback=None: (fallback, False))

    mark_row()

    def mark(price):
        """Every position is marked at ``price``: a pure mark-to-market move."""
        monkeypatch.setattr(engine, "_mark", lambda symbol, fallback=None: (float(price), True))

    def throttle(volume):
        """Publish this much 24h volume, so an order above its share is partial."""
        world["volume"] = volume

    def take_answer(state_string, questions):
        assert set(questions) == {"trade", "quality"}
        return {
            "answers": {
                "trade": {"probabilities": {"0": 0.2, "1": 0.8}},
                "quality": {"probabilities": {"0": 0.4, "1": 0.6}},
            }
        }

    monkeypatch.setattr(planner.jev, "ask", take_answer)

    return type(
        "Paper",
        (),
        {
            "install": staticmethod(install),
            "mark": staticmethod(mark),
            "mark_row": staticmethod(mark_row),
            "throttle": staticmethod(throttle),
        },
    )


# -------------------------------------------------------------------- helpers


def rows(query, *args):
    with db.conn() as c:
        return c.execute(query, args).fetchall()


def fee_on(notional):
    """What this account charges on a fill of ``notional``."""
    return notional * FEE_BPS / 10_000.0


def equity():
    """What the account is worth, from the live ledger."""
    return engine.ledger_equity()


def opening_equity():
    return float(db.get("starting_cash"))


def fill(paper, side=None):
    """Plan and place one trade, through the planner and the real fill model.

    Marks are put back on the position rows first: an earlier case's mark-to-market
    would otherwise still be in force and would mark this fill against a price from
    the previous trade, which is a mark, not a cost of opening.
    """
    if side == "SELL":
        paper.install(FALLING)
    paper.mark_row()
    plan = planner.plan(symbols=[BTC])
    assert plan["refusal_reason"] is None, plan["refusal_detail"]
    if side:
        assert plan["side"] == side
    result = planner.execute(plan)
    assert result["executed"] is True
    return result


def position():
    rows_ = rows("SELECT * FROM positions WHERE status='open'")
    assert len(rows_) <= 1
    return rows_[0] if rows_ else None


def close_at(paper, price):
    """Close the open position at exactly ``price``, through the planner's own exit."""
    paper.mark(price)
    record = planner.close_position(BTC)
    assert record.get("executed") is True, record
    return record


def identity(state):
    """The identity, from the numbers the account published and nothing else."""
    return (
        state["cash"]
        + state["long_capital"]
        + state["unrealized"]
        - state["short_margin_locked"]
    )


# ------------------------------------------------ a long, opened and closed


def test_opening_a_long_leaves_equity_unchanged_apart_from_the_fee(paper):
    """The reported defect, stated as a test.

    Cash falls by the notional and the fee; equity falls by the fee alone. Before
    the fix equity fell by the notional as well, which is how a 42 USD long on a
    100 USD account reported 57.73 of equity and kept reporting it.
    """
    before = opening_equity()

    result = fill(paper, side="BUY")

    notional = result["qty"] * result["price"]
    assert db.get_cash() == pytest.approx(before - notional - result["fee"])
    assert equity() == pytest.approx(before - result["fee"], rel=1e-9)

    # The notional is held, not gone: it is reported as the position's own capital,
    # and cash plus that capital is the account it started as.
    state = engine.get_state()
    assert state["long_capital"] == pytest.approx(notional, rel=1e-6)
    assert state["cash"] + state["long_capital"] == pytest.approx(before - result["fee"], rel=1e-9)
    # The entry fee is a real cost, and it is the only one.
    assert equity() < before


def test_closing_a_long_at_its_entry_price_costs_only_the_two_fees(paper):
    """Open then close at the same price: nothing but the fees moved.

    This is the property the whole identity is for. A round trip that ends on the
    same price has made no money and lost none, so equity comes back to where it
    started less what the two fills were charged.
    """
    before = opening_equity()
    result = fill(paper, side="BUY")
    entry, qty = result["price"], result["qty"]

    close_at(paper, entry)

    exit_fee = fee_on(entry * qty)
    assert db.get_cash() == pytest.approx(before - result["fee"] - exit_fee, rel=1e-6)
    assert equity() == pytest.approx(before - result["fee"] - exit_fee, rel=1e-9)
    # Flat, with nothing left to value and no margin to release.
    assert position() is None
    assert engine.open_long_capital() == pytest.approx(0.0)
    assert engine.get_state()["short_margin_locked"] == pytest.approx(0.0)


# ------------------------------------------------ a short, opened and closed


def test_opening_a_short_leaves_equity_unchanged_apart_from_the_fee(paper):
    """The same property on the other side.

    A short entry credits the full sale proceeds to cash and posts the same notional
    as margin, so the two cancel inside the identity and what is left is the fee.
    The balance is higher than it started and equity is not, because those proceeds
    are an obligation and the margin is what makes it one.
    """
    before = opening_equity()

    result = fill(paper, side="SELL")

    notional = result["qty"] * result["price"]
    assert db.get_cash() == pytest.approx(before + notional - result["fee"])
    assert db.get_margin_locked() == pytest.approx(notional)
    assert equity() == pytest.approx(before - result["fee"], rel=1e-9)

    state = engine.get_state()
    # A short holds no asset, so there is no long capital to add back.
    assert state["long_capital"] == pytest.approx(0.0)
    assert state["equity"] == pytest.approx(identity(state), rel=1e-6)


def test_covering_a_short_at_its_entry_price_costs_only_the_two_fees(paper):
    """The short's round trip: same price in and out."""
    before = opening_equity()
    result = fill(paper, side="SELL")
    entry, qty = result["price"], result["qty"]

    close_at(paper, entry)

    exit_fee = fee_on(entry * qty)
    assert db.get_margin_locked() == pytest.approx(0.0), "the margin came off lock"
    assert db.get_cash() == pytest.approx(before - result["fee"] - exit_fee, rel=1e-6)
    assert equity() == pytest.approx(before - result["fee"] - exit_fee, rel=1e-9)
    assert position() is None


# ------------------------------------------------------ mark to market, both ways


def test_a_mark_to_market_move_changes_equity_by_exactly_the_pnl(paper):
    """Up and down, long and short: equity moves by the P&L and by nothing else.

    Pinned in all four directions because the two sides are not the same code. The
    long adds through its mark inside ``unrealized``; the short through its
    unrealised P&L, while its proceeds and its margin stay exactly where they were.
    """
    before = opening_equity()

    for side in ("BUY", "SELL"):
        fill(paper, side=side)
        opened = equity()
        row = position()
        entry, qty = float(row["entry"]), float(row["qty"])
        for fraction in (0.01, -0.02) if side == "BUY" else (-0.01, 0.02):
            paper.mark(entry * (1 + fraction))
            pnl = fraction * entry * qty * (1 if side == "BUY" else -1)
            assert equity() == pytest.approx(opened + pnl, rel=1e-9), (
                f"{side} marked {fraction:+.0%}"
            )
            # The reported open P&L is the same figure, to the six decimals the state
            # publishes, and the identity holds on the published numbers mid-move.
            state = engine.get_state()
            assert state["unrealized"] == pytest.approx(pnl, abs=1e-6)
            assert state["equity"] == pytest.approx(identity(state), abs=1e-6)
        # Back to the entry price and the account is exactly where the fill left it:
        # a mark is not a cost, whichever path it took to get there.
        paper.mark(entry)
        assert equity() == pytest.approx(opened, rel=1e-9)
        assert equity() < before, "only the fee separates it from the starting capital"
        close_at(paper, entry)


def test_the_equity_row_and_the_state_agree_on_every_term(paper):
    """The identity has to be checkable from what the account publishes.

    ``cash``, ``long_capital``, ``unrealized``, ``short_margin_locked`` and
    ``equity`` are all reported, so a reader never has to take the number on trust
    or reconstruct the term that is missing. Checked after a fresh entry and again
    after the exit, which is where the two halves of the identity swap over.
    """
    engine._record_equity()  # a flat book: the opening row of the day

    result = fill(paper, side="BUY")
    entry, qty = result["price"], result["qty"]

    # After a fresh entry: the long is worth what it cost, so the row is the
    # starting capital less the entry fee and not the balance after the notional.
    engine._record_equity()
    state = engine.get_state()
    assert state["equity"] == pytest.approx(identity(state), rel=1e-6)
    row = rows("SELECT * FROM equity ORDER BY ts DESC LIMIT 1")[0]
    assert row["cash"] == pytest.approx(state["cash"])
    assert row["unrealized"] == pytest.approx(state["unrealized"])
    assert row["equity"] == pytest.approx(state["equity"], rel=1e-6)
    assert row["equity"] == pytest.approx(
        row["cash"] + state["long_capital"] + row["unrealized"], abs=1e-6
    )
    assert float(db.get("last_equity")) == pytest.approx(row["equity"], rel=1e-9)

    # And after the exit, with the book flat again.
    close_at(paper, entry)
    engine._record_equity()
    flat = engine.get_state()
    assert flat["long_capital"] == pytest.approx(0.0)
    assert flat["unrealized"] == pytest.approx(0.0)
    assert flat["equity"] == pytest.approx(identity(flat), rel=1e-6)
    assert flat["equity"] == pytest.approx(
        opening_equity() - 2 * fee_on(entry * qty), rel=1e-6
    )


# -------------------------------------------------------------- the partial fill


def test_a_partial_fill_leaves_equity_unchanged_too(paper):
    """A partial fill is the same arithmetic on fewer units.

    The account may take only a share of what traded, so the order comes back with
    fewer coins than it asked for. Equity cannot care: what it did not buy was never
    paid for, and what it did buy is still worth what it cost.
    """
    before = opening_equity()
    paper.throttle(200.0)

    result = fill(paper, side="BUY")

    notional = result["qty"] * result["price"]
    assert 0.0 < notional < engine.DEFAULT_MAX_POSITION_NOTIONAL_USD, "really is partial"
    assert db.get_cash() == pytest.approx(before - notional - result["fee"], rel=1e-6)
    assert equity() == pytest.approx(before - result["fee"], rel=1e-9)

    state = engine.get_state()
    assert state["long_capital"] == pytest.approx(notional, rel=1e-6)
    assert state["equity"] == pytest.approx(identity(state), rel=1e-6)

    # And closing the remainder at the same price is invariant in the same way.
    entry, qty = result["price"], result["qty"]
    close_at(paper, entry)
    assert equity() == pytest.approx(
        before - result["fee"] - fee_on(entry * qty), rel=1e-6
    )


# ------------------------------------------------------ the daily-loss guard


def test_the_guard_does_not_halt_a_freshly_opened_account(paper):
    """The halt that was reported, as a test.

    A day opened, a long filled, another equity row written. Nothing was lost: the
    account holds the position it paid for and is down only the entry fee. Before
    the fix the guard read first-row-minus-last-row of that table, saw the notional
    disappear, and declared the position's own size a loss -- which on a small
    account is more than the daily limit, so it halted a fresh account for the day.
    """
    engine._record_equity()  # the day's opening row, with a flat book

    result = fill(paper, side="BUY")
    notional = result["qty"] * result["price"]
    assert len(rows("SELECT ts FROM equity")) >= 2

    # A limit the old reading would have blown by a wide margin: the notional alone
    # was the loss it declared.
    blocked, detail = planner._daily_loss(min(notional, 1.0))
    assert blocked is False, detail
    assert config.is_halted() is False, "and nothing was halted behind the scenes"

    # And at the account's own configured limit.
    blocked, detail = planner._daily_loss(float(db.get("max_daily_loss")))
    assert blocked is False, detail


def test_the_guard_does_not_halt_a_short_entry_either(paper):
    """A short credits cash, so an equity built on cash alone would look like a gain."""
    engine._record_equity()

    fill(paper, side="SELL")
    assert db.get_cash() > opening_equity(), "the sale proceeds really are in cash"

    blocked, detail = planner._daily_loss(1.0)
    assert blocked is False, detail


def test_one_wrong_row_in_the_curve_cannot_corrupt_the_guard(paper):
    """The chart's table is not the guard's input.

    The equity table is what the dashboard draws, so it will eventually hold a row
    written by a cycle that was wrong, or by an older build. Measuring a day's loss
    out of it made a single bad row enough to halt an account that had lost nothing.
    """
    engine._record_equity()
    fill(paper, side="BUY")

    # A row from nowhere: a fraction of the equity, written by no cycle at all.
    with db.conn() as c:
        c.execute(
            "INSERT OR REPLACE INTO equity VALUES(?,?,?,?,?,?,?,?)",
            (time.time() - 3600.0, 1.0, 1.0, 0.0, 0.0, 0.0, 0.0, 99.0),
        )

    blocked, detail = planner._daily_loss(1.0)
    assert blocked is False, detail


def test_the_guard_still_halts_on_a_loss_the_account_actually_took(paper):
    """Robust is not toothless: a real drawdown still ends the day."""
    engine._record_equity()
    result = fill(paper, side="BUY")
    entry, qty = result["price"], result["qty"]
    limit = opening_equity() * 0.04

    # Mark the long down a tenth. That is a real loss, and a large one.
    paper.mark(entry * 0.9)

    assert equity() == pytest.approx(
        opening_equity() - result["fee"] - 0.1 * entry * qty, rel=1e-9
    )
    blocked, detail = planner._daily_loss(limit)
    assert blocked is True
    assert f"reached the {limit:.2f} USD limit" in detail


def test_a_realised_loss_is_seen_without_waiting_for_a_row(paper):
    """A stop that fires is a loss the moment it is covered.

    The guard used the table, so it could only see a loss once some later cycle
    wrote a row about it. It reads the ledger now, so the cover itself is enough --
    which is what this case shows by never writing a row.
    """
    engine._record_equity()
    result = fill(paper, side="BUY")
    entry, qty = result["price"], result["qty"]

    # Exit far below the entry. Nothing records an equity row: only the ledger moved.
    close_at(paper, entry * 0.5)
    assert len(rows("SELECT ts FROM equity ORDER BY ts DESC LIMIT 1")) == 1

    # What the guard measures is exactly the loss the two fills made: the exit fee,
    # the cover itself, and the entry fee the flat account had already paid.
    loss = 0.5 * entry * qty + fee_on(entry * qty) + fee_on(0.5 * entry * qty)
    assert engine.day_opening_equity() - equity() == pytest.approx(loss, rel=1e-9)

    # And a limit inside that loss ends the day.
    limit = loss * 0.9
    blocked, detail = planner._daily_loss(limit)
    assert blocked is True
    assert f"reached the {limit:.2f} USD limit" in detail


def test_the_guard_does_not_halt_when_the_days_opening_is_unknown(paper):
    """No opening figure, no halt.

    A fresh database, a path that never records equity, a pin left over from
    yesterday: each leaves the guard with no figure to measure against. Halting an
    account because it could not read its own opening balance would be worse than
    not guarding at all, so the answer is a plain no.
    """
    # Nothing has ever recorded equity, so nothing has pinned the day.
    assert engine.day_opening_equity() is None
    blocked, _ = planner._daily_loss(0.01)
    assert blocked is False

    fill(paper, side="BUY")
    # A pin stamped a day ago, carried across a restart, is not today's figure.
    engine.record_day_opening(1.0, time.time() - engine.SECONDS_PER_DAY)
    assert engine.day_opening_equity() is None
    blocked, _ = planner._daily_loss(0.01)
    assert blocked is False

    # Recording pins it, and the pin is what the guard measures from.
    engine._record_equity()
    assert engine.day_opening_equity() == pytest.approx(equity(), rel=1e-9)
    blocked, detail = planner._daily_loss(0.01)
    assert blocked is False, detail


def test_the_day_pins_once_and_is_re_pinned_after_the_day_rolls_over(paper):
    """One pin per day, re-pinned at the boundary."""
    fill(paper, side="BUY")
    engine._record_equity()
    first = engine.day_opening_equity()
    assert first == pytest.approx(equity(), rel=1e-9)

    # Later the same day: the opening figure must not drift with the account.
    paper.mark(float(position()["entry"]) * 0.5)
    engine._record_equity()
    assert engine.day_opening_equity() == pytest.approx(first, rel=1e-9)
    assert equity() < first

    # A pin stamped a day ago is yesterday's figure and is not used today.
    engine.record_day_opening(equity(), time.time() - engine.SECONDS_PER_DAY)
    assert engine.day_opening_equity() is None


# ------------------------------------------------------------------ reporting


def test_get_state_publishes_every_term_of_the_identity(paper):
    """No term of the identity is left for a reader to reconstruct."""
    fill(paper, side="BUY")

    state = engine.get_state()
    for key in ("cash", "long_capital", "unrealized", "short_margin_locked", "equity"):
        assert key in state, key
    assert state["equity"] == pytest.approx(identity(state), rel=1e-6)
    assert state["virtual_balance"] == pytest.approx(state["cash"])

    peak = float(db.get("peak_equity"))
    assert peak >= state["equity"] - 1e-6
    assert state["peak_equity"] == pytest.approx(peak)
    assert state["drawdown"] == pytest.approx(
        (peak - state["equity"]) / peak * 100, abs=1e-4
    )
