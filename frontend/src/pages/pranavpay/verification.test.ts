import { describe, expect, it } from 'vitest'
import type { PaperFill, PaperState } from './usePaperState'
import { denialText, paperVerification, parseFillExecution } from './usePaperState'

/**
 * The verification layer and the execution detail, read defensively.
 *
 * Both surfaces may not exist on the running engine yet, so the cases that
 * matter most are the absent ones: a payload with no `verdicts` key and a fill
 * with no pricing must come back as unreported, never as a zero that reads like
 * a pass.
 */

function state(overrides: Partial<PaperState> = {}): PaperState {
  return {
    starting_cash: 1000,
    cash: 1000,
    virtual_balance: 1000,
    short_margin_locked: 0,
    equity: 1000,
    realized: 0,
    unrealized: 0,
    fees: 0,
    peak_equity: 1000,
    drawdown: 0,
    positions: [],
    fills: [],
    equity_curve: [],
    strategies: [],
    experiment_count: 0,
    worker: null,
    decisions: [],
    jev_verdicts: 0,
    generated_at: 1790974611.5,
    ...overrides,
  }
}

function fill(overrides: Partial<PaperFill> = {}): PaperFill {
  return {
    order_id: 'plan-1',
    symbol: 'BTCUSDT',
    side: 'BUY',
    qty: 0.0059,
    price: 84450,
    fee: 0.19,
    slippage: 12.1,
    ts: 1790974294,
    ...overrides,
  }
}

describe('paperVerification', () => {
  it('reports no verdicts as unreported rather than as none denied', () => {
    const verification = paperVerification(state())
    expect(verification.verdictsReported).toBe(false)
    expect(verification.verdicts).toEqual([])
    expect(verification.rejectionsReported).toBe(false)
    expect(verification.allowedCount).toBeNull()
    expect(verification.deniedCount).toBeNull()
  })

  it('separates an empty verdict list from an absent one', () => {
    const verification = paperVerification(state({ verdicts: [] }))
    expect(verification.verdictsReported).toBe(true)
    expect(verification.verdicts).toEqual([])
  })

  it('reads a denial and keeps the reason it was given', () => {
    const verification = paperVerification(
      state({
        allowed_count: 41,
        denied_count: 3,
        verdicts: [
          {
            ts: 1790974611,
            symbol: 'BTCUSDT',
            side: 'BUY',
            allow: false,
            reasons: ['price_deviation'],
            checks: [
              { name: 'price_deviation', passed: false },
              { name: 'fresh_quote', passed: true },
            ],
          },
          { ts: 1790974500, symbol: 'ETHUSDT', side: 'SELL', allow: true, checks: [] },
        ],
      })
    )

    expect(verification.allowedCount).toBe(41)
    expect(verification.deniedCount).toBe(3)
    expect(verification.verdicts).toHaveLength(2)

    const [denied, allowed] = verification.verdicts
    expect(denied.allowed).toBe(false)
    expect(denied.reasons).toEqual(['price_deviation'])
    expect(denied.checks.map((check) => check.passed)).toEqual([false, true])
    expect(allowed.allowed).toBe(true)
    expect(allowed.reasons).toEqual([])
  })

  it('treats a bare number as no outcome at all, never as a status code', () => {
    const verification = paperVerification(
      state({ verdicts: [{ ts: 1, symbol: 'BTCUSDT', allow: 403, reason: 'liquidity' }] })
    )
    expect(verification.verdicts[0].allowed).toBeNull()
    expect(verification.verdicts[0].reasons).toEqual(['liquidity'])
  })

  it('reads the mirror denied flag, and a stored word', () => {
    const denied = paperVerification(state({ verdicts: [{ ts: 1, denied: true }] }))
    expect(denied.verdicts[0].allowed).toBe(false)
    const rejected = paperVerification(state({ verdicts: [{ ts: 1, verdict: 'rejected' }] }))
    expect(rejected.verdicts[0].allowed).toBe(false)
    const passed = paperVerification(state({ verdicts: [{ ts: 1, verdict: 'allow' }] }))
    expect(passed.verdicts[0].allowed).toBe(true)
  })

  it('falls back to the failed check when no reason was stored', () => {
    const verification = paperVerification(
      state({
        verdicts: [
          {
            ts: 1,
            symbol: 'BTCUSDT',
            allow: false,
            checks: [{ name: 'spread_too_wide', passed: false }],
          },
        ],
      })
    )
    expect(verification.verdicts[0].reasons).toEqual(['spread_too_wide'])
  })

  it('ignores a payload whose verdict rows are not objects', () => {
    const verification = paperVerification(state({ verdicts: ['nonsense', 7, null] as never }))
    expect(verification.verdicts).toEqual([])
    expect(verification.verdictsReported).toBe(true)
  })
})

