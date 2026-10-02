"""Position sizing by risk distance, and what it has to refuse to be honest.

The defect this file pins: sizing was capped by a flat coin quantity
(``max_position_qty``, default 0.01), so the same 0.01 units was $840 of BTC and
$1.18 of SOL. The risk actually reachable at the stop therefore differed by three
orders of magnitude between coins -- a SOL trade risked 0.0024 USD while the plan
claimed a 5 USD budget, and the reported ``risk_usd`` rounded to 0.00.

The rule now is ``qty = risk_usd / |entry - stop|``, with the configured caps as
ceilings on the result rather than as the sizing rule. These cases pin the rule,
every ceiling, both refusal floors, the fact that the numbers printed are the
numbers the order carries, and the two accounting questions that came with it: the
planner's ability to find a strategy once the "mirror" rows research used to write
are gone, and equity after a fresh entry.

Everything runs against a temporary paper database with mocked candles, mocked
marks and a mocked analyst, so nothing here touches the network and the repo's own
``data/paper.db`` is never opened.
"""

import json
import time

import pytest

from services.paper import db, engine, planner

BTC = "BTCUSDT"
SOL = "SOLUSDT"


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


# Realistic enough that the two coins differ by six hundred times in price, which
# is the whole point: a rule expressed in units cannot survive this.
BTC_CANDLES = series(84000.0, 100.0)
SOL_CANDLES = series(140.0, 0.5)
PRICES = {BTC: BTC_CANDLES, SOL: SOL_CANDLES}

GOOD_METRICS = {"trades": 24, "net_pnl": 41.5, "max_drawdown": 12.25, "fees": 3.1}


def take_answer(state, questions):
    """The analyst's own question set, answered, so a swap would be visible."""
    assert set(questions) == {"trade", "quality"}
    return {
        "answers": {
            "trade": {"probabilities": {"0": 0.28, "1": 0.72}},
            "quality": {"probabilities": {"0": 0.39, "1": 0.61}},
        }
    }


@pytest.fixture
def paper(tmp_path, monkeypatch):
    """An isolated paper account with 1000 USD, both coins and a silent feed."""
    monkeypatch.setattr(db, "DATA", tmp_path / "paper.db")
    db.init()

    def configure(**overrides):
        values = {
            "starting_cash": 1000.0,
            "cash": 1000.0,
            "short_margin_locked": 0.0,
            "max_exposure_pct": 0.5,
            "fee_bps": 4.0,
            "slippage_bps": 2.0,
            "max_daily_loss": 20.0,
            "max_risk_usd": 5.0,
            "max_position_notional_usd": engine.DEFAULT_MAX_POSITION_NOTIONAL_USD,
            # Deliberately left at the old default. Every existing installation has
            # this value persisted, so the tests that matter run with it in place:
            # if it were still a ceiling, no cheap coin could be sized at all.
            "max_position_qty": 0.01,
        }
        values.update(overrides)
        db.set_many(values)

    def register(symbol=BTC, family="momentum", interval=None, params=None, metrics=None):
        sid = f"{family}-{symbol}-{interval}" if interval else f"{family}-{symbol}"
        with db.conn() as c:
            c.execute(
                "INSERT OR REPLACE INTO strategies VALUES(?,?,?,?,?,?,?,?)",
                (
                    sid,
                    "research",
                    family,
                    params or '{"n": 5}',
                    json.dumps(GOOD_METRICS if metrics is None else metrics),
                    "active",
                    time.time(),
                    1,
                ),
            )
        return sid

    def register_mirror(symbol=BTC, family="momentum"):
        """The duplicate row research used to write so the planner could see it."""
        with db.conn() as c:
            c.execute(
                "INSERT OR REPLACE INTO strategies VALUES(?,?,?,?,?,?,?,?)",
                (
                    f"{family}-{symbol}",
                    engine.MIRROR_HYPOTHESIS,
                    family,
                    '{"n": 5}',
                    json.dumps(GOOD_METRICS),
                    "active",
                    time.time(),
                    1,
                ),
            )

    def open_position(symbol=SOL, side="BUY", qty=1.0, entry=100.0, mark=None):
        db.ensure_column("positions", "mark", "REAL")
        with db.conn() as c:
            c.execute(
                "INSERT OR REPLACE INTO positions"
                "(symbol,side,qty,entry,opened,strategy_id,sl,tp,status,mark)"
                " VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    symbol,
                    side,
                    qty,
                    entry,
                    time.time(),
                    "manual",
                    entry * 0.99 if side == "BUY" else entry * 1.01,
                    entry * 1.01 if side == "BUY" else entry * 0.99,
                    "open",
                    entry if mark is None else mark,
                ),
            )

    configure()
    # Marks come from the position row, never the network, so unrealized P&L is
    # exactly what the test put there.
    monkeypatch.setattr(engine, "_candles", lambda s, interval="5m": PRICES.get(s, []))
    monkeypatch.setattr(engine, "_mark", lambda symbol, fallback=None: (fallback, False))
    # planner and engine share one ``jev`` module, so this is the analyst both of
    # them call.
    monkeypatch.setattr(engine.jev, "ask", take_answer)

    return type(
        "Paper",
        (),
        {
            "configure": staticmethod(configure),
            "register": staticmethod(register),
            "register_mirror": staticmethod(register_mirror),
            "open_position": staticmethod(open_position),
        },
    )


