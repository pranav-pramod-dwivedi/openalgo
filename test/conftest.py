"""Safe environment defaults for tests collected outside an installation.

Database URLs are assigned, not defaulted. setdefault() would let an exported
DATABASE_URL or SANDBOX_DATABASE_URL win, so a developer or CI box with the
production values in its environment would run this suite against the real
databases - resetting funds and creating orders in live sandbox state. The
credentials below are still setdefault(), since those are only placeholders.
"""

import ast
import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("API_KEY_PEPPER", "0" * 64)
os.environ.setdefault("APP_KEY", "test-only-app-key")

# Neutralise dotenv before anything can call it.
#
# utils/config.py runs load_dotenv(override=True) at import, which re-reads the
# operator's .env and overwrites the assignments below. Whether that happens
# depends purely on which module a given test imports first, so the suite wrote
# to the isolated databases on some runs and to the real ones on others -- the
# Flow QA tests putting seven workflows into the operator's Flow Editor, needing
# manual deletion. Disabling the loader here is confined to the test harness and
# makes the isolation below hold whatever the import order turns out to be.
import dotenv

dotenv.load_dotenv = lambda *args, **kwargs: False
dotenv.main.load_dotenv = dotenv.load_dotenv

# Assigned unconditionally: test isolation must not be overridable from the
# environment.
os.environ["DATABASE_URL"] = "sqlite:///db/openalgo-test.db"
os.environ["SANDBOX_DATABASE_URL"] = "sqlite:///db/sandbox-test.db"
os.environ["LOGS_DATABASE_URL"] = "sqlite:///db/logs-test.db"
os.environ["LATENCY_DATABASE_URL"] = "sqlite:///db/latency-test.db"

# utils.logging calls setup_logging() at import time and always attaches a JSON
# handler on $LOG_DIR/errors.jsonl, so every error a test deliberately provokes
# was appended to the operator's production log -- the file CLAUDE.md names as
# the first place to look when debugging. Worse, setup_logging truncates that
# file to its last 1000 lines on startup, so a test run could evict real errors.
os.environ["LOG_DIR"] = "log/test"


# These are manual diagnostics, not pytest modules. test_bot_web.py starts the
# Telegram bot from its module body. The WebSocket scripts require a live proxy,
# operator API key and timed terminal interaction; their ``test_*`` helpers take
# ordinary arguments rather than fixtures.
#
# Keep the scripts runnable directly while preventing collection from starting
# external services or misclassifying their function parameters as fixtures.
collect_ignore = [
    "test_bot_web.py",
    "test_websocket.py",
    "test_websocket_service.py",
]


# ---------------------------------------------------------------------------
# The paper database is live state
#
# `data/paper.db` holds the running paper portfolio: open positions, cash, the
# equity curve. It is the one file in this repository that a mistake in a test
# destroys rather than recreates, because the paper database seeds cash and
# positions once and nothing in the suite can put them back.
#
# `PAPER_DB` defaults to `data/paper.db` everywhere in the paper system, so any
# test that touches it without saying otherwise writes to the operator's real
# book. A smoke test run by hand once closed a genuine open position that way.
# The fixture below makes that impossible rather than relying on every future
# test remembering to pass a path.
# ---------------------------------------------------------------------------

PAPER_PACKAGE = "services.paper"
REAL_PAPER_DB = Path(__file__).resolve().parent.parent / "data" / "paper.db"


def _imports_paper_package(module_file) -> bool:
    """Whether this test module imports ``services.paper``, by module path.

    Detection is on the import's module path rather than on the file's name or
    on a ``"paper"`` substring, so it survives a rename and does not drag in
    unrelated tests. The module's own source is parsed rather than
    ``sys.modules``, which pytest has already filled with every other test
    module's imports before the first test runs, and would therefore say yes to
    all of them.
    """
    if not module_file:
        return False
    try:
        tree = ast.parse(Path(module_file).read_text(encoding="utf-8", errors="replace"))
    except (OSError, SyntaxError, ValueError):
        return False
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            candidates = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            candidates = [base, *(f"{base}.{alias.name}" for alias in node.names)]
        else:
            continue
        for name in candidates:
            if name == PAPER_PACKAGE or name.startswith(f"{PAPER_PACKAGE}."):
                return True
    return False


@pytest.fixture(autouse=True)
def isolate_paper_db(request, tmp_path, monkeypatch):
    """Give every paper test its own throwaway database file.

    Applies to any test whose module imports ``services.paper``, and to nothing
    else. Both halves of the binding are covered, because
    ``services/paper/db.py`` reads ``PAPER_DB`` once at import into a module
    global:

    - ``PAPER_DB`` is set before the test body runs, so a module imported during
      the test picks up the scratch path, and a subprocess inherits it.
    - ``db.DATA`` is rebound for an already-imported module, because in a
      multi-file run the first paper test to be collected is what fixes it.

    Assigned, never defaulted: a ``PAPER_DB`` exported by the developer or CI box
    must not decide where the tests write, exactly as above for the database
    URLs. The real path is also refused outright, so a future edit that points
    the fixture at the operator's book fails loudly instead of writing to it.
    """
    module = getattr(request, "module", None)
    if not _imports_paper_package(getattr(module, "__file__", None)):
        yield
        return

    scratch = tmp_path / "paper.db"
    if scratch.resolve() == REAL_PAPER_DB.resolve():
        pytest.fail("the paper test fixture would write to the real data/paper.db")

    monkeypatch.setenv("PAPER_DB", str(scratch))
    imported = sys.modules.get(f"{PAPER_PACKAGE}.db")
    if imported is not None:
        monkeypatch.setattr(imported, "DATA", scratch)

    try:
        yield
    finally:
        for suffix in ("", "-wal", "-shm"):
            leftover = Path(f"{scratch}{suffix}")
            if leftover.exists():
                leftover.unlink()
