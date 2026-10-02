from __future__ import annotations

import json
import os
import time
import uuid

from utils.logging import get_logger

from . import config, db, jev
from .backtest import backtest_matrix, drawdown_pct, plan_matrix
from .strategies import FAMILIES, PARAM_GRID

logger = get_logger(__name__)

# One list of defaults, owned by the config module: the watchlist and the
# research timeframes are configuration, not engine literals.
DEFAULT_WATCHLIST = list(config.DEFAULT_SYMBOLS)
DEFAULT_INTERVAL = config.DEFAULT_INTERVAL
DEFAULT_RESEARCH_INTERVALS = list(config.DEFAULT_RESEARCH_INTERVALS)

# Kept as the module-level name other readers may still import. It is only the
# fallback now: the live watchlist comes from the config table on every cycle.
WATCHLIST = DEFAULT_WATCHLIST

# The timeframes the engine can be asked for. 15m and 1h are here as well as
# 5m, because research validates on them and a validated strategy has to be
# tradeable on the timeframe it was validated on.
ALLOWED_INTERVALS = ("1m", "3m", "5m", "15m", "30m", "1h")

# Research rotates the watchlist by this many places each cycle, so a capped
# cycle serves every symbol in turn instead of exhausting the first one forever.
KEY_RESEARCH_CURSOR = "research_cursor"
MIRROR_HYPOTHESIS = "research-symbol-mirror"

# Quote payloads differ by provider, so a mark is taken from the first key that
# actually carries a price rather than assuming one shape.
MARK_KEYS = ("last", "ltp", "price", "lastPrice", "mark_price", "close")

DEFAULT_CASH = 1000.0

# Cash and equity conventions
# --------------------------
# ``config.cash`` is the live balance and the single source of truth. Every
# simulated fill moves it:
#
#   * BUY  (opening a long, or covering a short)  cash -= price*qty + fee
#   * SELL (closing a long, or opening a short)   cash += price*qty - fee
#
# equity = cash + unrealized - short_margin_locked
#
# Short margin convention: a short entry credits the full sale proceeds to cash,
# because that is what a broker credits, and simultaneously posts the full entry
# notional as margin (``short_margin_locked``). The locked notional is not
# spendable -- it is the margin backing the short -- and it is released when the
# short is covered. It is also excluded from equity, otherwise the sale proceeds
# would be counted twice: once inside ``cash`` and once through ``unrealized``.
# With no short open the lock is zero and equity is exactly cash + unrealized.
#
# Realized P&L and fees keep their own running totals for the dashboard. They are
# reporting figures, never inputs to equity.


def _fee_rate(cfg: dict) -> float:
    return float(cfg.get("fee_bps", 4)) / 10_000.0


def _slip_rate(cfg: dict) -> float:
    return float(cfg.get("slippage_bps", 2)) / 10_000.0


def _cash_state(cfg: dict) -> tuple[float, float]:
    """Return (cash, short_margin_locked) from a config mapping."""
    cash = float(cfg.get("cash", cfg.get("starting_cash", DEFAULT_CASH)))
    return cash, float(cfg.get("short_margin_locked", 0.0))


def equity_of(cfg: dict, unrealized: float) -> float:
    """Equity is the live cash plus open P&L, less margin locked against shorts."""
    cash, locked = _cash_state(cfg)
    return cash + unrealized - locked


def available_cash(cfg: dict) -> float:
    """Cash a new position may draw on: the balance less margin locked on shorts."""
    cash, locked = _cash_state(cfg)
    return cash - locked