def rows(query, *args):
    with db.conn() as c:
        return c.execute(query, args).fetchall()


def nothing_was_traded():
    return (
        no_orders_or_fills()
        and rows("SELECT COUNT(*) AS n FROM positions")[0]["n"] == 0
    )


def no_orders_or_fills():
    """Nothing was placed -- for the cases that pre-seed a position on purpose."""
    return (
        rows("SELECT COUNT(*) AS n FROM orders")[0]["n"] == 0
        and rows("SELECT COUNT(*) AS n FROM fills")[0]["n"] == 0
    )


def plan_one(symbol=BTC, budget=5.0, **kwargs):
    return planner.plan(symbols=[symbol], max_risk=budget, **kwargs)


# ------------------------------------------------------------------ the rule


def test_the_same_budget_risks_the_same_money_on_a_600x_coin_and_a_cheap_one(paper):
    """The defect, stated as a test: comparable risk on BTC and SOL.

    Both plans carry the same 5 USD budget and the same 0.2% stop, so both may
    reach the same 500 USD of position and the same 1 USD of risk at the stop.
    Under the old flat 0.01-unit cap BTC stood $840 with $1.68 of reachable risk
    while SOL stood $1.18 with $0.0024 -- three orders of magnitude apart, on the
    same budget.
    """
    paper.register(symbol=BTC)
    paper.register(symbol=SOL)

    btc = plan_one(BTC)
    sol = plan_one(SOL)

    assert btc["refusal_reason"] is None and sol["refusal_reason"] is None

    # Six hundred times the price, so the quantities are nowhere near each other:
    # the same 500 USD is 0.0057 of BTC and 3.13 of SOL.
    assert btc["qty"] * btc["entry_price"] == pytest.approx(sol["qty"] * sol["entry_price"])
    assert sol["qty"] / btc["qty"] > 100

    # And the money at risk is the same, which is the entire point.
    assert btc["risk_usd"] == pytest.approx(sol["risk_usd"], rel=1e-3)
    assert btc["risk_usd"] == pytest.approx(1.0, rel=1e-3)
    assert sol["risk_usd"] == pytest.approx(1.0, rel=1e-3)

    # Neither rounds away to nothing, and neither exceeds what it asked for.
    for plan in (btc, sol):
        assert round(plan["risk_usd"], 2) > 0.0
        assert plan["risk_usd"] <= 5.0
        assert plan["risk_usd"] >= 5.0 * engine.MIN_RISK_FRACTION


