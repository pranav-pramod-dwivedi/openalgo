from __future__ import annotations

from .strategies import FAMILIES


def backtest(candles, family, params, fee_bps=4, slip_bps=2, tp=0.004, sl=0.002):
    fn = FAMILIES[family]
    cash = 1000.0
    pos = None
    fees = 0.0
    trades = 0
    peak = cash
    maxdd = 0.0
    for i in range(len(candles)):
        w = candles[: i + 1]
        px = float(w[-1]["close"])
        if pos is None:
            sig = fn(w, **params)
            if sig:
                entry = px * (1 + slip_bps / 10000)
                qty = min(0.01, cash * 0.25 / px)
                fee = entry * qty * fee_bps / 10000
                fees += fee
                pos = {
                    "side": sig,
                    "entry": entry,
                    "qty": qty,
                    "sl": entry * (1 - sl) if sig == "BUY" else entry * (1 + sl),
                    "tp": entry * (1 + tp) if sig == "BUY" else entry * (1 - tp),
                }
        else:
            hit_sl = px <= pos["sl"] if pos["side"] == "BUY" else px >= pos["sl"]
            hit_tp = px >= pos["tp"] if pos["side"] == "BUY" else px <= pos["tp"]
            opp = fn(w, **params)
            exit_now = (
                hit_sl
                or hit_tp
                or (
                    opp
                    and (
                        (opp == "SELL" and pos["side"] == "BUY")
                        or (opp == "BUY" and pos["side"] == "SELL")
                    )
                )
            )
            if exit_now:
                exit_px = pos["tp"] if hit_tp else (pos["sl"] if hit_sl else px)
                exit_px *= (
                    (1 - slip_bps / 10000) if pos["side"] == "BUY" else (1 + slip_bps / 10000)
                )
                pnl = (
                    (exit_px - pos["entry"]) * pos["qty"]
                    if pos["side"] == "BUY"
                    else (pos["entry"] - exit_px) * pos["qty"]
                )
                fee = exit_px * pos["qty"] * fee_bps / 10000
                fees += fee
                cash += pnl - fee
                trades += 1
                pos = None
        if pos is None:
            eq = cash
        else:
            eq = cash + (
                (px - pos["entry"]) * pos["qty"]
                if pos["side"] == "BUY"
                else (pos["entry"] - px) * pos["qty"]
            )
        peak = max(peak, eq)
        maxdd = max(maxdd, peak - eq)
    return {
        "trades": trades,
        "net_pnl": round(cash - 1000, 4),
        "max_drawdown": round(maxdd, 4),
        "fees": round(fees, 4),
    }
