"""Strategy families for the paper research loop.

Every family is a pure function of the candle list it is handed:

    family(candles, **params) -> "BUY" | "SELL" | None

``None`` means "no setup", which is the normal case. The functions read only
``close``/``high``/``low``/``volume``, are deterministic, and depend on nothing
outside the standard library, so the same candles always produce the same signal
and a backtest can be re-run to reproduce a result exactly.

Seven families, deliberately different in kind rather than in tuning:

``sma_cross``
    Trend by moving-average order.
``donchian``
    Trend by range expansion (price at the edge of the recent range).
``rsi_revert``
    Exhaustion fade on a bounded oscillator.
``momentum``
    Bare rate-of-change.
``atr_breakout``
    Breakout measured in units of true range, so the trigger widens when the
    market is quiet instead of firing on noise.
``volume_trend``
    Moving-average trend that only signals when the latest bar's volume
    confirms it.
``zscore_revert``
    Mean reversion on a band around the moving average, entered only while the
    move is still unfolding.
"""

from __future__ import annotations


def _closes(c):
    return [float(k["close"]) for k in c]


def _highs(c):
    return [float(k["high"]) for k in c]


def _lows(c):
    return [float(k["low"]) for k in c]


def _volumes(c):
    out = []
    for k in c:
        try:
            out.append(float(k.get("volume") or 0.0))
        except (TypeError, ValueError):
            out.append(0.0)
    return out


def _true_ranges(c):
    """Bar-over-bar true range: the high/low span widened by any gap."""
    out = []
    prev_close = None
    for k in c:
        high, low = float(k["high"]), float(k["low"])
        if prev_close is None:
            out.append(max(high - low, 0.0))
        else:
            out.append(max(high - low, abs(high - prev_close), abs(prev_close - low), 0.0))
        prev_close = float(k["close"])
    return out


def _mean(values):
    return sum(values) / len(values)


def _stdev(values):
    if len(values) < 2:
        return 0.0
    avg = _mean(values)
    var = sum((v - avg) ** 2 for v in values) / (len(values) - 1)
    return var**0.5


def sma_cross(c, fast=9, slow=21):
    if len(c) < slow + 2:
        return None
    f = sum(_closes(c)[-fast:]) / fast
    s = sum(_closes(c)[-slow:]) / slow
    if f > s * 1.0005:
        return "BUY"
    if f < s * 0.9995:
        return "SELL"
    return None


def donchian(c, n=20):
    if len(c) < n + 1:
        return None
    last = _closes(c)[-1]
    if last > max(_highs(c)[-n - 1 : -1]):
        return "BUY"
    if last < min(_lows(c)[-n - 1 : -1]):
        return "SELL"
    return None


def rsi_revert(c, period=14, lo=30, hi=70):
    if len(c) < period + 2:
        return None
    w = _closes(c)[-period - 1 :]
    gains = [max(w[i] - w[i - 1], 0) for i in range(1, len(w))]
    losses = [max(w[i - 1] - w[i], 0) for i in range(1, len(w))]
    rs = (sum(gains) / period) / max(sum(losses) / period, 1e-9)
    r = 100 - 100 / (1 + rs)
    if r < lo:
        return "BUY"
    if r > hi:
        return "SELL"
    return None


def momentum(c, n=10):
    if len(c) < n + 1:
        return None
    m = (_closes(c)[-1] - _closes(c)[-n]) / _closes(c)[-n]
    if m > 0.002:
        return "BUY"
    if m < -0.002:
        return "SELL"
    return None


def atr_breakout(c, n=14, mult=1.0, lookback=10):
    """Break of the recent range, measured in multiples of the average true range.

    A fixed percentage trigger fires on a quiet market and never fires on a
    violent one. Scaling the threshold by the market's own range keeps the
    comparison honest across symbols and timeframes.
    """
    if len(c) < n + lookback + 2:
        return None
    tr = _true_ranges(c)
    atr = _mean(tr[-n:])
    if atr <= 0:
        return None
    last = _closes(c)[-1]
    band = mult * atr
    upper = max(_highs(c)[-lookback - 1 : -1])
    lower = min(_lows(c)[-lookback - 1 : -1])
    if last > upper + band:
        return "BUY"
    if last < lower - band:
        return "SELL"
    return None