def test_the_stop_distance_decides_the_quantity(paper):
    """Sizing is the budget divided by the distance to the stop, not a config.

    Both ceilings are pushed out of the way here so the rule itself is what is
    being measured; ``test_the_notional_ceiling_is_a_ceiling_and_not_the_sizing_rule``
    is the same account with a ceiling that binds.
    """
    paper.configure(cash=1_000_000.0, starting_cash=1_000_000.0,
                    max_position_notional_usd=1_000_000.0)
    entry = 100.0
    stop = entry * (1 - planner.STOP_PCT)
    wide = {"exposure_room": 1_000_000.0}
    cfg = db.get_all()

    tight = engine.plan_size(cfg, entry, "BUY", risk_budget=5.0, stop_price=stop, **wide)
    roomy = engine.plan_size(
        cfg, entry, "BUY", risk_budget=5.0, stop_price=entry * (1 - 10 * planner.STOP_PCT), **wide
    )

    assert tight.reason == engine.SIZE_OK
    assert roomy.reason == engine.SIZE_OK
    # The stop 0.2% away costs 0.20 a unit, so 5 USD buys 25 of them. A stop ten
    # times further away costs 2.00 a unit and buys a tenth as many. Same budget,
    # same order of money at risk -- which is the whole point.
    assert tight.qty == pytest.approx(5.0 / abs(entry - stop))
    assert roomy.qty == pytest.approx(5.0 / (10 * planner.STOP_PCT * entry))
    assert roomy.qty < tight.qty
    # And the risk really is the budget, to the last bit, on both.
    for size, distance in (
        (tight, abs(entry - stop)),
        (roomy, 10 * planner.STOP_PCT * entry),
    ):
        assert distance * size.qty == pytest.approx(5.0)


def test_a_flat_quantity_left_in_the_config_no_longer_caps_the_size(paper):
    """The old default is still persisted on every existing installation.

    Honouring it as a ceiling is what made a 5 USD budget unfundable on a coin
    quoted in dollars, so it is no longer read. A quantity limit that cannot be
    stated in the unit the risk is measured in is not a guard.
    """
    cfg = db.get_all()
    assert cfg["max_position_qty"] == 0.01

    sol = engine.plan_size(cfg, 140.0, "BUY", risk_budget=5.0, stop_price=140.0 * 0.998)

    assert sol.reason == engine.SIZE_OK
    # 17.86 SOL is what the budget asks for; the 500 USD ceiling cuts it to 3.57.
    # Either way the answer is nowhere near the 0.01 the old cap allowed.
    assert sol.qty > 0.01
    assert sol.qty == pytest.approx(500.0 / 140.0, rel=1e-9)
    # And the key itself makes no difference at all, whatever it is set to.
    without = {k: v for k, v in cfg.items() if k != "max_position_qty"}
    assert engine.plan_size(without, 140.0, "BUY", risk_budget=5.0, stop_price=140.0 * 0.998) == sol
    cfg["max_position_qty"] = 5.0
    assert engine.plan_size(cfg, 140.0, "BUY", risk_budget=5.0, stop_price=140.0 * 0.998) == sol


def test_no_lot_or_step_rounding_is_invented(paper):
    """The paper engine has no per-symbol lot rules, so nothing rounds the qty.

    A lot size invented here would put a size in the journal that cannot be
    traded. The quantity is only floored, which can shrink a position but never
    grow one, so the risk can never land back above the budget.
    """
    entry = 140.0
    size = engine.plan_size(
        db.get_all(), entry, "BUY", risk_budget=5.0, stop_price=entry * 0.998
    )

    assert size.reason == engine.SIZE_OK
    # Fractional, and landing on the ceiling rather than on a round lot count.
    assert size.qty % 1 != 0
    assert size.qty == pytest.approx(500.0 / entry, rel=1e-9)
    # Flooring only: a qty below the budget's own arithmetic never rises above it.
    tiny_stop = entry * (1 - 1e-9)
    floored = engine.plan_size(
        db.get_all(), entry, "BUY", risk_budget=5.0, stop_price=tiny_stop
    )
    assert floored.qty <= 5.0 / abs(entry - tiny_stop)


