"""Every paper position must be able to close.

A paper position the planner opened is only half a trade: unless something
evaluates its stop, its target, its clock and its strategy, it stays open
forever and the account drifts. These tests run against a temporary paper
database with mocked candles and mocked quotes, so no network call is ever made,
and they pin the properties that make an exit trustworthy:

* a stop and a target each fire at their own level;
* a position held past the max-hold limit is closed even with the price still
  inside its bracket;
* an exit is the FULL position quantity, never sized off free cash -- the trap
  that would otherwise leave half a position open;
* cash, realised P&L and fees move by the same convention an entry used;
* a price between the levels exits nothing;
* a position whose symbol is no longer on the watchlist is still managed, so it
  cannot be orphaned;
* the kill switch stops exits too, and says so instead of failing silently.
"""

import json
import sys
import time
from pathlib import Path

import pytest

from services.paper import config, db, engine, planner

# ------------------------------------------------------------------- fixtures


def candles(closes, volumes=None):
    out = []
    for i, close in enumerate(closes):
        out.append(
            {
                "open": close,
                "high": close * 1.002,
                "low": close * 0.998,
                "close": close,
                "volume": (volumes[i] if volumes else 1000.0 + i),
            }
        )
    return out


RISING = candles([100.0 + i * 0.5 for i in range(40)])
FALLING = candles([200.0 - i * 0.5 for i in range(40)])


@pytest.fixture
def paper(tmp_path, monkeypatch):
    """An isolated paper database with cash, candles, marks and a silent analyst."""
    monkeypatch.setattr(db, "DATA", tmp_path / "paper.db")
    db.init()
    config.set_halted(False)
    monkeypatch.setattr(planner.jev, "ask", lambda *a, **k: None)

    def configure(**overrides):
        values = {
            "starting_cash": 1000.0,
            "cash": 1000.0,
            "max_position_qty": 0.01,
            "max_exposure_pct": 0.5,
            "fee_bps": 4.0,
            "slippage_bps": 2.0,
            "max_daily_loss": 20.0,
            "short_margin_locked": 0.0,
            "realized_total": 0.0,
            "fees_total": 0.0,
        }
        values.update(overrides)
        db.set_many(values)

    def register(family="momentum", symbol="BTCUSDT", params=None, metrics=None, sid=None):
        with db.conn() as c:
            c.execute(
                "INSERT OR REPLACE INTO strategies VALUES(?,?,?,?,?,?,?,?)",
                (
                    sid or f"{family}-{symbol}",
                    "research",
                    family,
                    params or '{"n": 5}',
                    json.dumps(
                        metrics
                        if metrics is not None
                        else {"trades": 24, "net_pnl": 41.5, "max_drawdown": 12.25, "fees": 3.1}
                    ),
                    "active",
                    time.time(),
                    1,
                ),
            )

    def open_position(symbol="BTCUSDT", side="BUY", qty=0.01, entry=100.0, sl=None, tp=None,
                      opened=None, strategy_id="manual"):
        """A position written the way the engine writes one, bracket included."""
        db.ensure_column("positions", "mark", "REAL")
        if sl is None:
            sl = entry * 0.998 if side == "BUY" else entry * 1.002
        if tp is None:
            tp = entry * 1.004 if side == "BUY" else entry * 0.996
        with db.conn() as c:
            c.execute(
                "INSERT OR REPLACE INTO positions"
                "(symbol,side,qty,entry,opened,strategy_id,sl,tp,status,mark) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    symbol,
                    side,
                    qty,
                    entry,
                    time.time() if opened is None else opened,
                    strategy_id,
                    sl,
                    tp,
                    "open",
                    entry,
                ),
            )

    def mark(price, live=True):
        """Every quote answers this one price. No network, no fallback path taken."""
        monkeypatch.setattr(engine, "_mark", lambda symbol, fallback=None: (price, live))

    def use(candle_data, symbol=None):
        monkeypatch.setattr(engine, "_candles", lambda s, interval="5m": candle_data)

    return type(
        "Paper",
        (),
        {
            "configure": staticmethod(configure),
            "register": staticmethod(register),
            "open_position": staticmethod(open_position),
            "mark": staticmethod(mark),
            "use": staticmethod(use),
        },
    )


def exits_of(kind=planner.KIND_EXITED):
    with db.conn() as c:
        rows = c.execute("SELECT payload FROM decisions WHERE kind=? ORDER BY id", (kind,)).fetchall()
    return [json.loads(r["payload"]) for r in rows]


