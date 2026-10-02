---
name: profitable-trade
description: Make one validated paper trade in a single step and report it in plain English. Use when the user says "make me a profitable trade", "find me a trade", "give me a setup", or asks for one position. Places the trade without asking for confirmation, works when the AI reviewer is rate-limited, and refuses when the planner refuses.
argument-hint: "[symbol, e.g. BTCUSDT] [max risk USD, e.g. 25]"
allowed-tools: Bash, Read, Grep
---

# Profitable trade — one request, one trade, one plain report

This is **PAPER TRADING: VIRTUAL MONEY.** Orders go to the paper database
(`data/paper.db` by default). No real broker, no real funds, no real fills.

There is one code path: `scripts/profitable_trade.py`. You never compute a
trade yourself, and `scripts/paper_worker.py` calls the identical planner, so
the agent and the worker can never diverge.

**Do not ask the user to confirm.** The request to make a trade is the
permission to place it. Ask only when a number is genuinely missing, and never
to confirm a plan you are about to place.

## Step 1 — see what the planner says

```bash
uv run python scripts/profitable_trade.py --symbol BTCUSDT --max-risk 25 --json
```

Defaults when the user omits them: the configured watchlist symbol from
`services/paper/config.py` and a small virtual-cash risk budget.

## Step 2 — place it

If the plan is clean, run the same script with `--yes`:

```bash
uv run python scripts/profitable_trade.py --symbol BTCUSDT --max-risk 25 --yes
```

### If the AI reviewer was rate-limited

If Step 1 came back refused because the AI reviewer could not be reached, retry
**once** with `--allow-rules-only`, which plans and places in one go:

```bash
uv run python scripts/profitable_trade.py --symbol BTCUSDT --max-risk 25 --yes --allow-rules-only
```

If that is also refused, report the reason and stop. Do not retry again.

When a trade was placed this way, the report **must** say plainly that no AI
reviewed it and that it rests on the backtested strategy alone. Never describe
an unreviewed trade as reviewed, endorsed or checked.

## Step 3 — report back in plain English

Write it for a trader, not a developer. No status codes, no field names, no
internal words such as `analyst_bypassed`, no JSON unless the user asked for it.
Cover, in this order:

- the coin, and whether it is a buy or a sell
- the amount
- the entry price
- the stop-loss, and the target
- how much money is at risk, and how much it makes if it works
- the strategy that produced it and how it was tested: backtested trades,
  net result, worst drawdown
- whether an AI reviewed it, and if not, that it did not
- what would make this trade wrong

Close by saying this is virtual money, and that a trade can still lose.

## Refusals are answers, not failures

Say the reason in one plain sentence and stop. Never try other coins or change
numbers to force a trade.

- no strategy had a setup worth taking
- not enough virtual cash left
- already using as much of the portfolio as allowed
- a trade in that coin is already open
- today's losses reached the limit set for the day
- the price data was too old to trust
- the AI reviewer could not be reached (only retry as in Step 2)

A refused trade is never a recommendation. Report nothing else.

## Hard rules

1. **Never invent** a price, amount, probability, or profit figure. Every
   number comes from the planner's output; if a number is missing, say it is
   unavailable.
2. **Never place an order the planner did not produce**, and never bypass the
   planner with a direct order call, a curl to an order endpoint, or
   hand-written Python.
3. **Never call a trade profitable, guaranteed or safe.** Report the risk, the
   reward, and that loss is still possible.
4. Only re-run the planner with **different real inputs the user supplied**.
   Never lower `--max-risk` on your own.
5. Always say whether an AI reviewed the trade. Silence is a lie by omission.

See `PAPER_TRADING.md` for the paper engine, strategies and the Jev reviewer.