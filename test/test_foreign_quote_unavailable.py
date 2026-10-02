"""A failed foreign quote must be reported as unavailable, never as a price.

services/foreign_data_service.fetch_crypto_quote used to answer a failed
Binance call with a hardcoded stand-in (76500 for BTC, 2800 for ETH, 100 for
anything else). Every consumer then priced at that constant during an outage,
with nothing in the payload saying the number was invented. These tests pin
the replacement: None out of the service, and every caller surfacing that as
unavailable instead of substituting a number of its own.
"""

import pytest

from services import foreign_data_service as fds


class _DeadResponse:
    """An HTTP error from the exchange feed."""

    status_code = 503


@pytest.fixture
def dead_feed(monkeypatch):
    """Make every outbound HTTP call fail and clear the quote cache."""
    monkeypatch.setattr(fds, "_QUOTE_CACHE", {})

    def _boom(*args, **kwargs):
        raise ConnectionError("exchange feed unreachable")

    monkeypatch.setattr(fds.requests, "get", _boom)
    return True


def test_fetch_crypto_quote_returns_none_when_feed_fails(dead_feed):
    assert fds.fetch_crypto_quote("BTCUSDT") is None


def test_fetch_crypto_quote_returns_none_on_http_error(monkeypatch):
    monkeypatch.setattr(fds, "_QUOTE_CACHE", {})
    monkeypatch.setattr(fds.requests, "get", lambda *a, **k: _DeadResponse())
    assert fds.fetch_crypto_quote("BTCUSDT") is None


def test_fetch_crypto_quote_does_not_reuse_a_stale_cache_entry_as_live(monkeypatch):
    """A cached quote past its TTL is a last-known price, not a live one."""
    monkeypatch.setattr(fds, "_QUOTE_CACHE", {})
    monkeypatch.setattr(fds.requests, "get", lambda *a, **k: _DeadResponse())

    stale = {"ltp": 50000.0, "open": 1.0, "high": 1.0, "low": 1.0, "volume": 0, "prev_close": 1.0}
    fds._QUOTE_CACHE["CRYPTO:BTCUSDT"] = (0.0, stale)  # timestamp 0.0 is long expired

    assert fds.fetch_crypto_quote("BTCUSDT") is None


def test_get_foreign_quote_propagates_none(dead_feed):
    assert fds.get_foreign_quote("BTCUSDT", "CRYPTO") is None


def test_get_foreign_quote_still_raises_for_an_unknown_exchange(dead_feed):
    with pytest.raises(ValueError):
        fds.get_foreign_quote("AAPL", "NASDAQ")


def test_get_quotes_reports_unavailable_rather_than_a_number(dead_feed):
    """The quotes API must not answer a broken feed with a successful price."""
    from services import quotes_service

    success, response, status_code = quotes_service.get_quotes(symbol="BTCUSDT", exchange="CRYPTO")

    assert success is False
    assert status_code == 503
    assert "unavailable" in response["message"].lower()
    assert "data" not in response


def test_get_quotes_with_auth_reports_unavailable_rather_than_a_number(dead_feed):
    from services import quotes_service

    success, response, status_code = quotes_service.get_quotes_with_auth(
        "token", None, "somebroker", "BTCUSDT", "CRYPTO"
    )

    assert success is False
    assert status_code == 503
    assert "data" not in response


def test_quotes_api_keeps_returning_live_prices_when_the_feed_works(monkeypatch):
    """The failure path must not become the only path."""
    from services import quotes_service

    monkeypatch.setattr(fds, "_QUOTE_CACHE", {})

    class _Ok:
        status_code = 200

        @staticmethod
        def json():
            return {
                "lastPrice": "65000.0",
                "openPrice": "64000.0",
                "highPrice": "66000.0",
                "lowPrice": "63000.0",
                "volume": "12",
                "prevClosePrice": "64500.0",
            }

    monkeypatch.setattr(fds.requests, "get", lambda *a, **k: _Ok())

    success, response, status_code = quotes_service.get_quotes(symbol="BTCUSDT", exchange="CRYPTO")

    assert (success, status_code) == (True, 200)
    assert response["data"]["ltp"] == 65000.0


def test_paper_mark_keeps_the_last_known_price_but_flags_it_not_live(monkeypatch):
    """The paper engine must not pass a stale mark off as a live one."""
    from services.paper import engine

    monkeypatch.setattr(engine, "_mark", engine._mark)
    monkeypatch.setattr(
        fds, "get_foreign_quote", lambda symbol, exchange: None, raising=True
    )

    mark, live = engine._mark("BTCUSDT", 50000.0)

    assert mark == 50000.0
    assert live is False


def test_paper_mark_reports_live_when_the_feed_works(monkeypatch):
    from services.paper import engine

    monkeypatch.setattr(fds, "get_foreign_quote", lambda symbol, exchange: {"ltp": 51000.0})

    mark, live = engine._mark("BTCUSDT", 50000.0)

    assert mark == 51000.0
    assert live is True
