from __future__ import annotations

import json
import os
import time
import uuid

from utils.logging import get_logger

from . import db, jev
from .backtest import backtest
from .strategies import FAMILIES, PARAM_GRID

logger = get_logger(__name__)

WATCHLIST = ["BTCUSDT", "SOLUSDT", "ETHUSDT"]

# Quote payloads differ by provider, so a mark is taken from the first key that
# actually carries a price rather than assuming one shape.
MARK_KEYS = ("last", "ltp", "price", "lastPrice", "mark_price", "close")


def _candles(symbol: str) -> list[dict]:
    from services.foreign_data_service import get_foreign_history

    try:
        return get_foreign_history(symbol, "CRYPTO", "5m", "", "")
    except Exception:
        return []


def _mark(symbol: str, fallback: float | None = None) -> float | None:
    """Live mark for a symbol, or None when no usable price came back."""
    from services.foreign_data_service import get_foreign_quote

    try:
        quote = get_foreign_quote(symbol, "CRYPTO")
    except Exception:
        logger.warning(f"Mark unavailable for {symbol}, keeping last known")
        return fallback
    if not isinstance(quote, dict):
        return fallback
    for key in MARK_KEYS:
        raw = quote.get(key)
        if raw is None:
            continue
        try:
            value = float(raw)
        except (TypeError, ValueError):
            continue
        if value > 0:
            return value
    return fallback


def mark_open_positions() -> tuple[list[dict], float]:
    """Mark every open position to market and return (positions, unrealized).

    A failed quote keeps the last known mark rather than dropping the position,
    so a provider outage shows a stale price instead of a vanishing trade.
    """
    with db.conn() as c:
        rows = c.execute("SELECT * FROM positions WHERE status='open'").fetchall()
        marks = []
        unrealized = 0.0
        updates = []
        for r in rows:
            mark = _mark(r["symbol"], r["mark"] if "mark" in r.keys() else r["entry"])
            entry, qty = float(r["entry"]), float(r["qty"])
            pnl = (mark - entry) * qty if r["side"] == "BUY" else (entry - mark) * qty
            unrealized += pnl
            updates.append((mark, r["symbol"]))
            marks.append(
                {
                    "symbol": r["symbol"],
                    "side": r["side"],
                    "qty": qty,
                    "entry": entry,
                    "mark": mark,
                    "unrealized": round(pnl, 6),
                    "strategy_id": r["strategy_id"],
                    "opened": r["opened"],
                }
            )
        if updates:
            c.executemany("UPDATE positions SET mark=? WHERE symbol=?", updates)
    return marks, unrealized


def research_cycle() -> None:
    """Generate experiments, backtest, register validated strategies."""
    db.log("research_start", {"watchlist": WATCHLIST})
    strategies = []
    exps = []
    for symbol in WATCHLIST:
        c = _candles(symbol)
        if len(c) < 60:
            continue
        for family, grid in PARAM_GRID.items():
            for params in grid:
                r = backtest(c, family, params)
                exp_id = f"exp-{family}-{symbol}-{abs(hash(json.dumps(params, sort_keys=True))) % 10_000_000}"
                exps.append(
                    (
                        exp_id,
                        "research",
                        json.dumps({"symbol": symbol, "params": params}),
                        json.dumps(r),
                        time.time(),
                        "ok" if r["trades"] > 0 else "no_trades",
                    )
                )
                # validate: at least 3 trades, positive net, bounded drawdown
                if r["trades"] >= 3 and r["net_pnl"] > 0 and r["max_drawdown"] < 50:
                    sid = f"{family}-{symbol}"
                    strategies.append(
                        (
                            sid,
                            "research",
                            family,
                            json.dumps(params),
                            json.dumps(r),
                            "active",
                            time.time(),
                            1,
                        )
                    )
                    db.log("strategy_registered", {"id": sid, "params": params, "metrics": r})
    with db.conn() as c:
        for e in exps:
            c.execute("INSERT OR REPLACE INTO experiments VALUES(?,?,?,?,?,?)", e)
        for st in strategies:
            c.execute("INSERT OR REPLACE INTO strategies VALUES(?,?,?,?,?,?,?,?)", st)


