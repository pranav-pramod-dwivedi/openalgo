"""Proof script: a dead exchange feed yields None, not a stand-in price.

Run: uv run python scripts/proof_foreign_quote_outage.py

Monkeypatches the HTTP client so every outbound call raises, then shows what
each consumer of the quote now does during an outage. Before the fix this
printed 76500.0 (BTC), 2800.0 (ETH) and 100.0 for everything else.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("API_KEY_PEPPER", "0" * 64)
os.environ.setdefault("APP_KEY", "proof-script-only")
os.environ["DATABASE_URL"] = "sqlite:///db/openalgo-proof.db"

from services import foreign_data_service as fds  # noqa: E402


def dead_get(*args, **kwargs):
    raise ConnectionError("exchange feed unreachable")


fds.requests.get = dead_get
fds._QUOTE_CACHE = {}
fds._RATES_CACHE = {}

from services import quotes_service  # noqa: E402
from services.paper import engine  # noqa: E402

ok = True

for symbol in ("BTCUSDT", "ETHUSDT", "SOLUSDT"):
    quote = fds.fetch_crypto_quote(symbol)
    print(f"fetch_crypto_quote({symbol}) -> {quote!r}")
    ok = ok and quote is None

quote = fds.get_foreign_quote("BTCUSDT", "CRYPTO")
print(f"get_foreign_quote(BTCUSDT, CRYPTO) -> {quote!r}")
ok = ok and quote is None

success, response, status = quotes_service.get_quotes(symbol="BTCUSDT", exchange="CRYPTO")
print(f"get_quotes(...) -> success={success} status={status} message={response['message']!r}")
ok = ok and success is False and status == 503

mark, live = engine._mark("BTCUSDT", 50000.0)
print(f"paper _mark(BTCUSDT, fallback=50000.0) -> mark={mark} mark_live={live}")
ok = ok and live is False

print("\nRESULT:", "PASS - no fabricated prices" if ok else "FAIL - a fabricated price survived")
sys.exit(0 if ok else 1)
