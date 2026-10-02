# Paper Trading Worker

The autonomous worker runs market research and simulated trading forever. Every
position, fill and P&L it produces is simulated and stored in a local SQLite
file. See [No live orders](#no-live-orders) - it is not a warning, it is a
property of the code.

## Run it

```sh
uv run python scripts/paper_worker.py
```

That is the default: the autonomous loop, running until you stop it with Ctrl+C.

The database defaults to `data/paper.db`. Point it elsewhere with `PAPER_DB`:

```sh
PAPER_DB=/tmp/pw.db uv run python scripts/paper_worker.py
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
- `--halt` never closes open positions. Paper positions simply stop being
  managed until `--resume`.

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

## Exits

Every monitoring cycle calls `planner.manage_open_positions()` before it looks
for new trades. For each open position it reads the live mark and exits when

- the mark reaches the stop-loss, or
- the mark reaches the take-profit, or
- the position has been held longer than `max_hold_minutes` (default 15), or
- the strategy that opened it now signals the opposite direction.

An exit always sells the whole position. `engine.plan_size` is entry sizing and
shrinks as free cash falls, so using it on the way out would leave most of a
position open when cash is low. Exits go through `engine._close`, the same
accounting path as an entry, so cash, realised P&L and fees stay consistent.

There are no resting reduce-only orders. The stop and target live on the
position row and are checked once per cycle, so a move inside one cycle is not
caught until the next check.

To exit deliberately:

```bash
uv run python scripts/profitable_trade.py --close BTCUSDT
```

The kill switch is authoritative: while halted, no exit is sent either, and the
status names the positions left unmanaged until `./paper` resumes.

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
`.opencode/skills/profitable-trade/SKILL.md`.

When the analyst is rate-limited the planner refuses with
`analyst_unavailable` and the agent retries exactly once with
`--allow-rules-only`. That plan carries `analyst_bypassed: true`, which is what
lets `planner.execute` place it (`execute` refuses a plan that is neither
analyst-backed nor explicitly bypassed), and the dashboard records it as
unreviewed. The agent must say so in the report. Every other refusal — no edge,
not enough cash, exposure cap, an open position, the daily loss limit, stale
data — ends the run in one sentence, with no retry on another symbol.

The planner always re-plans inside `--yes`; there is no path that places an
order the planner did not produce in that same invocation.
