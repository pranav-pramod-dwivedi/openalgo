# /make-profitable-trade

Produce one **validated paper trade** from a validated backtested strategy, then
place it on explicit confirmation.

**PAPER TRADING — VIRTUAL MONEY.** Orders go to the paper database
(`data/paper.db`), not a broker. Nothing here is real money and nothing here
guarantees profit.

Usage:

```text
/make-profitable-trade [SYMBOL] [MAX_RISK_USD]
/make-profitable-trade BTCUSDT 25
```

Defaults: the worker's watchlist symbol (`services/paper/config.py`) and a small
paper-cash risk budget.

## Plan (no `--yes`)

```bash
uv run python scripts/profitable_trade.py --symbol BTCUSDT --max-risk 25
uv run python scripts/profitable_trade.py --symbol BTCUSDT --max-risk 25 --json
```

Present: symbol, side, qty, entry, stop-loss, target, risk USD, reward USD,
R:R, the strategy and its backtest metrics, the Jev verdict with `p(take)` and
analyst availability, and the plain-English reasoning. Say it is paper trading.

If the planner refuses, report the reason verbatim and stop. Refusals: no edge,
insufficient cash, exposure cap, stale data, analyst unavailable.

## Execute (only after an explicit yes)

```bash
uv run python scripts/profitable_trade.py --symbol BTCUSDT --max-risk 25 --yes
```

Same script, same planner, no second code path.

## Never

- Invent prices, sizes, confidence or P&L.
- Place an order the planner did not produce.
- Call this a profitable or safe trade.

`scripts/paper_worker.py` calls the identical planner, so agent and worker never
diverge. Full rules: `.opencode/skills/profitable-trade/SKILL.md`.