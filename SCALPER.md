# AI Scalper — supervised Binance demo trading

Runs a capped, audited loop against the Binance demo (testnet) venue.

## What it does
- Polls candles/positions every 5 seconds.
- Enters one long or one short when 1m price crosses the 9-period SMA by > 0.08%.
- Immediately places native reduce-only `STOP_MARKET` and `TAKE_PROFIT_MARKET` exits.
- Exits early on SMA reversion or after 5 minutes.
- Logs every tick, signal, fill, and bracket to `log/ai_scalper.jsonl`.

## Before running
1. Ensure `.env` has `BINANCE_MODE='demo'`. Do not set real credentials.
2. `BINANCE_TRADING_FLOOR` protects savings: only equity above the floor is tradable, and the service layer blocks new exposure that exceeds it.

## Run
```bash
uv run python scripts/ai_scalper.py --symbol BTCUSDT --size 0.001 --yes
```
Drop `--yes` for a dry-run that prints signals without sending orders.

Stop with `Ctrl+C`: it cancels resting exit orders and leaves any position for manual review (no auto-close, so nothing races the cancel).

## Honesty notes
- There is no guarantee of profit. The signal is a simple 9-SMA cross and loses in chop.
- Testnet fills are not live liquidity. This proves the loop, not live P&L.
- The scalper does not need an LLM. To make it "AI driven", run the OpenAlgo agent and point it at the same audited tools — the same safety gates apply.

## Journal fields (`log/ai_scalper.jsonl`)
`event`: `start` | `signal` | `entry` | `brackets` | `tick` | `exit` | `flat` | `halt` | `error` | `stop`

Each event carries its reason. Re-run analysis with `jq` on that file.

## Multi-symbol and strategies
```bash
uv run python scripts/ai_scalper.py --symbols BTCUSDT,SOLUSDT,ETHUSDT --strategy donchian --yes
```
Strategies: `sma_cross` (1m price vs 9-SMA band), `donchian` (20-candle high/low breakout), `rsi_revert` (14-period RSI oversold/overbought). One open position per symbol max; native reduce-only SL/TP on every entry; per-symbol 5m max-hold; daily loss limit halts the loop.

## Report
```bash
uv run python scripts/ai_scalper.py --report
```
Summarizes the journal: entries, exits, halts, errors, and last halt reason.

## Appendix: does the Jev gate earn its keep? (`scripts/jev_arms.py`)

Before trusting any AI filter in front of a strategy, measure it against doing
nothing. `scripts/jev_arms.py` replays the **same candles** through three arms
per strategy family, and reports candidates, accepted, net P&L, max drawdown,
stand-aside rate, and Jev fallback count.

| Arm | Gate |
| --- | --- |
| **A** | none — unfiltered. Reproduces `services/paper/backtest.py` exactly. |
| **B** | Jev. When a signal fires, Jev scores the trade `skip` vs `take`; accept only if `probabilities["1"] >= 0.30`. |
| **C** | control. A seeded random gate (`seed 42`, 50% accept rate). |

Arm C exists because a filter that only removes trades always looks better in
isolation. Comparing B against a *random* filter of similar selectivity is what
tells you whether Jev adds signal or just reduces trade count.

### The 0.30 threshold

`0.30` means "keep the trade unless Jev thinks it is worse than a coin flip
against you." It is deliberately low. The filter is meant to remove the clearly
bad signals, not to express a view on direction. Raising it converts the gate
into a directional strategy bet, which is a different experiment.

### Cache

Every Jev call is appended to `log/jev_cache.jsonl`, keyed by the SHA-256 of the
exact request body (model + state + questions). A re-run reads the cache first
and makes **zero** API calls, so results are reproducible and free to re-inspect.
If Jev returns `None` or an answer without a usable probability, the signal is
**accepted** and counted in `jev_fallbacks` — the failure mode is "trade as
before", never "silently stop trading".

```bash
uv run python scripts/jev_arms.py --symbol BTCUSDT --limit 300
# flags: --symbol (default BTCUSDT), --limit (default 500), --interval (default 5m)
```

### Current result (BTCUSDT 5m, 300 candles) — read this before believing it

Every family shows `jev_fallbacks` 0 and arm B identical to arm A. That is not a
win for the gate; it means **the gate never fired**. Inspecting the 252 cached
responses, Jev returns `P(take)` between **0.68 and 0.86** — the free tier
answers almost every question near-confidently and positively, and the state
string here is too thin to move it. A 0.30 threshold on this distribution
rejects nothing.

So this run shows the gate is **safe but untested**, not that it is profitable.
Treat arm B == arm A as the baseline, not as evidence for Jev. To get a real
reading, either raise the threshold to sit inside the model's actual output
range, or enrich the state with context the model can discriminate on. Until
then the honest conclusion is that arm C (the random gate) does essentially
what a Jev gate would — it just does it for free.