# -------------------------------------------------------------- the ceilings


def test_the_notional_ceiling_is_a_ceiling_and_not_the_sizing_rule(paper):
    paper.register()
    paper.configure(max_exposure_pct=0.9)
    assert db.get("max_position_notional_usd") == pytest.approx(500.0)

    # A budget far larger than the ceiling can express at a 0.2% stop: the ceiling
    # binds, and the realised risk says so instead of claiming the budget.
    plan = plan_one(BTC, budget=50.0)

    assert plan["refusal_reason"] is None
    assert plan["qty"] * plan["entry_price"] == pytest.approx(500.0, rel=1e-6)
    assert plan["risk_usd"] == pytest.approx(1.0, rel=1e-6)
    assert plan["risk_usd"] < 50.0

    # Raise it and the same budget buys a bigger position with more risk behind it.
    # The exposure cap is lifted first, so it is the notional ceiling doing the work
    # rather than the two happening to sit at the same number.
    paper.configure(max_exposure_pct=0.9, max_position_notional_usd=900.0)
    roomier = plan_one(BTC, budget=50.0)
    assert roomier["refusal_reason"] is None
    assert roomier["qty"] > plan["qty"]
    assert roomier["qty"] * roomier["entry_price"] == pytest.approx(900.0, rel=1e-6)
    assert roomier["risk_usd"] == pytest.approx(1.8, rel=1e-6)


def test_insufficient_cash_still_refuses_rather_than_shrinking(paper):
    """Cash, not a cap, and the answer is no -- never a smaller trade.

    The exposure room is a share of equity, so it only outruns the balance when
    open P&L has inflated equity: that is the case where a plan can look fundable
    on paper and still not be, and it has to be refused.
    """
    paper.register(symbol=SOL)
    # 200 USD of cash, and an open position carrying a 60 USD unrealized gain, so
    # equity is 260 and, with the whole of it available, the exposure room is 215 USD
    # -- more than the balance can fund, once the 45 USD already committed to the
    # open position is taken off.
    paper.configure(cash=200.0, max_exposure_pct=1.0)
    paper.open_position(symbol=BTC, qty=0.5, entry=90.0, mark=210.0)
    assert engine.equity_of(db.get_all(), 60.0) == pytest.approx(260.0)

    size = engine.plan_size(
        db.get_all(),
        140.0,
        "BUY",
        risk_budget=5.0,
        stop_price=140.0 * 0.998,
        exposure_room=215.0,
    )
    assert size.reason == engine.SIZE_INSUFFICIENT_CASH
    assert "cannot fund" in size.detail

    plan = plan_one(SOL)
    assert plan["refusal_reason"] == planner.INSUFFICIENT_CASH
    assert no_orders_or_fills()

    # With the open P&L gone the same account can afford it again.
    with db.conn() as c:
        c.execute("UPDATE positions SET mark=entry WHERE symbol=?", (BTC,))
    affordable = plan_one(SOL)
    assert affordable["refusal_reason"] is None
    assert affordable["qty"] > 0


def test_the_exposure_cap_still_refuses_when_the_room_is_gone(paper):
    """Requirement: the exposure cap refuses, it does not merely shrink."""
    paper.register()
    # Half of 1000 USD of equity is already committed.
    paper.open_position(symbol=SOL, qty=5.0, entry=100.0)

    plan = plan_one(BTC)

    assert plan["refusal_reason"] == planner.EXPOSURE_CAP
    assert "cap" in plan["refusal_detail"]
    assert no_orders_or_fills()


def test_a_squeezed_exposure_room_refuses_rather_than_trading_dust(paper):
    """Room left, but not enough to be a trade: refuse, do not trade 4 USD."""
    paper.register()
    paper.open_position(symbol=SOL, qty=4.96, entry=100.0)  # 496 of the 500 room

    plan = plan_one(BTC)

    assert plan["refusal_reason"] == planner.BELOW_MIN_SIZE
    assert "minimum" in plan["refusal_detail"]
    assert no_orders_or_fills()


