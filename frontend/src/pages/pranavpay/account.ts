import { baseAsset, type EquityPoint, type FillRow } from './derive'
import type { PaperEquityPoint, PaperFill, PaperPosition, PaperState } from './usePaperState'

/**
 * The virtual trading account, read from the paper ledger.
 *
 * Paper trading is permanent here, so the paper portfolio *is* the account and
 * every account figure on this surface comes from `/api/paper/state`. The
 * Binance testnet books are a separate sandbox and live in their own section;
 * nothing in this file may be fed a testnet number.
 *
 * Every figure is nullable. The ledger is the only thing that may claim a
 * figure exists, and an unreported one reads as `NOT_REPORTED` rather than as a
 * zero that would look like a balance.
 */

/** What any figure the ledger did not report reads as. Never a zero. */
export const NOT_REPORTED = 'not reported'

/** The one name the virtual account is given wherever it is headed. */
export const ACCOUNT_LABEL = 'Virtual account'

/** The one name the Binance books are given. It says what they are, in full. */
export const SANDBOX_LABEL = 'Testnet sandbox (not your money)'

/** The command that starts the worker that fills the ledger. */
export const PAPER_COMMAND = './paper'

/** A finite number, or null. A missing figure is never a zero. */
export function figure(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

/**
 * The sum of what is open, at the marks the ledger reports.
 *
 * Arithmetic on reported figures, so it is a derived value and is labelled as
 * one. Null the moment a position reports no mark: a sum that quietly dropped
 * an unpriced leg would understate exposure, which is the one direction this
 * must not err in.
 */
export function openPositionValue(positions: PaperPosition[]): number | null {
  if (positions.length === 0) return 0
  let total = 0
  for (const position of positions) {
    const mark = figure(position.mark)
    const qty = figure(position.qty)
    if (mark === null || qty === null) return null
    total += Math.abs(qty) * mark
  }
  return total
}

/** Gross value traded, from the reported fills. Null if any price is missing. */
export function tradedValue(fills: PaperFill[]): number | null {
  if (fills.length === 0) return 0
  let total = 0
  for (const fill of fills) {
    const qty = figure(fill.qty)
    const price = figure(fill.price)
    if (qty === null || price === null) return null
    total += Math.abs(qty) * price
  }
  return total
}

export interface PaperAccount {
  startingCash: number | null
  /** Cash the next virtual order can spend. */
  cash: number | null
  /** Cash plus open P&L, less margin held against shorts. */
  equity: number | null
  realized: number | null
  unrealized: number | null
  fees: number | null
  peakEquity: number | null
  /** Percent below the peak the ledger recorded. */
  drawdown: number | null
  shortMarginLocked: number | null
  /** Derived: the marks of the open positions, summed. */
  openPositionValue: number | null
  /** Cash as a share of equity, or null when equity is unreported or zero. */
  cashShare: number | null
  /** Open value as a share of equity, on the same terms. */
  exposureShare: number | null
  /** Gross value traded across the fills the ledger returned. */
  tradedValue: number | null
  positions: PaperPosition[]
  fills: PaperFill[]
  equityCurve: PaperEquityPoint[]
  strategies: PaperState['strategies']
  worker: PaperState['worker']
  /**
   * No fill, no position and no equity point: the ledger has never recorded a
   * cycle. The page says so and points at `./paper`, rather than printing a
   * starting balance that reads like money.
   */
  virgin: boolean
}

/**
 * The account as the ledger reports it, or null when there is no ledger.
 *
 * Nothing here is inferred from the exchange, and no default is substituted for
 * a figure the ledger left out.
 *
 * One exception, and it is deliberate. A ledger the worker has never written
 * to reports a zero for every balance it has not computed yet, and a card that
 * prints `$0.00` reads as money that exists rather than as nothing having
 * happened. So while the ledger is virgin every money figure is reported here
 * as absent, and the pages render "not reported" and point at `./paper`.
 * Starting capital is the one figure kept, because the ledger genuinely seeds
 * it and it is what the account will hold once it starts trading.
 */
export function paperAccount(state: PaperState | null): PaperAccount | null {
  if (!state) return null

  const positions = Array.isArray(state.positions) ? state.positions : []
  const fills = Array.isArray(state.fills) ? state.fills : []
  const equityCurve = Array.isArray(state.equity_curve) ? state.equity_curve : []
  const virgin = fills.length === 0 && positions.length === 0 && equityCurve.length === 0
  const funded = (value: number | null): number | null => (virgin ? null : value)

  const equity = funded(figure(state.equity))
  const cash = funded(figure(state.cash))
  const open = funded(openPositionValue(positions))

  return {
    startingCash: figure(state.starting_cash),
    cash,
    equity,
    realized: funded(figure(state.realized)),
    unrealized: funded(figure(state.unrealized)),
    fees: funded(figure(state.fees)),
    peakEquity: funded(figure(state.peak_equity)),
    drawdown: funded(figure(state.drawdown)),
    shortMarginLocked: funded(figure(state.short_margin_locked)),
    openPositionValue: open,
    cashShare: share(cash, equity),
    exposureShare: share(open, equity),
    tradedValue: funded(tradedValue(fills)),
    positions,
    fills,
    equityCurve,
    strategies: Array.isArray(state.strategies) ? state.strategies : [],
    worker: state.worker ?? null,
    virgin,
  }
}

/** A share of equity, or null when the ratio would divide by nothing. */
function share(part: number | null, whole: number | null): number | null {
  if (part === null || whole === null || whole <= 0) return null
  return (part / whole) * 100
}

/**
 * One line telling the reader which of the two accounts a number came from.
 * Every card that shows a sandbox figure carries one, so a testnet balance can
 * never be read as the user's money.
 */
export function sandboxNote(): string {
  return `${SANDBOX_LABEL} — these balances are Binance practice funds, not yours.`
}

/** The empty state every account section shows before the worker has run. */
export const VIRGIN_TITLE = 'No virtual trades yet'

export const VIRGIN_DETAIL = `The paper ledger is empty because the worker has not run. Start it with ${PAPER_COMMAND}, then this page fills itself.`

export const VIRGIN_POSITIONS_TITLE = 'No virtual positions yet'

export const VIRGIN_POSITIONS_DETAIL = `Nothing is open in the virtual account. A position appears here the moment ${PAPER_COMMAND} places one.`

/** The one sentence that separates the two accounts for a first-time reader. */
export const TWO_ACCOUNTS =
  'PranavPay keeps two accounts apart. Yours is virtual: the paper worker trades it against live prices using no real money, and every figure here comes from its own ledger. The OpenAlgo terminal runs separately on a Binance testnet sandbox, and its balances are not yours.'

/** Paper fills as ledger rows. `pnl` is null because the engine reports none per fill. */
export function paperFillRows(fills: PaperFill[]): FillRow[] {
  return fills.map((fill, index) => {
    const quantity = figure(fill.qty) ?? 0
    const price = figure(fill.price) ?? 0
    return {
      id: `${fill.order_id}-${fill.ts ?? index}-${index}`,
      symbol: fill.symbol,
      asset: baseAsset(fill.symbol),
      action: String(fill.side ?? '').toUpperCase() === 'SELL' ? 'SELL' : 'BUY',
      quantity,
      price,
      value: Math.abs(quantity * price),
      // The engine reports realised P&L as one account total, not per fill.
      // Leaving it null keeps the fill out of every per-fill sum rather than
      // attributing the account's result to whichever fill happened to be last.
      pnl: null,
      fee: figure(fill.fee),
      slippage: figure(fill.slippage),
      orderId: fill.order_id ?? '',
      timestamp: typeof fill.ts === 'number' && Number.isFinite(fill.ts) ? fill.ts * 1000 : null,
      product: 'Paper',
      venue: 'Virtual',
      at: typeof fill.ts === 'number' && Number.isFinite(fill.ts) ? new Date(fill.ts * 1000) : null,
    }
  })
}

/**
 * The equity the ledger actually stored, oldest first.
 *
 * This is a stored series, not one reconstructed from fills, so every point on
 * it was measured. The head carries the live figure so the end of the line
 * follows the marks rather than the last snapshot on disk.
 */
export function paperEquitySeries(account: PaperAccount): EquityPoint[] {
  const points: EquityPoint[] = []
  for (const point of account.equityCurve) {
    const ts = figure(point.ts)
    const equity = figure(point.equity)
    if (ts === null || equity === null) continue
    points.push({ t: ts * 1000, equity, live: false })
  }
  points.sort((a, b) => a.t - b.t)
  if (account.equity !== null) points.push({ t: Date.now(), equity: account.equity, live: true })
  return points
}

/**
 * Realised result per calendar day, from the ledger's own equity points.
 *
 * The engine stores realised as a running total, so a day's result is the
 * difference between one point and the one before it. The first point has
 * nothing before it and contributes nothing: its opening balance is not a
 * result, and treating it as one would invent a day.
 */
export function realisedByDay(account: PaperAccount): Map<string, number> {
  const byDay = new Map<string, number>()
  const ordered = [...account.equityCurve]
    .filter((point) => figure(point.ts) !== null && figure(point.realized) !== null)
    .sort((a, b) => (figure(a.ts) ?? 0) - (figure(b.ts) ?? 0))

  let previous: number | null = null
  for (const point of ordered) {
    const realized = figure(point.realized)
    const ts = figure(point.ts)
    if (realized === null || ts === null) continue
    if (previous !== null) {
      const delta = realized - previous
      if (delta !== 0) {
        const day = new Date(ts * 1000).toISOString().slice(0, 10)
        byDay.set(day, (byDay.get(day) ?? 0) + delta)
      }
    }
    previous = realized
  }
  return byDay
}

export interface AccountAlert {
  id: string
  severity: 'info' | 'warning'
  title: string
  detail: string
}

/**
 * Notices drawn from what the ledger reports.
 *
 * There is no threshold table and no invented alarm: each one fires on a
 * reported fact, and the list is empty when nothing is wrong rather than padded
 * to look busy.
 */
export function accountAlerts(
  account: PaperAccount | null,
  { stale }: { stale: boolean }
): AccountAlert[] {
  if (!account) return []
  const found: AccountAlert[] = []
  if (stale) {
    found.push({
      id: 'stale',
      severity: 'warning',
      title: 'Figures may be out of date',
      detail: `The last refresh did not reach the paper ledger. Values shown are the last ones received.`,
    })
  }
  if (account.drawdown !== null && account.drawdown > 5) {
    found.push({
      id: 'drawdown',
      severity: 'warning',
      title: 'The account is below its peak',
      detail: `${account.drawdown.toFixed(2)}% under the ${account.peakEquity === null ? 'recorded peak' : `$${account.peakEquity.toFixed(2)}`} the ledger remembers.`,
    })
  }
  if (account.cash !== null && account.cash <= 0 && account.positions.length > 0) {
    found.push({
      id: 'no-cash',
      severity: 'warning',
      title: 'No virtual cash left',
      detail: 'Positions are still open but the account has no cash for another one.',
    })
  }
  if (account.worker?.error) {
    found.push({
      id: 'worker-error',
      severity: 'info',
      title: 'The paper worker reported a problem',
      detail: account.worker.error,
    })
  }
  if (account.virgin) {
    found.push({
      id: 'virgin',
      severity: 'info',
      title: VIRGIN_TITLE,
      detail: VIRGIN_DETAIL,
    })
  }
  return found
}