def plan_size(cfg: dict, price: float, side: str) -> tuple[float, str]:
    """Size a new position from the configured caps and the cash actually left.

    Returns ``(qty, reason)``. ``qty == 0`` means take no trade, and ``reason``
    is written to the decision log so a refusal is visible rather than silent.

    The configured caps decide the intended size -- exposure on the free balance
    and the per-position cap -- and the balance then has to fund it. When it
    cannot, the trade is refused rather than quietly shrunk: a strategy whose own
    cap the account can no longer afford is information the operator needs, not
    something to paper over with a dust position.
    """
    if price <= 0:
        return 0.0, "invalid_price"
    cash = available_cash(cfg)
    if cash <= 0:
        return 0.0, "no_cash"
    max_qty = float(cfg.get("max_position_qty", 0.01))
    exposure = float(cfg.get("max_exposure_pct", 0.5))
    qty = min(max_qty, max(0.0, cash * exposure) / price)
    if qty <= 0:
        return 0.0, "capped_by_config"
    # What the balance can fund. A buy must also pay the fee out of the same
    # cash; a short posts the full notional as margin, so the whole notional has
    # to be free even though only the fee leaves the balance.
    if side == "BUY":
        affordable = cash / (price * (1 + _fee_rate(cfg)))
    else:
        affordable = cash / price
    if qty > affordable:
        return 0.0, "insufficient_cash"
    return qty, "ok"


class IntervalNotAllowed(ValueError):
    """The requested candle timeframe is not one the paper engine can use."""


def validate_interval(value) -> str:
    """Return the timeframe, or raise IntervalNotAllowed naming what is allowed.

    An unrecognised timeframe is refused rather than quietly replaced by the
    default, because a silent downgrade looks like working data at the wrong
    resolution.
    """
    iv = str(value or "").strip()
    if iv not in ALLOWED_INTERVALS:
        raise IntervalNotAllowed(
            f"Candle interval '{value}' is not supported by paper trading. "
            f"Choose one of: {', '.join(ALLOWED_INTERVALS)}."
        )
    return iv


def resolve_watchlist() -> list[str]:
    """The configured watchlist, read fresh so a config change lands next cycle."""
    try:
        stored = db.get("symbols", None)
    except Exception:
        return list(DEFAULT_WATCHLIST)
    if isinstance(stored, str):
        parts = [p.strip().upper() for p in stored.replace(";", ",").split(",")]
    elif isinstance(stored, (list, tuple)):
        parts = [str(p).strip().upper() for p in stored]
    else:
        return list(DEFAULT_WATCHLIST)
    return [p for p in parts if p] or list(DEFAULT_WATCHLIST)


def resolve_interval() -> str:
    """The configured candle timeframe, validated against the allowed set."""
    try:
        stored = db.get("interval", None)
    except Exception:
        return DEFAULT_INTERVAL
    if stored is None:
        return DEFAULT_INTERVAL
    return validate_interval(stored)


def resolve_research_intervals() -> list[str]:
    """The timeframes research validates each symbol on. Falls back to the default."""
    try:
        stored = db.get("research_intervals", None)
    except Exception:
        return list(DEFAULT_RESEARCH_INTERVALS)
    if stored is None:
        return list(DEFAULT_RESEARCH_INTERVALS)
    try:
        intervals = config.normalize_intervals(stored)
    except Exception:
        return list(DEFAULT_RESEARCH_INTERVALS)
    return [iv for iv in intervals if iv in ALLOWED_INTERVALS] or list(DEFAULT_RESEARCH_INTERVALS)


def resolve_experiment_cap() -> int:
    """The per-cycle backtest budget, so a wider grid cannot balloon a cycle."""
    try:
        stored = db.get("research_max_experiments", None)
    except Exception:
        return config.DEFAULT_RESEARCH_MAX_EXPERIMENTS
    if stored is None:
        return config.DEFAULT_RESEARCH_MAX_EXPERIMENTS
    try:
        return max(1, int(float(stored)))
    except (TypeError, ValueError):
        return config.DEFAULT_RESEARCH_MAX_EXPERIMENTS


def validation_bar() -> dict:
    """The configured bar a backtest must clear: trades, net P&L, drawdown."""
    try:
        return config.load().validation_bar()
    except Exception:
        return {
            "min_trades": config.DEFAULT_MIN_TRADES,
            "min_net_pnl": config.DEFAULT_MIN_NET_PNL,
            "max_drawdown_pct": config.DEFAULT_MAX_DRAWDOWN_PCT,
        }


def _settings(watchlist=None, interval=None) -> tuple[list[str], str]:
    """Per-call overrides win; anything omitted comes from the config table."""
    wl = list(watchlist) if watchlist else resolve_watchlist()
    iv = validate_interval(interval) if interval else resolve_interval()
    return wl, iv


