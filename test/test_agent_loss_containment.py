"""Loss-containment guard proofs, with the broker mocked at the guard boundary.

The guard never fetches market data itself: the caller hands it `ltp` and
`available_funds`. Each test injects failures or seeds persisted state
directly, which is the mocked-broker seam the rest of the guard already used.
"""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from database import agent_db
from services.agent import settings as agent_settings
from services.agent.safety.risk import RiskCode, RiskGuard, clear_guards


@pytest.fixture(autouse=True)
def _clean_risk_state():
    agent_db.init_db()
    agent_db.clear_risk_counters()
    clear_guards()
    agent_settings.invalidate_cache()
    agent_db.clear_agent_cache()
    yield
    agent_db.clear_risk_counters()
    clear_guards()
    agent_settings.invalidate_cache()
    agent_db.clear_agent_cache()


def _today() -> str:
    return datetime.now(UTC).date().isoformat()


def _limits(**overrides):
    base = agent_settings.default_risk_limits()
    base = dataclasses.replace(base, trading_enabled=True, require_analyzer_mode=False)
    if overrides:
        base = dataclasses.replace(base, **overrides)
    return base


def _guard(limits, *, clock=None, session_id="loss-containment-test"):
    return RiskGuard(session_id=session_id, limits=limits, clock=clock)


def _buy(guard, symbol, *, ltp=Decimal("10"), funds=Decimal("1000"), qty=1):
    return guard.check_order(
        symbol=symbol,
        exchange="NSE",
        action="BUY",
        quantity=qty,
        product="CNC",
        ltp=ltp,
        available_funds=funds,
    )


def _sell(guard, symbol, *, ltp=Decimal("10"), funds=Decimal("1000"), qty=1):
    return guard.check_order(
        symbol=symbol,
        exchange="NSE",
        action="SELL",
        quantity=qty,
        product="CNC",
        ltp=ltp,
        available_funds=funds,
    )


def test_daily_loss_trips_the_halt():
    limits = _limits()
    guard = _guard(limits)

    agent_db.add_daily_realised_pnl(_today(), Decimal("-10"))

    blocked = _buy(guard, "LCLOSS1")
    assert not blocked.allowed
    assert blocked.code == RiskCode.DAILY_LOSS

    close_like = _sell(guard, "LCLOSS1")
    assert close_like.allowed


def test_cooldown_blocks_immediate_reentry():
    limits = _limits(cooldown_minutes=15)
    guard = _guard(limits)

    agent_db.record_symbol_exit("LCOOL1")

    blocked = _buy(guard, "LCOOL1")
    assert not blocked.allowed
    assert blocked.code == RiskCode.COOLDOWN

    allowed = _buy(guard, "LCOOL2")
    assert allowed.allowed


def test_cooldown_expires():
    limits = _limits(cooldown_minutes=15)
    now = datetime.now(UTC)
    guard = _guard(limits, clock=lambda: now)

    agent_db.record_symbol_exit("LCEX1", when=now - timedelta(minutes=16))

    allowed = _buy(guard, "LCEX1")
    assert allowed.allowed


def test_max_position_blocks_second_concurrent_position():
    limits = _limits()
    guard = _guard(limits)

    first = _buy(guard, "LCPOS1", qty=5)
    assert first.allowed

    second = _buy(guard, "LCPOS1", qty=1)
    assert not second.allowed
    assert second.code == RiskCode.MAX_POSITION

    assert agent_db.get_open_position("LCPOS1") == Decimal("50")


def test_exposure_cap_blocks_second_concurrent_position():
    limits = _limits()
    guard = _guard(limits)

    first = _buy(guard, "LCEXP1", qty=5)
    assert first.allowed

    second = _buy(guard, "LCEXP2", qty=6)
    assert not second.allowed
    assert second.code == RiskCode.EXPOSURE_CAP

    # The blocked claim must not have consumed persisted budget.
    assert agent_db.get_daily_order_count(_today()) == 1


def test_fetch_failure_blocks_increases_but_allows_closes():
    limits = _limits()
    guard = _guard(limits)

    buy_no_ltp = _buy(guard, "LCFETCH1", ltp=None)
    assert not buy_no_ltp.allowed
    assert buy_no_ltp.code == RiskCode.PRICE_UNAVAILABLE

    buy_no_funds = _buy(guard, "LCFETCH1", funds=None)
    assert not buy_no_funds.allowed
    assert buy_no_funds.code == RiskCode.FUNDS_UNAVAILABLE

    sell_no_ltp = _sell(guard, "LCFETCH1", ltp=None, funds=None)
    assert sell_no_ltp.allowed
    assert "warnings" in sell_no_ltp.details


def test_counters_survive_a_simulated_restart():
    limits = _limits()

    agent_db.add_daily_realised_pnl(_today(), Decimal("-10"))
    agent_db.record_symbol_exit("LCREST1")
    agent_db.add_open_position("LCREST2", Decimal("50"))

    # A simulated restart: every in-process cache and every guard is dropped;
    # only the database survives.
    clear_guards()
    agent_settings.invalidate_cache()
    agent_db.clear_agent_cache()

    guard = _guard(limits, session_id="loss-containment-restarted")

    blocked_loss = _buy(guard, "LCREST3")
    assert not blocked_loss.allowed
    assert blocked_loss.code == RiskCode.DAILY_LOSS

    # Clear the loss so the other persisted rules can be checked in turn.
    agent_db.add_daily_realised_pnl(_today(), Decimal("10"))
    agent_settings.invalidate_cache()
    agent_db.clear_agent_cache()

    still_blocked_cooldown = _buy(guard, "LCREST1")
    assert not still_blocked_cooldown.allowed
    assert still_blocked_cooldown.code == RiskCode.COOLDOWN

    second_position = _buy(guard, "LCREST2", qty=5)
    assert not second_position.allowed
    assert second_position.code == RiskCode.MAX_POSITION

    cap_blocked = _buy(guard, "LCREST4", qty=6)
    assert not cap_blocked.allowed
    assert cap_blocked.code == RiskCode.EXPOSURE_CAP


def test_session_budget_persists_across_restart():
    limits = _limits(max_orders_per_session=5)

    for i in range(5):
        verdict = _guard(limits, session_id="budget-a").check_order(
            symbol=f"LCBUDG{i}",
            exchange="NSE",
            action="BUY",
            quantity=1,
            product="CNC",
            ltp=Decimal("10"),
            available_funds=Decimal("1000"),
        )
        assert verdict.allowed, verdict.reason

    # Restart simulation: a brand new guard instance reads the same persisted count.
    guard = _guard(limits, session_id="budget-b")
    blocked = guard.check_order(
        symbol="LCBUDG5",
        exchange="NSE",
        action="BUY",
        quantity=1,
        product="CNC",
        ltp=Decimal("10"),
        available_funds=Decimal("1000"),
    )
    assert not blocked.allowed
    assert blocked.code == RiskCode.SESSION_CAP_REACHED