def test_an_exhausted_room_refuses_in_the_engine_too(paper):
    size = engine.plan_size(
        db.get_all(), 100.0, "BUY", risk_budget=5.0, stop_price=99.8, exposure_room=0.0
    )
    assert size.reason == engine.SIZE_EXPOSURE_CAP
    assert size.qty == 0.0


# ------------------------------------------------------------- honest refusal


def test_a_budget_too_small_to_trade_is_refused_with_the_numbers(paper):
    paper.register()

    plan = plan_one(BTC, budget=0.001)

    assert plan["refusal_reason"] == planner.BELOW_MIN_SIZE
    assert plan["qty"] == 0.0
    detail = plan["refusal_detail"]
    assert "sizing refused" in detail
    # The floor and the numbers that hit it, not a bare code.
    assert f"{engine.MIN_POSITION_NOTIONAL_USD:.2f}" in detail
    assert nothing_was_traded()


def test_a_budget_the_account_cannot_fund_at_all_is_refused(paper):
    """Ask for 1000 USD of risk on a 1000 USD account and the caps say no.

    The ceiling can only deliver 1 USD of risk at this stop distance, which is
    under the 1% share of the budget, so the plan is refused with that arithmetic
    in the detail instead of quietly taking the 1 USD and calling it 1000.
    """
    paper.register()

    plan = plan_one(BTC, budget=1000.0)

    assert plan["refusal_reason"] == planner.BELOW_MIN_SIZE
    assert "at risk against a" in plan["refusal_detail"]
    assert f"{engine.MIN_RISK_FRACTION:.0%}" in plan["refusal_detail"]
    assert nothing_was_traded()


def test_the_two_minimums_are_what_define_a_trade(paper):
    """Pinned explicitly, because they are a choice and not an accident.

    A position must carry at least 1% of the budget and be worth at least 10 USD
    (1% of the default 1000 USD account). Below either, the P&L rounds to 0.00 and
    the round trip is smaller than the fees.
    """
    entry = 100.0
    stop = entry * 0.998
    cfg = db.get_all()

    # Exactly on the notional floor: 10 USD of position at a 0.2% stop risks
    # 0.02 USD, which is 2% of a 1 USD budget, so it is allowed.
    just_enough = engine.plan_size(
        cfg, entry, "BUY", risk_budget=1.0, stop_price=stop, exposure_room=10.0
    )
    assert just_enough.reason == engine.SIZE_OK
    assert just_enough.qty * entry == pytest.approx(10.0)

    # One cent less and it is dust.
    nearly = engine.plan_size(
        cfg, entry, "BUY", risk_budget=1.0, stop_price=stop, exposure_room=9.99
    )
    assert nearly.reason == engine.SIZE_BELOW_MIN_SIZE

    # A notional plenty large enough, but so little risk that the P&L rounds away.
    thin = engine.plan_size(
        cfg, entry, "BUY", risk_budget=1000.0, stop_price=stop, exposure_room=500.0
    )
    assert thin.reason == engine.SIZE_BELOW_MIN_SIZE
    assert abs(entry - stop) * (500.0 / entry) < 1000.0 * engine.MIN_RISK_FRACTION


def test_an_unusable_budget_or_stop_is_refused_rather_than_sized(paper):
    cfg = db.get_all()

    for bad in (0.0, -5.0, "five"):
        size = engine.plan_size(cfg, 100.0, "BUY", risk_budget=bad, stop_price=99.8)
        assert size.reason == engine.SIZE_INVALID_BUDGET
        assert size.qty == 0.0

    flat = engine.plan_size(cfg, 100.0, "BUY", risk_budget=5.0, stop_price=100.0)
    assert flat.reason == engine.SIZE_ZERO_RISK_DISTANCE
    assert flat.qty == 0.0


