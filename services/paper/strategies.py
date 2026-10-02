from __future__ import annotations


def _closes(c):
    return [float(k["close"]) for k in c]


def _highs(c):
    return [float(k["high"]) for k in c]


def _lows(c):
    return [float(k["low"]) for k in c]


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


FAMILIES = {
    "sma_cross": sma_cross,
    "donchian": donchian,
    "rsi_revert": rsi_revert,
    "momentum": momentum,
}
PARAM_GRID = {
    "sma_cross": [{"fast": 5, "slow": 15}, {"fast": 9, "slow": 21}, {"fast": 12, "slow": 26}],
    "donchian": [{"n": 10}, {"n": 20}, {"n": 50}],
    "rsi_revert": [{"period": 10}, {"period": 14}, {"period": 20}],
    "momentum": [{"n": 5}, {"n": 10}, {"n": 20}],
}
