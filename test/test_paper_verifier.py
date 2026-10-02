"""The independent check on every paper trade, including the ones we wrote.

The point of the verifier is that it does not trust the planner, so these tests
never hand it a real plan: every case builds the plan by hand, which is also how a
fabricated plan would arrive. Each check is denied on its own violation while
everything else stays clean, so a failure names one cause rather than a pile.

Nothing here touches the network or the operator's book. The paper database is a
temporary file (``isolate_paper_db`` in ``conftest``), the live price is injected,
and the market history is mocked.
"""

import ast
import inspect
import json
import time

import pytest

from services.paper import db, engine, verifier

# --------------------------------------------------------------------- fixtures

LIVE_PRICE = 100.0
SYMBOL = "BTCUSDT"
STRATEGY_ID = "momentum-BTCUSDT-5m"
METRICS = {"trades": 24, "net_pnl": 41.5, "max_drawdown": 12.25, "fees": 3.1}
#: What one clean plan is worth, and what the account allows. The good plan trades
#: five units at 100, so the ceiling is stated in currency and sits clear of it.
PLAN_NOTIONAL_USD = 500.0
CEILING_USD = 600.0
#: The coin ceiling the config still carries on every installation and that the
#: verifier must not read. 0.01 units is about 840 USD of BTC and about 1.18 USD
#: of SOL, so honouring it as a ceiling denies every honest trade in the cheap
#: coins and waves through a large one in the expensive ones.
RETIRED_QTY_CEILING = 0.01
UNSET = object()


def bars(count=40, *, age=0.0, timestamp=True, interval=300):
    """``count`` 5m candles, the newest ``age`` seconds behind now."""
    now = time.time()
    out = []
    for i in range(count):
        close = 100.0 + i * 0.01
        bar = {
            "open": close,
            "high": close * 1.002,
            "low": close * 0.998,
            "close": close,
            "volume": 1000.0 + i,
        }
        if timestamp:
            bar["time"] = (now - age - (count - 1 - i) * interval) * 1000.0
        out.append(bar)
    return out


def good_plan(**overrides):
    """A plan that clears every check.

    Entry 100, stop 99, target 101.5, five units: 5 at risk, 7.5 to win, 1.5 times
    the risk. The brackets are a percent and a percent and a half off the entry,
    which is the shape the planner itself writes.
    """
    plan = {
        "symbol": SYMBOL,
        "side": "BUY",
        "qty": 5.0,
        "entry_price": 100.0,
        "stop_loss": 99.0,
        "take_profit": 101.5,
        "risk_usd": 5.0,
        "reward_usd": 7.5,
        "rr": 1.5,
        "strategy_id": STRATEGY_ID,
        "strategy_interval": "5m",
        "strategy_params": {"n": 5},
        "setup_family": "momentum",
        "metrics": dict(METRICS),
        "jev_verdict": {
            "available": True,
            "p_take": 0.72,
            "p_quality": 0.61,
            "quality_label": "strong",
        },
        "reasoning": "a plan the checker should accept",
        "refusal_reason": None,
        "analyst_available": True,
        "analyst_bypassed": False,
        "cash": 1000.0,
        "equity": 1000.0,
        "brackets_attached": False,
    }
    plan.update(overrides)
    return plan


@pytest.fixture
def paper(tmp_path, monkeypatch):
    """An isolated paper database with one registered, validated strategy."""
    monkeypatch.setattr(db, "DATA", tmp_path / "paper.db")
    db.init()
    db.set_many(
        {
            "starting_cash": 1000.0,
            "cash": 1000.0,
            "max_position_notional_usd": CEILING_USD,
            "max_position_qty": RETIRED_QTY_CEILING,
            "short_margin_locked": 0.0,
        }
    )
    register_strategy()
    return db


def register_strategy(sid=STRATEGY_ID, *, family="momentum", params=None, metrics=None):
    """File one active, validated strategy row the way the registry holds one."""
    with db.conn() as c:
        c.execute(
            "INSERT OR REPLACE INTO strategies VALUES(?,?,?,?,?,?,?,?)",
            (
                sid,
                "research",
                family,
                json.dumps({"n": 5} if params is None else params),
                json.dumps(dict(METRICS) if metrics is None else metrics),
                "active",
                time.time(),
                1,
            ),
        )
    return sid


@pytest.fixture
def market(monkeypatch):
    """The mocked live feed: a quote and a candle history, both replaceable.

    ``install()`` gives the ordinary case. ``install(quote=None)`` gives an
    unreachable feed, ``install(history=...)`` gives whatever candles a case needs,
    which is how "the feed said nothing" is expressed without touching the network.
    """
    state = {"quote": {"ltp": LIVE_PRICE}, "history": bars()}

    def quote(symbol, exchange):
        value = state["quote"]
        return dict(value) if isinstance(value, dict) else value

    def history(*args, **kwargs):
        return state["history"]

    monkeypatch.setattr("services.foreign_data_service.get_foreign_quote", quote)
    monkeypatch.setattr("services.foreign_data_service.get_foreign_history", history)

    def install(price=LIVE_PRICE, *, quote=UNSET, history=UNSET):
        state["quote"] = {"ltp": price} if quote is UNSET else quote
        state["history"] = bars() if history is UNSET else history

    install.raising_quote = lambda exc: monkeypatch.setattr(
        "services.foreign_data_service.get_foreign_quote",
        lambda symbol, exchange: (_ for _ in ()).throw(exc),
    )
    install.raising_history = lambda exc: monkeypatch.setattr(
        "services.foreign_data_service.get_foreign_history",
        lambda *args, **kwargs: (_ for _ in ()).throw(exc),
    )
    return install