def _candles(symbol: str, interval: str = DEFAULT_INTERVAL) -> list[dict]:
    from services.foreign_data_service import get_foreign_history

    try:
        return get_foreign_history(symbol, "CRYPTO", validate_interval(interval), "", "")
    except IntervalNotAllowed:
        raise
    except Exception:
        return []


def _mark(symbol: str, fallback: float | None = None) -> tuple[float | None, bool]:
    """Mark for a symbol as (price, is_live).

    ``get_foreign_quote`` returns None when the upstream feed could not be
    reached, so there is no live price. In that case the last known mark is
    returned (it is a real price, not an invented one) with ``is_live`` False,
    so callers label it stale instead of presenting it as current.
    """
    from services.foreign_data_service import get_foreign_quote

    try:
        quote = get_foreign_quote(symbol, "CRYPTO")
    except Exception:
        logger.warning(f"Mark unavailable for {symbol}, keeping last known")
        return fallback, False

    if not isinstance(quote, dict):
        return fallback, False

    for key in MARK_KEYS:
        raw = quote.get(key)
        if raw is None:
            continue
        try:
            value = float(raw)
        except (TypeError, ValueError):
            continue
        if value > 0:
            return value, True
    return fallback, False


def mark_open_positions() -> tuple[list[dict], float]:
    """Mark every open position to market and return (positions, unrealized).

    A failed quote keeps the last known mark rather than dropping the position,
    so a provider outage shows a stale price instead of a vanishing trade. Such
    a row carries ``mark_live: False`` so no consumer presents it as current.
    """
    with db.conn() as c:
        rows = c.execute("SELECT * FROM positions WHERE status='open'").fetchall()
        marks = []
        unrealized = 0.0
        updates = []
        for r in rows:
            mark, mark_live = _mark(
                r["symbol"], r["mark"] if "mark" in r.keys() else r["entry"]
            )
            if mark is None:
                # No last known mark and no live price: nothing to mark to.
                continue
            entry, qty = float(r["entry"]), float(r["qty"])
            pnl = (mark - entry) * qty if r["side"] == "BUY" else (entry - mark) * qty
            unrealized += pnl
            if mark_live:
                updates.append((mark, r["symbol"]))
            marks.append(
                {
                    "symbol": r["symbol"],
                    "side": r["side"],
                    "qty": qty,
                    "entry": entry,
                    "mark": mark,
                    "mark_live": mark_live,
                    "unrealized": round(pnl, 6),
                    "strategy_id": r["strategy_id"],
                    "opened": r["opened"],
                }
            )
        if updates:
            c.executemany("UPDATE positions SET mark=? WHERE symbol=?", updates)
    return marks, unrealized


def strategy_id(family: str, symbol: str, interval: str) -> str:
    """Build a strategy id. The timeframe is part of it, not a note beside it.

    ``donchian-BTCUSDT-15m`` says what was validated and where. Without the
    suffix a 15m-validated 20-period lookback would be applied to 5m candles as
    if it were the same strategy, and it is not.
    """
    return f"{family}-{symbol}-{interval}"


def parse_strategy_id(sid: str) -> tuple[str, str, str | None]:
    """Split a strategy id into ``(family, symbol, interval)``.

    The interval is ``None`` for rows registered before it was recorded; those
    stay tradable, because refusing them would silently unregister strategies
    that an older research cycle legitimately validated.
    """
    text = str(sid or "")
    family, _, rest = text.partition("-")
    if not rest:
        return text, "", None
    symbol, sep, interval = rest.rpartition("-")
    if sep and symbol and interval in ALLOWED_INTERVALS:
        return family, symbol, interval
    return family, rest, None


