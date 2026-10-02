"""One command, one trade, then it stops.

These tests pin the rule the paper console exists to keep: a trade command places
AT MOST ONE trade per invocation. Not one per symbol, not one per retry, not one
per loop tick -- one, and then it exits.

Everything runs against a temporary paper database with mocked candles and a
mocked analyst, so no network call is made and the real ``data/paper.db`` is
never opened. ``PAPER_DB`` is pointed at a scratch path before anything imports
``services.paper.db`` as well as per test, so an import-order accident cannot
reach live state.

The cases:

* ``profitable_trade`` places one order even when every symbol on the watchlist
  has a setup, and it never calls ``planner.execute`` twice;
* its guard refuses a second plan or a second order outright;
* a second invocation is a new single trade, not a continuation of the first;
* ``paper_run`` places at most one trade per cycle and its guard refuses a second
  order inside one cycle;
* the bash console's trade path reaches the planner exactly once, and prints one
  trade summary;
* status shows the last trade placed, so a command that did one thing is visible
  as one thing.
"""

import ast
import importlib.util
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

# Point PAPER_DB away from the repository before services.paper.db is imported
# anywhere. tmp_path cannot be used at import time, so the scratch path is made
# here and monkeypatched per test as well.
_SCRATCH = Path(tempfile.mkdtemp(prefix="paper-one-trade-"))
os.environ["PAPER_DB"] = str(_SCRATCH / "import-time-paper.db")


def _load(name: str, relative: str):
    """Import a script by path, the way its CLI does."""
    path = REPO_ROOT / relative
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


profitable_trade = _load("profitable_trade_under_test", "scripts/profitable_trade.py")
paper_run = _load("paper_run_under_one_trade_test", "scripts/paper_run.py")
paper_status = _load("paper_status_under_one_trade_test", "scripts/paper_status.py")

from services.paper import config, db, engine, planner  # noqa: E402

CONSOLE = REPO_ROOT / "paper"
WATCHLIST = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]


# ------------------------------------------------------------------- fixtures


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
                "time": time.time() - (len(closes) - i) * 60,
            }
        )
    return out


RISING = candles([100.0 + i * 0.5 for i in range(60)])

# An analyst that takes the trade, for the paths that refuse a rules-only plan.
ANALYST_VERDICT = {
    "answers": {
        "trade": {"probabilities": {"0": 0.28, "1": 0.72}},
        "quality": {"probabilities": {"0": 0.39, "1": 0.61}},
    }
}


@pytest.fixture
def paper(tmp_path, monkeypatch):
    """An isolated paper database with three symbols that all have a setup.

    Every symbol on the watchlist has a validated strategy and rising candles, so
    the planner has three candidates to choose from. A command that walked the
    watchlist would place three orders. A command that keeps to the rule places
    one.
    """
    scratch = tmp_path / "paper.db"
    monkeypatch.setenv("PAPER_DB", str(scratch))
    monkeypatch.setattr(db, "DATA", scratch)
    db.init()
    db.set_many(
        {
            "starting_cash": 1000.0,
            "cash": 1000.0,
            "max_position_qty": 0.01,
            "max_exposure_pct": 0.2,
            "fee_bps": 4.0,
            "slippage_bps": 2.0,
            "max_daily_loss": 20.0,
            "short_margin_locked": 0.0,
        }
    )
    config.set_halted(False)
    config.persist(symbols=",".join(WATCHLIST), interval="5m")
    paper_run.set_armed(False)
    for symbol in WATCHLIST:
        register(symbol)
    monkeypatch.setattr(engine, "_candles", lambda s, interval="5m": RISING)
    monkeypatch.setattr(engine, "research_cycle", lambda *a, **k: None)
    # An analyst that would answer if it were asked. Silence plus
    # --allow-rules-only is the unreviewed path; whether it answers or not, the
    # one-trade rule must hold.
    monkeypatch.setattr(planner.jev, "ask", lambda *a, **k: None)
    return tmp_path


