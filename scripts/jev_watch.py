#!/usr/bin/env python3
"""Wait for the Jev analyst to come back, and say so once when it does.

The analyst (``jev-1.13-free`` via OpenCode Zen) is a gate on every paper trade.
When it cannot answer, the planner refuses rather than trading on rules alone --
correct, but it means an exhausted free allowance silently stops the system and
the only symptom is a refusal line in a log nobody is reading. This watcher polls
the analyst on a fixed interval and announces the moment availability flips back,
so recovery is never something a human has to go and check for.

Read-only with respect to ``services/paper/``: it calls ``jev.ask`` and appends to
``decisions``, and changes nothing else.

    uv run python scripts/jev_watch.py                    # poll every 5 minutes
    uv run python scripts/jev_watch.py --interval 60      # poll every minute
    uv run python scripts/jev_watch.py --once             # one probe, 0 = up, 1 = down
    uv run python scripts/jev_watch.py --max-minutes 120   # give up after two hours

The probe is a tiny fixed request. It costs one analyst call per interval, which
is the smallest useful sample of "can the analyst answer right now" -- asking
whether it is *useful* is the planner's job, not this script's.

Two journal rows, and the difference matters. ``analyst_recovered`` is written
once per unavailable -> available flip, because recovery is the event an operator
is waiting for. ``analyst_still_unavailable`` is written at most once an hour, so
a long outage leaves one row an hour instead of one row per poll; the hourly
budget is read back out of the journal rather than kept in memory, so it holds
across separate ``--once`` invocations too.

Survives a dead endpoint by construction. A transport failure is the expected
case here, not an error: it becomes one printed line and a reason, never a
traceback, and the desktop notification is best-effort so a headless or non-macOS
host loses the banner and keeps the watcher.
"""

from __future__ import annotations

import argparse
import platform
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.paper import db, jev  # noqa: E402
from utils.logging import get_logger  # noqa: E402

logger = get_logger(__name__)

# The whole point of this script is that the analyst is a gate, so a probe that
# refuses to be answered is the failure being watched for. Kept fixed and tiny:
# one score question is the least an analyst can be asked and still answer.
PROBE_STATE = (
    "Availability probe. No trade is being proposed and nothing should be "
    "decided from this. Reply to the question so the caller can tell the "
    "analyst is answering again."
)
PROBE_QUESTIONS = {
    "availability": {"type": "score", "criteria": ["no", "yes"]},
}

# One journal row per hour while nothing changes. Long enough that an overnight
# outage is a readable handful of rows, short enough that "how long has it been
# down" is answerable from the journal alone.
STILL_DOWN_THROTTLE_SECONDS = 3600

UNKNOWN_REASON = "The analyst did not answer this probe."


def probe() -> tuple[bool, str]:
    """Ask the analyst once. Returns (available, plain-language reason).

    Never raises. ``jev.ask`` already turns a transport failure into a plain
    sentence; the guard here covers everything outside it -- an unexpected
    payload, a broken import-time global, anything a dead endpoint can turn into
    an exception that ``ask`` itself did not anticipate.
    """
    try:
        raw = jev.ask(PROBE_STATE, PROBE_QUESTIONS)
    except Exception as exc:
        # Never let a raised transport failure end the watcher.
        logger.warning("jev availability probe raised: %s", exc)
        return False, UNKNOWN_REASON
    if isinstance(raw, dict):
        return True, ""
    return False, jev.failure_reason() or UNKNOWN_REASON


def _last_logged(kind: str) -> float:
    """Timestamp of the most recent journal row of this kind, or 0 if never."""
    try:
        with db.conn() as c:
            row = c.execute("SELECT MAX(ts) AS ts FROM decisions WHERE kind=?", (kind,)).fetchone()
    except Exception as exc:
        logger.warning("could not read journal history for %s: %s", kind, exc)
        return 0.0
    return float(row["ts"] or 0.0) if row else 0.0


def _record(kind: str, payload: dict) -> None:
    """Append a decisions row. A failed write is logged, never raised."""
    try:
        db.init()
        db.log(kind, payload)
    except Exception as exc:
        logger.warning("could not write journal row %s: %s", kind, exc)


def notify(title: str, message: str) -> bool:
    """Best-effort macOS desktop notification. Returns whether it was shown.

    Deliberately swallows everything. A watcher that dies because it could not
    talk to the notification service has failed at its only job, which is to
    tell a human the outage is over.
    """
    if platform.system() != "Darwin":
        logger.info("desktop notifications need macOS; skipping")
        return False
    try:
        # Passed as argv rather than interpolated into the script text, so a
        # quote or backslash in a reason can never break the AppleScript.
        subprocess.run(
            [
                "osascript",
                "-e",
                "on run argv",
                "-e",
                "display notification (item 1 of argv) with title (item 2 of argv)",
                "-e",
                "end run",
                message,
                title,
            ],
            capture_output=True,
            timeout=10,
            check=False,
        )
    except Exception as exc:
        logger.warning("desktop notification failed: %s", exc)
        return False
    return True


class Watcher:
    """Holds the availability state across polls and announces each flip once."""

    def __init__(self, notify_enabled: bool = True) -> None:
        self.notify_enabled = notify_enabled
        # None until the first probe: an analyst that answers the very first
        # probe has not *recovered*, and saying so would be a false alarm.
        self.was_available: bool | None = None

    def check(self) -> bool:
        """Run one probe, print one line, journal any transition. Returns availability."""
        available, reason = probe()
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")

        if available:
            if self.was_available is False:
                print(f"{stamp} the analyst is answering again.")
                self._recover()
            else:
                print(f"{stamp} the analyst answered the probe.")
        else:
            print(f"{stamp} the analyst is still unavailable. {reason}")
            self._still_down(reason)

        self.was_available = available
        return available

    def _recover(self) -> None:
        shown = (
            notify("Paper trading", "The analyst is answering again.")
            if self.notify_enabled
            else False
        )
        _record(
            "analyst_recovered",
            {
                "reason": "The analyst answered an availability probe again, so it can review trades.",
                "notified": shown,
                "model": jev.MODEL,
            },
        )

    def _still_down(self, reason: str) -> None:
        last = _last_logged("analyst_still_unavailable")
        now = time.time()
        if now - last < STILL_DOWN_THROTTLE_SECONDS:
            return
        _record("analyst_still_unavailable", {"reason": reason, "model": jev.MODEL})


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--interval", type=float, default=300, help="Seconds between probes (default 300)."
    )
    parser.add_argument(
        "--max-minutes",
        type=float,
        default=0,
        help="Stop after this many minutes; 0 runs until interrupted.",
    )
    parser.add_argument(
        "--once", action="store_true", help="One probe, then exit: 0 if available, 1 if not."
    )
    parser.add_argument(
        "--no-notify", action="store_true", help="Never raise a desktop notification."
    )
    args = parser.parse_args(argv)

    watcher = Watcher(notify_enabled=not args.no_notify)

    if args.once:
        return 0 if watcher.check() else 1

    interval = max(5.0, args.interval)
    deadline = time.time() + args.max_minutes * 60 if args.max_minutes > 0 else None
    print(f"Watching the analyst every {interval:.0f}s. Press Ctrl+C to stop.")
    try:
        while True:
            watcher.check()
            if deadline is not None and time.time() >= deadline:
                print("Watch time is up.")
                break
            time.sleep(interval)
    except KeyboardInterrupt:
        print("\nStopped watching the analyst.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