def passes_validation(metrics: dict, bar: dict | None = None) -> tuple[bool, str]:
    """Return ``(passed, reason)`` for one backtest result.

    The configured bar sets the size of the hurdle, not whether a loss can pass
    it. A negative net P&L is refused here whatever ``min_net_pnl`` says: a
    losing backtest registered as active is the engine trading a known loser.
    """
    bar = bar or validation_bar()
    trades = int(metrics.get("trades", 0) or 0)
    net = float(metrics.get("net_pnl", 0.0) or 0.0)
    dd_pct = drawdown_pct(metrics)
    if net <= 0:
        return False, f"net_pnl {net:g} is not positive"
    min_net = float(bar.get("min_net_pnl", 0.0) or 0.0)
    if net < min_net:
        return False, f"net_pnl {net:g} is below the minimum {min_net:g}"
    min_trades = int(bar.get("min_trades", 0) or 0)
    if trades < min_trades:
        return False, f"trades {trades} is below the minimum {min_trades}"
    max_dd = float(bar.get("max_drawdown_pct", 100.0) or 100.0)
    if dd_pct >= max_dd:
        return False, f"drawdown {dd_pct:g}% is at or above the {max_dd:g}% limit"
    return True, "ok"


def _rotate(values: list, cursor: int) -> list:
    """Rotate a list by ``cursor`` places, wrapping. Keeps a capped cycle fair."""
    if not values:
        return []
    start = cursor % len(values)
    return values[start:] + values[:start]


def research_cycle(watchlist=None, interval=None) -> None:
    """Backtest the grid across symbols and timeframes, register what clears the bar.

    A cycle is bounded twice: by the per-cycle experiment cap, and by the fact
    that whatever the cap leaves out is counted and logged rather than run
    quietly forever. Both bounds matter because the grid now covers nine symbols
    on three timeframes.
    """
    watchlist, interval = _settings(watchlist, interval)
    intervals = resolve_research_intervals()
    cap = resolve_experiment_cap()
    bar = validation_bar()
    cursor = 0
    try:
        cursor = int(db.get(KEY_RESEARCH_CURSOR, 0) or 0)
    except Exception:
        cursor = 0
    order = _rotate(list(watchlist), cursor)
    db.log(
        "research_start",
        {
            "watchlist": watchlist,
            "interval": interval,
            "research_intervals": intervals,
            "max_experiments": cap,
            "bar": bar,
        },
    )

    experiments: list[tuple] = []
    registered: dict[str, dict] = {}
    skipped: list[dict] = []
    truncated = 0
    failed: list[dict] = []

    for symbol in order:
        remaining = cap - len(experiments)
        if remaining <= 0:
            # The budget is spent. Everything left is named in the summary
            # below rather than silently dropped, and the next cycle starts one
            # symbol further along so no symbol is left permanently unresearched.
            truncated += len(plan_matrix(intervals))
            continue
        result = backtest_matrix(
            symbol,
            intervals,
            lambda sym, iv: _candles(sym, iv),
            max_experiments=remaining,
        )
        truncated += result["truncated"]
        for skip in result["skipped"]:
            skipped.append({"symbol": symbol, **skip})
        for exp in result["experiments"]:
            metrics = exp["metrics"]
            experiments.append(
                (
                    _experiment_id(exp["family"], symbol, exp["interval"], exp["params"]),
                    "research",
                    json.dumps(
                        {"symbol": symbol, "interval": exp["interval"], "params": exp["params"]}
                    ),
                    json.dumps(metrics),
                    time.time(),
                    "ok" if metrics["trades"] > 0 else "no_trades",
                )
            )
            passed, why = passes_validation(metrics, bar)
            if not passed:
                failed.append(
                    {
                        "family": exp["family"],
                        "symbol": symbol,
                        "interval": exp["interval"],
                        "reason": why,
                    }
                )
                continue
            sid = strategy_id(exp["family"], symbol, exp["interval"])
            previous = registered.get(sid)
            if previous is None or _better(metrics, previous["metrics"]):
                registered[sid] = {
                    "family": exp["family"],
                    "symbol": symbol,
                    "interval": exp["interval"],
                    "params": exp["params"],
                    "metrics": metrics,
                }

    rows = [
        (
            sid,
            "research",
            entry["family"],
            json.dumps(entry["params"]),
            json.dumps(entry["metrics"]),
            "active",
            time.time(),
            1,
        )
        for sid, entry in registered.items()
    ]
    rows.extend(_mirror_rows(registered, interval))
    with db.conn() as c:
        for e in experiments:
            c.execute("INSERT OR REPLACE INTO experiments VALUES(?,?,?,?,?,?)", e)
        for st in rows:
            c.execute("INSERT OR REPLACE INTO strategies VALUES(?,?,?,?,?,?,?,?)", st)
    if watchlist:
        db.setc(KEY_RESEARCH_CURSOR, (cursor + 1) % len(watchlist))

    for sid, entry in registered.items():
        db.log(
            "strategy_registered",
            {
                "id": sid,
                "interval": entry["interval"],
                "params": entry["params"],
                "metrics": entry["metrics"],
            },
        )
    summary = {
        "symbols": order,
        "research_intervals": intervals,
        "experiments": len(experiments),
        "registered": len(registered),
        "skipped": len(skipped),
        "truncated": truncated,
        "cap": cap,
        "rejected": len(failed),
    }
    if truncated:
        # The cap is a decision the operator made, so the cycle says it happened.
        logger.info(
            f"Research hit the per-cycle cap of {cap} experiments; "
            f"{truncated} planned experiments were left for the next cycle"
        )
        db.log("research_capped", summary)
    if skipped:
        logger.info(
            f"Research skipped {len(skipped)} symbol/timeframe combinations with no usable candles"
        )
    db.log("research_done", summary)
    return summary