def verdict_of(plan, **kwargs):
    return verifier.verify(plan, **kwargs)


def slugs(result):
    return set(result["reason_slugs"])


def open_position(symbol=SYMBOL, side="BUY", qty=1.0, entry=100.0):
    db.ensure_column("positions", "mark", "REAL")
    with db.conn() as c:
        c.execute(
            "INSERT OR REPLACE INTO positions"
            "(symbol,side,qty,entry,opened,strategy_id,sl,tp,status,mark) "
            "VALUES(?,?,?,?,?,?,?,?,?,?)",
            (
                symbol,
                side,
                qty,
                entry,
                time.time(),
                STRATEGY_ID,
                entry * 0.99,
                entry * 1.01,
                "open",
                entry,
            ),
        )


# --------------------------------------------------------------- the happy path


def test_a_clean_plan_is_allowed(paper, market):
    market()
    result = verdict_of(good_plan())

    assert result["allow"] is True
    assert result["severity"] == verifier.NONE
    assert result["reason_slugs"] == []
    assert result["failed"] == []
    assert result["live_price"] == LIVE_PRICE
    assert all(result["checks"].values()), "every check must pass on a clean plan"


def test_every_check_records_its_own_named_boolean(paper, market):
    market()
    result = verdict_of(good_plan())

    for name in (
        verifier.CHECK_PRICE,
        verifier.CHECK_FRESHNESS,
        verifier.CHECK_RISK,
        verifier.CHECK_REWARD,
        verifier.CHECK_RR,
        verifier.CHECK_SIDES,
        verifier.CHECK_STOP_DISTANCE,
        verifier.CHECK_QTY,
        verifier.CHECK_SYMBOL,
        verifier.CHECK_SIDE,
        verifier.CHECK_NUMBERS,
        verifier.CHECK_STRATEGY_EXISTS,
        verifier.CHECK_STRATEGY_VALIDATED,
        verifier.CHECK_ANALYST_VERDICT,
        verifier.CHECK_NOT_OPEN,
        verifier.CHECK_COMPLETED,
        verifier.CHECK_RECORDED,
    ):
        assert result["checks"][name] is True, name


def test_a_short_with_its_stop_above_and_its_target_below_is_allowed(paper, market):
    market()
    plan = good_plan(side="SELL", stop_loss=101.0, take_profit=98.5)

    result = verdict_of(plan)

    assert result["allow"] is True
    assert result["checks"][verifier.CHECK_SIDES] is True


# ------------------------------------------------------------ price plausibility


def test_an_entry_far_from_the_live_price_is_denied(paper, market):
    market()
    plan = good_plan(
        qty=1.0,
        entry_price=250.0,
        stop_loss=247.5,
        take_profit=253.75,
        risk_usd=2.5,
        reward_usd=3.75,
    )

    result = verdict_of(plan)

    assert result["allow"] is False
    assert verifier.PRICE_NOT_PLAUSIBLE in slugs(result)
    assert result["checks"][verifier.CHECK_PRICE] is False
    assert "250" in result["reasons"][0]["message"]
    assert "100" in result["reasons"][0]["message"]


def test_a_stop_far_from_the_live_price_is_denied(paper, market):
    market()
    plan = good_plan(stop_loss=40.0, risk_usd=300.0)

    result = verdict_of(plan)

    assert result["allow"] is False
    assert verifier.PRICE_NOT_PLAUSIBLE in slugs(result)


def test_a_take_profit_far_from_the_live_price_is_denied(paper, market):
    market()
    plan = good_plan(take_profit=140.0, reward_usd=200.0)

    result = verdict_of(plan)

    assert result["allow"] is False
    assert verifier.PRICE_NOT_PLAUSIBLE in slugs(result)


def test_a_drift_inside_the_band_is_not_read_as_a_hallucination(paper, market):
    """A spread or a tick must not be mistaken for an invented price."""
    market(100.4)

    result = verdict_of(good_plan())

    assert result["allow"] is True


def test_an_unreachable_feed_denies_and_never_guesses(paper, market):
    market(quote=None)

    result = verdict_of(good_plan())

    assert result["allow"] is False
    assert verifier.DATA_UNAVAILABLE in slugs(result)
    assert result["live_price"] is None


def test_a_zero_live_price_is_treated_as_no_price(paper, market):
    market(quote={"ltp": 0.0})

    result = verdict_of(good_plan())

    assert result["allow"] is False
    assert verifier.DATA_UNAVAILABLE in slugs(result)


def test_a_negative_live_price_is_treated_as_no_price(paper, market):
    market(quote={"ltp": -100.0})

    result = verdict_of(good_plan())

    assert result["allow"] is False
    assert verifier.DATA_UNAVAILABLE in slugs(result)


