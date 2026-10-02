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
| Research | 300s | Backtest grid across the strategy families, strategy registration |

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
be passed again. Defaults are `BTCUSDT,SOLUSDT,ETHUSDT` and `5m`. Symbols are
uppercased and de-duplicated, and an unknown interval falls back to `5m` rather
than being rejected. Allowed intervals: `1m 3m 5m 15m 30m 1h 2h 4h 1d`.

### Watchlist limitation

**The configured watchlist does not yet change what the engine trades.**
`services/paper/engine.py` reads its symbols from the module-level constant
`WATCHLIST` and its timeframe from a `"5m"` literal inside `_candles`. Neither is
a parameter, a config read, or anything the worker can set without editing
`engine.py`, which is out of scope here.

So today:

- `--symbols` and `--interval` are stored, validated, shown by `--status`, and
  recorded in every heartbeat, so the intended configuration is durable and
  visible.
- The symbols actually backtested and traded remain `engine.WATCHLIST`, and the
  candles remain `5m`.

Until the engine takes a watchlist as an argument, treat the config as the
intended target rather than the active one. Closing the gap is a one-line change
in the engine (`research_cycle(watchlist)` and `trading_cycle(watchlist)`, with
`WATCHLIST` as the default) plus passing `cfg.symbols` and `cfg.interval` from
`PaperWorker._run_cycle_locked`, which is already the single call site for both.

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

## No live orders

There is no live-order path anywhere in this system.

- `scripts/paper_worker.py` and `services/paper/worker.py` never import a broker.
  They call `engine.research_cycle()` and `engine.trading_cycle()` and write
  health rows.
- `engine.trading_cycle()` computes a price from the last candle, applies a
  synthetic 2bp slippage and 4bp fee, and inserts the order, the fill and the
  position into SQLite. Nothing leaves the process.
- The paper database is a plain file with no broker connection, credentials or
  API key. There is no code path that reads one to place an order.
- `services/paper/jev.py` is the only outbound call, and it asks an analyst model
  for a score. It has no order capability and returns to the engine only as a
  number.
- `services/paper/db.py` holds no broker adapter and exposes no order function.

If you ever want this to trade real money, that is a different system, and it
should be built as one rather than by loosening a switch in here.