def _experiment_id(family: str, symbol: str, interval: str, params: dict) -> str:
    """A stable id for one experiment, so a re-run replaces rather than piles up."""
    key = json.dumps({"f": family, "s": symbol, "i": interval, "p": params}, sort_keys=True)
    return f"exp-{family}-{symbol}-{interval}-{abs(hash(key)) % 10_000_000}"


def _better(metrics: dict, incumbent: dict) -> bool:
    """Rank two passing results for the same strategy: P&L first, trades to break ties."""
    if float(metrics.get("net_pnl", 0.0)) != float(incumbent.get("net_pnl", 0.0)):
        return float(metrics.get("net_pnl", 0.0)) > float(incumbent.get("net_pnl", 0.0))
    return int(metrics.get("trades", 0)) > int(incumbent.get("trades", 0))


def _mirror_rows(registered: dict, trading_interval: str) -> list:
    """Symbol-level rows in the legacy ``family-SYMBOL`` shape, for the planner.

    ``planner._strategies_for`` matches a strategy to a symbol by taking
    everything after the first dash of its id, so a row carrying a timeframe in
    its id is invisible to it. planner.py is not owned here and was not changed,
    so the best validated strategy per family and symbol is also written under
    the legacy id, preferring the timeframe the worker actually trades. These
    rows carry the same metrics and are re-validated by the planner on its own
    bar before anything is traded.
    """
    best: dict[tuple[str, str], dict] = {}
    for entry in registered.values():
        key = (entry["family"], entry["symbol"])
        current = best.get(key)
        if current is None:
            best[key] = entry
            continue
        same_interval = entry["interval"] == current["interval"]
        wants_trading = entry["interval"] == trading_interval
        current_wants = current["interval"] == trading_interval
        if wants_trading and not current_wants:
            best[key] = entry
        elif same_interval and _better(entry["metrics"], current["metrics"]):
            best[key] = entry
        elif (
            wants_trading == current_wants
            and not same_interval
            and _better(entry["metrics"], current["metrics"])
        ):
            best[key] = entry

    rows = []
    for (family, symbol), entry in best.items():
        rows.append(
            (
                f"{family}-{symbol}",
                MIRROR_HYPOTHESIS,
                family,
                json.dumps(entry["params"]),
                json.dumps(entry["metrics"]),
                "active",
                time.time(),
                1,
            )
        )
        db.log(
            "strategy_mirrored",
            {
                "id": f"{family}-{symbol}",
                "from": strategy_id(family, symbol, entry["interval"]),
                "params": entry["params"],
                "metrics": entry["metrics"],
            },
        )
    return rows