def trading_cycle() -> None:
    """Signal -> paper order -> fill for active strategies."""
    db.init()
    with db.conn() as c:
        strats = c.execute("SELECT * FROM strategies WHERE status='active'").fetchall()
        cash_row = c.execute("SELECT v FROM config WHERE k='starting_cash'").fetchone()
    cash = float(json.loads(cash_row["v"])) if cash_row else 1000.0
    for s in strats:
        symbol = s["id"].split("-", 1)[1]
        c = _candles(symbol)
        if len(c) < 30:
            continue
        fn = FAMILIES[s["family"]]
        sig = fn(c, **json.loads(s["params"]))
        if sig:
            state = f"{symbol} {s['family']} signal={sig} last={float(c[-1]['close']):.2f} regime={json.loads(s['metrics']).get('net_pnl', 0)}"
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
                qty = min(0.01, cash * 0.05 / float(c[-1]["close"]))
                if qty > 0:
                    oid = str(uuid.uuid4())[:8]
                    px = float(c[-1]["close"]) * (1 + 0.0002)
                    fee = px * qty * 0.0004
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
                        (oid, symbol, sig, qty, px, fee, px * 0.0002, time.time()),
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
    _record_equity()


def _record_equity() -> dict:
    """Mark to market, carry realized/fees/peak forward, and append an equity row."""
    db.ensure_column("positions", "mark", "REAL")
    marks, unrealized = mark_open_positions()
    cfg = db.get_all()
    cash = float(cfg.get("starting_cash", 1000.0))
    realized = float(cfg.get("realized_total", 0.0))
    fees = float(cfg.get("fees_total", 0.0))
    peak = max(float(cfg.get("peak_equity", 0.0)), cash + realized + unrealized)
    equity = cash + realized + unrealized
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
    return {"marks": marks, "equity": equity, "drawdown": drawdown, "peak": peak}


def _close(cc, pos, px, sid):
    qty = pos["qty"]
    pnl = (px - pos["entry"]) * qty if pos["side"] == "BUY" else (pos["entry"] - px) * qty
    fee = px * qty * 0.0004
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
            px * 0.0002,
            time.time(),
        ),
    )
    cc.execute("UPDATE positions SET status='closed' WHERE symbol=?", (pos["symbol"],))
    net = pnl - fee
    cfg = db.get_all(cc)
    db.set_many(
        {
            "realized_total": float(cfg.get("realized_total", 0.0)) + pnl,
            "fees_total": float(cfg.get("fees_total", 0.0)) + fee,
        },
        cc,
    )
    db.log("paper_close", {"symbol": pos["symbol"], "pnl": round(net, 4)}, cc)
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
        equity = c.execute(
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
    cash = float(cfg.get("starting_cash", 1000.0))
    realized = float(cfg.get("realized_total", 0.0))
    fees = float(cfg.get("fees_total", 0.0))
    return {
        "starting_cash": cash,
        "virtual_balance": round(cash + realized - fees, 6),
        "equity": round(cash + realized + unrealized, 6),
        "realized": round(realized, 6),
        "unrealized": round(unrealized, 6),
        "fees": round(fees, 6),
        "peak_equity": float(cfg.get("peak_equity", 0.0)),
        "drawdown": round(
            (float(cfg.get("peak_equity", 0.0)) - (cash + realized + unrealized))
            / float(cfg.get("peak_equity", 0.0))
            * 100,
            4,
        )
        if float(cfg.get("peak_equity", 0.0)) > 0
        else 0.0,
        "positions": marks,
        "fills": [dict(f) for f in fills],
        "equity_curve": [dict(e) for e in equity],
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