def register(symbol="BTCUSDT", family="momentum"):
    with db.conn() as c:
        c.execute(
            "INSERT OR REPLACE INTO strategies VALUES(?,?,?,?,?,?,?,?)",
            (
                f"{family}-{symbol}",
                "research",
                family,
                '{"n": 5}',
                json.dumps({"trades": 24, "net_pnl": 41.5, "max_drawdown": 12.25, "fees": 3.1}),
                "active",
                time.time(),
                1,
            ),
        )


def open_position(symbol, qty=0.01, entry=100.0, side="BUY"):
    """Write an open position row directly, with no order behind it.

    Used to put the account in a state a test names, instead of waiting for a
    balance to drift into it: whether a cycle can trade is decided by rules, and
    a rule can be set up exactly. An incidental balance is not a fixture.
    """
    db.ensure_column("positions", "mark", "REAL")
    row = (symbol, side, qty, entry, time.time(), "manual")
    with db.conn() as c:
        c.execute(
            "INSERT OR REPLACE INTO positions"
            "(symbol,side,qty,entry,opened,strategy_id,sl,tp,status,mark) VALUES(?,?,?,?,?,?,?,?,?,?)",
            row + (entry * 0.99, entry * 1.01, "open", entry),
        )


def orders():
    with db.conn() as c:
        return [dict(r) for r in c.execute("SELECT id, symbol, side, qty FROM orders ORDER BY created")]


def fills():
    with db.conn() as c:
        return [dict(r) for r in c.execute("SELECT order_id, symbol, side, qty FROM fills ORDER BY id")]


def count_calls(monkeypatch):
    """Wrap planner.plan and planner.execute so every call is recorded."""
    calls = []
    real_plan, real_execute = planner.plan, planner.execute

    def counting_plan(**kwargs):
        calls.append(("plan", kwargs))
        return real_plan(**kwargs)

    def counting_execute(plan):
        calls.append(("execute", plan))
        return real_execute(plan)

    monkeypatch.setattr(planner, "plan", counting_plan)
    monkeypatch.setattr(planner, "execute", counting_execute)
    return calls


# -------------------------------------------------- profitable_trade: one order


def test_profitable_trade_places_one_order_even_when_every_symbol_has_a_setup(paper, monkeypatch):
    calls = count_calls(monkeypatch)

    code = profitable_trade.main(["--yes", "--allow-rules-only"])

    plans = [c for c in calls if c[0] == "plan"]
    executions = [c for c in calls if c[0] == "execute"]

    assert len(plans) == 1, "one plan attempt per invocation"
    assert len(executions) == 1, "at most one execution per invocation"
    # The whole watchlist went into that single plan call. It was not walked:
    # no symbol was handed to the planner on its own after the first.
    assert plans[0][1]["symbols"] == WATCHLIST

    placed = orders()
    assert len(placed) == 1, f"exactly one order, not one per symbol: {placed}"
    assert placed[0]["symbol"] in WATCHLIST
    assert len(fills()) == 1
    assert code == 0


def test_profitable_trade_reports_and_stops_when_nothing_has_a_setup(paper, monkeypatch, capsys):
    # No validated strategy at all: nothing to trade, and no second symbol to try.
    with db.conn() as c:
        c.execute("DELETE FROM strategies")
    calls = count_calls(monkeypatch)

    code = profitable_trade.main(["--yes", "--allow-rules-only", "--plain"])

    assert [c[0] for c in calls] == ["plan", "execute"], (
        "one plan and at most one execute, with no retry and no second symbol"
    )
    assert orders() == []
    assert code == 1
    out = capsys.readouterr().out
    assert "No trade was made" in out
    assert "does not try the next coin" in out


