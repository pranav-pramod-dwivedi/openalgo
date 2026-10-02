from __future__ import annotations

import json
import os
import time
import uuid

from . import db, jev
from .backtest import backtest
from .strategies import FAMILIES, PARAM_GRID

WATCHLIST = ["BTCUSDT", "SOLUSDT", "ETHUSDT"]


def _candles(symbol: str) -> list[dict]:
    from services.foreign_data_service import get_foreign_history

    try:
        return get_foreign_history(symbol, "CRYPTO", "5m", "", "")
    except Exception:
        return []


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
            verdict = jev.ask(state, {"trade": {"type": "score", "criteria": ["skip", "take"]}})
            db.log("jev", {"symbol": symbol, "state": state, "verdict": verdict})
            if (
                verdict
                and verdict.get("answers", {}).get("trade", {}).get("probabilities", {}).get("1", 0)
                < 0.55
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
                        "INSERT OR REPLACE INTO positions VALUES(?,?,?,?,?,?,?,?,?)",
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
                        ),
                    )
                    db.log(
                        "paper_order",
                        {"symbol": symbol, "side": sig, "qty": qty, "strategy": s["id"]},
                    )
    with db.conn() as c:
        c.execute(
            "INSERT OR REPLACE INTO equity VALUES(?,?,?,?,?,?,?,?)",
            (time.time(), cash, cash, 0.0, 0.0, 0.0, 0.0, 0.0),
        )


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
    db.log("paper_close", {"symbol": pos["symbol"], "pnl": round(pnl - fee, 4)})


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