def trading_cycle(watchlist=None, interval=None) -> None:
    """Signal -> paper order -> fill for active strategies.

    A strategy registered by an earlier research cycle can outlive a watchlist
    change, so symbols outside the current watchlist are skipped rather than
    traded. A strategy validated on one timeframe is also skipped on another:
    the id carries the timeframe it was validated on, and a setup tuned to 15m
    candles is a different strategy when fed 5m ones.
    """
    db.init()
    # The position write below carries a mark, so the column must exist before
    # the first signal rather than waiting for _record_equity at cycle end.
    db.ensure_column("positions", "mark", "REAL")
    watchlist, interval = _settings(watchlist, interval)
    mismatched = 0
    with db.conn() as c:
        strats = c.execute("SELECT * FROM strategies WHERE status='active'").fetchall()
    for s in strats:
        family, symbol, validated_on = parse_strategy_id(s["id"])
        if not symbol:
            continue
        if symbol not in watchlist:
            continue
        if validated_on is not None and validated_on != interval:
            mismatched += 1
            continue
        c = _candles(symbol, interval)
        if len(c) < 30:
            continue
        fn = FAMILIES[family]
        sig = fn(c, **json.loads(s["params"]))
        if sig:
            state = f"{symbol} {family} interval={interval} signal={sig} last={float(c[-1]['close']):.2f} regime={json.loads(s['metrics']).get('net_pnl', 0)}"
            verdict = jev.ask(
                state,
                {
                    "trade": {"type": "score", "criteria": ["skip", "take"]},
                    "risk": {"type": "score", "criteria": ["safe", "risky"]},
                },
            )
            db.log("jev", {"symbol": symbol, "state": state, "verdict": verdict})
            if (
                verdict
                and verdict.get("answers", {}).get("trade", {}).get("probabilities", {}).get("1", 0)
                < 0.30
            ):
                continue
        with db.conn() as cc:
            pos = cc.execute(
                "SELECT * FROM positions WHERE symbol=? AND status='open'", (symbol,)
            ).fetchone()
            if (
                pos
                and sig
                and (
                    (sig == "SELL" and pos["side"] == "BUY")
                    or (sig == "BUY" and pos["side"] == "SELL")
                )
            ):
                _close(cc, pos, float(c[-1]["close"]), s["id"])
            elif not pos and sig:
                last = float(c[-1]["close"])
                cfg = db.get_all(cc)
                px = last * (1 + _slip_rate(cfg))
                qty, reason = plan_size(cfg, px, sig)
                if qty <= 0:
                    # Refused: log why rather than trading on money that is not
                    # there. No order, no fill, no position.
                    db.log(
                        "paper_refused",
                        {
                            "symbol": symbol,
                            "side": sig,
                            "price": round(px, 6),
                            "reason": reason,
                            "cash": round(cfg.get("cash", 0.0), 6),
                            "available": round(available_cash(cfg), 6),
                            "locked_margin": round(cfg.get("short_margin_locked", 0.0), 6),
                            "strategy": s["id"],
                        },
                        cc,
                    )
                    continue
                oid = str(uuid.uuid4())[:8]
                fee = px * qty * _fee_rate(cfg)
                # Cash moves on the fill, inside this transaction. A buy
                # debits notional plus fee; a short entry credits the sale
                # proceeds and posts the notional as margin.
                delta = -(px * qty + fee) if sig == "BUY" else (px * qty - fee)
                db.move_cash(delta, cc)
                if sig == "SELL":
                    cfg["short_margin_locked"] = float(
                        cfg.get("short_margin_locked", 0.0)
                    ) + px * qty
                    db.set_margin(cfg["short_margin_locked"], cc)
                cc.execute(
                    "INSERT INTO orders VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (
                        oid,
                        symbol,
                        sig,
                        qty,
                        "MARKET",
                        "filled",
                        time.time(),
                        time.time(),
                        s["id"],
                        "paper signal",
                    ),
                )
                cc.execute(
                    "INSERT INTO fills(order_id,symbol,side,qty,price,fee,slippage,ts) VALUES(?,?,?,?,?,?,?,?)",
                    (oid, symbol, sig, qty, px, fee, px * _slip_rate(cfg), time.time()),
                )
                cc.execute(
                    "INSERT OR REPLACE INTO positions"
                    "(symbol,side,qty,entry,opened,strategy_id,sl,tp,status,mark)"
                    " VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (
                        symbol,
                        sig,
                        qty,
                        px,
                        time.time(),
                        s["id"],
                        px * 0.998 if sig == "BUY" else px * 1.002,
                        px * 1.004 if sig == "BUY" else px * 0.996,
                        "open",
                        px,
                    ),
                )
                db.log(
                    "paper_order",
                    {"symbol": symbol, "side": sig, "qty": qty, "strategy": s["id"]},
                    cc,
                )
    if mismatched:
        # Strategies validated on other timeframes stay registered and simply do
        # not apply here. Worth saying out loud, because otherwise a widened
        # research grid looks like it produced fewer signals than it did.
        logger.info(
            f"Skipped {mismatched} active strategies validated on a timeframe other than {interval}"
        )
    _record_equity()