def test_a_live_quote_shaped_like_the_providers_payload_is_read(paper, market):
    market(quote={"lastPrice": "100.0", "bid": "99.99"})

    result = verdict_of(good_plan())

    assert result["allow"] is True
    assert result["live_price"] == 100.0


def test_a_quote_missing_every_price_key_is_no_price(paper, market):
    market(quote={"volume": 10})

    result = verdict_of(good_plan())

    assert result["allow"] is False
    assert verifier.DATA_UNAVAILABLE in slugs(result)


def test_a_quote_feed_that_raises_denies(paper, market):
    market()
    market.raising_quote(ConnectionError("the feed is unreachable"))

    result = verdict_of(good_plan())

    assert result["allow"] is False
    assert verifier.DATA_UNAVAILABLE in slugs(result)


def test_a_history_feed_that_raises_denies(paper, market):
    market()
    market.raising_history(TimeoutError("no history"))

    result = verdict_of(good_plan())

    assert result["allow"] is False
    assert verifier.DATA_UNVERIFIABLE in slugs(result)


def test_the_live_price_can_be_injected_instead_of_fetched(paper, market):
    market()
    plan = good_plan(entry_price=105.0, stop_loss=104.0, take_profit=106.5)

    result = verdict_of(plan, live={"ltp": 105.0})

    assert result["allow"] is True
    assert result["live_price"] == 105.0


# ---------------------------------------------------------------------- staleness


def test_stale_candles_are_denied(paper, market):
    market(history=bars(age=4000.0))

    result = verdict_of(good_plan())

    assert result["allow"] is False
    assert verifier.DATA_STALE in slugs(result)
    assert result["checks"][verifier.CHECK_FRESHNESS] is False


def test_candles_with_no_timestamp_are_denied_not_assumed_fresh(paper, market):
    market(history=bars(timestamp=False))

    result = verdict_of(good_plan())

    assert result["allow"] is False
    assert verifier.DATA_UNVERIFIABLE in slugs(result)


def test_no_candles_at_all_are_denied(paper, market):
    market(history=[])

    result = verdict_of(good_plan())

    assert result["allow"] is False
    assert verifier.DATA_UNVERIFIABLE in slugs(result)


def test_a_candlestick_in_seconds_is_read_as_seconds(paper, market, monkeypatch):
    """A feed in seconds must not be read as the year 56000."""
    monkeypatch.setattr(
        "services.foreign_data_service.get_foreign_history",
        lambda *args, **kwargs: [
            {"close": 100.0, "time": time.time() - i * 300} for i in reversed(range(40))
        ],
    )

    result = verdict_of(good_plan())

    assert result["allow"] is True


# ------------------------------------------------------------------- arithmetic


def test_risk_that_does_not_match_the_stop_is_denied(paper, market):
    market()
    plan = good_plan(risk_usd=2.5)

    result = verdict_of(plan)

    assert result["allow"] is False
    assert verifier.RISK_MISMATCH in slugs(result)
    assert result["checks"][verifier.CHECK_RISK] is False
    assert "2.5" in result["reasons"][0]["message"]
    assert "5" in result["reasons"][0]["message"]


def test_reward_that_does_not_match_the_target_is_denied(paper, market):
    market()
    plan = good_plan(reward_usd=99.0)

    result = verdict_of(plan)

    assert result["allow"] is False
    assert verifier.REWARD_MISMATCH in slugs(result)


def test_an_rr_that_does_not_match_reward_over_risk_is_denied(paper, market):
    market()
    plan = good_plan(rr=9.5)

    result = verdict_of(plan)

    assert result["allow"] is False
    assert verifier.RR_MISMATCH in slugs(result)


def test_an_invented_reward_on_a_short_is_caught(paper, market):
    market()
    plan = good_plan(side="SELL", stop_loss=101.0, take_profit=98.5, reward_usd=250.0)

    result = verdict_of(plan)

    assert result["allow"] is False
    assert verifier.REWARD_MISMATCH in slugs(result)


def test_rounding_inside_the_tolerance_is_not_a_mismatch(paper, market):
    """A plan that rounds its money to cents must not be refused for it."""
    market()
    plan = good_plan(risk_usd=5.004, reward_usd=7.496, rr=1.4992)

    result = verdict_of(plan)

    assert result["allow"] is True


def test_a_stop_on_the_winning_side_is_denied(paper, market):
    market()
    plan = good_plan(stop_loss=101.0, risk_usd=5.0)

    result = verdict_of(plan)

    assert result["allow"] is False
    assert verifier.BRACKET_SIDE_WRONG in slugs(result)


def test_a_target_below_the_entry_on_a_buy_is_denied(paper, market):
    market()
    plan = good_plan(take_profit=99.0, reward_usd=5.0)

    result = verdict_of(plan)

    assert result["allow"] is False
    assert verifier.BRACKET_SIDE_WRONG in slugs(result)


def test_a_stop_too_far_to_be_real_is_denied(paper, market):
    market()
    plan = good_plan(stop_loss=50.0, risk_usd=250.0)

    result = verdict_of(plan)

    assert result["allow"] is False
    assert verifier.STOP_TOO_FAR in slugs(result)