# ------------------------------------------------- the numbers that get placed


def test_every_reported_number_comes_from_the_quantity_that_is_placed(paper):
    paper.register()
    paper.register_mirror()

    plan = plan_one(BTC, budget=5.0)
    assert plan["refusal_reason"] is None

    result = planner.execute(plan)
    assert result["executed"] is True

    fill = rows("SELECT * FROM fills WHERE order_id=?", result["order_id"])[0]
    entry = float(fill["price"])
    qty = float(fill["qty"])
    stop, target = plan["stop_loss"], plan["take_profit"]

    # The order carries the plan's quantity, and the reported money is that
    # quantity times the distances of the order actually placed.
    assert qty == pytest.approx(plan["qty"])
    assert entry == pytest.approx(plan["entry_price"])
    assert plan["risk_usd"] == pytest.approx(abs(entry - stop) * qty, rel=1e-6)
    assert plan["reward_usd"] == pytest.approx(abs(target - entry) * qty, rel=1e-6)
    assert plan["rr"] == pytest.approx(abs(target - entry) / abs(entry - stop), rel=1e-6)
    # The reasoning quotes the same money, not a recomputation of it.
    assert f"{plan['risk_usd']:.2f}" in plan["reasoning"]
    assert f"{plan['reward_usd']:.2f}" in plan["reasoning"]


def test_the_reward_is_the_r_multiple_the_distances_imply(paper):
    paper.register()
    plan = plan_one(BTC, budget=5.0)

    ratio = abs(plan["take_profit"] - plan["entry_price"]) / abs(
        plan["entry_price"] - plan["stop_loss"]
    )
    assert ratio == pytest.approx(planner.TARGET_PCT / planner.STOP_PCT)
    assert plan["rr"] == pytest.approx(ratio, rel=1e-6)
    assert plan["reward_usd"] == pytest.approx(plan["risk_usd"] * ratio, rel=1e-6)


def test_a_short_is_sized_by_the_same_rule_and_posts_its_notional_as_margin(paper):
    paper.register(symbol=SOL)
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(engine, "_candles", lambda s, interval="5m": series(140.0, -0.5))
    try:
        plan = plan_one(SOL, budget=5.0)
    finally:
        monkeypatch.undo()

    assert plan["side"] == "SELL"
    assert plan["risk_usd"] == pytest.approx(1.0, rel=1e-3)
    assert plan["risk_usd"] == pytest.approx(
        abs(plan["entry_price"] - plan["stop_loss"]) * plan["qty"], rel=1e-6
    )

    result = planner.execute(plan)
    assert result["executed"] is True
    notional = result["price"] * result["qty"]
    # Sale proceeds credited, the whole notional posted as margin, fee paid.
    assert db.get_cash() == pytest.approx(1000.0 + notional - result["fee"])
    assert db.get_margin_locked() == pytest.approx(notional)
    assert engine.available_cash(db.get_all()) == pytest.approx(1000.0 - result["fee"])
    # Equity excludes the lock, because the proceeds are already inside cash.
    state = engine.get_state()
    assert state["equity"] == pytest.approx(
        state["cash"] + state["unrealized"] - state["short_margin_locked"]
    )


# ---------------------------------------------------------------- accounting


def test_equity_after_a_fresh_entry_is_cash_plus_unrealized_less_locked_margin(paper):
    """The identity, on a position opened by this planner, immediately after."""
    paper.register()
    plan = plan_one(BTC, budget=5.0)
    result = planner.execute(plan)
    assert result["executed"] is True

    state = engine.get_state()

    assert state["cash"] == pytest.approx(db.get_cash())
    assert state["short_margin_locked"] == pytest.approx(0.0)
    assert state["equity"] == pytest.approx(
        state["cash"] + state["unrealized"] - state["short_margin_locked"]
    )
    # A long just filled is held at its entry, so it carries no open P&L yet.
    assert state["unrealized"] == pytest.approx(0.0)
    assert state["equity"] == pytest.approx(result["equity"])
    # And the cash really did move: this is not the pre-trade figure.
    assert state["equity"] < state["starting_cash"]
    # 1000 less the position and the entry fee: the notional is now held, not spent
    # on nothing, but this account measures equity as cash plus open P&L.
    notional = plan["qty"] * plan["entry_price"]
    assert result["equity"] == pytest.approx(1000.0 - notional - result["fee"], rel=1e-6)
    assert result["equity"] != pytest.approx(1000.0)


