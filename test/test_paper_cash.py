"""Real cash accounting for the paper portfolio.

Every simulated fill must move the balance, sizing must be bounded by the cash
actually left, and equity must be derivable from that balance. These run
against a temporary paper database with mocked candles and quotes, so nothing
here touches the network.
"""

import time

import pytest

from services.paper import db, engine


@pytest.fixture
def paper(tmp_path, monkeypatch):
    """An isolated paper database plus mocks for every outbound call."""
    monkeypatch.setattr(db, "DATA", tmp_path / "paper.db")
    db.init()

    monkeypatch.setattr(engine, "mark_open_positions", lambda: ([], 0.0))
    monkeypatch.setattr(engine, "_mark", lambda symbol, fallback=None: fallback)
    monkeypatch.setattr(engine.jev, "ask", lambda *a, **k: None)

    def configure(cash=1000.0, max_position_qty=0.01, max_exposure_pct=0.5, fee_bps=4.0):
        db.set_many(
            {
                "starting_cash": cash,
                "cash": cash,
                "max_position_qty": max_position_qty,
                "max_exposure_pct": max_exposure_pct,
                "fee_bps": fee_bps,
                "slippage_bps": 2.0,
            }
        )

    def register(family="momentum", symbol="BTCUSDT", params=None):
        with db.conn() as c:
            c.execute(
                "INSERT OR REPLACE INTO strategies VALUES(?,?,?,?,?,?,?,?)",
                (
                    f"{family}-{symbol}",
                    "research",
                    family,
                    params or '{"n": 5}',
                    '{"net_pnl": 1}',
                    "active",
                    time.time(),
                    1,
                ),
            )

    return type("Paper", (), {"configure": configure, "register": register, "cash": db.get_cash})


def candles(closes):
    return [{"open": c, "high": c, "low": c, "close": c, "volume": 1} for c in closes]


RISING = candles([100.0 + i for i in range(30)])
FALLING = candles([200.0 - i for i in range(30)])


def open_position(paper, monkeypatch, candles_data, cash=1000.0, **caps):
    paper.configure(cash=cash, **caps)
    paper.register()
    monkeypatch.setattr(engine, "_candles", lambda symbol, interval="5m": candles_data)
    engine.trading_cycle()
    with db.conn() as c:
        return c.execute("SELECT * FROM positions WHERE status='open'").fetchone()


def entry_fill(paper):
    with db.conn() as c:
        return c.execute("SELECT * FROM fills ORDER BY id DESC LIMIT 1").fetchone()


def test_buy_debits_cash_by_notional_plus_fee(paper, monkeypatch):
    monkeypatch.setattr(engine, "_candles", lambda symbol, interval="5m": RISING)
    paper.configure(cash=1000.0)
    paper.register()
    engine.trading_cycle()

    fill = entry_fill(paper)
    expected = 1000.0 - (fill["price"] * fill["qty"] + fill["fee"])
    assert fill["side"] == "BUY"
    assert db.get_cash() == pytest.approx(expected)
    assert db.get_cash() < 1000.0


def test_sell_credits_cash_on_exit(paper, monkeypatch):
    monkeypatch.setattr(engine, "_candles", lambda symbol, interval="5m": RISING)
    pos = open_position(paper, monkeypatch, RISING)
    cash_after_buy = db.get_cash()

    # The signal flips, so the engine exits the long with a SELL.
    exit_candles = RISING + RISING
    monkeypatch.setattr(engine, "_candles", lambda symbol, interval="5m": exit_candles)
    px = float(exit_candles[-1]["close"])
    with db.conn() as c:
        closed = engine._close(c, pos, px, "momentum-BTCUSDT")

    fill = entry_fill(paper)
    assert fill["side"] == "SELL"
    assert db.get_cash() == pytest.approx(cash_after_buy + px * fill["qty"] - fill["fee"])
    assert closed == pytest.approx((px - pos["entry"]) * pos["qty"] - fill["fee"])
    # The dashboard totals still track realized P&L and fees independently.
    assert float(db.get("realized_total")) == pytest.approx((px - pos["entry"]) * pos["qty"])
    assert float(db.get("fees_total")) > 0


def test_insufficient_cash_refuses_the_trade(paper, monkeypatch):
    # Enough for a sliver of notional, nowhere near the requested position.
    paper.configure(cash=5.0, max_position_qty=1.0, max_exposure_pct=1.0)
    paper.register()
    monkeypatch.setattr(engine, "_candles", lambda symbol, interval="5m": RISING)
    monkeypatch.setattr(engine, "mark_open_positions", lambda: ([], 0.0))
    engine.trading_cycle()

    with db.conn() as c:
        positions = c.execute("SELECT * FROM positions").fetchall()
        fills = c.execute("SELECT * FROM fills").fetchall()
        refusals = [
            r
            for r in c.execute("SELECT kind,payload FROM decisions").fetchall()
            if r["kind"] == "paper_refused"
        ]
    assert positions == []
    assert fills == []
    assert db.get_cash() == pytest.approx(5.0)
    assert len(refusals) == 1
    assert "insufficient_cash" in refusals[0]["payload"]


