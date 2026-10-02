"""The planner's gate: nothing reaches a paper order without a reason.

Every test here runs against a temporary paper database with mocked candles,
mocked strategies and a mocked Jev analyst, so no network call is ever made.
The cases pin the behaviour that matters: a validated strategy with a high
p(take) produces a positive-reward plan with real reasoning, and every way of
being unfit produces a refusal with a stated reason.
"""

import json
import time

import pytest

from services.paper import db, engine, planner

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
    """An isolated paper database with cash, candles and a silent analyst."""
    monkeypatch.setattr(db, "DATA", tmp_path / "paper.db")
    db.init()

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

    def use(candle_data, symbol="BTCUSDT"):
        monkeypatch.setattr(engine, "_candles", lambda s, interval="5m": candle_data)

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
    """A bigger budget buys more risk, up to the caps, and never past them."""
    paper.configure()
    paper.register()
    paper.use(RISING)
    monkeypatch.setattr(planner.jev, "ask", take_answer)

    small = planner.plan(symbols=["BTCUSDT"], max_risk=0.5)
    large = planner.plan(symbols=["BTCUSDT"], max_risk=1.0)

    assert small["refusal_reason"] is None and large["refusal_reason"] is None
    assert small["qty"] < large["qty"]
    assert small["risk_usd"] == pytest.approx(0.5)
    # One budget's risk is the whole budget. The larger one asks for exactly the
    # notional ceiling's worth of position, so the ceiling is what binds it.
    assert large["qty"] * large["entry_price"] == pytest.approx(500.0, rel=1e-6)
    assert large["risk_usd"] == pytest.approx(1.0)


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