def test_the_journal_reports_the_balance_as_it_is_after_the_fill(paper):
    """The reported defect: equity 1000 beside a cash balance of 499.80.

    Not an accounting bug -- the identity holds, and the ledger always agreed with
    itself. A display one: the plan carried the balance it read *before* the fill,
    and the journal wrote it next to the fill's own cash. Both numbers are now
    read after it, from the same transaction that moved the money.
    """
    paper.register()
    plan = plan_one(BTC, budget=5.0)
    result = planner.execute(plan)
    assert result["executed"] is True

    logged = json.loads(
        rows(
            "SELECT payload FROM decisions WHERE kind='plan_executed' ORDER BY id DESC LIMIT 1"
        )[0]["payload"]
    )

    assert logged["cash"] == pytest.approx(result["cash"])
    assert logged["equity"] == pytest.approx(result["equity"])
    assert logged["execution"]["cash"] == pytest.approx(result["cash"])
    # The same numbers the balance actually holds now, not the ones it held before.
    assert logged["cash"] == pytest.approx(db.get_cash())
    assert logged["equity"] == pytest.approx(engine.equity_of(db.get_all(), 0.0))


def test_an_entry_writes_an_equity_row_so_the_daily_loss_guard_can_see_it(paper):
    """The equity curve, peak and drawdown only move if a row is written.

    Only ``engine.trading_cycle`` used to write one and the worker no longer runs
    that path, so a planner entry left the table untouched -- which also left the
    daily-loss guard, that reads this table, blind to the position it had just
    opened.
    """
    paper.register()
    assert rows("SELECT COUNT(*) AS n FROM equity")[0]["n"] == 0

    planner.execute(plan_one(BTC, budget=5.0))

    curve = rows("SELECT cash,equity,unrealized FROM equity")
    assert len(curve) == 1
    assert curve[0]["cash"] == pytest.approx(db.get_cash())
    assert curve[0]["equity"] == pytest.approx(db.get_cash())
    assert float(db.get("last_equity")) == pytest.approx(db.get_cash())
    assert float(db.get("peak_equity")) >= db.get_cash()

    # Mark the position down and record again: two rows is what the guard needs to
    # have a day to measure a loss against.
    with db.conn() as c:
        c.execute("UPDATE positions SET mark=entry*0.99 WHERE symbol=?", (BTC,))
    engine._record_equity()
    assert len(rows("SELECT ts FROM equity")) == 2

    blocked, detail = planner._daily_loss(4.0)
    assert blocked is True
    assert "reached the 4.00 USD limit" in detail
    assert planner._daily_loss(20.0)[0] is False


# ---------------------------------------------- strategies without the mirror


def test_the_planner_sees_a_strategy_whose_id_carries_a_timeframe(paper):
    """Requirement 5, and the check that matters most.

    Matching a strategy to a symbol by taking everything after the first dash of
    its id cannot see ``momentum-BTCUSDT-15m``: it would look for a symbol called
    ``BTCUSDT-15m`` and find nothing. That is why research wrote mirror rows, and
    with the mirror gone a parsing mistake here means no candidates and no trades
    at all -- silently.
    """
    paper.register(symbol=BTC, interval="15m")

    found = planner._strategies_for(BTC, "15m")

    assert [row["id"] for row in found] == ["momentum-BTCUSDT-15m"]


