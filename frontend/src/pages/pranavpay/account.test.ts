import { describe, expect, it } from 'vitest'
import {
  ACCOUNT_LABEL,
  NOT_REPORTED,
  openPositionValue,
  type PaperPosition,
  type PaperState,
  paperAccount,
  paperEquitySeries,
  paperFillRows,
  realisedByDay,
  SANDBOX_LABEL,
} from './account'

function position(overrides: Partial<PaperPosition> = {}): PaperPosition {
  return {
    symbol: 'BTCUSDT',
    side: 'BUY',
    qty: 0.01,
    entry: 80000,
    mark: 84450,
    mark_live: true,
    unrealized: 44.5,
    strategy_id: 'strat-1',
    opened: 1790973353,
    ...overrides,
  }
}

/** A ledger with one fill, one position and one equity point. */
function liveState(): PaperState {
  return {
    starting_cash: 1000,
    cash: 999.47,
    virtual_balance: 999.47,
    short_margin_locked: 0,
    equity: 999.89,
    realized: 0.2757,
    unrealized: 0.4135,
    fees: 0.4004,
    peak_equity: 999.8877,
    drawdown: 0,
    positions: [position()],
    fills: [
      {
        order_id: 'plan-1',
        symbol: 'BTCUSDT',
        side: 'SELL',
        qty: 0.0059,
        price: 84468.89,
        fee: 0.1999,
        slippage: 16.89,
        ts: 1790973353.96429,
      },
    ],
    equity_curve: [
      {
        ts: 1790973353,
        cash: 999.4,
        equity: 999.7,
        realized: 0.27,
        unrealized: 0.3,
        fees: 0.4,
        slippage: 16.8,
        drawdown: 0,
      },
      {
        ts: 1790974294,
        cash: 999.47,
        equity: 999.89,
        realized: 0.2757,
        unrealized: 0.4135,
        fees: 0.4004,
        slippage: 16.89,
        drawdown: 0,
      },
    ],
    strategies: [],
    experiment_count: 105,
    worker: { ts: 1790974611.4, status: 'refused', error: null, cycle: 48 },
    decisions: [],
    jev_verdicts: 3,
    generated_at: 1790974611.5,
  }
}

/** A ledger the worker has never written to. */
function virginState(): PaperState {
  return {
    ...liveState(),
    cash: 0,
    equity: 0,
    realized: 0,
    unrealized: 0,
    fees: 0,
    peak_equity: 0,
    positions: [],
    fills: [],
    equity_curve: [],
    worker: null,
    experiment_count: 0,
  }
}

describe('labels', () => {
  it('names the sandbox so it can never be read as the account', () => {
    expect(SANDBOX_LABEL).toBe('Testnet sandbox (not your money)')
  })

  it('names the virtual account distinctly', () => {
    expect(ACCOUNT_LABEL).toBe('Virtual account')
  })
})

