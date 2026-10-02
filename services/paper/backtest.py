"""Backtesting for the paper research loop.

One backtest is a simple long/short walk over a candle list with a fixed stop
and target, a fee on both sides and a synthetic slippage. It is not a model of
execution quality; its only job is to rank one parameter set against another on
exactly the same candles, so that the strategies registered from it are at
least self-consistent.

``backtest_matrix`` runs the grid across several timeframes for one symbol. A
symbol whose candles are missing for a timeframe is skipped and counted, never
raised: a feed that carries 5m and 1h but not 15m is normal, and one absent bar
series must not abort the cycle.
"""

from __future__ import annotations

from utils.logging import get_logger

from .strategies import FAMILIES, PARAM_GRID

logger = get_logger(__name__)

# Fixed capital for every backtest, so ``max_drawdown`` from different
# experiments is comparable and can be read as a percentage of the account.
STARTING_CASH = 1000.0

# Below this a backtest is not evidence of anything: the longest lookback in the
# grid needs the window, and a shorter one would be backfilled silence.
MIN_CANDLES = 60

DEFAULT_MAX_EXPERIMENTS = 300


def drawdown_pct(metrics: dict, starting_cash: float = STARTING_CASH) -> float:
    """Peak-to-trough loss as a percentage of the account it was measured on."""
    if starting_cash <= 0:
        return 0.0
    return round(float(metrics.get("max_drawdown", 0.0)) / starting_cash * 100.0, 4)


def backtest(
    candles, family, params, fee_bps=4, slip_bps=2, tp=0.004, sl=0.002, starting_cash=STARTING_CASH
):
    fn = FAMILIES[family]
    cash = starting_cash
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
        "net_pnl": round(cash - starting_cash, 4),
        "max_drawdown": round(maxdd, 4),
        "max_drawdown_pct": drawdown_pct({"max_drawdown": maxdd}, starting_cash),
        "fees": round(fees, 4),
    }


def plan_matrix(intervals, families=None) -> list:
    """Every (interval, family, params) job for one symbol, in a stable order.

    Intervals vary fastest inside a family so that a truncated cycle still gets
    some coverage of each timeframe rather than exhausting the first one.
    """
    families = families or PARAM_GRID
    jobs = []
    for family, grid in families.items():
        for iv in intervals:
            for params in grid:
                jobs.append((iv, family, params))
    return jobs


def backtest_matrix(
    symbol: str,
    intervals,
    load_candles,
    families=None,
    max_experiments: int = DEFAULT_MAX_EXPERIMENTS,
    min_candles: int = MIN_CANDLES,
) -> dict:
    """Run the grid for one symbol across ``intervals``.

    ``load_candles(symbol, interval)`` is called at most once per timeframe and
    is expected to return a list of candles, or an empty list when the timeframe
    is unavailable. Anything it raises is recorded as a skip, because a research
    cycle must survive a bad feed rather than abort on it.

    Returns ``experiments`` (one entry per backtest actually run), ``skipped``
    (one entry per timeframe with no usable candles) and ``truncated`` (how many
    planned experiments the cap left out).
    """
    intervals = [iv for iv in dict.fromkeys(intervals) if iv]
    families = families or PARAM_GRID
    jobs = plan_matrix(intervals, families)
    cap = max(1, int(max_experiments))

    experiments: list[dict] = []
    skipped: list[dict] = []
    cache: dict[str, list] = {}
    truncated = 0

    for iv, family, params in jobs:
        if len(experiments) >= cap:
            truncated += 1
            continue
        if iv not in cache:
            try:
                candles = load_candles(symbol, iv) or []
            except Exception as exc:  # a bad feed is a skip, not a failed cycle
                logger.warning(
                    f"Research: candles unavailable for {symbol} {iv}: {type(exc).__name__}: {exc}"
                )
                candles = []
            if len(candles) < min_candles:
                skipped.append(
                    {"interval": iv, "reason": "insufficient_candles", "candles": len(candles)}
                )
            cache[iv] = candles
        candles = cache[iv]
        if len(candles) < min_candles:
            continue
        try:
            metrics = backtest(candles, family, params)
        except Exception as exc:  # a parameter set that blows up is a bad one
            logger.warning(
                f"Research: backtest failed for {family} {params} on "
                f"{symbol} {iv}: {type(exc).__name__}: {exc}"
            )
            skipped.append({"interval": iv, "reason": f"backtest_error: {exc}"})
            continue
        experiments.append({"interval": iv, "family": family, "params": params, "metrics": metrics})

    return {
        "symbol": symbol,
        "experiments": experiments,
        "skipped": skipped,
        "truncated": truncated,
        "planned": len(jobs),
    }