def volume_trend(c, n=20, vol_n=20, vol_mult=1.2):
    """Moving-average trend, taken only when the newest bar carries the volume.

    Direction comes from the average moving higher or lower; participation comes
    from the latest bar's volume against its own recent average. Without volume
    the family stays silent rather than trading an unconfirmed move.
    """
    if len(c) < max(n, vol_n) + 2:
        return None
    closes = _closes(c)
    vols = _volumes(c)
    vavg = _mean(vols[-vol_n:])
    if vavg <= 0:
        # No volume in the feed for this symbol: nothing to confirm with.
        return None
    confirmed = vols[-1] >= vol_mult * vavg
    if not confirmed:
        return None
    ma = _mean(closes[-n:])
    prev_ma = _mean(closes[-n - 1 : -1])
    if ma > prev_ma * 1.0005:
        return "BUY"
    if ma < prev_ma * 0.9995:
        return "SELL"
    return None


def zscore_revert(c, n=20, entry_z=1.5, exit_z=0.5):
    """Fade a price that has moved away from its own moving average.

    The z-score is the distance from the average in standard deviations. A
    signal is taken while the move is entering the band (``|z|`` between
    ``exit_z`` and ``entry_z``), not after it has already extended past it, so
    the family buys a stretch rather than an exhaustion it cannot see coming.
    """
    if len(c) < n + 2:
        return None
    closes = _closes(c)[-n:]
    sd = _stdev(closes)
    if sd <= 0:
        return None
    z = (_closes(c)[-1] - _mean(closes)) / sd
    if abs(z) < exit_z or abs(z) > entry_z:
        return None
    if z < 0:
        return "BUY"
    return "SELL"


FAMILIES = {
    "sma_cross": sma_cross,
    "donchian": donchian,
    "rsi_revert": rsi_revert,
    "momentum": momentum,
    "atr_breakout": atr_breakout,
    "volume_trend": volume_trend,
    "zscore_revert": zscore_revert,
}

# The grid is deliberately small: a research cycle is bounded, so every extra
# parameter point is taken from a symbol's other experiments. Four points per
# family is enough to see which shapes of a family suit which timeframe.
PARAM_GRID = {
    "sma_cross": [
        {"fast": 5, "slow": 15},
        {"fast": 9, "slow": 21},
        {"fast": 12, "slow": 26},
        {"fast": 21, "slow": 50},
    ],
    "donchian": [{"n": 10}, {"n": 20}, {"n": 50}, {"n": 100}],
    "rsi_revert": [
        {"period": 10},
        {"period": 14},
        {"period": 20},
        {"period": 14, "lo": 25, "hi": 75},
    ],
    "momentum": [{"n": 5}, {"n": 10}, {"n": 20}, {"n": 40}],
    "atr_breakout": [
        {"n": 14, "mult": 0.5, "lookback": 10},
        {"n": 14, "mult": 1.0, "lookback": 10},
        {"n": 20, "mult": 1.0, "lookback": 20},
        {"n": 14, "mult": 1.5, "lookback": 40},
    ],
    "volume_trend": [
        {"n": 20, "vol_n": 20, "vol_mult": 1.2},
        {"n": 10, "vol_n": 20, "vol_mult": 1.2},
        {"n": 40, "vol_n": 40, "vol_mult": 1.0},
        {"n": 20, "vol_n": 50, "vol_mult": 1.5},
    ],
    "zscore_revert": [
        {"n": 20, "entry_z": 1.5, "exit_z": 0.5},
        {"n": 20, "entry_z": 2.0, "exit_z": 0.5},
        {"n": 50, "entry_z": 1.5, "exit_z": 0.5},
        {"n": 10, "entry_z": 1.2, "exit_z": 0.3},
    ],
}