describe('paperAccount', () => {
  it('reads every figure from the ledger, never from a broker payload', () => {
    const account = paperAccount(liveState())
    expect(account?.cash).toBe(999.47)
    expect(account?.equity).toBe(999.89)
    expect(account?.realized).toBe(0.2757)
    expect(account?.unrealized).toBe(0.4135)
    expect(account?.fees).toBe(0.4004)
    expect(account?.startingCash).toBe(1000)
  })

  it('sums open positions at their reported marks', () => {
    const account = paperAccount(liveState())
    expect(account?.openPositionValue).toBeCloseTo(844.5, 6)
  })

  it('reports zero exposure for an account holding nothing', () => {
    const account = paperAccount({ ...liveState(), positions: [] })
    expect(account?.openPositionValue).toBe(0)
  })

  it('refuses to total exposure when a mark is missing', () => {
    const state = liveState()
    const broken = { ...position(), mark: Number.NaN }
    const account = paperAccount({ ...state, positions: [broken] })
    // A sum that dropped an unpriced leg would understate what is at risk.
    expect(account?.openPositionValue).toBeNull()
  })

  it('has no share of equity when equity is unreported', () => {
    const state = { ...liveState(), equity: Number.NaN }
    expect(paperAccount(state)?.cashShare).toBeNull()
    expect(paperAccount(state)?.exposureShare).toBeNull()
  })

  it('has no share of a zero equity rather than dividing by it', () => {
    const state = { ...liveState(), equity: 0 }
    expect(paperAccount(state)?.exposureShare).toBeNull()
  })

  it('is null when there is no ledger at all', () => {
    expect(paperAccount(null)).toBeNull()
  })

  it('marks a ledger the worker has never written to as virgin', () => {
    expect(paperAccount(liveState())?.virgin).toBe(false)
    expect(paperAccount(virginState())?.virgin).toBe(true)
  })

  it('does not call a ledger virgin just because its balances are zero', () => {
    const state = { ...virginState(), fills: liveState().fills }
    expect(paperAccount(state)?.virgin).toBe(false)
  })

  it('reports no money at all on an untouched ledger, so no card prints a zero balance', () => {
    const account = paperAccount(virginState())
    // The ledger returns 0 for everything it has not computed; a card printing
    // that would read as money that exists.
    expect(account?.cash).toBeNull()
    expect(account?.equity).toBeNull()
    expect(account?.realized).toBeNull()
    expect(account?.unrealized).toBeNull()
    expect(account?.fees).toBeNull()
    expect(account?.openPositionValue).toBeNull()
    expect(account?.cashShare).toBeNull()
    expect(account?.exposureShare).toBeNull()
    expect(account?.tradedValue).toBeNull()
  })

  it('still reports the starting capital a virgin ledger was seeded with', () => {
    // It is a real fact, and it is what the account will hold once it runs.
    expect(paperAccount(virginState())?.startingCash).toBe(1000)
  })

  it('reports a genuine zero on a ledger that has traded and come back to flat', () => {
    const state = { ...liveState(), cash: 0, equity: 0, realized: 0 }
    const account = paperAccount(state)
    expect(account?.virgin).toBe(false)
    expect(account?.cash).toBe(0)
    expect(account?.equity).toBe(0)
  })
})

describe('openPositionValue', () => {
  it('is zero, not null, for an empty book', () => {
    expect(openPositionValue([])).toBe(0)
  })

  it('takes the absolute size of a short', () => {
    const short = position({ side: 'SELL', qty: 0.02, mark: 50000 })
    expect(openPositionValue([short])).toBe(1000)
  })
})

describe('paperFillRows', () => {
  it('builds a row from a ledger fill and leaves its result unreported', () => {
    const [row] = paperFillRows(liveState().fills)
    expect(row?.symbol).toBe('BTCUSDT')
    expect(row?.asset).toBe('BTC')
    expect(row?.action).toBe('SELL')
    expect(row?.value).toBeCloseTo(0.0059 * 84468.89, 4)
    // The engine stores no realized result on a fill; inventing one would
    // attribute the account's total to whichever fill happened to be last.
    expect(row?.pnl).toBeNull()
    expect(row?.venue).toBe('Virtual')
    expect(row?.orderId).toBe('plan-1')
  })

  it('is stable and unique per fill', () => {
    const rows = paperFillRows(liveState().fills)
    expect(new Set(rows.map((row) => row.id)).size).toBe(rows.length)
  })
})

describe('paperEquitySeries', () => {
  it('orders stored points oldest first and ends on the live figure', () => {
    const series = paperEquitySeries(paperAccount(liveState())!)
    expect(series.map((point) => point.equity)).toEqual([999.7, 999.89, 999.89])
    expect(series[0].live).toBe(false)
    expect(series[series.length - 1].live).toBe(true)
  })

  it('has no live head when the ledger reported no equity', () => {
    const account = paperAccount({ ...liveState(), equity: Number.NaN })!
    expect(paperEquitySeries(account).some((point) => point.live)).toBe(false)
  })
})

describe('realisedByDay', () => {
  it('reports the change between successive stored totals, not the totals', () => {
    const byDay = realisedByDay(paperAccount(liveState())!)
    const values = [...byDay.values()]
    expect(values).toHaveLength(1)
    expect(values[0]).toBeCloseTo(0.0057, 6)
  })

  it('has nothing to report on an untouched ledger', () => {
    expect(realisedByDay(paperAccount(virginState())!).size).toBe(0)
  })

  it('does not treat the first stored total as a result', () => {
    const state = liveState()
    const single = { ...state, equity_curve: [state.equity_curve[1]] }
    expect(realisedByDay(paperAccount(single)!).size).toBe(0)
  })
})

describe('the unreported label', () => {
  it('is words, never a number', () => {
    expect(Number.isNaN(Number(NOT_REPORTED))).toBe(true)
  })
})