def test_a_stop_at_the_entry_leaves_no_risk_to_divide(paper, market):
    market()
    plan = good_plan(stop_loss=100.0, risk_usd=0.0)

    result = verdict_of(plan)

    assert result["allow"] is False
    assert verifier.RR_MISMATCH in slugs(result)


# ----------------------------------------------------------------------- sanity


@pytest.mark.parametrize(
    "field,value",
    [
        ("qty", None),
        ("qty", 0),
        ("qty", -1.0),
        ("entry_price", None),
        ("entry_price", float("nan")),
        ("entry_price", float("inf")),
        ("stop_loss", 0),
        ("take_profit", -101.5),
        ("risk_usd", 0),
        ("reward_usd", None),
        ("rr", -1.5),
        ("qty", "not a number"),
    ],
)
def test_a_missing_or_unusable_number_is_denied(paper, market, field, value):
    market()
    plan = good_plan(**{field: value})

    result = verdict_of(plan)

    assert result["allow"] is False
    assert verifier.NUMBERS_INVALID in slugs(result)


def test_the_refusal_names_the_figure_that_is_missing_in_words(paper, market):
    market()

    result = verdict_of(good_plan(risk_usd=None))

    message = next(
        reason["message"]
        for reason in result["reasons"]
        if reason["slug"] == verifier.NUMBERS_INVALID
    )
    assert "the amount it says it risks is missing" in message
    assert "risk_usd" not in message


def test_a_lowercase_symbol_is_denied(paper, market):
    market()
    plan = good_plan(symbol="btcusdt")

    result = verdict_of(plan)

    assert result["allow"] is False
    assert verifier.SYMBOL_INVALID in slugs(result)
    assert "BTCUSDT" in result["reasons"][0]["message"]


@pytest.mark.parametrize("symbol", ["BTC", "BTCUSDT-PERP", "", "BT CUSDT", "BTCUSD"])
def test_a_symbol_that_is_not_a_real_pair_is_denied(paper, market, symbol):
    market()
    plan = good_plan(symbol=symbol)

    result = verdict_of(plan)

    assert result["allow"] is False
    assert verifier.SYMBOL_INVALID in slugs(result)


@pytest.mark.parametrize("side", ["buy", "LONG", "", None, "HOLD"])
def test_a_side_that_is_not_buy_or_sell_is_denied(paper, market, side):
    market()
    plan = good_plan(side=side)

    result = verdict_of(plan)

    assert result["allow"] is False
    assert verifier.SIDE_INVALID in slugs(result)


def oversized_plan(**overrides):
    """A clean plan that is worth ``2,000 USD``: twice the account's ceiling."""
    plan = good_plan(qty=20.0, risk_usd=20.0, reward_usd=30.0)
    plan.update(overrides)
    return plan


def ceiling_message(result):
    return next(
        reason["message"]
        for reason in result["reasons"]
        if reason["slug"] == verifier.QTY_ABOVE_CEILING
    )


def test_a_notional_above_the_configured_ceiling_is_denied(paper, market):
    """The ceiling is in currency now, and it still denies a position that is too big."""
    market()
    db.setc("max_position_notional_usd", 1000.0)

    result = verdict_of(oversized_plan())

    assert result["allow"] is False
    assert verifier.QTY_ABOVE_CEILING in slugs(result)
    assert result["checks"][verifier.CHECK_QTY] is False
    assert result["failed"] == [verifier.CHECK_QTY], "the ceiling is the only thing wrong here"
    assert "2,000 USD" in ceiling_message(result)
    assert "is 1,000 USD" in ceiling_message(result)


def test_the_position_ceiling_can_be_overridden_for_one_call(paper, market):
    market()
    plan = oversized_plan()

    assert verdict_of(plan, limits={"max_notional_usd": 5000.0})["allow"] is True

    denied = verdict_of(plan, limits={"max_notional_usd": 1000.0})
    assert denied["allow"] is False
    assert verifier.QTY_ABOVE_CEILING in slugs(denied)
    assert denied["failed"] == [verifier.CHECK_QTY]


# PORTED DEFECT: the ceiling used to be read from ``max_position_qty``, a flat coin
# count seeded at 0.01. That number is about 840 USD of BTC and about 1.18 USD of
# SOL, so it waved through a large position in the expensive coins and denied every
# honest trade in the cheap ones. The ceiling is now in currency. Both directions
# are pinned below, because either one alone would pass under the old rule too.


def test_a_tiny_coin_quantity_worth_a_large_sum_is_denied(paper, market):
    """0.01 BTC is 840 USD, which sat exactly on the retired 0.01 ceiling and passed it."""
    market(84_000.0)
    db.setc("max_position_qty", RETIRED_QTY_CEILING)
    plan = good_plan(
        qty=0.01,
        entry_price=84_000.0,
        stop_loss=83_200.0,
        take_profit=85_600.0,
        risk_usd=8.0,
        reward_usd=16.0,
        rr=2.0,
    )

    result = verdict_of(plan)

    assert plan["qty"] == db.get("max_position_qty"), "the coin ceiling that let this through"
    assert result["allow"] is False
    assert verifier.QTY_ABOVE_CEILING in slugs(result)
    assert result["failed"] == [verifier.CHECK_QTY]
    assert "840 USD" in ceiling_message(result)
    assert f"is {CEILING_USD:g} USD" in ceiling_message(result)


