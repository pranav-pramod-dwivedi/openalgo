# Paper Trading Worker

The autonomous worker runs market research and simulated trading forever. Every
position, fill and P&L it produces is simulated and stored in a local SQLite
file. See [No live orders](#no-live-orders) - it is not a warning, it is a
property of the code.

## `data/paper.db` is live state

The default database, `data/paper.db`, is the running paper portfolio: open
positions, the cash balance, the equity curve and every journal row. It is
**not** a scratch file and **not** regenerated. Nothing in this repository can
put back a position that was closed in it, so a command run by hand against it
changes the operator's book for good.

**Every command you run by hand must point `PAPER_DB` at a scratch file.** Put
it in front of the command, exactly like this:

```sh
PAPER_DB=/tmp/scratch-paper.db uv run python scripts/profitable_trade.py --symbol BTCUSDT --max-risk 25 --yes
```

Running several commands in one shell session? Export it once, then every
command in that session is safe:

```sh
export PAPER_DB=/tmp/scratch-paper.db
```

Leave `PAPER_DB` unset only when you mean the real book: the worker's own
commands, `./paper`, `./paper status` and `./paper stop`. A thrown-away test,
a smoke test and a one-off experiment are never that.

The test suite is already protected against this. Any test that imports
`services.paper` gets its own empty database file in a temporary directory,
assigned before the test runs and deleted afterwards, so a test cannot reach
`data/paper.db` even by accident. See the `isolate_paper_db` fixture in
`test/conftest.py`.

## Run it

```sh
uv run python scripts/paper_worker.py
```

That is the default: the autonomous loop, running until you stop it with Ctrl+C.

The database defaults to `data/paper.db`, which is live state. Read
[`data/paper.db` is live state](#datapaperdb-is-live-state) before running
anything against it. Point it elsewhere with `PAPER_DB`:

```sh
PAPER_DB=/tmp/scratch-paper.db uv run python scripts/paper_worker.py
```

Useful while you are setting things up:

```sh
uv run python scripts/paper_worker.py --once    # run a single cycle, print health, exit
uv run python scripts/paper_worker.py --status  # print health, change nothing
```

`--once` and `--status` exit when they are done. Any other flag combination with
no mode flag starts the loop, so adding `--symbols` to a running command starts
(or keeps) the loop rather than exiting. Add `--status` if you only want to save
settings and see them.

## Kill switch

The worker checks a `halted` flag in the paper config table at the **top of every
cycle**, before any data is fetched and before any signal is evaluated. While it
is set, the worker writes a `halted` heartbeat and idles without trading.

```sh
uv run python scripts/paper_worker.py --halt    # stop trading, persistently
uv run python scripts/paper_worker.py --resume  # allow trading again
```

The flag lives in the database, not in the process, so it survives a restart and
takes effect for a worker started later:

- `--halt` against a running worker lands on its next tick (within the monitor
  cadence, default 60s). The loop sleeps in 1s slices for exactly this reason,
  so a halt does not have to wait out a full sleep.
- A worker that was already inside a research cycle when the flag was set may
  finish that cycle. Halting stops new work; it does not roll back the cycle in
  progress.
- `--halt` never closes open positions, and it does not manage them either. See
  [The kill switch outranks the exits](#the-kill-switch-outranks-the-exits):
  a halted system leaves its open positions with no stop, no target and no
  max-hold limit acting on them until `--resume`.

Both of these commands act on the real book, so run them without `PAPER_DB`.

## Cadences

| Cadence | Default | What it does |
| --- | --- | --- |
| Monitor | 60s | Signals, paper orders, fills, positions, equity snapshot |
| Research | 300s | Backtest grid across symbols, timeframes and strategy families; strategy registration |

Research is the expensive half, so it runs on its own, slower clock. A monitor
tick runs research only when the research interval has elapsed, then does its own
work. A monitor cadence slower than the research cadence is clamped down to
match it, because the inverse would mean research ran more often than it was
meant to.

```sh
uv run python scripts/paper_worker.py --monitor-seconds 30 --research-seconds 120
```

### No overlapping cycles

A research cycle can outlast a monitor tick. A tick that arrives while one is
still running is **skipped**, not queued: the cycle guard is a single
`threading.Lock` in `services/paper/worker.py` acquired non-blocking, and a
failed acquisition records `skipped_busy` and returns immediately.

Two limits worth knowing. The loop is single-threaded, so the guard is there for
a future threaded scheduler rather than for today's code - nothing today can
actually overlap. And it is a **process-local** lock: it does not stop a second
`paper_worker.py` process from running against the same `PAPER_DB` at the same
time. Run one worker per database file.

## Watchlist and candles

```sh
uv run python scripts/paper_worker.py --symbols BTCUSDT,SOLUSDT --interval 15m
```

Both are persisted in the config table, so a restart keeps them; nothing has to
be passed again. The default watchlist is nine liquid Binance pairs:

```
BTCUSDT, ETHUSDT, SOLUSDT, BNBUSDT, XRPUSDT, ADAUSDT, DOGEUSDT, LINKUSDT, AVAXUSDT
```

They are chosen for depth and history, not for a story: a thin pair cannot be
backtested honestly. The default timeframe is `5m`. Symbols are uppercased and
de-duplicated, and an unknown interval falls back to `5m` rather than being
rejected. Intervals accepted by config: `1m 3m 5m 15m 30m 1h 2h 4h 1d`. The
engine itself can be asked for `1m 3m 5m 15m 30m 1h` (`engine.ALLOWED_INTERVALS`),
which includes the `15m` and `1h` that research validates on.

The engine reads the watchlist from the config table on every cycle, so a
`--symbols` change lands on the next cycle and there is no second list to edit.

## Research

A research cycle backtests the whole parameter grid for each symbol on each
research timeframe, then registers the results that clear the validation bar.

### Timeframes

`research_intervals` (default `5m,15m,1h`) says which timeframes each symbol is
validated on. They are validated separately and produce separately bound
strategies, because a 20-period lookback on 15m candles is not the same strategy
as the same lookback on 5m ones. `interval` is the timeframe the worker trades;
`research_intervals` is the set it learns on.

A symbol whose candles are missing or too short for one timeframe is **skipped
and counted**, not raised: a feed that carries 5m and 1h but not 15m is normal,
and one absent bar series must not abort a cycle. Skips appear in the
`research_done` decision and in the log.

### The per-cycle cap

`research_max_experiments` (default 300) bounds how many backtests one cycle may
run. Nine symbols x three timeframes x seven families is more work than a cycle
should hold, so the grid is cut off rather than allowed to run long.

When the cap bites, the cycle logs `research_capped` with the planned count, the
number left out, and the cap itself, and says so on the logger. The watchlist is
**rotated by one symbol per cycle**, so a capped cycle still works its way around
the whole list rather than leaving the tail of it permanently unresearched.

### Families

Seven families, different in kind rather than in tuning:

| Family | What it looks at |
| --- | --- |
| `sma_cross` | Moving-average order |
| `donchian` | Price at the edge of the recent range |
| `rsi_revert` | Exhaustion fade on a bounded oscillator |
| `momentum` | Rate of change |
| `atr_breakout` | Range break measured in multiples of true range |
| `volume_trend` | Moving-average trend, taken only when volume confirms it |
| `zscore_revert` | Band fade on the z-score of price against its average |

Every family is a pure function of the candle list, deterministic and
stdlib-only, so the same candles always produce the same signal and a result can
be reproduced exactly. Each has four parameter points in `strategies.PARAM_GRID`.

### The validation bar

A backtest becomes a registered strategy only if it clears all of:

| Key | Default | Meaning |
| --- | --- | --- |
| `min_trades` | 2 | Fewest closed trades in the backtest |
| `min_net_pnl` | 0.0 | Net P&L in account currency, fees included |
| `max_drawdown_pct` | 5.0 | Peak-to-trough loss as a percentage of the account |

These are config keys, so the bar is explicit rather than buried in the code.
`engine.passes_validation(metrics)` returns `(passed, reason)` and names the bar
that failed, so a rejection is readable rather than a bare "invalid".

Two rules the configuration cannot talk its way past:

- **A negative net P&L is never registered as active**, whatever `min_net_pnl`
  is set to. A losing backtest is the clearest signal there is, and lowering the
  bar does not turn it into a strategy.
- The defaults are lower than the old hard-coded bar (`trades >= 3`) on purpose,
  so a setup that fires twice is still evidence. They are not lowered further
  than that.

### Strategies are bound to a timeframe

A registered strategy id carries the timeframe it was validated on:

```
donchian-BTCUSDT-15m
```

`engine.trading_cycle` parses that id and **skips any strategy whose timeframe is
not the one currently being traded**, logging how many it skipped. A strategy
validated before this change has no timeframe in its id and stays tradable,
because refusing it would silently unregister work an earlier cycle did
properly.

One consequence worth knowing: `planner._strategies_for` (in `planner.py`, not
owned by this work) matches a strategy to a symbol by taking everything after the
first dash in its id, so a timeframe-bound id is invisible to it. To keep the
planner working, research **also** writes the best validated strategy per family
and symbol under the legacy `family-SYMBOL` id, marked with the
`research-symbol-mirror` hypothesis and preferring the timeframe the worker
actually trades. Those rows carry the same metrics and the planner re-validates
them on its own bar before anything is traded. The proper fix belongs in
`planner._strategies_for`, which should parse the timeframe out of the id the way
`engine.parse_strategy_id` does.

## One command, one trade

`planner.plan()` returns **one** plan, or a refusal. Never a list. It walks the
watchlist, throws out every candidate that does not qualify, and keeps the single
best one. Whichever runner asked gets that one plan and can place at most that
one order:

- The `./paper` console places exactly one trade per invocation and then stops.
  It is the only command a person needs: bare `./paper` starts the worker if it
  is not already running, asks for one plan, places it, prints a plain-English
  summary and exits. `./paper trade` is the same thing spelled out.
- `scripts/profitable_trade.py` plans once behind a guard that refuses a second
  attempt, and places only when `--yes` is given.
- `scripts/paper_run.py` calls the planner exactly once per cycle and executes
  exactly once. There is no retry and no second call inside a cycle, so one
  cycle can never place two orders.

The same rule holds when the planner refuses. A refusal ends that command. The
agent does not walk the watchlist looking for a coin that will say yes, does not
lower `--max-risk` on its own, and does not retry on a different symbol: a
refusal is the planner's answer, and answering twice with different coins is the
same as having no threshold at all.

The one retry that does exist is documented in the agent skill, and only for one
reason. If the analyst could not be reached, the agent may re-run once with
`--allow-rules-only`, which plans and places in one go and marks the trade as
unreviewed. That trade is never described as reviewed.

## Sizing

Size is derived from **risk distance**, not from a fixed quantity:

```
qty = risk_budget / abs(entry - stop)
```

The planner computes the distance from the entry to the stop and divides the
risk budget by it. A wider stop therefore buys a smaller position, which is the
point: the money at risk is what the budget says it is, rather than what the
price happened to be.

That number is then **capped**, and a cap can only ever make it smaller:

| Cap | Key | Default | What it bounds |
| --- | --- | --- | --- |
| Notional ceiling | `max_position_notional_usd` | 50.0 | The most one position may be **worth**, in USD. Set it to 0 to leave the exposure cap as the only notional ceiling. |
| Exposure cap | `max_exposure_pct` | 0.5 | Total notional open across all positions, as a share of equity. |
| Available cash | `cash` (live balance) | 100.0 | A buy pays notional plus fee out of the same balance; a short posts its whole notional as margin (`short_margin_locked`). |
| Daily loss | `max_daily_loss` | 20 | Reaching it refuses every new trade for the day. On the default 100 USD account this is deliberately loose; set it to what you would actually tolerate. |
| Minimum position | `MIN_POSITION_NOTIONAL_USD` | 5.0 | Below this a trade is refused rather than placed as dust. |
| Minimum risk | `MIN_RISK_FRACTION` | 0.01 | A plan must risk at least 1% of its budget, or it is refused. |

The account starts at **100 USD** (`starting_cash`). One position may be worth at
most 50 USD, at most half the account may be open at once, and the default risk
budget is 2 USD.

The notional cap is in **currency, deliberately**. The old rule was a fixed
quantity, `max_position_qty` (default 0.01), and 0.01 units is about 840 USD of
BTC and about 1.18 USD of SOL. A quantity is not comparable across coins, so a
5 USD risk budget became 0.0024 USD of real risk on one coin and five times the
intended risk on another, and `risk_usd` rounded to 0.00 in every report that
printed it. **`max_position_qty` is no longer read at all.** It is still in every
existing database, but honouring it as a ceiling would preserve exactly the bug
it caused. Operators who want a per-coin ceiling now state it in currency with
`max_position_notional_usd`.

Two floors apply after every ceiling, because a position too small to express its
own edge says nothing: it must still be worth at least
`MIN_POSITION_NOTIONAL_USD` (10.0) and must still carry at least
`MIN_RISK_FRACTION` (1%) of the budget. Below either, the planner refuses and
names the numbers. It never fills a dust position to make a plan succeed.

Quantities are floored to `QTY_DECIMALS` (12), so float rounding can only shrink
a position, never push the realised risk back over the budget.

An exit is never sized at all. `plan_size` is entry sizing and is not used on
the way out; an exit is always the full quantity.

The budget arrives from the command line as `--max-risk`, in USD, and defaults
to `planner.DEFAULT_MAX_RISK` (5.0, the same literal as the engine's
`DEFAULT_RISK_BUDGET_USD`). Only the user may change it. An agent that lowers it
to force a trade through has not produced a trade, it has produced a different,
smaller one.

Every key above lives in the paper database's `config` table, not in code, so an
operator can change any of them without editing anything:

```sh
PAPER_DB=/tmp/scratch-paper.db uv run python -c "import sys; sys.path.insert(0,'.'); from services.paper import db; db.init(); db.set_many({'max_position_notional_usd': 250, 'max_daily_loss': 20}); print(db.get_all())"
```

## Exits

Every monitoring cycle calls `planner.manage_open_positions()` **before** it
looks for new trades, so a stop is acted on in the tick it is seen rather than
after the next entry has been considered. It walks every open position, not only
the watchlist's, so a position whose symbol has been dropped from the watchlist
is still managed rather than orphaned.

For each one it reads the live mark and closes the position when

- the mark reaches the stop-loss, or
- the mark reaches the take-profit, or
- the position has been held longer than `max_hold_minutes` (default 15), or
- the strategy that opened it now signals the opposite direction.

Those four are checked in that order, and it is deliberate: a bracket the
operator can see outranks a judgement the engine makes up. A fifth reason,
`manual`, is a deliberate exit asked for by name.

An exit is always the **full position quantity**. `engine.plan_size` is entry
sizing and shrinks as free cash falls, so sizing an exit from it would leave most
of a position open exactly when cash is low. Exits go through `engine._close`,
the same accounting path as an entry, so cash, realised P&L and fees stay
consistent.

Every one of those exits is **enforced each cycle**, not merely recorded. The
stop and target are stored on the position row, and that row is what
`manage_open_positions()` reads on every single monitoring cycle and acts on. One
bad position is skipped and journalled, never raised, so the remaining positions
are still managed.

There are no resting reduce-only orders, and nothing watches the price between
cycles. A move that crosses the stop and comes back inside one cycle is not seen
until the next check, so the exit price is the mark at that check, not the level
the mark touched. The default 60s monitor cadence is the worst-case delay.

A strategy signal is only trusted with at least `MIN_SIGNAL_CANDIDLES` (30)
candles on the timeframe that strategy was validated on. A position with no
strategy row, an unknown family or too little history is simply held, because an
exit fired on a guess is worse than a late one.

To exit deliberately:

```bash
PAPER_DB=/tmp/scratch-paper.db uv run python scripts/profitable_trade.py --close BTCUSDT
```

### The kill switch outranks the exits

This is the one place where "stop everything" and "manage what is open" pull
against each other, and the kill switch wins. While `halted` is set, **no order
of any kind is placed, an exit included**: `manage_open_positions()` returns
before it looks at a single position, and `close_position()` refuses by name.

**State this plainly: a halted system leaves its open positions unmanaged.** Their
stops, targets and max-hold limits all wait. A position can keep moving against
you, well past the level that would have closed it, for as long as the system
stays halted. Nothing is closed for you, and nothing is protected.

That is a deliberate choice, not an oversight: an exit is an order, and the
meaning of a kill switch is that no orders are sent. What the system does about
it is refuse to be quiet about it. Every halted cycle writes a
`position_exit_halted` journal row naming the positions it is not managing, logs
a warning, and the status line names them too, so "halted" never reads as
"nothing is open".

Resume with `./paper`, or:

```sh
uv run python scripts/paper_worker.py --resume
```

## Health

Every cycle writes two things.

The `heartbeat` row (one row, id 1): timestamp, status (`ok`, `error` or
`halted`), the error text, and the cycle number. One bad cycle is recorded and
the loop continues.

A `worker_health` JSON snapshot in the config table, holding what the heartbeat
table has no column for: symbols, interval, cycle start time, total/monitor/
research durations, whether research ran, the last successful cycle, and the
next scheduled cycle and research times.

`--status` reads all of it straight from the database, so it works from any
shell, whether or not a worker is running:

```sh
uv run python scripts/paper_worker.py --status
```

```
Paper worker status
  kill switch      : running
  watchlist        : BTCUSDT,SOLUSDT @ 15m
  monitor cadence  : 30s
  research cadence : 120s
  heartbeat        : ok at 2026-10-02 22:00:00
  cycle number     : 20
  last successful  : 2026-10-02 22:00:00
  next cycle       : 2026-10-02 22:00:30
  last error       : none
```

## Watching the analyst come back

The analyst gates every trade. When it cannot answer, the planner refuses, which
is correct and completely silent: an exhausted free allowance stops the system
and the only symptom is a refusal line in a log nobody is reading. The watcher
polls it and announces the moment availability flips back.

```sh
uv run python scripts/jev_watch.py                    # poll every 5 minutes
uv run python scripts/jev_watch.py --interval 60      # poll every minute
uv run python scripts/jev_watch.py --once             # one probe, 0 = up, 1 = down
uv run python scripts/jev_watch.py --max-minutes 120   # stop after two hours
uv run python scripts/jev_watch.py --no-notify         # journal only, no desktop banner
```

One line per poll. The line that matters is the flip:

```
2026-10-03 09:12:41 the analyst is still unavailable. The analyst's free daily allowance is used up, so it cannot review a trade right now. Add credits to the Zen account, or wait for the allowance to reset.
2026-10-03 17:04:02 the analyst is answering again.
```

The probe is a fixed, tiny request: one analyst call per interval, which is the
smallest useful sample of "can it answer right now". Whether it is *useful* stays
the planner's job.

Two journal rows, and the difference is deliberate.

- `analyst_recovered` is written once per unavailable -> available flip, and
  raises a macOS desktop notification at the same time. Recovery is the event an
  operator is waiting on, so it is recorded once and only once.
- `analyst_still_unavailable` is written at most once an hour while the outage
  lasts, carrying `jev.failure_reason()` in plain words. An overnight outage
  leaves a readable handful of rows instead of one row per poll. The hourly
  budget is read back out of the journal rather than held in memory, so it holds
  across separate `--once` invocations as well.

```sh
uv run python -c "import sys; sys.path.insert(0,'.'); from services.paper import db; [print(r['kind'], r['n']) for r in db.conn().execute(\"SELECT kind, COUNT(*) n FROM decisions WHERE kind LIKE 'analyst_%' GROUP BY kind\")]"
```

A transport failure is the expected case, not an error: it becomes one printed
line and a reason, never a traceback, and the script keeps polling. The
notification is best-effort, so a headless or non-macOS host loses the banner and
keeps the watcher. The first probe answering does *not* count as a recovery --
there was no known outage to recover from.

## No live orders

There is no live-order path anywhere in this system.

- `scripts/paper_worker.py` and `services/paper/worker.py` never import a broker.
  They call `engine.research_cycle()` and the planner, and write health rows.
- `engine.research_cycle()` only reads candles and writes experiments and
  strategy rows. Nothing in it can place an order.
- `engine.trading_cycle()` computes a price from the last candle, applies a
  synthetic 2bp slippage and 4bp fee, and inserts the order, the fill and the
  position into SQLite. Nothing leaves the process.
- The paper database is a plain file with no broker connection, credentials or
  API key. There is no code path that reads one to place an order.
- `services/paper/jev.py` is the only outbound call, and it asks an analyst model
  for a score. It has no order capability and returns to the engine only as a
  number.
- `services/paper/db.py` holds no broker adapter and exposes no order function.
- `scripts/jev_watch.py` only calls `jev.ask` and appends to `decisions`.

If you ever want this to trade real money, that is a different system, and it
should be built as one rather than by loosening a switch in here.

## Asking the agent for a trade

`/make-profitable-trade [SYMBOL] [MAX_RISK_USD]` places one paper trade in a
single request and reports it back in plain English. There is no confirmation
step: the request is the permission. Rules are in
`.opencode/skills/profitable-trade/SKILL.md`. See
[One command, one trade](#one-command-one-trade) for what the agent may and may
not do when it refuses.

When the analyst is rate-limited the planner refuses with
`analyst_unavailable` and the agent retries exactly once with
`--allow-rules-only`. That plan carries `analyst_bypassed: true`, which is what
lets `planner.execute` place it (`execute` refuses a plan that is neither
analyst-backed nor explicitly bypassed), and the dashboard records it as
unreviewed. The agent must say so in the report. Every other refusal (no edge,
not enough cash, exposure cap, an open position, the daily loss limit, stale
data) ends the run in one sentence, with no retry on another symbol.

The planner always re-plans inside `--yes`; there is no path that places an
order the planner did not produce in that same invocation. `MAX_RISK_USD` in
the request is the risk budget described under
[Sizing](#sizing); the agent may not choose it.