def _record_equity() -> dict:
    """Mark to market, carry realized/fees/peak forward, and append an equity row."""
    db.ensure_column("positions", "mark", "REAL")
    marks, unrealized = mark_open_positions()
    cfg = db.get_all()
    cash, locked = _cash_state(cfg)
    realized = float(cfg.get("realized_total", 0.0))
    fees = float(cfg.get("fees_total", 0.0))
    equity = equity_of(cfg, unrealized)
    peak = max(float(cfg.get("peak_equity", 0.0)), equity)
    drawdown = (peak - equity) / peak * 100 if peak > 0 else 0.0
    db.set_many(
        {
            "realized_total": realized,
            "fees_total": fees,
            "peak_equity": peak,
            "last_equity": equity,
        }
    )
    with db.conn() as c:
        c.execute(
            "INSERT OR REPLACE INTO equity VALUES(?,?,?,?,?,?,?,?)",
            (time.time(), cash, equity, realized, unrealized, fees, 0.0, drawdown),
        )
    return {
        "marks": marks,
        "cash": cash,
        "equity": equity,
        "locked_margin": locked,
        "drawdown": drawdown,
        "peak": peak,
    }


def _close(cc, pos, px, sid):
    """Close an open position: move cash the other way and release any margin.

    Closing a long sells, so cash is credited the proceeds less the fee.
    Covering a short buys, so cash is debited the cost plus the fee, and the
    margin posted at the entry is released. move_cash refuses to take the
    balance below zero, which is the backstop against a losing cover.
    """
    qty = pos["qty"]
    entry = float(pos["entry"])
    pnl = (px - entry) * qty if pos["side"] == "BUY" else (entry - px) * qty
    cfg = db.get_all(cc)
    fee = px * qty * _fee_rate(cfg)
    delta = (px * qty - fee) if pos["side"] == "BUY" else -(px * qty + fee)
    try:
        new_cash = db.move_cash(delta, cc)
    except ValueError:
        db.log(
            "paper_refused",
            {
                "symbol": pos["symbol"],
                "side": "BUY" if pos["side"] == "SELL" else "SELL",
                "price": round(px, 6),
                "qty": qty,
                "reason": "insufficient_cash",
                "cash": round(cfg.get("cash", 0.0), 6),
                "cost": round(px * qty + fee, 6),
                "strategy": sid,
            },
            cc,
        )
        return None
    if pos["side"] == "SELL":
        # The short is covered, so the notional posted at entry comes off lock.
        db.set_margin(max(0.0, float(cfg.get("short_margin_locked", 0.0)) - entry * qty), cc)
    oid = "close-" + str(uuid.uuid4())[:8]
    cc.execute(
        "INSERT INTO orders VALUES(?,?,?,?,?,?,?,?,?,?)",
        (
            oid,
            pos["symbol"],
            "SELL" if pos["side"] == "BUY" else "BUY",
            qty,
            "MARKET",
            "filled",
            time.time(),
            time.time(),
            sid,
            "exit signal",
        ),
    )
    cc.execute(
        "INSERT INTO fills(order_id,symbol,side,qty,price,fee,slippage,ts) VALUES(?,?,?,?,?,?,?,?)",
        (
            oid,
            pos["symbol"],
            "SELL" if pos["side"] == "BUY" else "BUY",
            qty,
            px,
            fee,
            px * _slip_rate(cfg),
            time.time(),
        ),
    )
    cc.execute("UPDATE positions SET status='closed' WHERE symbol=?", (pos["symbol"],))
    net = pnl - fee
    db.set_many(
        {
            "realized_total": float(cfg.get("realized_total", 0.0)) + pnl,
            "fees_total": float(cfg.get("fees_total", 0.0)) + fee,
        },
        cc,
    )
    db.log(
        "paper_close",
        {"symbol": pos["symbol"], "pnl": round(net, 4), "cash": round(new_cash, 6)},
        cc,
    )
    return net