def open_positions():
    with db.conn() as c:
        return c.execute("SELECT * FROM positions WHERE status='open'").fetchall()


def last_fill():
    with db.conn() as c:
        return c.execute("SELECT * FROM fills ORDER BY id DESC LIMIT 1").fetchone()


# ----------------------------------------------------------------------- cases


def test_stop_hit_closes_the_position_at_the_stop_price(paper):
    paper.configure()
    paper.open_position(entry=100.0, sl=99.0, tp=101.0)
    paper.mark(98.5)

    exits = planner.manage_open_positions()

    assert len(exits) == 1
    assert exits[0]["reason"] == planner.EXIT_STOP
    assert exits[0]["exit_price"] == pytest.approx(99.0)
    assert exits[0]["status"] == "closed"
    assert open_positions() == []
    # The mark that triggered it is kept beside the fill, so a gap is visible.
    assert exits[0]["mark"] == pytest.approx(98.5)


def test_target_hit_closes_the_position_at_the_target(paper):
    paper.configure()
    paper.open_position(entry=100.0, sl=99.0, tp=101.0)
    paper.mark(101.5)

    exits = planner.manage_open_positions()

    assert len(exits) == 1
    assert exits[0]["reason"] == planner.EXIT_TARGET
    assert exits[0]["exit_price"] == pytest.approx(101.0)
    assert open_positions() == []


def test_a_short_stop_fires_from_above_and_its_target_from_below(paper):
    paper.configure()
    paper.open_position(side="SELL", entry=100.0, sl=101.0, tp=99.0)

    paper.mark(101.4)
    stopped = planner.manage_open_positions()
    assert stopped[0]["reason"] == planner.EXIT_STOP
    assert stopped[0]["exit_price"] == pytest.approx(101.0)

    paper.open_position(side="SELL", entry=100.0, sl=101.0, tp=99.0)
    paper.mark(98.6)
    targeted = planner.manage_open_positions()
    assert targeted[0]["reason"] == planner.EXIT_TARGET
    assert targeted[0]["exit_price"] == pytest.approx(99.0)


def test_max_hold_closes_the_position_while_the_price_is_still_inside_the_bracket(paper):
    paper.configure()
    # Nothing is wrong with the price: the clock alone ends this trade.
    paper.open_position(entry=100.0, sl=99.0, tp=101.0, opened=time.time() - 16 * 60)
    paper.mark(100.2)

    exits = planner.manage_open_positions()

    assert len(exits) == 1
    assert exits[0]["reason"] == planner.EXIT_MAX_HOLD
    assert exits[0]["exit_price"] == pytest.approx(100.2)
    assert exits[0]["held_seconds"] > 15 * 60
    assert open_positions() == []


def test_max_hold_default_is_fifteen_minutes_and_the_config_key_moves_it(paper):
    paper.configure()
    assert planner.max_hold_seconds(db.get_all()) == pytest.approx(15 * 60)

    # Just inside the default limit: nothing to do.
    paper.open_position(entry=100.0, sl=99.0, tp=101.0, opened=time.time() - 10 * 60)
    paper.mark(100.0)
    assert planner.manage_open_positions() == []
    assert len(open_positions()) == 1

    # A shorter configured limit closes it on the same tick.
    with db.conn() as c:
        c.execute("UPDATE positions SET opened=? WHERE symbol='BTCUSDT'", (time.time() - 11 * 60,))
    db.setc(planner.KEY_MAX_HOLD_MINUTES, 5.0)
    exits = planner.manage_open_positions()
    assert [e["reason"] for e in exits] == [planner.EXIT_MAX_HOLD]
    assert planner.max_hold_seconds(db.get_all()) == pytest.approx(300.0)


def test_exit_takes_the_full_quantity_even_when_free_cash_is_tiny(paper):
    """The trap: entry sizing shrinks with cash, so it must never size an exit."""
    paper.configure(cash=0.02)
    paper.open_position(entry=100.0, qty=0.01, sl=99.0, tp=101.0)
    paper.mark(98.0)

    # Entry sizing on this balance comes back with a sliver -- far below one
    # percent of the position -- so sizing an exit with it would leave almost the
    # whole position open. That is the trap, shown rather than asserted.
    entry_size = engine.plan_size(db.get_all(), 99.0, "SELL", risk_budget=1.0, stop_price=98.0)
    assert entry_size.qty < 0.01 / 2

    exits = planner.manage_open_positions()

    assert len(exits) == 1
    assert exits[0]["qty"] == pytest.approx(0.01)
    assert last_fill()["qty"] == pytest.approx(0.01)
    assert open_positions() == []