def test_a_large_coin_quantity_worth_a_small_sum_is_allowed(paper, market):
    """0.3 SOL is 35.40 USD: far over the retired 0.01 coin ceiling, well inside the real one."""
    market(118.0)
    db.setc("max_position_qty", RETIRED_QTY_CEILING)
    strategy_id = register_strategy("momentum-SOLUSDT-5m")
    plan = good_plan(
        symbol="SOLUSDT",
        strategy_id=strategy_id,
        qty=0.3,
        entry_price=118.0,
        stop_loss=117.0,
        take_profit=120.0,
        risk_usd=0.3,
        reward_usd=0.6,
        rr=2.0,
    )

    result = verdict_of(plan)

    assert plan["qty"] > db.get("max_position_qty"), "the coin ceiling that denied this"
    assert result["allow"] is True
    assert result["failed"] == []
    assert result["checks"][verifier.CHECK_QTY] is True


def test_with_no_configured_ceiling_the_engine_default_applies(paper, market):
    """An absent ceiling is not an unbounded one: it falls back to the engine's own default."""
    market()
    with db.conn() as c:
        c.execute("DELETE FROM config WHERE k='max_position_notional_usd'")
    default = engine.DEFAULT_MAX_POSITION_NOTIONAL_USD

    result = verdict_of(good_plan())

    assert db.get("max_position_notional_usd", None) is None
    assert result["allow"] is False
    assert verifier.QTY_ABOVE_CEILING in slugs(result)
    assert result["failed"] == [verifier.CHECK_QTY]
    assert f"{PLAN_NOTIONAL_USD:g} USD" in ceiling_message(result)
    assert f"is {default:g} USD" in ceiling_message(result)


def test_a_configured_zero_disables_the_ceiling(paper, market):
    market()
    db.setc("max_position_notional_usd", 0.0)

    result = verdict_of(good_plan())

    assert result["allow"] is True, ceiling_message_or_none(result)
    assert result["failed"] == []
    assert result["checks"][verifier.CHECK_QTY] is True


def ceiling_message_or_none(result):
    """The ceiling refusal when there is one, for a failure an operator has to read."""
    matches = [r["message"] for r in result["reasons"] if r["slug"] == verifier.QTY_ABOVE_CEILING]
    return matches[0] if matches else result["reason_slugs"]


def test_a_plan_that_is_not_a_mapping_is_denied(paper, market):
    market()

    result = verdict_of("buy 5 BTC please")

    assert result["allow"] is False
    assert verifier.PLAN_MALFORMED in slugs(result)


def test_a_plan_that_already_carries_a_refusal_is_denied(paper, market):
    market()
    plan = good_plan(refusal_reason="insufficient_cash", refusal_detail="no money")

    result = verdict_of(plan)

    assert result["allow"] is False
    assert verifier.ALREADY_REFUSED in slugs(result)
    assert "insufficient cash" in result["reasons"][0]["message"]


# ------------------------------------------------------------------- provenance


def test_a_strategy_that_is_not_on_file_is_denied(paper, market):
    market()
    plan = good_plan(strategy_id="donchian-ETHUSDT-1h")

    result = verdict_of(plan)

    assert result["allow"] is False
    assert verifier.STRATEGY_UNKNOWN in slugs(result)
    assert result["checks"][verifier.CHECK_STRATEGY_VALIDATED] is False


@pytest.mark.parametrize(
    "metrics",
    [
        {"trades": 1, "net_pnl": 41.5, "max_drawdown": 12.25, "fees": 3.1},
        {"trades": 24, "net_pnl": -5.0, "max_drawdown": 12.25, "fees": 3.1},
        {"trades": 24, "net_pnl": 41.5, "max_drawdown": 900.0, "fees": 3.1},
        {},
    ],
)
def test_a_strategy_whose_record_does_not_clear_the_bar_is_denied(paper, market, metrics):
    market()
    with db.conn() as c:
        c.execute(
            "UPDATE strategies SET metrics=? WHERE id=?",
            (json.dumps(metrics), STRATEGY_ID),
        )
    plan = good_plan(metrics=dict(metrics))

    result = verdict_of(plan)

    assert result["allow"] is False
    assert verifier.STRATEGY_UNVALIDATED in slugs(result)


def test_a_plan_naming_no_strategy_at_all_is_denied(paper, market):
    market()
    plan = good_plan(strategy_id=None)

    result = verdict_of(plan)

    assert result["allow"] is False
    assert verifier.STRATEGY_UNKNOWN in slugs(result)


def test_a_deactivated_strategy_is_denied(paper, market):
    market()
    with db.conn() as c:
        c.execute("UPDATE strategies SET status='retired' WHERE id=?", (STRATEGY_ID,))

    result = verdict_of(good_plan())

    assert result["allow"] is False
    assert verifier.STRATEGY_INACTIVE in slugs(result)