def test_the_planner_plans_from_a_timeframe_bound_strategy_with_no_mirror_rows(paper):
    paper.register(symbol=BTC, interval="5m")
    assert rows("SELECT COUNT(*) AS n FROM strategies WHERE hypothesis_id=?",
                engine.MIRROR_HYPOTHESIS)[0]["n"] == 0

    plan = plan_one(BTC, budget=5.0)

    assert plan["refusal_reason"] is None
    assert plan["strategy_id"] == "momentum-BTCUSDT-5m"
    assert plan["strategy_interval"] == "5m"
    assert plan["qty"] > 0


def test_a_legacy_id_still_matches_and_a_timeframe_match_is_preferred(paper):
    """Rows registered before ids carried a timeframe stay tradeable.

    Ordering is a preference, not a filter: a strategy validated on 15m is still
    returned for a 5m plan, behind the ones that were validated on 5m and behind
    the legacy rows that make no timeframe claim at all. It is not refused, because
    refusing it would turn a widened research grid into fewer trades than it had.
    """
    paper.register(symbol=SOL, family="donchian", interval="1h")
    paper.register(symbol=SOL, family="momentum")  # legacy, no interval
    paper.register(symbol=SOL, family="sma_cross", interval="5m")
    paper.register(symbol=BTC, family="momentum")

    sol = planner._strategies_for(SOL, "5m")

    assert [row["id"] for row in sol] == [
        "sma_cross-SOLUSDT-5m",
        "momentum-SOLUSDT",
        "donchian-SOLUSDT-1h",
    ]
    assert planner._strategies_for(BTC, "5m")[0]["id"] == "momentum-BTCUSDT"
    assert planner._strategies_for("ETHUSDT", "5m") == []


def test_research_purges_the_mirror_rows_and_writes_none(paper, monkeypatch):
    paper.register(symbol=BTC, interval="15m")
    paper.register_mirror(symbol=BTC)
    paper.register_mirror(symbol=SOL)
    assert rows("SELECT COUNT(*) AS n FROM strategies WHERE hypothesis_id=?",
                engine.MIRROR_HYPOTHESIS)[0]["n"] == 2

    # One backtest that clears the bar, so the cycle has something to register.
    passing = {
        "experiments": [
            {
                "interval": "15m",
                "family": "momentum",
                "params": {"n": 5},
                "metrics": dict(GOOD_METRICS, max_drawdown=10.0),
            }
        ],
        "skipped": [],
        "truncated": 0,
    }
    monkeypatch.setattr(engine, "backtest_matrix", lambda symbol, intervals, load, **kw: dict(passing))

    summary = engine.research_cycle(["BTC"], "15m")

    assert summary["mirror_rows_removed"] == 2
    assert rows("SELECT COUNT(*) AS n FROM strategies WHERE hypothesis_id=?",
                engine.MIRROR_HYPOTHESIS)[0]["n"] == 0
    ids = {r["id"] for r in rows("SELECT id FROM strategies")}
    assert "momentum-BTCUSDT-15m" in ids
    # And with the mirror gone, the real row is still visible to the planner.
    assert [row["id"] for row in planner._strategies_for(BTC, "15m")] == ["momentum-BTCUSDT-15m"]


def test_the_purge_is_idempotent_and_only_touches_mirror_rows(paper):
    paper.register(symbol=BTC, interval="15m")
    paper.register_mirror(symbol=BTC)

    assert engine.purge_mirror_strategies() == 1
    assert engine.purge_mirror_strategies() == 0
    assert [r["id"] for r in rows("SELECT id FROM strategies")] == ["momentum-BTCUSDT-15m"]


def test_a_refused_plan_still_names_the_strategy_that_refused(paper):
    """Every rejection keeps its strategy id, so a refusal is actionable."""
    paper.register(symbol=BTC, interval="5m")

    plan = plan_one(BTC, budget=0.001)

    assert plan["refusal_reason"] == planner.BELOW_MIN_SIZE
    assert [c["strategy_id"] for c in plan["considered"]] == ["momentum-BTCUSDT-5m"]