def test_cash_realised_pnl_and_fees_move_by_the_entry_convention(paper):
    paper.configure()
    paper.open_position(entry=100.0, qty=0.01, sl=99.0, tp=101.0)
    # A long was debited notional plus fee at entry; reconstruct that balance.
    entry_fee = 100.0 * 0.01 * 0.0004
    db.set_many({"cash": 1000.0 - (100.0 * 0.01 + entry_fee)})
    before = db.get_cash()
    paper.mark(101.2)

    exits = planner.manage_open_positions()

    # A long closes with a SELL: cash is credited proceeds less the exit fee.
    fee = 101.0 * 0.01 * 0.0004
    gross = (101.0 - 100.0) * 0.01
    assert last_fill()["side"] == "SELL"
    assert db.get_cash() == pytest.approx(before + 101.0 * 0.01 - fee)
    # The engine's own totals keep gross P&L and fees apart, as they always have.
    assert float(db.get("realized_total")) == pytest.approx(gross)
    assert float(db.get("fees_total")) == pytest.approx(fee)
    # The caller is told the net of the fee.
    assert exits[0]["realized_pnl"] == pytest.approx(gross - fee)
    assert exits[0]["gross_pnl"] == pytest.approx(gross)
    assert exits[0]["fee"] == pytest.approx(fee)
    assert exits[0]["cash"] == pytest.approx(db.get_cash())


def test_covering_a_short_releases_its_margin(paper):
    paper.configure()
    paper.open_position(side="SELL", entry=100.0, qty=0.01, sl=101.0, tp=99.0)
    db.set_many({"short_margin_locked": 100.0 * 0.01})
    paper.mark(98.5)

    exits = planner.manage_open_positions()

    assert exits[0]["reason"] == planner.EXIT_TARGET
    assert last_fill()["side"] == "BUY"
    assert db.get_margin_locked() == pytest.approx(0.0)


def test_nothing_exits_while_the_price_sits_between_the_levels(paper):
    paper.configure()
    paper.register()
    paper.open_position(entry=100.0, sl=99.0, tp=101.0, strategy_id="momentum-BTCUSDT")
    paper.mark(100.25)
    paper.use(RISING)  # the owning strategy still agrees with the long

    assert planner.manage_open_positions() == []
    assert len(open_positions()) == 1
    assert exits_of() == []


def test_the_owning_strategy_now_signalling_the_other_way_closes_the_position(paper):
    paper.configure()
    paper.register(family="momentum", symbol="BTCUSDT")
    paper.open_position(entry=100.0, sl=90.0, tp=110.0, strategy_id="momentum-BTCUSDT")
    # Price well inside the bracket, so only the signal can explain the exit.
    paper.mark(100.0)
    paper.use(FALLING)

    exits = planner.manage_open_positions()

    assert len(exits) == 1
    assert exits[0]["reason"] == planner.EXIT_STRATEGY_SIGNAL
    assert exits[0]["exit_price"] == pytest.approx(100.0)
    assert "SELL" in exits[0]["detail"]
    assert exits[0]["strategy_id"] == "momentum-BTCUSDT"


def test_an_unusable_strategy_never_forces_an_exit(paper):
    """An unknown or unrunnable strategy means 'no opinion', not 'exit'."""
    paper.configure()
    paper.open_position(entry=100.0, sl=90.0, tp=110.0, strategy_id="no-such-family-BTCUSDT")
    paper.mark(100.0)

    assert planner.manage_open_positions() == []
    assert len(open_positions()) == 1


def test_a_position_off_the_watchlist_is_still_managed(paper):
    """A symbol dropped from the watchlist is still held, so it is still managed."""
    paper.configure()
    paper.open_position(symbol="SOLUSDT", entry=50.0, sl=49.0, tp=51.0)
    with db.conn() as c:
        c.execute("INSERT OR REPLACE INTO config VALUES('symbols','\"BTCUSDT\"')")
    paper.mark(48.0)

    # Called with no symbols at all, as the worker does: everything is managed.
    exits = planner.manage_open_positions()

    assert [e["symbol"] for e in exits] == ["SOLUSDT"]
    assert exits[0]["reason"] == planner.EXIT_STOP
    assert open_positions() == []