def test_profitable_trade_never_calls_execute_twice(paper, monkeypatch):
    """The guard, called directly: the second order never reaches the planner."""
    calls = count_calls(monkeypatch)
    guard = profitable_trade.OneTradeGuard()
    plan = guard.plan(symbols=WATCHLIST, mode="execute", max_risk=5.0, allow_rules_only=True)

    guard.execute(plan)
    with pytest.raises(profitable_trade.TooManyTrades):
        guard.execute(plan)
    with pytest.raises(profitable_trade.TooManyTrades):
        guard.plan(symbols=WATCHLIST, mode="execute", max_risk=5.0, allow_rules_only=True)

    assert [c[0] for c in calls] == ["plan", "execute"], (
        "exactly one execute reached planner.execute; the second was refused at the guard"
    )
    assert len(orders()) == 1
    assert guard.placed == [plan["symbol"]]


def test_the_guard_allows_a_second_order_only_in_a_new_run(paper):
    """One guard belongs to one invocation, so a second run is not blocked."""
    guard = profitable_trade.OneTradeGuard()
    assert guard.max_trades == profitable_trade.MAX_TRADES_PER_RUN == 1
    assert guard.execute_calls == 0
    # A fresh guard, as main() builds per run, has its allowance back.
    fresh = profitable_trade.OneTradeGuard()
    plan = fresh.plan(symbols=["BTCUSDT"], mode="execute", max_risk=5.0, allow_rules_only=True)
    assert fresh.execute(plan).get("executed") is True
    assert fresh.placed == ["BTCUSDT"]


def test_a_second_invocation_is_a_new_single_trade_not_a_continuation(paper, monkeypatch):
    calls = count_calls(monkeypatch)

    first = profitable_trade.main(["--yes", "--allow-rules-only"])
    second = profitable_trade.main(["--yes", "--allow-rules-only"])

    assert first == 0 and second == 0
    placed = orders()
    assert len(placed) == 2, "one trade per invocation, not one trade per account"
    assert len({o["id"] for o in placed}) == 2, "two distinct orders"
    assert len([c for c in calls if c[0] == "plan"]) == 2
    assert len([c for c in calls if c[0] == "execute"]) == 2


def test_a_single_symbol_symbol_option_still_places_one_trade(paper, monkeypatch):
    calls = count_calls(monkeypatch)

    assert profitable_trade.main(["--symbol", "ethusdt", "--yes", "--allow-rules-only"]) == 0

    assert len(orders()) == 1
    assert orders()[0]["symbol"] == "ETHUSDT"
    assert len(calls) == 2


def test_profitable_trade_has_no_loop_over_the_watchlist():
    """The one-trade rule is visible in the source, not only in the tests."""
    tree = ast.parse(Path(profitable_trade.__file__).read_text())
    functions = {
        node.name: node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
    }
    for name in ("main", "attempt"):
        loops = [
            node
            for node in ast.walk(functions[name])
            if isinstance(node, (ast.For, ast.While, ast.AsyncFor))
        ]
        assert loops == [], f"{name} must contain no loop over symbols or attempts"
    main_source = ast.unparse(functions["main"])
    assert "OneTradeGuard()" in main_source, "main must go through the guard"
    assert "attempt(" in main_source, "main must plan through the guarded attempt"


# ------------------------------------------------------------- paper_run: once


def armed_runner(**overrides):
    kwargs = {"armed": True, "verbose": False, "interval_seconds": 1}
    kwargs.update(overrides)
    return paper_run.RulesOnlyRunner(**kwargs)


