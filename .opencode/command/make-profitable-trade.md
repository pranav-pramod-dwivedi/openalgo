# /make-profitable-trade

Make one validated **paper trade** and report it in plain English. One request,
one trade, no confirmation step.

**PAPER TRADING - VIRTUAL MONEY.** Orders go to the paper database
(`data/paper.db`), not a broker. Nothing here is real money and nothing here
guarantees profit.

Usage:

```text
/make-profitable-trade [SYMBOL] [MAX_RISK_USD]
/make-profitable-trade BTCUSDT 25
```

Defaults: the configured watchlist symbol (`services/paper/config.py`) and a
small virtual-cash risk budget.

## Workflow

1. Plan, machine-readable:

```bash
uv run python scripts/profitable_trade.py --symbol BTCUSDT --max-risk 25 --json
```

2. Place it, same script:

```bash
uv run python scripts/profitable_trade.py --symbol BTCUSDT --max-risk 25 --yes
```

3. If step 1 was refused because the AI reviewer was rate-limited, retry once
   with `--allow-rules-only`, which plans and places together:

```bash
uv run python scripts/profitable_trade.py --symbol BTCUSDT --max-risk 25 --yes --allow-rules-only
```

4. Report back in plain English: coin, buy or sell, amount, entry price,
   stop-loss, target, money at risk, money if it works, the strategy and how it
   was tested, whether an AI reviewed it, and what would make it wrong. No
   status codes, no field names, no JSON unless asked.

If a trade came from step 3, the report must say no AI reviewed it. If the
planner refused for any other reason, say so in one plain sentence and stop -
do not try other coins.

Never invent prices, amounts or profit. Never place an order the planner did not
produce. Never call a trade profitable or safe.

`scripts/paper_worker.py` calls the identical planner, so agent and worker never
diverge. Full rules: `.opencode/skills/profitable-trade/SKILL.md`.