def test_a_plan_quoting_a_better_record_than_the_registry_is_denied(paper, market):
    market()
    plan = good_plan(metrics={"trades": 900, "net_pnl": 4100.0, "max_drawdown": 0.5, "fees": 3.1})

    result = verdict_of(plan)

    assert result["allow"] is False
    assert verifier.STRATEGY_METRICS_MISMATCH in slugs(result)
    assert "900" in result["reasons"][0]["message"]
    assert "24" in result["reasons"][0]["message"]


def test_a_plan_describing_a_different_setup_is_denied(paper, market):
    market()
    plan = good_plan(setup_family="breakout")

    result = verdict_of(plan)

    assert result["allow"] is False
    assert verifier.STRATEGY_SETUP_MISMATCH in slugs(result)


def test_a_plan_with_different_params_is_denied(paper, market):
    market()
    plan = good_plan(strategy_params={"n": 500})

    result = verdict_of(plan)

    assert result["allow"] is False
    assert verifier.STRATEGY_SETUP_MISMATCH in slugs(result)


# ---------------------------------------------------------------------- analyst


def test_claiming_a_review_without_a_verdict_is_denied(paper, market):
    market()
    plan = good_plan(analyst_available=True, jev_verdict=None)

    result = verdict_of(plan)

    assert result["allow"] is False
    assert verifier.ANALYST_CLAIMED_WITHOUT_VERDICT in slugs(result)


@pytest.mark.parametrize("p_take", [1.7, -0.2, 42.0])
def test_a_probability_outside_zero_to_one_is_denied(paper, market, p_take):
    market()
    plan = good_plan(jev_verdict={"available": True, "p_take": p_take})

    result = verdict_of(plan)

    assert result["allow"] is False
    assert verifier.ANALYST_CLAIMED_WITHOUT_VERDICT in slugs(result)


def test_a_verdict_with_no_probability_in_it_is_denied(paper, market):
    market()
    plan = good_plan(jev_verdict={"available": True, "quality_label": "strong"})

    result = verdict_of(plan)

    assert result["allow"] is False
    assert verifier.ANALYST_CLAIMED_WITHOUT_VERDICT in slugs(result)


def test_claiming_no_opinion_while_carrying_one_is_denied(paper, market):
    market()
    plan = good_plan(analyst_available=False, analyst_bypassed=True)

    result = verdict_of(plan)

    assert result["allow"] is False
    assert verifier.ANALYST_VERDICT_WITHOUT_CLAIM in slugs(result)


def test_no_opinion_and_no_bypass_is_denied(paper, market):
    market()
    plan = good_plan(
        analyst_available=False,
        analyst_bypassed=False,
        jev_verdict={"available": False, "p_take": None},
    )

    result = verdict_of(plan)

    assert result["allow"] is False
    assert verifier.ANALYST_UNAVAILABLE in slugs(result)


def test_a_stamp_of_unreviewed_on_a_reviewed_trade_is_denied(paper, market):
    market()
    plan = good_plan(analyst_available=True, analyst_bypassed=True)

    result = verdict_of(plan)

    assert result["allow"] is False
    assert verifier.ANALYST_BYPASS_INCONSISTENT in slugs(result)


def test_an_explicitly_bypassed_review_is_allowed(paper, market):
    """The operator override is stated, so the trade says plainly nobody reviewed it."""
    market()
    plan = good_plan(
        analyst_available=False,
        analyst_bypassed=True,
        jev_verdict={
            "available": False,
            "p_take": None,
            "p_quality": None,
            "quality_label": "unknown",
        },
    )

    result = verdict_of(plan)

    assert result["allow"] is True
    assert result["checks"][verifier.CHECK_ANALYST_QUIET] is True


# ----------------------------------------------------------------- open position


def test_a_second_position_in_an_open_symbol_is_denied(paper, market):
    market()
    open_position()

    result = verdict_of(good_plan())

    assert result["allow"] is False
    assert verifier.SYMBOL_ALREADY_OPEN in slugs(result)


def test_a_closed_position_in_the_same_symbol_does_not_block(paper, market):
    market()
    open_position()
    with db.conn() as c:
        c.execute("UPDATE positions SET status='closed' WHERE symbol=?", (SYMBOL,))

    result = verdict_of(good_plan())

    assert result["allow"] is True


# ------------------------------------------------------------------- fail closed


def test_every_declared_check_always_reports_a_boolean(paper, market):
    """No check may go missing on a plan that can be read: a silent check is an off check."""
    declared = {value for name, value in vars(verifier).items() if name.startswith("CHECK_")}
    market()

    for plan in (
        good_plan(),
        good_plan(symbol="btcusdt"),
        good_plan(qty=None),
        good_plan(strategy_id="nothing-registered-1h"),
        good_plan(
            entry_price=250.0, stop_loss=247.5, take_profit=253.75, risk_usd=12.5, reward_usd=18.75
        ),
    ):
        result = verdict_of(plan)
        assert set(result["checks"]) == declared
        assert all(isinstance(value, bool) for value in result["checks"].values())


def test_a_plan_that_cannot_be_read_is_still_recorded(paper, market):
    """Nothing readable means no per-check verdict, but the refusal is still on record."""
    market()

    result = verdict_of("buy 5 BTC please")

    assert result["allow"] is False
    assert result["checks"][verifier.CHECK_PLAN_MAPPING] is False
    assert verifier.PLAN_MALFORMED in slugs(result)
    stored = verifier.recent(1)[0]
    assert stored["allow"] is False
    assert verifier.PLAN_MALFORMED in stored["slugs"]