def test_paper_run_places_at_most_one_trade_per_cycle(paper, monkeypatch):
    # Exits are a separate concern and are covered in test_paper_exits.py. Muted
    # here so the order count is entries only, which is what this rule is about.
    monkeypatch.setattr(planner, "manage_open_positions", lambda *a, **k: [])
    calls = count_calls(monkeypatch)

    runner = armed_runner()

    # Two cycles, two plan attempts, one trade in each: the loop keeps trading
    # across cycles and neither of them stacked a second order.
    first = runner.run_cycle()
    second = runner.run_cycle()

    assert [first["trades_placed"], second["trades_placed"]] == [1, 1]
    assert first["planner_verdict"] == "executed"
    assert len([c for c in calls if c[0] == "plan"]) == 2, "one plan per cycle, never a retry"
    assert len([c for c in calls if c[0] == "execute"]) == 2, "one execute per trading cycle"
    assert len(orders()) == 2, f"one trade per cycle, not one per symbol: {orders()}"
    # Two cycles, two different coins: neither cycle walked on to a second symbol.
    assert orders()[0]["symbol"] != orders()[1]["symbol"]

    # The third cycle is blocked by a rule this test sets up, not by whatever
    # balance the first two happened to leave behind: the one symbol the planner
    # has not traded is opened here, so the cycle is refused by the already-open
    # rule and has nothing to place.
    for symbol in set(WATCHLIST) - {o["symbol"] for o in orders()}:
        open_position(symbol)
    third = runner.run_cycle()
    results = [first, second, third]

    assert [r["trades_placed"] for r in results] == [1, 1, 0]
    assert all(r["trades_placed"] <= 1 for r in results), "never more than one in a cycle"
    assert third["planner_verdict"] == "refused"
    assert third["refusal_reason"] == planner.SYMBOL_ALREADY_OPEN
    assert third["refusal_reason"] != paper_run.TOO_MANY_TRADES, (
        "the cycle was refused by the planner, not by the one-trade guard"
    )
    assert len(orders()) == 2, "a refused cycle places nothing"
    # Three cycles, three plan attempts, two of them executed: a refused cycle
    # still asks exactly once and never retries into a second symbol.
    assert len([c for c in calls if c[0] == "plan"]) == 3, "one plan per cycle, never a retry"
    assert len([c for c in calls if c[0] == "execute"]) == 2, (
        "the refused cycle reached execute() zero times"
    )


def test_paper_run_may_trade_again_in_the_next_cycle(paper, monkeypatch):
    """The allowance is per cycle: the loop keeps trading, one trade each tick."""
    monkeypatch.setattr(planner, "manage_open_positions", lambda *a, **k: [])
    runner = armed_runner()

    assert runner.run_cycle()["trades_placed"] == 1
    assert planner.close_position("BTCUSDT")["executed"] is True

    second = runner.run_cycle()

    assert second["trades_placed"] == 1, "a new cycle gets the allowance back"
    assert second["planner_verdict"] == "executed"
    assert len(orders()) == 3, "two entries and one deliberate close"
    assert [o["side"] for o in orders()].count("BUY") == 2


def test_paper_run_refuses_a_second_order_inside_one_cycle(paper):
    runner = armed_runner()
    plan = {
        "symbol": "BTCUSDT",
        "side": "BUY",
        "qty": 0.01,
        "entry_price": 100.0,
        "stop_loss": 99.0,
        "take_profit": 102.0,
        "analyst_available": False,
        "analyst_bypassed": True,
        "reasoning": "test",
    }
    runner._cycle_trades = 1

    result = runner._execute_once(plan)

    assert result["executed"] is False
    assert result["refusal_reason"] == paper_run.TOO_MANY_TRADES
    assert orders() == [], "the refused second order was never sent"
    # And it is in the journal rather than silently dropped.
    with db.conn() as c:
        rows = [
            json.loads(r["payload"])
            for r in c.execute("SELECT payload FROM decisions WHERE kind=?", (paper_run.KIND_REFUSED,))
        ]
    assert any(r["refusal_reason"] == paper_run.TOO_MANY_TRADES for r in rows)


def test_paper_run_cli_once_places_at_most_one_trade(paper, monkeypatch, capsys):
    monkeypatch.setattr(planner, "manage_open_positions", lambda *a, **k: [])
    calls = count_calls(monkeypatch)

    code = paper_run.main(["--once", "--autonomous", "--quiet"])

    assert code == 0
    assert len(orders()) == 1
    assert len([c for c in calls if c[0] == "plan"]) == 1
    out = capsys.readouterr().out
    assert "this cycle placed 1 trade (at most 1 per cycle)" in out


def test_paper_run_still_refuses_to_trade_unarmed(paper, monkeypatch):
    calls = count_calls(monkeypatch)

    code = paper_run.main(["--once", "--quiet"])

    assert code == 2, "arming is still required"
    assert calls == []
    assert orders() == []


