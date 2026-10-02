"""The worker keeps trading while the analyst is down, and says so.

A free-tier analyst that answers nothing used to be the end of trading: every
cycle refused with ``analyst_unavailable``, the journal filled with the same row
once a minute, and to a user the worker looked stuck rather than blocked. These
tests pin the behaviour that replaced it:

* after N cycles in a row with no analyst answer the worker stops refusing and
  trades on rules alone, with every trade stamped unreviewed (N is a config key,
  default 3);
* the switch itself is journalled **once** -- ``analyst_outage_started`` and
  ``analyst_outage_ended`` -- however many cycles the outage lasts;
* the analyst is asked first on every cycle, outage or not, so reviewed trading
  resumes on the next cycle in which the quota has reset: no restart, no manual
  step;
* an identical refusal is journalled at most once an hour, and the throttle is
  read from the journal so it cannot drift between processes;
* one trade per cycle and the kill switch both still hold in outage mode.

Everything runs against a temporary paper database with a synthetic market,
mocked research and a mocked analyst, so no network call is made and the
operator's ``data/paper.db`` is never opened. The market is synthetic for the
same reason every paper fixture's is: placing one of these trades runs the
independent verifier against the live quote and then fills against the real book,
and a ladder closing at 129.50 is not what either of them is looking at. See
``install_market``.
"""

import importlib.util
import json
import sys
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


def _load(name: str, relative: str):
    """Import a script by path, the way its CLI does."""
    path = REPO_ROOT / relative
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


paper_status = _load("paper_status_under_outage_test", "scripts/paper_status.py")

from services.paper import config, db, engine, planner, worker  # noqa: E402

SYMBOL = "BTCUSDT"
OTHER = "ETHUSDT"

# What a capped free tier actually looks like from the planner's side: ``ask``
# answers nothing and leaves a sentence behind saying why.
FREE_TIER_SPENT = (
    "The analyst's free daily allowance is used up, so it cannot review a trade "
    "right now. Add credits to the Zen account, or wait for the allowance to reset."
)

ANSWER = {
    "answers": {
        "trade": {"probabilities": {"0": 0.25, "1": 0.75}},
        "quality": {"probabilities": {"0": 0.35, "1": 0.65}},
    }
}


def candles(closes):
    out = []
    for i, close in enumerate(closes):
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


RISING = candles([100.0 + i * 0.5 for i in range(60)])


# ---------------------------------------------------------- the synthetic book
#
# The planner's entry is the newest close moved by the account's slippage; the
# verifier re-fetches the quote and checks the plan's prices against it; the
# execution model fills against the real bid and ask. All three come from this
# one ladder, so the entry a plan was built from, the market it is checked
# against and the book it is filled off are the same price level. The book is
# two-sided with a real spread either side of the mid, so there is a book to
# cross and a day of volume to measure the order against. The shape is
# ``fetch_crypto_quote``'s, so the stub cannot pass either module by carrying
# something the real feed never sends.

#: A BTC or ETH major on Binance sits a couple of basis points wide; the paper
#: account refuses a book wider than ``execution.DEFAULT_MAX_SPREAD_BPS``.
BOOK_SPREAD_BPS = 3.0


