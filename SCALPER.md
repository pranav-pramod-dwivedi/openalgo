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
per strategy family, then sweeps arm B across a range of acceptance thresholds.

| Arm | Gate |
| --- | --- |
| **A** | none - unfiltered. Reproduces `services/paper/backtest.py` exactly (asserted every run). |
| **B** | Jev. Accept only when `p(take) >= threshold`, swept over 0.30 / 0.50 / 0.60 / 0.70 / 0.75 / 0.80 / 0.85 / 0.90. |
| **C** | control. A seeded random gate (`seed 42`, 50% accept rate). |

Arm C exists because a filter that only removes trades always looks better in
isolation. Comparing B against a *random* filter of similar selectivity is what
tells you whether Jev adds signal or just reduces trade count.

### Why the state string is the whole experiment

The first version of this script asked Jev one question about a state string
carrying only symbol, family, params, signal and price. Jev answered
`p(take) = 0.68-0.86` to all 252 of them, so a 0.30 gate rejected nothing and
the gate was **never actually tested**. A gate you cannot make fire is not a
safe gate, it is an untested one.

The state now carries, per candidate:

- symbol, interval, strategy family and the exact params
- the signal direction (`BUY` / `SELL`)
- `setup_*` - the four metrics `backtest()` returns for that family and params
- `ret_stdev_20` - realised volatility of the last 20 returns
- `range_20` - (window high - window low) / close
- `volume_trend_20` - mean volume of the window over the mean of the window before it
- `price_pos_20` - where close sits inside the recent high/low range, 0 to 1

Every one of those is a number the script measured on the candles it was given.
There is no filler field and nothing invented.

`setup_*` is computed on the **causal prefix** `candles[:i+1]`, not on the full
window. It is the same `backtest()` function either way, so the fields are
exactly the ones the reference engine reports, but a candidate at candle `i` is
only ever shown what a live system would have known at candle `i`. Computing
them over the whole window would hand the model a summary of the future it is
being asked to judge, and arm B would beat any deployable version of itself.

### Two questions, one call

Each request asks `trade` (`skip` vs `take`) and `risk` (`safe` vs `risky`)
together. The free tier prices a request, not a question, so the risk read costs
nothing.

**Risk is recorded and never acts.** The strategy decides direction; `p(risky)`
is reported next to the realised outcome so it can be judged on its own, and it
is not allowed to flip an accept into a reject. A risk filter that vetoes trades
is a second strategy wearing the first one's clothes.

### Threshold sweep

For each threshold the script prints accepted count, net P&L, max drawdown,
stand-aside rate, and how often `risk=risky` landed on a trade that then lost.

The sweep exists so the threshold is **not** chosen to flatter the result. A
threshold picked because it produced a nice rejection rate is a fitted number
with no meaning. If the model says "take" to everything at every threshold, that
is the finding and it gets reported as one.

### Cache

Every Jev answer is appended to `log/jev_cache.jsonl`, keyed by the SHA-256 of
the exact request body (model + state + questions). The state string is part of
the body, so **widening the state necessarily changes the key** - a richer state
can never be answered out of the cache with a thinner state's verdict. A re-run
reads the cache first and makes **zero** API calls, so a result is free to
re-inspect and reproducible.

A failed request (`None`, or an answer with no usable probability) is **not**
cached, so a transient outage is never frozen into the file as a verdict.

If Jev returns nothing usable, the signal is **accepted** and counted in
`jev_fallbacks` - the failure mode is "trade as before", never "silently stop
trading" - and the run prints a warning, because a partly-answered arm B is
partly arm A and the stand-aside column is then only an upper bound.

### Free-tier limits

`jev-1.13-free` answers `HTTP 429 FreeUsageLimitError` under a burst, and
`jev.ask()` swallows the status code, so a throttled request is indistinguishable
from an outage. The script therefore paces itself process-wide
(`--min-interval`, default 2s), backs off between retries, and gives up after a
run of consecutive failures rather than grinding through a quota wall. A run
that gives up says so at the top of its output.

```bash
uv run python scripts/jev_arms.py --symbol BTCUSDT --limit 300
# flags: --symbol (default BTCUSDT), --limit (default 300), --interval (default 5m),
#        --min-interval (default 2.0 seconds between API calls)
```

### Reading the result

Every completed trade is logged, so the script can also run a two-proportion
z-test on the win rate of B against A and against C. **Treat that number as
weak evidence.** At a few dozen completed trades a large edge and pure noise
look identical, and the p-value is a floor on the doubt, not a proof of absence.
The threshold that maximises P&L in the sweep is the threshold that maximised
P&L on this sample, which is not the same as the threshold that maximises P&L
out of sample.

### Current result: the sweep is built and verified, but not yet measured live

The harness is complete: causal rich state, two questions per call, the
threshold sweep, risk recorded against realised outcomes, and a cache that
makes a re-run free. It was validated end to end against a stubbed transport
(full sweep table produced, arm A matching `backtest()`, re-run reporting
**0 API calls** with the cache line count unchanged).

It has **not** been measured against the real model, because the free tier
stopped answering:

```
HTTP 429 {"type":"error","error":{"type":"FreeUsageLimitError",
                                  "message":"Rate limit exceeded. Please try again later."}}
```

sustained for every attempt across both symbols. The script detects this, gives
up rather than grinding, and prints `STOPPED EARLY` above its own numbers so
they cannot be read as a result. Re-run when the quota resets; every state
already answered is cached, so it resumes rather than restarting.

**What the richer state did buy, from the four calls that got through before the
limiter closed.** The old thin state answered `p(take) = 0.68-0.86`, a 0.18-wide
band centred on "yes". Four richer states answered:

| state | `p(take)` | `p(risky)` |
| --- | --- | --- |
| `sma_cross` BUY, `setup_net_pnl=+4.12`, `price_pos=0.62`, volume rising | 0.80 | 0.60 |
| same family SELL, `setup_net_pnl=-7.30`, `price_pos=0.11` | 0.40 | 0.90 |
| same family, `setup_net_pnl=-22.5`, volume falling | 0.54 | 0.85 |
| `donchian n=50`, `range=0.099`, `price_pos=0.99`, `setup_net_pnl=-1.05` | 0.60 | 0.93 |

`p(take)` spread widened from 0.18 to 0.40 and now moves in the right direction:
positive setup plus price mid-range gets the higher `take`, negative setup gets
the lower. `p(risky)` spread wider still, 0.60 to 0.93.

**n = 4. That is not evidence that the gate works.** It is evidence that the
model is no longer answering a constant, which is a precondition for the gate
being testable at all, and nothing more. Whether the ordering survives on the
~1200-1500 states a full sweep needs, and whether any of it survives out of
sample, is exactly what is still unknown.

Cost of the pending run, measured offline against a stub: **1215 distinct states
for BTCUSDT and 1518 for SOLUSDT** at `--limit 300`. At the default 2s pacing
that is roughly 40 and 50 minutes of API time, so check the quota before
starting.
