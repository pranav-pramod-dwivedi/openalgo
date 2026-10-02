---
name: profitable-trade
description: Produce and place one validated paper trade from a validated backtested strategy. Use when the user says "make me a profitable trade", "find me a trade", "give me a setup", or asks for a single position on a symbol. Runs the deterministic planner, presents the plan, and refuses when the planner refuses.
argument-hint: "[symbol, e.g. BTCUSDT] [max risk USD, e.g. 25]"
allowed-tools: Bash, Read, Grep
---

# Profitable trade — one command, one validated plan

This is **PAPER TRADING: VIRTUAL MONEY.** Orders go to the paper database
(`data/paper.db` by default). No real broker, no real funds, no real fills.

There is one code path: `scripts/profitable_trade.py`. You never compute a
trade yourself, and the background worker (`scripts/paper_worker.py`) calls the
**identical planner**, so the agent and the worker can never diverge.

## Step 1 — get the plan. Never pass `--yes` here.

```bash
uv run python scripts/profitable_trade.py --symbol BTCUSDT --max-risk 25
```

Add `--json` when you want to parse the result instead of reading prose:

```bash
uv run python scripts/profitable_trade.py --symbol BTCUSDT --max-risk 25 --json
```

Defaults if the user omits them: the worker's watchlist symbol from
`services/paper/config.py` and a small paper-cash risk budget.

## Step 2 — present the plan, then stop

Report every field below, from the planner's output only:

- **symbol**, **side** (BUY/SELL), **qty**
- **entry**, **stop-loss**, **target**
- **risk in USD**, **reward in USD**, **R:R**
- **strategy** that generated it, plus its **backtest metrics**
  (win rate, profit factor, sample size)
- **Jev verdict**: `p(take)` and whether the **analyst was available**
- the **plain-English reasoning** string, quoted

Then say plainly: this is paper trading, virtual money, and no profit is
guaranteed. Ask for confirmation. Do not place anything yet.

## Step 3 — only on an explicit yes

Run the same script with `--yes`. No other command, no manual order call:

```bash
uv run python scripts/profitable_trade.py --symbol BTCUSDT --max-risk 25 --yes
```

Report the resulting paper order id and status exactly as returned.

## Refusals are answers, not failures

If the planner refuses, say so in one line and give the reason verbatim. Never
retry with invented inputs to force a trade.

Refusal reasons:

- **no edge** — no validated strategy currently clears its threshold
- **insufficient cash** — paper cash cannot cover the position
- **exposure cap** — this symbol already sits at its exposure limit
- **stale data** — quotes or candles are too old to trade on
- **analyst unavailable** — the Jev analyst did not answer

A refused or unvalidated trade is never a recommendation. If the planner
refuses, there is no trade to present. Report nothing else.

## Hard rules

1. **Never invent** a price, size, confidence percentage, probability, or P&L.
   Every number you state comes from the planner's output.
2. **Never place an order the planner did not produce.** If you did not get a
   plan id from a successful run, you may not execute.
3. **Never claim a trade is profitable**, guaranteed, or "safe". The planner
   reports risk, reward and R:R — you report those, plus the fact that this is
   simulated and loss is still possible.
4. **Never bypass the planner** with a direct order-service call, a curl to an
   order endpoint, or hand-written Python.
5. Re-running the planner after a refusal is fine only with **different real
   inputs** the user supplied. Never loosen `--max-risk` silently.
6. If `--json` output is missing a field you were asked for, say the field is
   unavailable. Do not fill it in from memory.

See `PAPER_TRADING.md` for the paper engine, strategies and Jev analyst.