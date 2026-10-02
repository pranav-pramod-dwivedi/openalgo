"""The planner's gate: nothing reaches a paper order without a reason.

Every test here runs against a temporary paper database with a synthetic market,
mocked strategies and a mocked Jev analyst, so no network call is ever made.
The cases pin the behaviour that matters: a validated strategy with a high
p(take) produces a positive-reward plan with real reasoning, and every way of
being unfit produces a refusal with a stated reason.

The last section covers the gate ``execute`` now runs before it writes anything:
the independent verifier, and the fill priced off a real two-sided book. Both
read the market for real, so both need a market that agrees with the candles the
planner planned from -- see ``use()`` below.
"""

import json
import time

import pytest

from services.paper import db, engine, execution, planner, verifier

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


# ------------------------------------------------------------- the synthetic book
#
# ``planner.execute`` reads the market for real, twice. The verifier re-fetches the
# live quote and the candle history and refuses any plan whose prices do not sit
# near them; the execution model then fills the order against the real bid and
# ask. Neither will accept the price this ladder is written at -- BTC trades near
# 84,500 and RISING closes at 119.75 -- and neither is wrong to say so: a gap of
# that size is exactly the fabrication the verifier exists to catch, and widening
# the band to let a hundred-dollar candle through would delete the check.
#
# So the tests build a world that is internally consistent instead. The candles
# the planner reads, the quote the verifier checks the plan against, the history
# it age-checks and the book the fill crosses are all the same bars, so the entry
# a plan is built from and the price it is filled at cannot disagree about what
# the market is. The book is two-sided with a real spread either side of the mid,
# so the execution model has a book to cross and a volume to measure the order
# against rather than one price wearing two names.
#
# The field names and the shape are ``fetch_crypto_quote``'s, so the stub cannot
# pass either module by carrying something the real feed never sends.

#: A BTC major on Binance sits a couple of basis points wide; the paper account
#: refuses a book wider than ``execution.DEFAULT_MAX_SPREAD_BPS``.
BOOK_SPREAD_BPS = 3.0


def book_from(ladder, *, spread_bps=BOOK_SPREAD_BPS, volume=None):
    """A two-sided quote for exactly the world ``ladder`` describes.

    The mid is the newest close, which is the number the planner turned into an
    entry price, so the verifier's plausibility band is cleared because the two
    were derived from one thing rather than because a figure was tuned. The
    volume is the day's worth of the same ladder, which is what a rolling 24h
    figure is, and is deep enough that an ordinary paper order fills whole.
    """
    mid = float(ladder[-1]["close"])
    half = spread_bps / 20_000.0
    return {
        "ltp": mid,
        "bid": round(mid * (1.0 - half), 8),
        "ask": round(mid * (1.0 + half), 8),
        "open": float(ladder[0]["open"]),
        "high": round(max(float(bar["high"]) for bar in ladder), 8),
        "low": round(min(float(bar["low"]) for bar in ladder), 8),
        "prev_close": float(ladder[-2]["close"]),
        "volume": float(volume) if volume is not None else float(sum(bar["volume"] for bar in ladder)),
        "oi": 0.0,
    }


def stamped(ladder, *, age=0.0, interval=300.0):
    """The ladder with a clock on every bar that has none, newest ``age`` seconds old.

    The clock is written when the world is installed rather than when the ladder
    is written, because a ladder built at import time is an hour stale by the
    time a long suite reaches the test that uses it, and the verifier reads a
    stale bar for what it is. A bar that already carries a timestamp keeps it:
    that is how a case says in one line that these bars are five hours old.
    Milliseconds, as crypto feeds send them.
    """
    now = time.time()
    out = []
    for i, bar in enumerate(ladder):
        copied = dict(bar)
        if not any(key in copied for key in ("time", "timestamp", "ts", "open_time", "date")):
            copied["time"] = (now - age - (len(ladder) - 1 - i) * interval) * 1000.0
        out.append(copied)
    return out


def verdict(p_take=0.72, p_quality=0.61):
    return {
        "answers": {
            "trade": {"probabilities": {"0": round(1 - p_take, 4), "1": p_take}},
            "quality": {"probabilities": {"0": round(1 - p_quality, 4), "1": p_quality}},
        }
    }


def take_answer(state, questions):
    """The analyst's own question set, answered, so a swap is visible."""
    assert set(questions) == {"trade", "quality"}
    assert questions["trade"]["criteria"] == ["skip", "take"]
    assert questions["quality"]["criteria"] == ["weak", "strong"]
    return verdict()