def test_a_position_can_be_narrowed_to_named_symbols(paper):
    paper.configure()
    paper.open_position(symbol="SOLUSDT", entry=50.0, sl=49.0, tp=51.0)
    paper.open_position(symbol="BTCUSDT", entry=100.0, sl=99.0, tp=101.0)
    paper.mark(48.0)

    exits = planner.manage_open_positions(symbols=["SOLUSDT"])

    assert [e["symbol"] for e in exits] == ["SOLUSDT"]
    with db.conn() as c:
        remaining = c.execute("SELECT symbol FROM positions WHERE status='open'").fetchall()
    assert [r["symbol"] for r in remaining] == ["BTCUSDT"]


def test_the_kill_switch_stops_exits_and_says_why(paper):
    paper.configure()
    paper.open_position(entry=100.0, sl=99.0, tp=101.0)
    paper.mark(98.0)
    config.set_halted(True)

    exits = planner.manage_open_positions()

    # Halted means no orders at all, and an exit is an order.
    assert exits == []
    assert len(open_positions()) == 1
    assert last_fill() is None
    reported = exits_of(planner.KIND_EXIT_HALTED)
    assert len(reported) == 1
    assert reported[0]["reason"] == planner.HALTED
    assert reported[0]["open_positions"] == ["BTCUSDT"]
    assert "unmanaged" in reported[0]["detail"]


def test_one_bad_position_does_not_stop_the_others_from_being_managed(paper, monkeypatch):
    paper.configure()
    paper.open_position(symbol="BTCUSDT", entry=100.0, sl=99.0, tp=101.0)
    paper.open_position(symbol="ETHUSDT", entry=100.0, sl=99.0, tp=101.0)
    paper.mark(98.0)

    real_exit_reason = planner._exit_reason

    def explode_for_eth(row, mark, cfg, now):
        if row["symbol"] == "ETHUSDT":
            raise RuntimeError("this row is malformed")
        return real_exit_reason(row, mark, cfg, now)

    monkeypatch.setattr(planner, "_exit_reason", explode_for_eth)

    exits = planner.manage_open_positions()

    assert [e["symbol"] for e in exits] == ["BTCUSDT"]
    with db.conn() as c:
        still_open = c.execute("SELECT symbol FROM positions WHERE status='open'").fetchall()
    assert [r["symbol"] for r in still_open] == ["ETHUSDT"]
    assert exits_of(planner.KIND_EXIT_SKIPPED)


def test_the_exit_journal_row_carries_every_number_a_reader_needs(paper):
    paper.configure()
    paper.register(family="momentum", symbol="BTCUSDT")
    paper.open_position(
        symbol="BTCUSDT", entry=100.0, qty=0.01, sl=99.0, tp=101.0, strategy_id="momentum-BTCUSDT"
    )
    paper.mark(101.5)

    planner.manage_open_positions()

    rows = exits_of()
    assert len(rows) == 1
    row = rows[0]
    assert row["symbol"] == "BTCUSDT"
    assert row["side"] == "BUY"
    assert row["qty"] == pytest.approx(0.01)
    assert row["entry"] == pytest.approx(100.0)
    assert row["exit_price"] == pytest.approx(101.0)
    assert row["reason"] == planner.EXIT_TARGET
    assert row["strategy_id"] == "momentum-BTCUSDT"
    # Realised P&L net of fees, which is the figure a trader wants to read.
    assert row["realized_pnl"] == pytest.approx((101.0 - 100.0) * 0.01 - 101.0 * 0.01 * 0.0004)


def test_close_position_closes_by_name_and_reports_a_symbol_that_is_not_open(paper):
    paper.configure()
    paper.open_position(symbol="SOLUSDT", entry=50.0, qty=0.01, sl=49.0, tp=51.0)
    paper.mark(50.4)

    record = planner.close_position("solusdt")

    assert record["executed"] is True
    assert record["symbol"] == "SOLUSDT"
    assert record["reason"] == planner.EXIT_MANUAL
    assert record["qty"] == pytest.approx(0.01)
    assert record["exit_price"] == pytest.approx(50.4)
    assert open_positions() == []

    refused = planner.close_position("SOLUSDT")
    assert refused["executed"] is False
    assert refused["refusal_reason"] == planner.SYMBOL_NOT_OPEN


