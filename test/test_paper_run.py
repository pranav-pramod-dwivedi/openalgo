"""The rules-only paper runner: trading with no analyst at all, loudly.

Every test here runs against a temporary paper database with a synthetic market,
mocked research and a mocked analyst that would happily return a verdict, so no
network call is ever made and the analyst's silence cannot be blamed on the
fixture.

The cases pin the four properties that make this runner safe to point at a
rate-limited analyst: the kill switch is respected, one cycle is one plan and at
most one execution, ``jev.ask`` is never reached even when it would answer, and
every execution leaves a ``rules_only_execution`` journal row carrying the full
plan.

The synthetic market matters here for the same reason as anywhere else in the
paper suite. Placing a trade runs the independent verifier, which re-reads the
live quote and the candle history and refuses prices that are not near them, and
then the execution model, which fills the order against the real bid and ask. The
ladder this file plans from closes at 129.50, so the market it is checked against
has to be that ladder and not the live one. See ``install_market``.
"""

import ast
import importlib.util
import json
import sys
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


def _load_runner():
    """Import ``scripts/paper_run.py`` as a module, the way the CLI runs it."""
    path = REPO_ROOT / "scripts" / "paper_run.py"
    spec = importlib.util.spec_from_file_location("paper_run_under_test", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


paper_run = _load_runner()

from services.paper import config, db, engine, planner  # noqa: E402

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


RISING = candles([100.0 + i * 0.5 for i in range(60)])


# ---------------------------------------------------------- the synthetic book
#
# The planner's entry is the newest close moved by the account's slippage. The
# verifier re-fetches the quote and prices the plan against it; the execution
# model then fills against the real bid and ask. Both reads come from this one
# ladder, so the entry a plan was built from, the market it is checked against
# and the book it is filled off are the same price level. The book is two-sided
# with a real spread either side of the mid, so there is a book to cross and a
# day of volume to measure the order against rather than one price wearing two
# names. The shape is ``fetch_crypto_quote``'s, so the stub cannot pass either
# module by carrying something the real feed never sends.

#: A BTC major on Binance sits a couple of basis points wide; the paper account
#: refuses a book wider than ``execution.DEFAULT_MAX_SPREAD_BPS``.
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


def install_market(monkeypatch, ladder, symbol="BTCUSDT", *, age=0.0, interval=300.0):
    """Serve ``engine._candles``, the quote and the history from one ladder.

    Stamping the clock here rather than in the ladder keeps the bars from going
    stale while a long suite works its way to them; the verifier age-checks the
    history and would read a bar built at import time for what it is. The clock
    is written in milliseconds, as crypto feeds send it.
    """
    now = time.time()
    bars = []
    for i, bar in enumerate(ladder):
        copied = dict(bar)
        copied["time"] = (now - age - (len(ladder) - 1 - i) * interval) * 1000.0
        bars.append(copied)
    quote = book_from(bars)

    # The runner imports ``engine`` from ``services.paper``, so this one line is
    # the fixture's ``engine._candles`` patch as well.
    monkeypatch.setattr(engine, "_candles", lambda s, interval="5m": bars)

    def get_quote(sym, exchange):
        return dict(quote) if str(sym).upper() == symbol.upper() else None

    def get_history(sym, exchange, iv="5m", start_date="", end_date=""):
        return list(bars) if str(sym).upper() == symbol.upper() else []

    monkeypatch.setattr("services.foreign_data_service.get_foreign_quote", get_quote)
    monkeypatch.setattr("services.foreign_data_service.get_foreign_history", get_history)
    return quote


@pytest.fixture
def paper(tmp_path, monkeypatch):
    """An isolated paper database with cash, a validated strategy and a market."""
    monkeypatch.setattr(db, "DATA", tmp_path / "paper.db")
    db.init()
    db.set_many(
        {
            "starting_cash": 1000.0,
            "cash": 1000.0,
            # The verifier's per-position ceiling, in coins. Every installation
            # still carries this key at the pre-resize 0.01 -- the flat quantity
            # the sizing rule was written to stop reading -- which at this world's
            # price is 1.29 USD and would refuse every position a 1000 USD account
            # can open. One coin is a real limit here and sits well above the
            # largest position the notional cap allows.
            "max_position_qty": 1.0,
            "max_exposure_pct": 0.5,
            "fee_bps": 4.0,
            "slippage_bps": 2.0,
            "max_daily_loss": 20.0,
            "short_margin_locked": 0.0,
        }
    )
    config.set_halted(False)
    paper_run.set_armed(False)
    install_market(monkeypatch, RISING)
    monkeypatch.setattr(engine, "research_cycle", lambda *a, **k: None)
    monkeypatch.setattr(paper_run.engine, "research_cycle", lambda *a, **k: None)
    # The runner must leave the planner's analyst hook exactly as it found it,
    # so this fixture pins the untouched original and asserts it afterwards.
    original_hook = planner._ask_analyst
    return type(
        "Paper", (), {"db": db, "config": config, "engine": engine, "analyst_hook": original_hook}
    )()


def register(symbol="BTCUSDT", family="momentum", metrics=None):
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


def armed_runner(**overrides):
    kwargs = {"armed": True, "verbose": False, "interval_seconds": 1}
    kwargs.update(overrides)
    return paper_run.RulesOnlyRunner(**kwargs)


def decisions_of(kind):
    with db.conn() as c:
        rows = c.execute(
            "SELECT payload FROM decisions WHERE kind=? ORDER BY id", (kind,)
        ).fetchall()
    return [json.loads(r["payload"]) for r in rows]


# ----------------------------------------------------------------------- cases


def test_kill_switch_refuses_the_cycle_and_places_nothing(paper, monkeypatch):
    register()
    config.set_halted(True)
    calls = []
    monkeypatch.setattr(
        planner, "plan", lambda **kwargs: calls.append(kwargs) or {"refusal_reason": "x"}
    )

    result = armed_runner().run_cycle()

    assert result["status"] == "halted"
    assert calls == [], "the planner must not be reached while the kill switch is set"
    with db.conn() as c:
        assert c.execute("SELECT COUNT(*) AS n FROM orders").fetchone()["n"] == 0


def test_unarmed_runner_refuses_before_the_planner(paper, monkeypatch):
    register()
    calls = []
    monkeypatch.setattr(
        planner, "plan", lambda **kwargs: calls.append(kwargs) or {"refusal_reason": "x"}
    )

    result = armed_runner(armed=False).run_cycle()

    assert result["status"] == "unarmed"
    assert calls == []


def test_one_cycle_plans_once_and_executes_once_never_twice(paper, monkeypatch):
    register()
    plans = []
    executions = []
    real_plan = planner.plan
    real_execute = planner.execute

    def counting_plan(**kwargs):
        plans.append(kwargs)
        return real_plan(**kwargs)

    def counting_execute(plan):
        executions.append(plan)
        return real_execute(plan)

    monkeypatch.setattr(planner, "plan", counting_plan)
    monkeypatch.setattr(planner, "execute", counting_execute)

    runner = armed_runner()
    result = runner.run_cycle()

    assert len(plans) == 1, "the planner must be asked exactly once per cycle"
    assert len(executions) == 1, "one cycle must place at most one order"
    assert plans[0]["allow_rules_only"] is True
    assert result["planner_verdict"] == "executed"
    assert result["trades_taken"] == 1

    # A second cycle is a second cycle, not a repeat of the first order.
    runner.run_cycle()
    assert len(plans) == 2


def test_refused_plan_is_never_executed(paper, monkeypatch):
    # No strategy registered: there is no validated setup to trade.
    executions = []
    monkeypatch.setattr(planner, "execute", lambda plan: executions.append(plan))

    result = armed_runner().run_cycle()

    assert executions == []
    assert result["planner_verdict"] == "refused"
    assert result["plans_refused"] == 1
    assert decisions_of(paper_run.KIND_REFUSED)


def test_jev_is_never_asked_even_when_it_would_answer(paper, monkeypatch):
    register()
    asked = []

    def answering_analyst(state, questions):
        """A perfectly healthy analyst. It must still never be consulted."""
        asked.append(state)
        return {
            "answers": {
                "trade": {"probabilities": {"0": 0.2, "1": 0.8}},
                "quality": {"probabilities": {"0": 0.4, "1": 0.6}},
            }
        }

    monkeypatch.setattr(planner.jev, "ask", answering_analyst)

    result = armed_runner().run_cycle()

    assert asked == [], "services.paper.jev must not be asked for a decision"
    # And the planner is handed back able to answer again: the runner swapped
    # the hook for the duration of the plan call and put it back afterwards.
    assert planner._ask_analyst is not paper_run._disabled_analyst
    assert callable(planner._ask_analyst)
    assert planner._ask_analyst.__code__ is paper.analyst_hook.__code__
    assert result["planner_verdict"] == "executed"
    with db.conn() as c:
        position = c.execute("SELECT * FROM positions WHERE status='open'").fetchone()
    assert position is not None
    # The order exists, and nothing about it claims a model opinion.
    assert position["strategy_id"] == "momentum-BTCUSDT"


def test_the_executed_plan_is_stamped_bypassed_and_journalled(paper):
    register()

    result = armed_runner().run_cycle()
    assert result["planner_verdict"] == "executed"

    rows = decisions_of(paper_run.KIND_EXECUTION)
    assert len(rows) == 1
    row = rows[0]
    assert row["analyst_bypassed"] is True
    assert row["analyst_available"] is False

    plan = row["plan"]
    assert plan["analyst_bypassed"] is True
    assert plan["symbol"] == "BTCUSDT"
    assert plan["side"] == "BUY"
    assert plan["qty"] > 0
    assert plan["risk_usd"] > 0
    # The full plan, reasoning included, so the journal says plainly that
    # nothing reviewed this trade.
    assert plan["reasoning"]
    assert "did not answer" in plan["reasoning"]
    assert plan["metrics"]["trades"] == 24
    assert row["execution"]["status"] == "filled"


def test_status_reports_cash_equity_positions_trades_and_refusals(paper):
    register()
    armed_runner().run_cycle()

    state = paper_run.status()
    text = paper_run.format_status(state)

    assert state["halted"] is False
    assert state["analyst_bypassed"] is True
    assert state["open_position_count"] == 1
    assert state["trades_taken"] == 1
    assert state["cash"] < 1000.0
    assert "analyst" in text.lower()
    assert "cash" in text.lower()
    assert "equity" in text.lower()
    assert "open positions" in text.lower()
    assert "plans refused" in text.lower()


def test_banner_names_the_analyst_the_unreviewed_trades_and_the_money(capsys):
    text = paper_run.banner().lower()
    assert capsys.readouterr().out, "the first banner must reach the terminal"
    assert "analyst is off" in text
    assert "nothing reviewed" in text
    assert "paper money" in text
    # Once per process: a second call does not print again.
    paper_run.banner()
    assert capsys.readouterr().out == ""


def test_the_module_never_imports_the_analyst_itself():
    """The docstring says it does not; the imports have to agree."""
    tree = ast.parse((REPO_ROOT / "scripts" / "paper_run.py").read_text())
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
            imported.update(f"{node.module or ''}.{a.name}" for a in node.names)
    assert "services.paper.jev" not in imported
    assert "jev" not in imported
    assert not any(name.endswith(".jev") for name in imported)


def test_cli_refuses_to_trade_without_arming(paper, monkeypatch, capsys):
    register()
    calls = []
    monkeypatch.setattr(
        planner, "plan", lambda **kwargs: calls.append(kwargs) or {"refusal_reason": "x"}
    )

    code = paper_run.main(["--once"])

    assert code == 2
    assert calls == []
    assert "not armed" in capsys.readouterr().out.lower()


def test_halt_and_resume_round_trip_through_the_shared_kill_switch(paper):
    assert paper_run.main(["--halt"]) == 0
    assert config.is_halted() is True
    assert paper_run.main(["--status"]) == 0
    assert paper_run.main(["--resume"]) == 0
    assert config.is_halted() is False