def book_from(ladder, *, spread_bps=BOOK_SPREAD_BPS, volume=None):
    """A two-sided quote for exactly the world ``ladder`` describes.

    The mid is the newest close, and the volume is the day's worth of this
    ladder -- what a rolling 24h figure is -- so an ordinary paper order is a
    rounding error of the market it crosses.
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


def install_market(monkeypatch, ladder, *, age=0.0, interval=300.0):
    """Serve ``engine._candles``, the quote and the history from one ladder.

    The clock is stamped here rather than in the ladder so the bars cannot go
    stale while a long suite works its way to them: the verifier age-checks the
    history, and would read a ladder built at import time for exactly what it is.
    Written in milliseconds, as crypto feeds send it.
    """
    now = time.time()
    bars = []
    for i, bar in enumerate(ladder):
        copied = dict(bar)
        copied["time"] = (now - age - (len(ladder) - 1 - i) * interval) * 1000.0
        bars.append(copied)
    quote = book_from(bars)

    monkeypatch.setattr(engine, "_candles", lambda s, interval="5m": bars)

    def get_quote(symbol, exchange):
        return dict(quote)

    def get_history(symbol, exchange, iv="5m", start_date="", end_date=""):
        return list(bars)

    monkeypatch.setattr("services.foreign_data_service.get_foreign_quote", get_quote)
    monkeypatch.setattr("services.foreign_data_service.get_foreign_history", get_history)
    return quote


# ------------------------------------------------------------------- fixtures


@pytest.fixture
def analyst(monkeypatch):
    """A switchable analyst, answering or not, counting every call.

    Returns the list of state strings it was asked to judge, so a test can prove
    the analyst is still being consulted during an outage rather than skipped.
    """
    asked: list[str] = []
    answering = {"now": False}

    def ask(state, questions):
        asked.append(state)
        if answering["now"]:
            return ANSWER
        # The real client leaves the reason behind for the operator to read.
        planner.jev._last_failure = FREE_TIER_SPENT
        return None

    monkeypatch.setattr(planner.jev, "ask", ask)
    monkeypatch.setattr(planner.jev, "_last_failure", "", raising=False)
    return type("Analyst", (), {"asked": asked, "answering": answering})()


@pytest.fixture
def paper(tmp_path, monkeypatch, analyst):
    """An isolated account with a validated strategy, rising candles, no exits.

    Cash is roomy so a case about *how many* trades a cycle may place is not
    decided by the engine refusing the second position for lack of money.
    """
    monkeypatch.setattr(db, "DATA", tmp_path / "paper.db")
    db.init()
    db.set_many(
        {
            "starting_cash": 10_000.0,
            "cash": 10_000.0,
            # The verifier's per-position ceiling, in coins. Every installation
            # still carries this key at the pre-resize 0.01 -- the flat quantity
            # the sizing rule was written to stop reading -- which at this world's
            # price is 1.29 USD and would refuse every position this account can
            # open. One coin is a real limit here and sits well above the largest
            # position the notional cap allows.
            "max_position_qty": 1.0,
            "max_exposure_pct": 0.5,
            "fee_bps": 4.0,
            "slippage_bps": 2.0,
            "max_daily_loss": 20.0,
            "short_margin_locked": 0.0,
        }
    )
    config.set_halted(False)
    config.persist(symbols=SYMBOL, interval="5m")
    register(SYMBOL)
    install_market(monkeypatch, RISING)
    monkeypatch.setattr(engine, "research_cycle", lambda *a, **k: None)
    # Exits are a separate concern, covered in test_paper_exits.py. Muted so the
    # order counts here are entries only, which is what these rules are about.
    monkeypatch.setattr(planner, "manage_open_positions", lambda *a, **k: [])
    return analyst


def register(symbol=SYMBOL, family="momentum", metrics=None):
    with db.conn() as c:
        c.execute(
            "INSERT OR REPLACE INTO strategies VALUES(?,?,?,?,?,?,?,?)",
            (
                f"{family}-{symbol}",
                "research",
                family,
                '{"n": 5}',
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


def decisions_of(kind):
    with db.conn() as c:
        rows = c.execute(
            "SELECT ts, payload FROM decisions WHERE kind=? ORDER BY id", (kind,)
        ).fetchall()
    return [json.loads(r["payload"]) for r in rows]


def refusals(reason=None):
    rows = decisions_of(worker.KIND_SKIPPED)
    return [r for r in rows if reason is None or r.get("refusal_reason") == reason]


def orders():
    with db.conn() as c:
        return [dict(r) for r in c.execute("SELECT id, symbol, side, qty FROM orders ORDER BY created")]


def executed_plans():
    return decisions_of("plan_executed")


def run(worker_obj, cycles=1):
    return [worker_obj.run_cycle() for _ in range(cycles)]


# ------------------------------------------------- the switch into unreviewed


def test_three_failed_analyst_calls_then_unreviewed_trading(paper):
    """The reported symptom, inverted: refusal twice, then a trade regardless."""
    w = worker.PaperWorker(verbose=False)

    results = run(w, cycles=3)

    assert results[0]["planner_verdict"] == "refused", "the first silence is a refusal"
    assert results[1]["planner_verdict"] == "refused", "so is the second"
    assert refusals(worker.PLANNER_ANALYST_UNAVAILABLE), "and both were journalled"

    assert results[2]["planner_verdict"] == "executed", (
        "the third consecutive failure stops being a refusal"
    )
    assert results[2]["trades_placed"] == 1
    assert results[2]["unreviewed"] is True, "and it is labelled unreviewed"
    assert results[2]["analyst_outage"] is True

    placed = orders()
    assert len(placed) == 1, "an account that was stuck is trading again"
    assert placed[0]["symbol"] == SYMBOL

    # Nothing about the order claims a review. Both the planner's own record of
    # the plan and the worker's separate unreviewed row say so.
    plan = executed_plans()[0]
    assert plan["analyst_bypassed"] is True
    assert plan["analyst_available"] is False
    assert plan["analyst_outage"] is True
    unreviewed = decisions_of(worker.KIND_UNREVIEWED_TRADE)
    assert len(unreviewed) == 1
    assert unreviewed[0]["symbol"] == SYMBOL
    assert unreviewed[0]["unreviewed_in_this_outage"] == 1
    assert "nothing reviewed it" in unreviewed[0]["detail"]


def test_the_threshold_is_a_config_key_defaulting_to_three(paper):
    assert config.load().analyst_outage_cycles == 3

    config.persist(analyst_outage_cycles=1)
    w = worker.PaperWorker(verbose=False)

    # One cycle is enough to stop waiting, and it still asks first.
    result = w.run_cycle()

    assert result["planner_verdict"] == "executed"
    assert result["unreviewed"] is True
    assert len(paper.asked) >= 1, "the analyst is still asked on the very first cycle"
    assert refusals(worker.PLANNER_ANALYST_UNAVAILABLE) == [], "nothing was refused"


def test_the_switch_is_journalled_once_not_once_per_failed_cycle(paper):
    config.persist(analyst_outage_cycles=3)
    w = worker.PaperWorker(verbose=False)

    # Five cycles through the outage: three that fail, then more trading on rules.
    run(w, cycles=3)
    register(OTHER)
    config.persist(symbols=f"{SYMBOL},{OTHER}")
    run(w, cycles=2)

    started = decisions_of(worker.KIND_OUTAGE_STARTED)
    assert len(started) == 1, f"the switch is one event, not one row per cycle: {started}"
    assert started[0]["failed_cycles"] == 3
    assert started[0]["outage_cycles"] == 3
    assert len(decisions_of(worker.KIND_OUTAGE_ENDED)) == 0
    assert len(decisions_of(worker.KIND_UNREVIEWED_TRADE)) == 2, "one row per unreviewed trade"


def test_one_unreviewed_trade_per_cycle_at_most(paper):
    config.persist(analyst_outage_cycles=3)
    register(OTHER)
    config.persist(symbols=f"{SYMBOL},{OTHER}")
    w = worker.PaperWorker(verbose=False)

    results = run(w, cycles=5)

    assert all(r["trades_placed"] <= 1 for r in results), (
        f"a cycle places at most one trade: {results}"
    )
    assert [r["trades_placed"] for r in results] == [0, 0, 1, 1, 0]
    assert len(orders()) == 2, "two symbols, two cycles, one trade each"
    assert len(decisions_of(worker.KIND_UNREVIEWED_TRADE)) == 2


def test_the_one_trade_guard_still_refuses_a_second_order_in_an_outage_cycle(paper):
    w = worker.PaperWorker(verbose=False)
    run(w, cycles=3)  # into the outage
    w._cycle_trades = worker.MAX_TRADES_PER_CYCLE

    result = w._execute_once(
        planner,
        {"symbol": SYMBOL, "side": "BUY", "qty": 0.01, "entry_price": 100.0},
        cycle_no=99,
        symbols=[SYMBOL],
        unreviewed=True,
    )

    assert result["executed"] is False
    assert result["refusal_reason"] == worker.TOO_MANY_TRADES
    assert len(orders()) == 1, "the refused second order was never sent"
    assert refusals(worker.TOO_MANY_TRADES), "and it is on record"


def test_an_outage_never_starts_on_a_planner_that_is_missing(paper, monkeypatch):
    """No planner means no risk rules, which unreviewed trading does not forgive."""
    monkeypatch.setattr(worker, "_load_planner", lambda: None)
    config.persist(analyst_outage_cycles=1)
    w = worker.PaperWorker(verbose=False)

    result = w.run_cycle()

    assert result["refusal_reason"] == worker.PLANNER_UNAVAILABLE
    assert orders() == []
    assert decisions_of(worker.KIND_OUTAGE_STARTED) == []


# --------------------------------------------------------------- the recovery


def test_the_analyst_is_asked_on_every_cycle_including_during_an_outage(paper):
    """Requirement 2, made visible: it is asked first, every cycle, always.

    The position is cleared between cycles so each one has a plan to put to the
    analyst. A worker that had stopped asking after declaring the outage would
    show fewer calls than cycles.
    """
    w = worker.PaperWorker(verbose=False)

    # This case opens five positions in a row, and the daily-loss guard is a rule of
    # its own that watches the account's live equity. Switching it off here keeps
    # it from deciding how many cycles run; it says nothing about the analyst.
    db.setc("max_daily_loss", 0)

    results = []
    for _ in range(5):
        results.append(w.run_cycle())
        with db.conn() as c:
            c.execute("DELETE FROM positions")

    assert len(paper.asked) == 5, (
        f"one analyst call per cycle, outage included: {len(paper.asked)} for 5 cycles"
    )
    assert results[2]["unreviewed"] is True, "the third cycle traded unreviewed"
    assert results[4]["unreviewed"] is True, "and so did the fifth"


def test_recovery_on_the_next_cycle_flips_back_to_reviewed(paper):
    w = worker.PaperWorker(verbose=False)
    run(w, cycles=3)
    assert w.run_cycle is not None
    # Free the symbol so the recovery cycle has something to trade.
    assert planner.close_position(SYMBOL)["executed"] is True

    paper.answering["now"] = True
    result = w.run_cycle()

    assert result["planner_verdict"] == "executed"
    assert result["unreviewed"] is False, "reviewed trading resumed by itself"
    assert result["analyst_available"] is True
    assert result["analyst_outage"] is False

    ended = decisions_of(worker.KIND_OUTAGE_ENDED)
    assert len(ended) == 1, f"the recovery is one row, not one per cycle: {ended}"
    assert ended[0]["unreviewed_trades"] == 1
    assert len(decisions_of(worker.KIND_OUTAGE_STARTED)) == 1

    plan = executed_plans()[-1]
    assert plan["analyst_bypassed"] is False, "the recovered trade carries a review"
    assert plan["analyst_available"] is True
    assert len(decisions_of(worker.KIND_UNREVIEWED_TRADE)) == 1, "no second unreviewed trade"

    state = worker.analyst_outage_state()
    assert state["active"] is False
    assert state["unreviewed_trades"] == 0


def test_the_outage_survives_a_restart_without_re_journalling_the_switch(paper):
    """A restart in the middle of an outage keeps trading, and keeps quiet."""
    config.persist(analyst_outage_cycles=3)
    register(OTHER)
    config.persist(symbols=f"{SYMBOL},{OTHER}")
    run(worker.PaperWorker(verbose=False), cycles=3)

    # A brand new process: nothing carried in memory, only the database.
    restarted = worker.PaperWorker(verbose=False)
    result = restarted.run_cycle()

    assert result["unreviewed"] is True, "still trading unreviewed after a restart"
    assert result["analyst_outage"] is True
    assert len(decisions_of(worker.KIND_OUTAGE_STARTED)) == 1, (
        "the switch was already recorded; a restart does not record it again"
    )
    assert worker.analyst_outage_state()["unreviewed_trades"] == 2


def test_a_healthy_analyst_is_never_bypassed(paper):
    paper.answering["now"] = True
    w = worker.PaperWorker(verbose=False)

    result = w.run_cycle()

    assert result["planner_verdict"] == "executed"
    assert result["unreviewed"] is False
    assert result["analyst_available"] is True
    assert decisions_of(worker.KIND_OUTAGE_STARTED) == []
    assert decisions_of(worker.KIND_UNREVIEWED_TRADE) == []
    assert executed_plans()[0]["analyst_bypassed"] is False


def test_a_refusal_for_another_reason_does_not_open_an_outage(paper):
    """No setup is not a broken analyst, and must not be counted as one."""
    with db.conn() as c:
        c.execute("DELETE FROM strategies")
    w = worker.PaperWorker(verbose=False)

    results = run(w, cycles=4)

    assert all(r["refusal_reason"] != worker.PLANNER_ANALYST_UNAVAILABLE for r in results)
    assert decisions_of(worker.KIND_OUTAGE_STARTED) == []
    assert orders() == []


# --------------------------------------------------------------- the throttle


def test_identical_refusals_are_throttled_to_one_per_hour(paper):
    # A threshold no test run could reach: this case is about the journal alone.
    config.persist(analyst_outage_cycles=config.MAX_ANALYST_OUTAGE_CYCLES)
    w = worker.PaperWorker(verbose=False)

    results = run(w, cycles=5)

    assert [r["planner_verdict"] for r in results] == ["refused"] * 5
    assert len(refusals(worker.PLANNER_ANALYST_UNAVAILABLE)) == 1, (
        "five identical refusals are one row, not five"
    )
    # The throttle is not a mute: the cycle still reports why it did nothing.
    assert results[4]["refusal_logged"] is False
    assert results[4]["refusal_reason"] == worker.PLANNER_ANALYST_UNAVAILABLE


def test_a_different_reason_is_never_throttled_away(paper):
    config.persist(analyst_outage_cycles=config.MAX_ANALYST_OUTAGE_CYCLES)
    w = worker.PaperWorker(verbose=False)
    w.run_cycle()
    assert len(refusals(worker.PLANNER_ANALYST_UNAVAILABLE)) == 1

    # A second reason is a second fact, and must reach the journal.
    with db.conn() as c:
        c.execute("DELETE FROM strategies")
    w.run_cycle()

    assert refusals(worker.PLANNER_ANALYST_UNAVAILABLE) == [
        refusals(worker.PLANNER_ANALYST_UNAVAILABLE)[0]
    ], "the first row is still the only analyst one"
    assert len(refusals("no_edge")) == 1


def test_the_throttle_reopens_after_the_hour(paper):
    config.persist(analyst_outage_cycles=config.MAX_ANALYST_OUTAGE_CYCLES)
    w = worker.PaperWorker(verbose=False)
    w.run_cycle()
    assert len(refusals(worker.PLANNER_ANALYST_UNAVAILABLE)) == 1

    with db.conn() as c:
        c.execute(
            "UPDATE decisions SET ts=? WHERE kind=?",
            (time.time() - worker.REFUSAL_LOG_INTERVAL_SECONDS - 60, worker.KIND_SKIPPED),
        )
    w.run_cycle()

    assert len(refusals(worker.PLANNER_ANALYST_UNAVAILABLE)) == 2, (
        "an hour later the same reason is worth writing again"
    )


def test_the_throttle_state_lives_in_the_journal_not_in_the_process(paper, monkeypatch):
    """Two processes must agree, so the answer cannot be a private counter."""
    config.persist(analyst_outage_cycles=config.MAX_ANALYST_OUTAGE_CYCLES)
    first = worker.PaperWorker(verbose=False)
    first.run_cycle()
    assert len(refusals(worker.PLANNER_ANALYST_UNAVAILABLE)) == 1

    # A second worker object with no memory of the first cycle, which is what a
    # restart looks like from the throttle's point of view.
    second = worker.PaperWorker(verbose=False)
    assert second.run_cycle()["refusal_logged"] is False
    assert len(refusals(worker.PLANNER_ANALYST_UNAVAILABLE)) == 1


def test_the_throttle_reads_rows_written_by_another_writer(paper):
    """The row that decides the throttle is written by the planner, not us."""
    config.persist(analyst_outage_cycles=config.MAX_ANALYST_OUTAGE_CYCLES)
    db.log(
        worker.KIND_SKIPPED,
        {"cycle": 1, "refusal_reason": worker.PLANNER_ANALYST_UNAVAILABLE},
    )
    w = worker.PaperWorker(verbose=False)

    assert w.run_cycle()["refusal_logged"] is False
    assert len(refusals(worker.PLANNER_ANALYST_UNAVAILABLE)) == 1


# ------------------------------------------------------------------ the halt


def test_the_kill_switch_still_blocks_everything_during_an_outage(paper):
    w = worker.PaperWorker(verbose=False)
    run(w, cycles=3)
    assert w.trades_placed == 1
    placed_before = orders()

    reached: list[dict] = []
    real_plan = planner.plan

    def spy(**kwargs):
        reached.append(kwargs)
        return real_plan(**kwargs)

    paper_module_planner = planner
    paper_module_planner.plan = spy
    try:
        config.set_halted(True)
        result = w.run_cycle()
    finally:
        paper_module_planner.plan = real_plan

    assert result["status"] == "halted"
    assert result["trades_placed"] == 0
    assert reached == [], "the planner is not reached at all while halted"
    assert orders() == placed_before, "no order, unreviewed or otherwise"

    # And the outage is not quietly declared over by standing still.
    assert result["analyst_outage"] is True
    assert worker.analyst_outage_state()["active"] is True
    assert len(decisions_of(worker.KIND_OUTAGE_ENDED)) == 0

    # Resuming returns to unreviewed trading on the very next cycle.
    config.set_halted(False)
    assert planner.close_position(SYMBOL)["executed"] is True
    resumed = w.run_cycle()
    assert resumed["planner_verdict"] == "executed"
    assert resumed["unreviewed"] is True
    assert len(decisions_of(worker.KIND_OUTAGE_STARTED)) == 1


def test_a_halted_outage_names_the_open_position_it_is_not_managing(paper, monkeypatch):
    w = worker.PaperWorker(verbose=False)
    run(w, cycles=3)  # trades unreviewed, leaving a position open

    monkeypatch.setattr(planner, "manage_open_positions", lambda *a, **k: [])
    config.set_halted(True)
    result = w.run_cycle()

    assert result["status"] == "halted"
    assert result["unmanaged_positions"] == [SYMBOL]
    reported = decisions_of(worker.EXIT_HALTED_KIND)
    assert reported and reported[-1]["open_positions"] == [SYMBOL]


# ------------------------------------------------------------------ the status


def test_status_says_in_plain_words_that_trading_is_unreviewed(paper):
    w = worker.PaperWorker(verbose=False)
    run(w, cycles=3)

    state = worker.status()
    text = worker.format_status(state)

    assert state["analyst_outage"] is True
    assert state["analyst_available"] is False
    assert state["analyst_outage_started_at"] > 0
    assert state["unreviewed_trades_this_outage"] == 1

    assert "AI reviewer      : NOT ANSWERING" in text
    assert "trading mode     : UNREVIEWED" in text
    assert "unreviewed trades: 1 placed with no AI review" in text
    assert "unreviewed since :" in text
    # The next action is stated, because "waiting" is only useful with a next.
    assert "as soon as it answers" in text
    # No internal vocabulary in front of a user.
    for jargon in ("analyst_bypassed", "allow_rules_only", "planner.plan", "planner_unavailable"):
        assert jargon not in text


def test_status_says_the_analyst_is_answering_when_it_is(paper):
    paper.answering["now"] = True
    worker.PaperWorker(verbose=False).run_cycle()

    text = worker.format_status(worker.status())

    assert "AI reviewer      : answering" in text
    assert "trading mode     : reviewed" in text
    assert "unreviewed since" not in text


def test_status_explains_a_run_of_silent_cycles_before_it_gives_up_waiting(paper):
    config.persist(analyst_outage_cycles=5)
    w = worker.PaperWorker(verbose=False)

    w.run_cycle()

    text = worker.format_status(worker.status())
    assert "not answering - 1 cycle(s) in a row" in text
    assert "after 5 cycles in a row" in text
    assert "cycles to wait   : 4 more" in text


def test_paper_status_reports_the_outage_for_a_non_developer(paper, capsys):
    worker.PaperWorker(verbose=False).run_cycle()
    worker.PaperWorker(verbose=False).run_cycle()
    worker.PaperWorker(verbose=False).run_cycle()

    paper_status.main()
    out = capsys.readouterr().out

    assert "AI reviewer        : not answering" in out
    assert "but unreviewed since" in out
    assert "No-review trades   : 1 placed with no AI reviewing them" in out
    assert "reviewed trading" in out, "the reader is told what happens next"
    for jargon in ("analyst_outage", "allow_rules_only", "plan_refused"):
        assert jargon not in out


def test_paper_status_still_shows_the_halt_while_an_outage_is_on(paper, capsys):
    worker.PaperWorker(verbose=False).run_cycle()
    worker.PaperWorker(verbose=False).run_cycle()
    worker.PaperWorker(verbose=False).run_cycle()

    config.set_halted(True)
    paper_status.main()
    out = capsys.readouterr().out

    assert "STOPPED" in out
    assert "placed with no AI reviewing them" not in out, (
        "nothing traded while stopped, so nothing is counted as unreviewed"
    )