@pytest.fixture
def paper(tmp_path, monkeypatch):
    """An isolated paper database with cash, a synthetic market and a silent analyst."""
    monkeypatch.setattr(db, "DATA", tmp_path / "paper.db")
    db.init()

    def configure(**overrides):
        values = {
            "starting_cash": 1000.0,
            "cash": 1000.0,
            # The verifier's per-position ceiling is the account's own
            # ``max_position_qty``, in coins. Every installation still carries
            # that key at the pre-resize default of 0.01 -- the flat quantity
            # the sizing rule was written to stop reading -- which on this
            # account is 1.20 USD and would refuse every position the notional
            # cap allows. One coin is a real limit here and sits above the
            # largest position a 1000 USD account can open at these prices, so
            # the check runs against a genuine number rather than being widened
            # out of the way; ``test_the_coin_ceiling_still_refuses_an_oversized
            # _position`` proves it is armed.
            "max_position_qty": 1.0,
            "max_exposure_pct": 0.5,
            "fee_bps": 4.0,
            "slippage_bps": 2.0,
            "max_daily_loss": 20.0,
            "short_margin_locked": 0.0,
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

    def open_position(symbol="BTCUSDT", side="BUY", qty=0.005, entry=100.0):
        db.ensure_column("positions", "mark", "REAL")
        with db.conn() as c:
            c.execute(
                "INSERT OR REPLACE INTO positions"
                "(symbol,side,qty,entry,opened,strategy_id,sl,tp,status,mark) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (symbol, side, qty, entry, time.time(), "manual", entry * 0.99, entry * 1.01, "open", entry),
            )

    def use(candle_data, symbol="BTCUSDT", **book):
        """Install one symbol's world, from the same bars every reader gets.

        The planner reads the candles through ``engine._candles``; the verifier
        re-reads the history from the provider and prices the plan against the
        provider's quote. Pointing all three at one ladder is what makes the
        entry a plan was built from, the price its levels are checked against
        and the price it is filled at the same level. ``book`` goes to
        ``book_from``, so a case that needs a wide spread or a thin market says
        so instead of patching the model.
        """
        bars = stamped(candle_data)
        quote = book_from(bars, **book)
        monkeypatch.setattr(engine, "_candles", lambda s, interval="5m": bars)

        def get_quote(sym, exchange):
            return dict(quote) if str(sym).upper() == symbol.upper() else None

        def get_history(sym, exchange, interval="5m", start_date="", end_date=""):
            return list(bars) if str(sym).upper() == symbol.upper() else []

        monkeypatch.setattr("services.foreign_data_service.get_foreign_quote", get_quote)
        monkeypatch.setattr("services.foreign_data_service.get_foreign_history", get_history)
        return quote

    return type(
        "Paper",
        (),
        {
            "configure": staticmethod(configure),
            "register": staticmethod(register),
            "open_position": staticmethod(open_position),
            "use": staticmethod(use),
        },
    )


def counts():
    """How many orders, fills and positions exist, for a gate that wrote nothing."""
    with db.conn() as c:
        return tuple(
            c.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"]
            for table in ("orders", "fills", "positions")
        )


def clean_plan(paper, monkeypatch, ladder=RISING, book=None, **kwargs):
    """A plan that clears every gate, so a case can break exactly one thing.

    ``book`` goes to ``use``, so a case that needs the market itself to be wrong
    (a book too wide to cross, one too thin to fill) says so here instead of
    patching the model underneath.
    """
    paper.configure()
    paper.register()
    paper.use(ladder, **(book or {}))
    monkeypatch.setattr(planner.jev, "ask", take_answer)
    plan = planner.plan(symbols=["BTCUSDT"], **kwargs)
    assert plan["refusal_reason"] is None, plan.get("refusal_detail")
    return plan


# ----------------------------------------------------------------------- cases


def test_validated_strategy_with_high_p_take_produces_a_rr_positive_plan(paper, monkeypatch):
    paper.configure()
    paper.register()
    paper.use(RISING)
    monkeypatch.setattr(planner.jev, "ask", take_answer)

    plan = planner.plan(symbols=["BTCUSDT"])

    assert plan["refusal_reason"] is None
    assert plan["side"] == "BUY"
    assert plan["qty"] > 0
    assert plan["rr"] > 0
    assert plan["reward_usd"] > 0
    assert plan["risk_usd"] > 0
    assert plan["strategy_id"] == "momentum-BTCUSDT"
    assert plan["metrics"]["trades"] == 24
    assert plan["metrics"]["net_pnl"] == pytest.approx(41.5)
    assert plan["analyst_available"] is True
    assert plan["jev_verdict"]["p_take"] == pytest.approx(0.72)
    assert plan["brackets_attached"] is False


def test_reasoning_quotes_the_real_numbers_and_the_invalidation(paper, monkeypatch):
    paper.configure()
    paper.register()
    paper.use(RISING)
    monkeypatch.setattr(planner.jev, "ask", take_answer)

    plan = planner.plan(symbols=["BTCUSDT"])
    text = plan["reasoning"]

    # What was expected, from the recorded backtest.
    assert "24" in text
    assert "41.50" in text
    # What the data showed.
    assert "volatility" in text
    assert plan["side"] in text
    assert str(plan["qty"])[:6] in text
    # What would invalidate it.
    assert f"{plan['stop_loss']:.4f}" in text
    assert f"{plan['take_profit']:.4f}" in text
    # Plain English, several sentences, no invented confidence figure.
    assert text.count(".") >= 4
    assert "confidence" not in text.lower()


def test_failed_validation_is_refused_and_the_reason_is_recorded(paper, monkeypatch):
    paper.configure()
    paper.register(metrics={"trades": 2, "net_pnl": 18.0, "max_drawdown": 5.0})
    paper.use(RISING)
    monkeypatch.setattr(planner.jev, "ask", take_answer)

    plan = planner.plan(symbols=["BTCUSDT"])

    assert plan["refusal_reason"] == planner.FAILED_VALIDATION
    assert "trades 2" in plan["refusal_detail"]
    rejection = [c for c in plan["considered"] if c["reason"] == planner.FAILED_VALIDATION]
    assert rejection and rejection[0]["strategy_validated"] is False


def test_negative_net_pnl_and_deep_drawdown_are_both_refused(paper, monkeypatch):
    paper.configure()
    paper.register(sid="loser-BTCUSDT", metrics={"trades": 30, "net_pnl": -4.2, "max_drawdown": 3.0})
    paper.use(RISING)
    monkeypatch.setattr(planner.jev, "ask", take_answer)
    assert planner.plan(symbols=["BTCUSDT"])["refusal_reason"] == planner.FAILED_VALIDATION
    assert "not positive" in planner.plan(symbols=["BTCUSDT"])["refusal_detail"]

    paper.register(sid="deep-BTCUSDT", metrics={"trades": 30, "net_pnl": 9.0, "max_drawdown": 61.0})
    plan = planner.plan(symbols=["BTCUSDT"])
    assert plan["refusal_reason"] == planner.FAILED_VALIDATION
    assert "drawdown" in plan["refusal_detail"]


def test_analyst_unavailable_refuses_unless_rules_only_is_explicit(paper, monkeypatch):
    paper.configure()
    paper.register()
    paper.use(RISING)
    monkeypatch.setattr(planner.jev, "ask", lambda *a, **k: None)

    refused = planner.plan(symbols=["BTCUSDT"])
    assert refused["refusal_reason"] == planner.ANALYST_UNAVAILABLE
    assert refused["analyst_available"] is False

    allowed = planner.plan(symbols=["BTCUSDT"], allow_rules_only=True)
    assert allowed["refusal_reason"] is None
    assert allowed["analyst_available"] is False
    assert "did not answer" in allowed["reasoning"]


def test_analyst_failure_is_never_fabricated_into_a_verdict(paper, monkeypatch):
    paper.configure()
    paper.register()
    paper.use(RISING)

    def boom(*_a, **_k):
        raise RuntimeError("network down")

    monkeypatch.setattr(planner.jev, "ask", boom)
    plan = planner.plan(symbols=["BTCUSDT"])
    assert plan["refusal_reason"] == planner.ANALYST_UNAVAILABLE
    assert plan["jev_verdict"] is None


def test_low_p_take_is_refused_even_when_the_setup_looks_good(paper, monkeypatch):
    paper.configure()
    paper.register()
    paper.use(RISING)
    monkeypatch.setattr(planner.jev, "ask", lambda s, q: verdict(p_take=0.11, p_quality=0.9))

    plan = planner.plan(symbols=["BTCUSDT"])
    assert plan["refusal_reason"] == planner.LOW_TAKE_PROBABILITY
    assert "0.11" in plan["refusal_detail"]


def test_insufficient_cash_is_refused(paper, monkeypatch):
    paper.configure(cash=3.0, max_position_qty=5.0, max_exposure_pct=1.0)
    paper.register()
    paper.use(RISING)
    monkeypatch.setattr(planner.jev, "ask", take_answer)

    plan = planner.plan(symbols=["BTCUSDT"])
    assert plan["refusal_reason"] == planner.INSUFFICIENT_CASH


def test_already_open_symbol_is_refused(paper, monkeypatch):
    paper.configure()
    paper.register()
    paper.use(RISING)
    monkeypatch.setattr(planner.jev, "ask", take_answer)
    paper.open_position(symbol="BTCUSDT")

    plan = planner.plan(symbols=["BTCUSDT"])
    assert plan["refusal_reason"] == planner.SYMBOL_ALREADY_OPEN


def test_stale_candles_are_refused(paper, monkeypatch):
    paper.configure()
    paper.register()
    now = time.time()
    stale = candles([100.0 + i * 0.5 for i in range(40)])
    for offset, bar in enumerate(stale):
        bar["time"] = now - 20000 + offset * 300  # every bar over five hours old
    paper.use(stale)
    monkeypatch.setattr(planner.jev, "ask", take_answer)

    plan = planner.plan(symbols=["BTCUSDT"], interval="5m")
    assert plan["refusal_reason"] == planner.STALE_DATA
    assert "old" in plan["refusal_detail"]


def test_the_model_never_flips_the_direction(paper, monkeypatch):
    paper.configure()
    paper.register()
    paper.use(FALLING)
    # An analyst that demands a BUY in words. The plan must not follow it.
    monkeypatch.setattr(planner.jev, "ask", lambda s, q: verdict(p_take=0.95, p_quality=0.95))

    plan = planner.plan(symbols=["BTCUSDT"])
    assert plan["refusal_reason"] is None
    assert plan["side"] == "SELL"

    result = planner.execute(plan)
    assert result["executed"] is True
    with db.conn() as c:
        pos = c.execute("SELECT side FROM positions WHERE status='open'").fetchone()
        fill = c.execute("SELECT side FROM fills ORDER BY id DESC LIMIT 1").fetchone()
    assert pos["side"] == "SELL"
    assert fill["side"] == "SELL"
    # A short posts margin rather than spending notional.
    assert db.get_margin_locked() > 0


def test_a_budget_too_small_to_trade_is_refused_rather_than_shrunk_to_dust(paper, monkeypatch):
    """The old behaviour let a 0.001 USD budget open a position risking nothing.

    Shrinking to fit was never the honest answer: the plan claimed a budget it was
    not using and the resulting risk rounded to 0.00 in every report. Too small to
    be a trade is a refusal with the numbers that produced it.
    """
    paper.configure()
    paper.register()
    paper.use(RISING)
    monkeypatch.setattr(planner.jev, "ask", take_answer)

    generous = planner.plan(symbols=["BTCUSDT"], max_risk=5.0)
    dust = planner.plan(symbols=["BTCUSDT"], max_risk=0.001)
    unfundable = planner.plan(symbols=["BTCUSDT"], max_risk=1000.0)

    assert generous["refusal_reason"] is None
    assert generous["side"] == "BUY"
    assert dust["refusal_reason"] == planner.BELOW_MIN_SIZE
    assert "sizing refused" in dust["refusal_detail"]
    assert "minimum" in dust["refusal_detail"]
    # A budget the account cannot fund at all is refused for the same reason,
    # with the numbers that produced it rather than a position nobody sized.
    assert unfundable["refusal_reason"] == planner.BELOW_MIN_SIZE
    assert "at risk against a" in unfundable["refusal_detail"]
    # Nothing was opened for either.
    with db.conn() as c:
        assert c.execute("SELECT COUNT(*) AS n FROM positions").fetchone()["n"] == 0


def test_a_budget_raises_the_position_until_the_notional_ceiling_stops_it(paper, monkeypatch):
    """A bigger budget buys more risk, up to the caps, and never past them.

    The rule is ``qty = risk_budget / |entry - stop|``, and every ceiling after
    that only makes the position smaller. The stop is ``planner.STOP_PCT`` from
    the entry, so a notional is worth ``notional * STOP_PCT`` of risk, and that is
    what fixes the window in which a budget still moves the size: the
    per-position ceiling is reached at ``ceiling * STOP_PCT`` of risk, and below
    ``MIN_POSITION_NOTIONAL_USD * STOP_PCT`` there is no trade to size at all.

    On the default account that window exists and is wide enough to demonstrate,
    but it sits far below ``engine.DEFAULT_RISK_BUDGET_USD``, so at any budget a
    caller would really pass the ceiling has already bound and a bigger budget is
    not a bigger position. That is the part worth pinning, and both sides of the
    crossover are pinned below, with every figure read out of the engine's own
    constants and the account rather than written down here.
    """
    # The account the engine itself seeds, with the exposure cap opened above the
    # per-position ceiling so the notional ceiling is unambiguously the cap that
    # binds. Exposure has its own cases and its own figures.
    paper.configure(cash=engine.DEFAULT_CASH, max_exposure_pct=1.0)
    paper.register()
    paper.use(RISING)
    monkeypatch.setattr(planner.jev, "ask", take_answer)

    ceiling_notional = engine.DEFAULT_MAX_POSITION_NOTIONAL_USD
    min_notional = engine.MIN_POSITION_NOTIONAL_USD

    # The plans, not this test's algebra, define the window: how much of an entry
    # the planner's own stop puts at risk is what turns a notional into a budget.
    probe = planner.plan(symbols=["BTCUSDT"], max_risk=engine.DEFAULT_RISK_BUDGET_USD)
    assert probe["refusal_reason"] is None
    risk_per_notional = (probe["entry_price"] - probe["stop_loss"]) / probe["entry_price"]
    assert risk_per_notional == pytest.approx(planner.STOP_PCT), (
        "the stop distance is what converts a notional into a risk budget"
    )

    # The two budgets that bound the window, both derived rather than written.
    smallest_budget = min_notional * risk_per_notional
    crossover_budget = ceiling_notional * risk_per_notional
    assert crossover_budget > smallest_budget, (
        "the account must leave a budget range in which a bigger budget does buy a "
        "bigger position, or this rule has nothing left to prove"
    )
    window = crossover_budget - smallest_budget

    # Below the crossover, and clear of both edges, a bigger budget buys more of
    # the position: nothing capped either one, so the budget is the whole risk.
    small = planner.plan(symbols=["BTCUSDT"], max_risk=smallest_budget + window / 3)
    large = planner.plan(symbols=["BTCUSDT"], max_risk=smallest_budget + 2 * window / 3)

    assert small["refusal_reason"] is None and large["refusal_reason"] is None
    assert small["qty"] < large["qty"]
    assert small["risk_usd"] == pytest.approx(smallest_budget + window / 3)
    assert large["risk_usd"] == pytest.approx(smallest_budget + 2 * window / 3)
    assert large["qty"] * large["entry_price"] < ceiling_notional

    # At and past the crossover the ceiling binds, so the position is worth what
    # the ceiling says it is worth no matter what was asked for.
    at_ceiling = planner.plan(symbols=["BTCUSDT"], max_risk=crossover_budget)
    past_ceiling = planner.plan(symbols=["BTCUSDT"], max_risk=crossover_budget * 10)
    default_budget = planner.plan(symbols=["BTCUSDT"])

    assert at_ceiling["refusal_reason"] is None and past_ceiling["refusal_reason"] is None
    assert past_ceiling["qty"] == at_ceiling["qty"], (
        "past the crossover the notional ceiling binds, so a bigger budget must not "
        "buy a bigger position"
    )
    for capped in (at_ceiling, past_ceiling):
        assert capped["qty"] * capped["entry_price"] == pytest.approx(ceiling_notional, rel=1e-6)
        assert capped["risk_usd"] == pytest.approx(crossover_budget)
    # Which is why the account's own default budget already buys the ceiling's
    # position: at this scale a bigger budget is not a bigger trade.
    assert default_budget["refusal_reason"] is None
    assert default_budget["risk_usd"] == pytest.approx(crossover_budget)

    # The ceiling can only carry so much risk. Past the budget whose minimum risk
    # floor the ceiling cannot meet, the honest answer is a refusal naming both
    # numbers -- not a position shrunk to carry less than the floor demands.
    overreach = at_ceiling["risk_usd"] / engine.MIN_RISK_FRACTION * 2
    unfundable = planner.plan(symbols=["BTCUSDT"], max_risk=overreach)

    assert unfundable["refusal_reason"] == planner.BELOW_MIN_SIZE
    detail = unfundable["refusal_detail"]
    assert (
        f"{at_ceiling['risk_usd']:.4f} USD at risk against a {overreach:.2f} USD budget" in detail
    ), f"the refusal must name the risk the ceiling can carry: {detail}"
    assert (
        f"under the {engine.MIN_RISK_FRACTION:.0%} "
        f"({overreach * engine.MIN_RISK_FRACTION:.4f} USD) minimum" in detail
    ), f"and the floor it could not meet: {detail}"


def test_execute_refuses_a_refused_plan_and_writes_nothing(paper, monkeypatch):
    paper.configure(cash=1.0, max_position_qty=5.0, max_exposure_pct=1.0)
    paper.register()
    paper.use(RISING)
    monkeypatch.setattr(planner.jev, "ask", take_answer)

    plan = planner.plan(symbols=["BTCUSDT"])
    assert plan["refusal_reason"] == planner.INSUFFICIENT_CASH

    result = planner.execute(plan)
    assert result["executed"] is False
    assert result["refusal_reason"] == planner.INSUFFICIENT_CASH
    with db.conn() as c:
        assert c.execute("SELECT COUNT(*) AS n FROM orders").fetchone()["n"] == 0
        assert c.execute("SELECT COUNT(*) AS n FROM fills").fetchone()["n"] == 0


def test_execute_refuses_a_rules_only_plan(paper, monkeypatch):
    """Without the explicit override, a plan the analyst never saw is refused."""
    paper.configure()
    paper.register()
    paper.use(RISING)
    monkeypatch.setattr(planner.jev, "ask", lambda *a, **k: None)

    plan = planner.plan(symbols=["BTCUSDT"], allow_rules_only=False)
    assert plan["refusal_reason"] == planner.ANALYST_UNAVAILABLE
    assert plan.get("analyst_bypassed") is not True

    result = planner.execute(plan)
    assert result["executed"] is False
    assert result["refusal_reason"] == planner.ANALYST_UNAVAILABLE


def test_explicit_override_executes_but_is_flagged_as_unreviewed(paper, monkeypatch):
    """The bypass is allowed only when asked for, and is always labelled."""
    paper.configure()
    paper.register()
    paper.use(RISING)
    monkeypatch.setattr(planner.jev, "ask", lambda *a, **k: None)

    plan = planner.plan(symbols=["BTCUSDT"], allow_rules_only=True)
    assert plan["refusal_reason"] is None
    assert plan["analyst_available"] is False
    assert plan["analyst_bypassed"] is True

    result = planner.execute(plan)
    assert result["executed"] is True


def test_execute_moves_cash_and_writes_a_plan_executed_decision(paper, monkeypatch):
    paper.configure()
    paper.register()
    paper.use(RISING)
    monkeypatch.setattr(planner.jev, "ask", take_answer)

    plan = planner.plan(symbols=["BTCUSDT"], max_risk=5.0)
    before = db.get_cash()
    result = planner.execute(plan)

    assert result["executed"] is True
    assert result["brackets_attached"] is False
    assert result["bracket_note"]

    # A long debit is notional plus fee, taken from the one live balance.
    with db.conn() as c:
        fill = c.execute("SELECT * FROM fills WHERE order_id=?", (result["order_id"],)).fetchone()
    assert db.get_cash() == pytest.approx(before - (fill["price"] * fill["qty"] + fill["fee"]))

    with db.conn() as c:
        row = c.execute(
            "SELECT payload FROM decisions WHERE kind='plan_executed' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        pos = c.execute("SELECT * FROM positions WHERE status='open'").fetchone()
    logged = json.loads(row["payload"])
    assert logged["reasoning"] == plan["reasoning"]
    assert logged["strategy_id"] == plan["strategy_id"]
    assert logged["execution"]["order_id"] == result["order_id"]
    # The stop and target live on the position row, since no resting exit exists.
    assert pos["sl"] == pytest.approx(plan["stop_loss"])
    assert pos["tp"] == pytest.approx(plan["take_profit"])


# ------------------------------------------------------------- the gate itself
#
# Everything below is about what ``execute`` does before it writes a row. The
# planner is our own code and is not evidence: it builds the entry from the last
# close, the quantity from a risk budget and the money from that quantity, so a
# bug anywhere in those steps produces a plan whose numbers are plausible and
# invented. The verifier re-derives what can be re-derived from the live market
# and refuses the rest, and each case here breaks exactly one thing on a plan
# that is otherwise clean, so a failure names one cause rather than a pile.
#
# Nothing here weakens a check to make a case pass. Where the gate refuses, the
# case asserts the refusal *and* that no order, fill or position row was written,
# because a gate that denies and then books the trade anyway is not a gate.


def moved_plan(plan, factor):
    """The same plan with every price level moved, and its money re-derived.

    The point is to break the *price* check and nothing else: a plan whose entry
    is far from the market while its own arithmetic is perfectly consistent is
    what a fabricated plan looks like, and it has to be refusable on the price
    alone. So the stop, the target, the risk, the reward and the ratio all move
    with the entry and the arithmetic still adds up.
    """
    moved = dict(plan)
    entry = plan["entry_price"] * factor
    stop = plan["stop_loss"] * factor
    target = plan["take_profit"] * factor
    moved.update(
        entry_price=entry,
        stop_loss=stop,
        take_profit=target,
        risk_usd=abs(entry - stop) * plan["qty"],
        reward_usd=abs(target - entry) * plan["qty"],
        rr=abs(target - entry) / abs(entry - stop),
    )
    return moved


def test_a_plan_priced_far_from_the_market_is_denied_and_nothing_is_written(
    paper, monkeypatch
):
    """The anti-hallucination check, at the only door a plan can come through.

    The world is the ordinary one -- RISING, quoted at its own newest close -- and
    the plan claims to trade the same coin at a tenth of it. Its arithmetic is
    internally perfect, which is the whole difficulty: nothing about the numbers
    on the page is wrong except that no such price exists.
    """
    plan = moved_plan(clean_plan(paper, monkeypatch), 0.1)

    result = planner.execute(plan)

    assert result["executed"] is False
    assert result["refusal_reason"] == verifier.PRICE_NOT_PLAUSIBLE
    assert counts() == (0, 0, 0), "a denied plan may not leave an order, a fill or a position"
    assert db.get_cash() == pytest.approx(1000.0), "and may not have moved the balance"
    # The verdict is journalled, so the dashboard can show what was refused.
    verdict = verifier.recent(1)[0]
    assert verdict["allow"] is False
    assert verifier.PRICE_NOT_PLAUSIBLE in verdict["slugs"]
    assert verdict["checks"][verifier.CHECK_PRICE] is False
    # The price is the only thing wrong with this plan. Every other check clears,
    # so it is the fabrication this case is about and nothing else.
    assert verdict["checks"][verifier.CHECK_RISK] is True
    assert verdict["checks"][verifier.CHECK_RR] is True
    assert verdict["checks"][verifier.CHECK_FRESHNESS] is True
    assert [name for name, ok in verdict["checks"].items() if not ok] == [verifier.CHECK_PRICE]
    # Both prices are in the sentence a trader reads, and so is the gap.
    message = verdict["reasons"][0]["message"]
    assert f"{plan['entry_price']:,.5f}" in message
    assert str(RISING[-1]["close"]) in message
    assert "90.00%" in message


def test_a_plan_whose_risk_is_not_its_own_arithmetic_is_denied(paper, monkeypatch):
    """``risk_usd`` must equal ``|entry - stop| * qty`` and is re-derived, not trusted.

    Everything else on the plan is genuine, including a perfectly plausible entry
    sitting on the live quote. Only the money it claims to risk has been edited,
    which is exactly the field a bug in the sizing step would get wrong.
    """
    plan = clean_plan(paper, monkeypatch)
    claimed = plan["risk_usd"]
    plan["risk_usd"] = claimed * 1.5

    result = planner.execute(plan)

    assert result["executed"] is False
    assert result["refusal_reason"] == verifier.RISK_MISMATCH
    assert counts() == (0, 0, 0)
    verdict = verifier.recent(1)[0]
    assert verifier.RISK_MISMATCH in verdict["slugs"]
    assert verdict["checks"][verifier.CHECK_RISK] is False


def test_a_plan_naming_a_strategy_that_is_not_on_file_is_denied(paper, monkeypatch):
    """A trade with no recorded backtest behind it is not a trade."""
    plan = clean_plan(paper, monkeypatch)
    plan["strategy_id"] = "momentum-BTCUSDT-that-was-never-tested"

    result = planner.execute(plan)

    assert result["executed"] is False
    assert result["refusal_reason"] == verifier.STRATEGY_UNKNOWN
    assert counts() == (0, 0, 0)
    verdict = verifier.recent(1)[0]
    assert verifier.STRATEGY_UNKNOWN in verdict["slugs"]
    assert verdict["checks"][verifier.CHECK_STRATEGY_EXISTS] is False


def test_a_plan_naming_a_strategy_whose_own_record_fails_the_bar_is_denied(
    paper, monkeypatch
):
    """Registered and active is not enough; the row's own metrics have to clear it.

    The plan here quotes the *good* record while naming a row that holds a bad
    one, so both provenance checks speak: the strategy has not been validated,
    and the plan is describing a backtest better than the one that happened.
    """
    plan = clean_plan(paper, monkeypatch)
    paper.register(
        sid="weak-BTCUSDT",
        metrics={"trades": 4, "net_pnl": -2.5, "max_drawdown": 9.0, "fees": 1.1},
    )
    plan["strategy_id"] = "weak-BTCUSDT"

    result = planner.execute(plan)

    assert result["executed"] is False
    assert result["refusal_reason"] == verifier.STRATEGY_UNVALIDATED
    assert counts() == (0, 0, 0)
    verdict = verifier.recent(1)[0]
    assert verifier.STRATEGY_UNVALIDATED in verdict["slugs"]
    assert verdict["checks"][verifier.CHECK_STRATEGY_EXISTS] is True
    assert verdict["checks"][verifier.CHECK_STRATEGY_ACTIVE] is True
    assert verdict["checks"][verifier.CHECK_STRATEGY_VALIDATED] is False
    # And the plan's own flattering copy of the record is caught with it.
    assert verifier.STRATEGY_METRICS_MISMATCH in verdict["slugs"]


def test_the_coin_ceiling_still_refuses_an_oversized_position(paper, monkeypatch):
    """The per-position ceiling is armed, not merely set out of the way.

    ``max_position_qty`` is the one config key the sizing rule stopped reading,
    so it is tempting to leave it wherever it lies and let the ceiling pass. This
    is the case that says the ceiling is still a ceiling: with the account's own
    limit set just above the plan's size, the same plan is refused for wanting
    more of the coin than the account allows, and nothing is written.
    """
    plan = clean_plan(paper, monkeypatch)
    # The ceiling is a CURRENCY limit, not a coin count. A coin count is not
    # comparable across coins, so it was retired from the engine and must not be
    # revived here: a $50 position is 0.0006 BTC but 0.42 SOL.
    paper.configure(max_position_notional_usd=plan["qty"] * plan["entry_price"] / 2.0)

    result = planner.execute(plan)

    assert result["executed"] is False
    assert result["refusal_reason"] == verifier.QTY_ABOVE_CEILING
    assert counts() == (0, 0, 0)
    verdict = verifier.recent(1)[0]
    assert "worth" in verdict["reasons"][0]["message"]


def test_a_clean_plan_fills_off_the_real_book_and_records_the_fill_it_got(paper, monkeypatch):
    """The other half of the gate: nothing is refused, and the numbers are real.

    The recorded price is the book's, not the plan's. A buy lifts the ask and
    pays the model's slippage, which is a share of the spread actually observed,
    so the fill can be neither the plan's entry nor the last close. The fee is
    charged on that fill, and the whole outcome is journalled with the verdict
    that allowed it.
    """
    book = paper.use(RISING)
    plan = clean_plan(paper, monkeypatch)
    before = db.get_cash()

    result = planner.execute(plan)

    assert result["executed"] is True
    assert result["status"] == "filled"
    # The fill is the ask plus a share of the spread the book is actually
    # showing, which is the model crossing a market that costs what it costs. The
    # share is this account's own ``slippage_bps`` -- the value the model falls
    # back to when the config carries no ``execution_slippage_bps`` of its own.
    spread = book["ask"] - book["bid"]
    share = engine._config_float(db.get_all(), "slippage_bps", execution.DEFAULT_SPREAD_SLIPPAGE_BPS)
    expected = book["ask"] + spread * share / 10_000.0
    assert result["price"] == pytest.approx(round(expected, 8))
    assert result["price"] != pytest.approx(plan["entry_price"])
    assert book["bid"] < book["ask"] and book["ltp"] == pytest.approx(RISING[-1]["close"])
    # The quantity placed is the plan's, and the fee is charged on the fill.
    assert result["qty"] == pytest.approx(plan["qty"])
    assert result["fee"] == pytest.approx(
        result["price"] * result["qty"] * engine._fee_rate(db.get_all()), rel=1e-6
    )
    assert result["fee"] > 0.0
    assert db.get_cash() == pytest.approx(before - result["price"] * result["qty"] - result["fee"])

    # The fill row carries the book's price, and the verdict that allowed it is on
    # file next to it.
    with db.conn() as c:
        fill = c.execute("SELECT * FROM fills WHERE order_id=?", (result["order_id"],)).fetchone()
    assert fill["price"] == pytest.approx(result["price"])
    assert fill["fee"] == pytest.approx(result["fee"])
    assert fill["slippage"] > 0.0
    verdict = verifier.recent(1)[0]
    assert verdict["allow"] is True
    assert verdict["live_price"] == pytest.approx(book["ltp"])
    assert counts() == (1, 1, 1)


def test_a_book_too_wide_to_cross_is_denied_with_that_reason_and_writes_nothing(
    paper, monkeypatch
):
    """Past the configured spread there is no honest fill, so there is no fill.

    The plan is entirely sound and the price is real; what refuses it is the book
    being 200 bps wide, four times what this account will cross. The refusal has
    to name that number, because a reader told only "refused" cannot tell whether
    the market was closed or the plan was nonsense.
    """
    plan = clean_plan(paper, monkeypatch, book={"spread_bps": 200.0})

    result = planner.execute(plan)

    assert result["executed"] is False
    assert result["refusal_reason"] == execution.SPREAD_TOO_WIDE
    assert "bps wide" in result["detail"]
    assert counts() == (0, 0, 0)
    assert db.get_cash() == pytest.approx(1000.0)


def test_a_quote_with_a_last_price_but_no_book_is_denied_for_having_no_price(
    paper, monkeypatch
):
    """Enough to check a plan against, not enough to fill it.

    Binance's 24h ticker publishes a last price and no order book, so a quote can
    arrive carrying ``ltp`` and nothing else. That is a real price, so the
    verifier can confirm the plan is near the market and lets it through; and it
    is not a book, so the execution model has no side to cross and refuses. The
    two answers are both right, which is why the second one is reported as
    ``price_unavailable`` rather than folded into the first.
    """
    plan = clean_plan(paper, monkeypatch)
    monkeypatch.setattr(
        "services.foreign_data_service.get_foreign_quote",
        lambda symbol, exchange: {"ltp": float(RISING[-1]["close"])},
    )

    result = planner.execute(plan)

    assert result["executed"] is False
    assert result["refusal_reason"] == execution.PRICE_UNAVAILABLE
    assert counts() == (0, 0, 0)
    assert db.get_cash() == pytest.approx(1000.0)
    # The gate itself was happy with this plan: the refusal came from the book.
    verdict = verifier.recent(1)[0]
    assert verdict["allow"] is True
    assert verdict["checks"][verifier.CHECK_PRICE] is True


def test_an_order_bigger_than_the_market_can_absorb_fills_partially(paper, monkeypatch):
    """The volume path is exercised, not bypassed by a book deep enough to ignore.

    A real feed's volume is the rolling day, and the model allows this account a
    small share of it. Sizing the plan past that share is what a thin coin looks
    like from here, and the answer is a partial fill with the remainder left open
    -- the paper book is not infinitely deep, and pretending otherwise is the
    defect the model was written to remove.
    """
    plan = clean_plan(paper, monkeypatch)
    volume = plan["qty"] * 20.0
    paper.use(RISING, volume=volume)

    result = planner.execute(plan)

    # The account may take one percent of what the day traded, and at this price
    # that is a fraction of the order, so it cannot be filled whole.
    assert result["executed"] is True
    assert result["qty"] < plan["qty"]
    assert result["qty"] == pytest.approx(
        execution.DEFAULT_VOLUME_FRACTION * volume / result["price"], rel=1e-6
    )
    # What did fill is booked, and the balance moved only by that much.
    with db.conn() as c:
        fill = c.execute("SELECT * FROM fills WHERE order_id=?", (result["order_id"],)).fetchone()
    assert float(fill["qty"]) == pytest.approx(result["qty"])
    assert db.get_cash() == pytest.approx(1000.0 - result["price"] * result["qty"] - result["fee"])