def test_close_position_is_refused_while_the_kill_switch_is_set(paper):
    paper.configure()
    paper.open_position(entry=100.0, sl=99.0, tp=101.0)
    paper.mark(100.5)
    config.set_halted(True)

    record = planner.close_position("BTCUSDT")

    assert record["executed"] is False
    assert record["refusal_reason"] == planner.HALTED
    assert "unmanaged" in record["detail"]
    assert len(open_positions()) == 1


def test_a_stale_mark_still_enforces_the_bracket_and_says_it_is_not_live(paper):
    """A quote outage must not leave a breached stop open."""
    paper.configure()
    paper.open_position(entry=100.0, sl=99.0, tp=101.0)
    paper.mark(98.0, live=False)

    exits = planner.manage_open_positions()

    assert [e["reason"] for e in exits] == [planner.EXIT_STOP]
    assert exits[0]["mark_live"] is False


def test_managing_an_empty_account_is_not_an_event(paper):
    paper.configure()
    paper.mark(100.0)
    assert planner.manage_open_positions() == []
    assert exits_of() == []
    assert exits_of(planner.KIND_EXIT_HALTED) == []


# --------------------------------------------------------- worker and runner


def _load_runner():
    """Import ``scripts/paper_run.py`` the way its CLI does."""
    import importlib.util

    path = Path(__file__).resolve().parent.parent / "scripts" / "paper_run.py"
    spec = importlib.util.spec_from_file_location("paper_run_under_exits_test", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_the_worker_manages_exits_before_it_plans(paper, monkeypatch):
    from services.paper import worker

    paper.configure()
    paper.open_position(entry=100.0, sl=99.0, tp=101.0)
    paper.mark(98.0)
    monkeypatch.setattr(engine, "research_cycle", lambda *a, **k: None)

    order = []
    real_exits = planner.manage_open_positions

    def counting_exits(*a, **k):
        order.append("exits")
        return real_exits(*a, **k)

    monkeypatch.setattr(planner, "manage_open_positions", counting_exits)
    monkeypatch.setattr(
        planner, "plan", lambda **k: order.append("plan") or {"refusal_reason": planner.NO_EDGE}
    )

    result = worker.PaperWorker(verbose=False).run_cycle()

    # A stop acted on in the same tick it was seen, never after the next plan.
    assert order == ["exits", "plan"]
    assert result["positions_closed"] == 1
    assert result["exits"][0]["reason"] == planner.EXIT_STOP
    assert open_positions() == []


def test_a_halted_worker_cycle_names_the_positions_it_is_not_managing(paper, monkeypatch):
    from services.paper import worker

    paper.configure()
    paper.open_position(entry=100.0, sl=99.0, tp=101.0)
    paper.mark(98.0)
    config.set_halted(True)
    reached = []
    monkeypatch.setattr(
        planner, "manage_open_positions", lambda *a, **k: reached.append("exits") or []
    )
    monkeypatch.setattr(planner, "plan", lambda **k: reached.append("plan") or {})

    result = worker.PaperWorker(verbose=False).run_cycle()

    assert reached == []
    assert result["status"] == "halted"
    assert result["unmanaged_positions"] == ["BTCUSDT"]
    assert len(open_positions()) == 1
    reported = exits_of(worker.EXIT_HALTED_KIND)
    assert reported and reported[0]["open_positions"] == ["BTCUSDT"]
    assert "unmanaged" in reported[0]["detail"]


def test_the_rules_only_runner_manages_exits_before_it_plans(paper, monkeypatch):
    runner = _load_runner()
    paper.configure()
    paper.open_position(entry=100.0, sl=99.0, tp=101.0)
    paper.mark(101.5)
    monkeypatch.setattr(runner.engine, "research_cycle", lambda *a, **k: None)

    order = []
    real_exits = planner.manage_open_positions

    def counting_exits(*a, **k):
        order.append("exits")
        return real_exits(*a, **k)

    monkeypatch.setattr(planner, "manage_open_positions", counting_exits)
    monkeypatch.setattr(
        planner, "plan", lambda **k: order.append("plan") or {"refusal_reason": planner.NO_EDGE}
    )

    # Armed, and with the analyst off: an exit must not depend on either.
    result = runner.RulesOnlyRunner(armed=True, verbose=False, interval_seconds=1).run_cycle()

    assert order == ["exits", "plan"]
    assert result["positions_closed"] == 1
    assert result["exits"][0]["reason"] == planner.EXIT_TARGET
    assert open_positions() == []