# ----------------------------------------------------------- status: one trade


def test_status_shows_the_last_trade_placed(paper, monkeypatch, capsys):
    assert profitable_trade.main(["--yes", "--allow-rules-only"]) == 0
    placed = orders()[0]

    paper_status.main()
    out = capsys.readouterr().out

    assert "One trade each time" in out, "the one-trade rule is stated"
    assert "Last trade placed" in out
    assert "bought" in out
    assert placed["symbol"] in out


def test_worker_status_shows_the_last_trade_and_the_per_cycle_cap(paper, monkeypatch):
    from services.paper import worker

    # The worker trades only on an analyst-backed plan, so the analyst answers
    # here. Exits are muted so the cycle's single order is the only one.
    monkeypatch.setattr(planner, "manage_open_positions", lambda *a, **k: [])
    monkeypatch.setattr(engine, "research_cycle", lambda *a, **k: None)
    monkeypatch.setattr(planner.jev, "ask", lambda *a, **k: ANALYST_VERDICT)

    runner = worker.PaperWorker(verbose=False)
    summary = runner.run_cycle()

    assert summary["trades_placed"] == 1
    assert summary["planner_verdict"] == "executed"
    assert len(orders()) == 1

    text = worker.format_status(worker.status())
    assert f"at most {worker.MAX_TRADES_PER_CYCLE}" in text
    assert "last trade placed" in text
    assert "bought" in text
    assert worker.last_trade()["symbol"] in WATCHLIST


# ------------------------------------------------- the bash console: one trade


def _shell_function(name: str) -> str:
    """One shell function lifted out of the console, by name."""
    text = CONSOLE.read_text()
    start = text.index(f"\n{name}() {{")
    end = text.index("\n}\n", start)
    return text[start + 1 : end + 2]


def _console_variables() -> str:
    """The console's own PAPER_DB / PIDFILE / LOG assignments, cd removed.

    They sit above the functions in the console, so a harness that only sources
    the functions would run with unbound variables instead of the console's own
    settings. Each respects an already-exported value, which is how the harness
    points the whole command at a scratch database.
    """
    text = CONSOLE.read_text()
    start = text.index('cd "$(dirname "$0")"')
    block = text[start : text.index("\n\n", start)]
    return "\n".join(line for line in block.splitlines() if not line.startswith("cd "))