def test_a_fault_inside_the_verifier_denies_rather_than_allows(paper, market, monkeypatch):
    market()

    def boom():
        raise RuntimeError("the database went away")

    monkeypatch.setattr(verifier.db, "ensure_verdicts", boom)
    result = verdict_of(good_plan())

    assert result["allow"] is False
    assert result["severity"] == verifier.BLOCKING
    assert slugs(result) == {verifier.VERIFIER_ERROR}
    assert result["checks"][verifier.CHECK_COMPLETED] is False


def test_a_fault_in_a_single_check_denies_the_whole_verdict(paper, market, monkeypatch):
    """A check that cannot answer is a check that denies, not one that is skipped."""
    market()
    monkeypatch.setattr(verifier, "_check_arithmetic", lambda ctx, checks, reasons: 1 / 0)

    result = verdict_of(good_plan())

    assert result["allow"] is False
    assert slugs(result) == {verifier.VERIFIER_ERROR}


def test_a_failure_to_record_the_verdict_is_advisory_not_a_denial(paper, market, monkeypatch):
    """Losing the audit row must not punish a sound trade, but it must be said."""
    market()

    def boom(*args, **kwargs):
        raise OSError("no space left on the device")

    monkeypatch.setattr(verifier, "_persist", boom)
    result = verdict_of(good_plan())

    assert result["allow"] is True
    assert result["severity"] == verifier.ADVISORY
    assert verifier.VERDICT_NOT_RECORDED in slugs(result)
    assert result["verdict_id"] is None
    assert result["checks"][verifier.CHECK_RECORDED] is False


def test_an_unknown_limit_name_denies_instead_of_being_ignored(paper, market):
    market()

    result = verdict_of(good_plan(), limits={"max_entry_drift": 0.5})

    assert result["allow"] is False
    assert slugs(result) == {verifier.VERIFIER_ERROR}


def test_a_wider_limit_allows_a_price_the_default_band_would_refuse(paper, market):
    market()
    plan = good_plan(
        entry_price=102.0,
        stop_loss=101.0,
        take_profit=104.0,
        risk_usd=5.0,
        reward_usd=10.0,
        rr=2.0,
    )

    assert verdict_of(plan)["allow"] is False
    assert (
        verdict_of(plan, limits={"max_entry_drift_pct": 0.05, "max_level_drift_pct": 0.06})["allow"]
        is True
    )


# --------------------------------------------------------------------- advisory


def test_an_overclaimed_bracket_is_advisory_and_still_allows(paper, market):
    market()
    plan = good_plan(brackets_attached=True)

    result = verdict_of(plan)

    assert result["allow"] is True
    assert result["severity"] == verifier.ADVISORY
    assert result["reasons"][0]["severity"] == verifier.ADVISORY
    assert verifier.BRACKETS_NOT_RESTING in slugs(result)


def test_a_balance_that_moved_since_the_plan_is_advisory(paper, market):
    market()
    plan = good_plan(cash=250.0)

    result = verdict_of(plan)

    assert result["allow"] is True
    assert verifier.ACCOUNT_MOVED in slugs(result)


def test_a_blocking_reason_outranks_an_advisory_one(paper, market):
    market()
    plan = good_plan(brackets_attached=True, cash=250.0, rr=9.9)

    result = verdict_of(plan)

    assert result["allow"] is False
    assert result["severity"] == verifier.BLOCKING
    assert verifier.RR_MISMATCH in slugs(result)
    assert verifier.BRACKETS_NOT_RESTING in slugs(result)


# ------------------------------------------------------------------ persistence


def test_a_denied_verdict_is_written_with_its_reasons(paper, market):
    market()

    result = verdict_of(good_plan(symbol="ethusdt"))

    assert result["verdict_id"] is not None
    stored = verifier.recent(1)[0]
    assert stored["id"] == result["verdict_id"]
    assert stored["allow"] is False
    assert stored["severity"] == verifier.BLOCKING
    assert verifier.SYMBOL_INVALID in stored["slugs"]
    assert stored["reasons"][0]["message"]
    assert stored["entry_price"] == 100.0
    assert stored["strategy_id"] == STRATEGY_ID
    assert stored["side"] == "BUY"


def test_an_allowed_verdict_is_written_too(paper, market):
    market()
    verdict_of(good_plan())

    stored = verifier.recent(1)[0]
    assert stored["allow"] is True
    assert stored["slugs"] == []
    assert stored["checks"][verifier.CHECK_PRICE] is True
    assert stored["live_price"] == LIVE_PRICE
    assert stored["symbol"] == SYMBOL
    assert stored["ts"] > 0


def test_recent_returns_newest_first_and_honours_the_limit(paper, market):
    market()
    for _ in range(3):
        verdict_of(good_plan())

    rows = verifier.recent(2)

    assert len(rows) == 2
    assert rows[0]["id"] > rows[1]["id"]


def test_recent_survives_a_database_it_cannot_read(paper, market, monkeypatch):
    market()
    monkeypatch.setattr(
        verifier.db, "conn", lambda: (_ for _ in ()).throw(OSError("the file is locked"))
    )

    assert verifier.recent() == []