describe('denialText', () => {
  it('rewrites a known slug as a sentence a trader can read', () => {
    expect(denialText('price_deviation')).toEqual({
      text: 'the price was too far from the live market price for the order to be trusted',
      spelledOut: true,
    })
    expect(denialText('Price Too Far').spelledOut).toBe(true)
  })

  it('never leaves a raw slug standing alone', () => {
    const unknown = denialText('phase_of_the_moon')
    expect(unknown.spelledOut).toBe(false)
    expect(unknown.text).toContain('phase of the moon')
    expect(unknown.text).not.toBe('phase_of_the_moon')
  })
})

describe('parseFillExecution', () => {
  it('reports nothing as unreported rather than as a zero spread', () => {
    const execution = parseFillExecution(fill())
    expect(execution.reported).toBe(false)
    expect(execution.basis).toBeNull()
    expect(execution.spread).toBeNull()
    expect(execution.latencyMs).toBeNull()
    expect(execution.remainderQty).toBeNull()
    expect(execution.fill).toBeNull()
  })

  it('reads the basis, the spread and the modelled latency off the fill', () => {
    const execution = parseFillExecution(
      fill({ fill_basis: 'ask', spread: 4.25, latency_ms: 18, unfilled_qty: 0 })
    )
    expect(execution.basis).toBe('ask')
    expect(execution.spread).toBe(4.25)
    expect(execution.latencyMs).toBe(18)
    expect(execution.remainderQty).toBe(0)
    expect(execution.fill).toBe('complete')
  })

  it('calls a fill partial when the order traded in part, and keeps the remainder', () => {
    const execution = parseFillExecution(
      fill({ fill_basis: 'bid', spread: 4.25, latency_ms: 18, filled_qty: 0.0041 })
    )
    expect(execution.fill).toBe('partial')
    // Derived from two reported figures, so it is a result and not a guess.
    expect(execution.remainderQty).toBeCloseTo(0.0018, 10)
  })

  it('prefers a stated remainder over the difference of two figures', () => {
    const execution = parseFillExecution(fill({ filled_qty: 0.0041, unfilled_qty: 0.0005 }))
    expect(execution.remainderQty).toBe(0.0005)
    expect(execution.fill).toBe('partial')
  })

  it('reads a nested execution block, and scales a latency stored in seconds', () => {
    const execution = parseFillExecution(
      fill({ execution: { basis: 'bid', spread: 0.0006, latency: { seconds: 0.018 } } })
    )
    expect(execution.basis).toBe('bid')
    expect(execution.spread).toBe(0.0006)
    expect(execution.spreadIsPercent).toBe(true)
    expect(execution.latencyMs).toBeCloseTo(18, 10)
  })

  it('reads a spread stored in basis points', () => {
    const execution = parseFillExecution(fill({ spread_bps: 6.5 }))
    expect(execution.spread).toBeCloseTo(0.00065, 10)
  })

  it('leaves the fill unstated when nothing about it was reported', () => {
    const execution = parseFillExecution(fill({ fill_basis: 'ask' }))
    expect(execution.reported).toBe(true)
    expect(execution.fill).toBeNull()
    expect(execution.remainderQty).toBeNull()
  })
})