def _console_trade_path(tmp_path: Path, tag: str = "one") -> str:
    """Run the console's own ``one_trade`` with ``uv`` stubbed, and return its output.

    The console's functions are executed as written; only ``uv`` is replaced, so
    no environment is synced, no worker is really started and the trade itself
    goes through the real ``profitable_trade`` with the market data and the
    analyst stubbed (the wrapper is test-side; no production hook is involved).
    ``tag`` keeps each invocation's logs separate while they share one scratch
    database, which is how two commands in a row are compared.
    """
    work = tmp_path / tag
    work.mkdir(exist_ok=True)
    bin_dir = work / "bin"
    bin_dir.mkdir(exist_ok=True)
    uv_log = work / "uv.log"
    planner_log = work / "planner.log"

    wrapper = work / "wrapper.py"
    wrapper.write_text(
        "import importlib.util, json, os, sys, time\n"
        f"sys.path.insert(0, {str(REPO_ROOT)!r})\n"
        f"spec = importlib.util.spec_from_file_location('pt', {str(REPO_ROOT / 'scripts/profitable_trade.py')!r})\n"
        "pt = importlib.util.module_from_spec(spec)\n"
        "spec.loader.exec_module(pt)\n"
        "from services.paper import engine, planner\n"
        "closes = [100.0 + i * 0.5 for i in range(60)]\n"
        "engine._candles = lambda s, interval='5m': [\n"
        "    {'open': c, 'high': c * 1.002, 'low': c * 0.998, 'close': c,\n"
        "     'volume': 1000.0 + i, 'time': time.time() - (60 - i) * 60}\n"
        "    for i, c in enumerate(closes)]\n"
        "planner.jev.ask = lambda *a, **k: None\n"
        "log = os.environ['PLANNER_LOG']\n"
        "real_plan, real_execute = planner.plan, planner.execute\n"
        "def plan(**kw):\n"
        "    open(log, 'a').write(json.dumps({'call': 'plan', 'symbols': kw.get('symbols')}) + '\\n')\n"
        "    return real_plan(**kw)\n"
        "def execute(plan):\n"
        "    open(log, 'a').write(json.dumps({'call': 'execute', 'symbol': plan.get('symbol')}) + '\\n')\n"
        "    return real_execute(plan)\n"
        "planner.plan, planner.execute = plan, execute\n"
        "sys.exit(pt.main(sys.argv[1:]))\n"
    )

    uv = bin_dir / "uv"
    uv.write_text(
        "#!/usr/bin/env bash\n"
        f'printf "%s\\n" "$*" >> "{uv_log}"\n'
        'if [ "$1" = "run" ] && [ "$2" = "python" ] && [ "$3" = "scripts/profitable_trade.py" ]; then\n'
        '  shift 3\n'
        f'  exec "{sys.executable}" "{wrapper}" "$@"\n'
        "fi\n"
        'if [ "$1" = "run" ] && [ "$2" = "python" ] && [ "$3" = "scripts/paper_worker.py" ] && [ "${4:-}" != "--resume" ]; then\n'
        "  sleep 30\n"
        "fi\n"
        "exit 0\n"
    )
    uv.chmod(uv.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

    functions = "\n".join(
        _shell_function(name)
        for name in ("say", "worker_pid", "first_time_setup", "ensure_worker", "one_trade")
    )
    harness = work / "harness.sh"
    harness.write_text(
        "set -euo pipefail\n"
        f'cd "{REPO_ROOT}"\n'
        f'export PATH="{bin_dir}:$PATH"\n'
        f'export PLANNER_LOG="{planner_log}"\n'
        f'export PAPER_DB="{tmp_path / "console.db"}"\n'
        f'export PAPER_PIDFILE="{work / "worker.pid"}"\n'
        f'export PAPER_LOG="{work / "worker.log"}"\n'
        f"{_console_variables()}\n"
        f"{functions}\n"
        "one_trade\n"
    )

    proc = subprocess.run(
        ["bash", str(harness)],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert proc.returncode == 0, f"console trade path failed: {proc.stderr}"

    calls = [
        json.loads(line)
        for line in planner_log.read_text().splitlines()
        if line.strip()
    ]
    uv_calls = uv_log.read_text().splitlines()
    return json.dumps({"output": proc.stdout, "calls": calls, "uv_calls": uv_calls})


def _seed_console_db(tmp_path: Path) -> Path:
    """A scratch account with a validated strategy on every watchlist symbol."""
    scratch = tmp_path / "console.db"
    previous = db.DATA
    db.DATA = scratch
    try:
        db.init()
        db.set_many(
            {
                "starting_cash": 1000.0,
                "cash": 1000.0,
                "max_position_qty": 0.01,
                "max_exposure_pct": 0.2,
                "fee_bps": 4.0,
                "slippage_bps": 2.0,
                "max_daily_loss": 20.0,
                "short_margin_locked": 0.0,
            }
        )
        config.set_halted(False)
        config.persist(symbols=",".join(WATCHLIST), interval="5m")
        for symbol in WATCHLIST:
            register(symbol)
    finally:
        db.DATA = previous
    return scratch


def _console_orders(tmp_path: Path) -> list[dict]:
    """Read the orders the console wrote into its own scratch database."""
    previous = db.DATA
    db.DATA = tmp_path / "console.db"
    try:
        with db.conn() as c:
            return [
                dict(r)
                for r in c.execute("SELECT id, symbol, side, qty FROM orders ORDER BY created")
            ]
    finally:
        db.DATA = previous


def test_the_console_trade_path_reaches_the_planner_exactly_once(tmp_path):
    _seed_console_db(tmp_path)

    result = json.loads(_console_trade_path(tmp_path, "one"))

    plans = [c for c in result["calls"] if c["call"] == "plan"]
    executions = [c for c in result["calls"] if c["call"] == "execute"]
    assert len(plans) == 1, f"the console must ask the planner once: {result['calls']}"
    assert len(executions) <= 1, f"the console must place at most one trade: {result['calls']}"
    # One uv invocation of the trade script, and it was told to place the trade.
    trade_calls = [c for c in result["uv_calls"] if "profitable_trade.py" in c]
    assert len(trade_calls) == 1
    assert "--yes" in trade_calls[0]
    # The worker was started in the background as part of the same command, and
    # the user was told so in one line.
    assert any("paper_worker.py" in c for c in result["uv_calls"])
    assert "watching the account in the background" in result["output"]

    placed = _console_orders(tmp_path)
    assert len(placed) == 1, f"one trade from one bare command: {placed}"
    assert "One paper trade placed" in result["output"]


def test_a_second_console_command_is_again_a_single_trade(tmp_path):
    _seed_console_db(tmp_path)

    first = json.loads(_console_trade_path(tmp_path, "first"))
    second = json.loads(_console_trade_path(tmp_path, "second"))

    for result in (first, second):
        assert len([c for c in result["calls"] if c["call"] == "plan"]) == 1
        assert len([c for c in result["calls"] if c["call"] == "execute"]) <= 1

    placed = _console_orders(tmp_path)
    assert len(placed) == 2, "one trade per command, never a batch"


def test_the_console_writes_only_to_the_database_it_was_given(tmp_path):
    """PAPER_DB decides where a run lands, and the run honours it.

    The console is pointed at a scratch database here, and the order it places is
    in that file. (Comparing ``data/paper.db`` timestamps would be worthless: a
    live worker on this box writes to its own database continuously.)
    """
    scratch = _seed_console_db(tmp_path)

    _console_trade_path(tmp_path)

    assert scratch.exists()
    assert len(_console_orders(tmp_path)) == 1, "the order landed in the scratch database"
    assert 'PAPER_DB="${PAPER_DB:-data/paper.db}"' in CONSOLE.read_text()


# --------------------------------------------------- the console's own surface


def test_the_console_offers_three_commands_and_no_setup_or_start():
    text = CONSOLE.read_text()
    assert '""' in text and "|trade)" in text, "a bare ./paper makes the trade"
    assert "status)" in text
    assert "stop)" in text
    assert "help|--help|-h)" in text
    # The setup and start steps are folded into the trade command, not offered
    # as commands of their own.
    assert "setup)" not in text
    assert "start)" not in text


def test_the_console_trade_path_has_no_loop():
    body = _shell_function("one_trade")
    for loop in ("for ", "while ", "until "):
        assert loop not in body, f"one_trade must not contain {loop!r}"
    assert body.count("scripts/profitable_trade.py") == 1, "one call site, one trade"


def test_the_console_help_is_plain_and_short():
    text = CONSOLE.read_text()
    help_start = text.index("help_text() {")
    help_body = text[help_start : text.index("\n}\n", help_start)]
    words = len(help_body.split())
    assert words < 200, f"the help must stay a few lines, not {words} words"
    for jargon in ("refusal_reason", "planner", "PAPER_DB", "planner.execute"):
        assert jargon not in help_body.lower(), f"{jargon} is not plain English"
    assert not any(ord(ch) > 0x2190 for ch in help_body), "no emoji"
    assert "./paper" in help_body and "virtual money" in help_body.lower()


def test_console_does_not_run_against_live_state_by_default():
    """The default database is the repo's paper database; nothing else is implied."""
    variables = _console_variables()
    assert 'PAPER_DB="${PAPER_DB:-data/paper.db}"' in variables
    assert "PAPER_PIDFILE" in CONSOLE.read_text(), "the pidfile and log can be moved off the repo too"
    assert shutil.which("bash"), "bash is what the console runs on"