def test_position_is_capped_by_available_cash(paper, monkeypatch):
    # Enough cash to fund several of these, but not the full requested size, so
    # the exposure cap binds and the trade is taken inside the balance.
    paper.configure(cash=200.0, max_position_qty=10.0, max_exposure_pct=0.25)
    paper.register()
    monkeypatch.setattr(engine, "_candles", lambda symbol, interval="5m": RISING)
    engine.trading_cycle()

    fill = entry_fill(paper)
    cost = fill["price"] * fill["qty"] + fill["fee"]
    assert cost <= 200.0
    # Well under the 10 unit request: the exposure cap, not the cap config, bound it.
    assert fill["qty"] < 10.0
    assert db.get_cash() >= 0.0


def test_short_entry_posts_notional_as_margin(paper, monkeypatch):
    pos = open_position(paper, monkeypatch, FALLING)
    fill = entry_fill(paper)
    notional = fill["price"] * fill["qty"]

    assert fill["side"] == "SELL"
    assert pos["side"] == "SELL"
    # Sale proceeds credited, full entry notional locked as margin, fee paid.
    assert db.get_cash() == pytest.approx(1000.0 + notional - fill["fee"])
    assert float(db.get("short_margin_locked")) == pytest.approx(notional)
    # The lock is not spendable a second time.
    assert engine.available_cash(db.get_all()) == pytest.approx(1000.0 - fill["fee"])


def test_covering_a_short_releases_margin(paper, monkeypatch):
    pos = open_position(paper, monkeypatch, FALLING)
    cash_after_entry = db.get_cash()

    cover_px = float(FALLING[-1]["close"]) * 0.9
    with db.conn() as c:
        engine._close(c, pos, cover_px, "momentum-BTCUSDT")
    cover = entry_fill(paper)

    assert cover["side"] == "BUY"
    assert db.get_cash() == pytest.approx(cash_after_entry - cover_px * cover["qty"] - cover["fee"])
    assert float(db.get("short_margin_locked")) == pytest.approx(0.0)


def test_equity_includes_the_capital_an_open_long_has_tied_up(paper, monkeypatch):
    """The identity, on a real long opened by the engine's own signal path.

        equity = cash + long_capital + unrealized - short_margin_locked

    ``long_capital`` is the term that was missing: a long pays its notional out of
    the balance and owns the asset instead, and that asset is part of the account.
    Leaving it out reported the position's own size as a permanent loss -- this
    long is worth 50 USD, and equity used to be exactly 50 USD below where it
    started before a single coin had been lost.
    """
    monkeypatch.setattr(engine, "_candles", lambda symbol, interval="5m": RISING)
    paper.configure(cash=1000.0)
    paper.register()
    engine.trading_cycle()

    with db.conn() as c:
        row = c.execute("SELECT entry,qty FROM positions WHERE status='open'").fetchone()
    notional = float(row["entry"]) * float(row["qty"])
    marks, unrealized = _mark_real(row["entry"], row["qty"])
    monkeypatch.setattr(engine, "mark_open_positions", lambda: (marks, unrealized))

    engine._record_equity()
    state = engine.get_state()

    # The capital is reported, and the identity holds on the reported numbers.
    assert state["long_capital"] == pytest.approx(notional)
    assert state["equity"] == pytest.approx(
        state["cash"] + state["long_capital"] + state["unrealized"] - state["short_margin_locked"]
    )
    assert state["cash"] == pytest.approx(db.get_cash())
    assert state["cash"] < state["starting_cash"]
    assert state["unrealized"] == pytest.approx(unrealized)
    assert state["short_margin_locked"] == pytest.approx(0.0)
    # And the whole point: the balance fell by the notional and the entry fee, and
    # the equity fell by the entry fee alone.
    entry_fee = entry_fill(paper)["fee"]
    assert state["equity"] == pytest.approx(state["starting_cash"] - entry_fee, rel=1e-6)
    assert state["equity"] == pytest.approx(state["cash"] + notional, rel=1e-6)


def test_equity_excludes_locked_short_margin(paper, monkeypatch):
    open_position(paper, monkeypatch, FALLING)
    with db.conn() as c:
        row = c.execute("SELECT entry,qty FROM positions WHERE status='open'").fetchone()
    marks, unrealized = _mark_real(row["entry"], row["qty"])
    monkeypatch.setattr(engine, "mark_open_positions", lambda: (marks, unrealized))

    engine._record_equity()
    state = engine.get_state()
    locked = float(db.get("short_margin_locked"))

    # A short holds no asset, so there is no long capital to add back.
    assert state["long_capital"] == pytest.approx(0.0)
    assert state["equity"] == pytest.approx(
        state["cash"] + state["long_capital"] + state["unrealized"] - locked
    )
    # Without the margin deduction the sale proceeds would be counted twice.
    assert state["equity"] != pytest.approx(state["cash"] + state["unrealized"])


def test_move_cash_rejects_going_below_zero(paper):
    paper.configure(cash=10.0)
    with pytest.raises(ValueError):
        db.move_cash(-10.01)
    assert db.get_cash() == pytest.approx(10.0)


def test_cash_survives_a_restart(paper, monkeypatch):
    monkeypatch.setattr(engine, "_candles", lambda symbol, interval="5m": RISING)
    paper.configure(cash=1000.0)
    paper.register()
    engine.trading_cycle()
    balance = db.get_cash()

    db.init()  # what the next process does on startup
    assert db.get_cash() == pytest.approx(balance)


def _mark_real(entry, qty, mark=None):
    """Build the marked position the engine would hold for one long row."""
    mark = float(entry) if mark is None else mark
    unrealized = (mark - float(entry)) * float(qty)
    return [{"symbol": "BTCUSDT", "unrealized": unrealized}], unrealized