def get_state() -> dict:
    """Everything the paper panel shows, read fresh from the paper database."""
    db.init()
    db.ensure_column("positions", "mark", "REAL")
    marks, unrealized = mark_open_positions()
    with db.conn() as c:
        fills = c.execute(
            "SELECT order_id,symbol,side,qty,price,fee,slippage,ts FROM fills"
            " ORDER BY ts DESC LIMIT 50"
        ).fetchall()
        curve = c.execute(
            "SELECT ts,cash,equity,realized,unrealized,fees,slippage,drawdown"
            " FROM equity ORDER BY ts DESC LIMIT 50"
        ).fetchall()
        strats = c.execute(
            "SELECT id,family,params,metrics,status,created,version FROM strategies"
            " WHERE status='active' ORDER BY created DESC"
        ).fetchall()
        experiments = c.execute("SELECT COUNT(*) AS n FROM experiments").fetchone()
        hb = c.execute("SELECT ts,status,error,cycle FROM heartbeat WHERE id=1").fetchone()
        decisions = c.execute(
            "SELECT ts,kind,payload FROM decisions ORDER BY id DESC LIMIT 20"
        ).fetchall()
        jev_verdicts = c.execute("SELECT COUNT(*) AS n FROM decisions WHERE kind='jev'").fetchone()
    cfg = db.get_all()
    cash, locked = _cash_state(cfg)
    starting = float(cfg.get("starting_cash", DEFAULT_CASH))
    realized = float(cfg.get("realized_total", 0.0))
    fees = float(cfg.get("fees_total", 0.0))
    equity = equity_of(cfg, unrealized)
    peak = float(cfg.get("peak_equity", 0.0))
    return {
        # ``starting_cash`` is the capital the paper account started with and
        # never changes; ``cash`` is the real balance left right now, after
        # every fill. ``virtual_balance`` stays as the frontend's key for it.
        "starting_cash": starting,
        "cash": round(cash, 6),
        "virtual_balance": round(cash, 6),
        "short_margin_locked": round(locked, 6),
        "equity": round(equity, 6),
        "realized": round(realized, 6),
        "unrealized": round(unrealized, 6),
        "fees": round(fees, 6),
        "peak_equity": peak,
        "drawdown": round((peak - equity) / peak * 100, 4) if peak > 0 else 0.0,
        "positions": marks,
        "fills": [dict(f) for f in fills],
        "equity_curve": [dict(e) for e in curve],
        "strategies": [dict(s) for s in strats],
        "experiment_count": experiments["n"] if experiments else 0,
        "worker": dict(hb) if hb else None,
        "decisions": [
            {
                "ts": d["ts"],
                "kind": d["kind"],
                "payload": _safe_payload(d["payload"]),
            }
            for d in decisions
        ],
        "jev_verdicts": jev_verdicts["n"] if jev_verdicts else 0,
        "generated_at": time.time(),
    }


def _safe_payload(raw):
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return raw


def snapshot() -> dict:
    with db.conn() as c:
        pos = c.execute("SELECT * FROM positions WHERE status='open'").fetchall()
        eq = c.execute("SELECT * FROM equity ORDER BY ts DESC LIMIT 1").fetchone()
        hb = c.execute("SELECT * FROM heartbeat WHERE id=1").fetchone()
        strats = c.execute(
            "SELECT family,status,COUNT(*) AS n FROM strategies GROUP BY family,status"
        ).fetchall()
    return {
        "open_positions": len(pos),
        "latest_equity": dict(eq) if eq else None,
        "worker": dict(hb) if hb else None,
        "strategies": [dict(x) for x in strats],
    }
