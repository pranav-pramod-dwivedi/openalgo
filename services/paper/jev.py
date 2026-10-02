"""AI analyst via Jev 1.13 Free through OpenCode Zen."""

from __future__ import annotations

import json
import os
import urllib.request
from pathlib import Path

from dotenv import load_dotenv

# The worker's Zen key lives in .env (gitignored) so no key is ever committed.
# OPENCODE_API_KEY wins, then PAPER_JEV_API_KEY, then the opencode CLI's own auth.
load_dotenv(Path(__file__).resolve().parents[2] / ".env")

AUTH_PATH = os.path.expanduser("~/.local/share/opencode/auth.json")
ZEN_URL = "https://opencode.ai/zen/v1/systemone"


def _key() -> str | None:
    for var in ("OPENCODE_API_KEY", "PAPER_JEV_API_KEY"):
        if os.getenv(var):
            return os.getenv(var)
    try:
        data = json.loads(open(AUTH_PATH).read())
        return data.get("opencode", {}).get("key")
    except Exception:
        return None


def ask(state: str, questions: dict) -> dict | None:
    """Return Jev's typed decisions, or None if unavailable."""
    key = _key()
    if not key:
        return None
    body = json.dumps({"model": "jev-1.13-free", "state": state, "questions": questions}).encode()
    req = urllib.request.Request(ZEN_URL, data=body, method="POST", headers={
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "User-Agent": "openalgo-paper/1.0",
    })
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read())
    except Exception:
        return None
