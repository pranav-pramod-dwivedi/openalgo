"""AI analyst via Jev 1.13 Free through OpenCode Zen.

The analyst is a gate, not a signal source. When it cannot answer, callers must
refuse to trade rather than fall back to rules, so the reason it was unavailable
is recorded and surfaced in plain words instead of a status code.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path

from dotenv import load_dotenv

from utils.logging import get_logger

logger = get_logger(__name__)

# The worker's Zen key lives in .env (gitignored) so no key is ever committed.
# OPENCODE_API_KEY wins, then PAPER_JEV_API_KEY, then the opencode CLI's own auth.
load_dotenv(Path(__file__).resolve().parents[2] / ".env")

AUTH_PATH = os.path.expanduser("~/.local/share/opencode/auth.json")
ZEN_URL = "https://opencode.ai/zen/v1/systemone"
MODEL = "jev-1.13-free"

# Why the analyst was last unavailable, as an operator-facing sentence. Empty
# when the last call succeeded. Surfaced by the planner so a refusal explains
# itself instead of reading as "no edge".
_last_failure = ""


def failure_reason() -> str:
    """Plain-language reason the analyst could not answer, or '' if it can."""
    return _last_failure


def _key() -> str | None:
    for var in ("OPENCODE_API_KEY", "PAPER_JEV_API_KEY"):
        if os.getenv(var):
            return os.getenv(var)
    try:
        data = json.loads(open(AUTH_PATH).read())
        return data.get("opencode", {}).get("key")
    except Exception:
        return None


def _explain(exc: Exception, status: int | None, payload: str) -> str:
    """Turn a transport failure into one sentence an operator can act on."""
    if status == 401:
        return "The analyst's Zen key was rejected. Check the key in your .env file."
    if status == 429 or "FreeUsageLimit" in payload or "rate limit" in payload.lower():
        return (
            "The analyst's free daily allowance is used up, so it cannot review a "
            "trade right now. Add credits to the Zen account, or wait for the "
            "allowance to reset."
        )
    if status == 403:
        return (
            "The Zen account's free tier is not currently available for this model. "
            "Use a different analyst model or add credits."
        )
    if status is not None and status >= 500:
        return (
            "The analyst service is temporarily unavailable. Waiting is the whole "
            "of it; the next cycle will try again."
        )
    return (
        "The analyst could not be reached. Check the network and the Zen key in "
        "your .env file."
    )


def ask(state: str, questions: dict) -> dict | None:
    """Return Jev's typed decisions, or None when it cannot answer."""
    global _last_failure
    key = _key()
    if not key:
        _last_failure = (
            "No Zen key is configured. Add OPENCODE_API_KEY to your .env file for "
            "the analyst to review trades."
        )
        return None

    body = json.dumps({"model": MODEL, "state": state, "questions": questions}).encode()
    req = urllib.request.Request(
        ZEN_URL,
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "User-Agent": "openalgo-paper/1.0",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            _last_failure = ""
            return json.loads(r.read())
    except urllib.error.HTTPError as exc:
        payload = ""
        try:
            payload = exc.read().decode()[:400]
        except Exception:
            pass
        _last_failure = _explain(exc, exc.code, payload)
        logger.warning("Analyst call failed (%s): %s", exc.code, payload)
    except Exception as exc:
        _last_failure = _explain(exc, None, "")
        logger.warning("Analyst call failed: %s", exc)
    return None