def test_the_verdicts_table_is_created_by_init_alone(paper):
    with db.conn() as c:
        names = {r["name"] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}

    assert db.VERDICTS_TABLE in names


def test_ensure_verdicts_tops_up_a_partial_table_and_is_idempotent(paper):
    with db.conn() as c:
        c.execute(f"DROP TABLE {db.VERDICTS_TABLE}")
        c.execute(
            f"CREATE TABLE {db.VERDICTS_TABLE} (id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL)"
        )
        c.execute(f"INSERT INTO {db.VERDICTS_TABLE}(ts) VALUES(1.0)")

    assert db.ensure_verdicts() is True
    assert db.ensure_verdicts() is False

    with db.conn() as c:
        cols = {r["name"] for r in c.execute(f"PRAGMA table_info({db.VERDICTS_TABLE})")}
        kept = c.execute(f"SELECT ts FROM {db.VERDICTS_TABLE}").fetchone()
    assert {name for name, _ in db.VERDICT_COLUMNS} <= cols
    assert kept["ts"] == 1.0, "an existing row must survive the top-up"


# --------------------------------------------------------------------- the slugs


# Which argument of each recording helper carries the slug.
SLUG_ARGUMENT = {"_record": 4, "_reason": 0}


def _slug_value(node):
    """A slug written as a constant name or as a bare literal, else None."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Name):
        value = vars(verifier).get(node.id)
        return value if isinstance(value, str) else None
    return None


def emitted_slugs() -> set:
    """Every slug the module can hand to a caller, read from how it records reasons.

    Two shapes, both taken from the module's own source rather than from a list
    written here: the slug argument of a ``_record``/``_reason`` call, and the
    ``(check, slug)`` pairs the loops that record several checks at once iterate
    over. A constant the module no longer records anything with is not one of
    those, so a retired slug can leave ``REASON_SLUGS`` while the constant that
    used to name it is still there. A bare string literal in either position is
    still collected, because a typo looks exactly like that.
    """
    tree = ast.parse(inspect.getsource(verifier))
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            index = SLUG_ARGUMENT.get(node.func.id)
            if index is not None and len(node.args) > index:
                slug = _slug_value(node.args[index])
                if slug:
                    found.add(slug)
        elif isinstance(node, ast.Tuple) and len(node.elts) == 2:
            check, slug_node = node.elts
            if isinstance(check, ast.Name) and check.id.startswith("CHECK_"):
                slug = _slug_value(slug_node)
                if slug:
                    found.add(slug)
    return found


def test_every_slug_the_module_can_emit_is_declared_in_one_place():
    """A typo in a literal would otherwise become a slug nothing counts."""
    declared = set(verifier.REASON_SLUGS)
    assert len(declared) == len(verifier.REASON_SLUGS), "a slug is declared twice"

    emitted = emitted_slugs()
    assert len(emitted) >= 20, (
        f"the scan found only {len(emitted)} slugs; it is not reading the module"
    )

    undeclared = emitted - declared
    assert not undeclared, f"a slug nothing counts: {sorted(undeclared)}"
    unreachable = declared - emitted
    assert not unreachable, f"declared but recorded by nothing: {sorted(unreachable)}"


def test_the_module_holds_no_number_that_could_stand_in_for_a_price():
    """Requirement five, pinned: a constant above 100 has to be a declared duration.

    The whole point of the anti-hallucination check is that nothing in this module
    can answer "what is the price of X". Every tolerance is a fraction; the only
    large numbers allowed are the units a candle interval is converted with.
    """
    allowed_large = set(verifier.INTERVAL_SECONDS.values())
    allowed_large.add(verifier.LIMITS["min_candle_age_seconds"])

    for name, value in vars(verifier).items():
        if name.startswith("_") or isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        if value in allowed_large:
            continue
        assert value <= 100, f"{name} = {value} looks like a stand-in price"


@pytest.mark.parametrize(
    "plan_kwargs",
    [
        {"symbol": "ethusdt"},
        {"rr": 9.9},
        {"qty": 20.0, "risk_usd": 20.0, "reward_usd": 30.0},
        {
            "analyst_available": False,
            "analyst_bypassed": False,
            "jev_verdict": {"available": False, "p_take": None},
        },
        {
            "entry_price": 250.0,
            "stop_loss": 247.5,
            "take_profit": 253.75,
            "qty": 1.0,
            "risk_usd": 2.5,
            "reward_usd": 3.75,
        },
        {"strategy_id": "donchian-ETHUSDT-1h"},
    ],
)
def test_every_denial_carries_a_sentence_a_trader_can_act_on(paper, market, plan_kwargs):
    market()
    result = verdict_of(good_plan(**plan_kwargs))

    assert result["allow"] is False
    assert result["reason_slugs"], "a denial must name a reason"
    for reason in result["reasons"]:
        assert reason["severity"] == verifier.BLOCKING
        assert len(reason["message"]) > 40, "a refusal must explain itself"
        for jargon in ("None", "nan", "inf", "Traceback", "Exception", "_"):
            assert jargon not in reason["message"], reason["message"]
