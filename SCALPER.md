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
