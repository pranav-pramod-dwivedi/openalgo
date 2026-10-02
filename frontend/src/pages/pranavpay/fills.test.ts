import { describe, expect, it } from 'vitest'
import type { FillRow } from './derive'
import { contributionRows, fillShare, fillsCsvFilename, fillsToCsv, realisedTotal } from './fills'

/**
 * A ledger row as the paper engine produces one.
 *
 * `pnl` is null for every row here unless a test asks for otherwise, because
 * that is the truth of the paper ledger: it reports realized P&L as one account
 * total and stores none on an individual fill. A fixture that gave fills a
 * realized result would assert a breakdown the engine never produces.
 */
function fill(id: string, pnl: number | null, overrides: Partial<FillRow> = {}): FillRow {
  return {
    id,
    symbol: 'BTCUSDT',
    asset: 'BTC',
    action: 'SELL',
    quantity: 0.01,
    price: 84450,
    value: 844.5,
    pnl,
    fee: 0.3378,
    slippage: 0.169,
    orderId: `order-${id}`,
    timestamp: 1790973353.96429 * 1000,
    product: 'Paper',
    venue: 'Virtual',
    at: new Date(1790973353964.29),
    ...overrides,
  }
}

describe('realisedTotal', () => {
  it('sums only closed fills and ignores open ones', () => {
    const fills = [fill('a', 10), fill('b', null), fill('c', -4)]
    expect(realisedTotal(fills)).toBe(6)
  })

  it('is zero when nothing has closed', () => {
    expect(realisedTotal([fill('a', null)])).toBe(0)
    expect(realisedTotal([])).toBe(0)
  })

  it('is zero for a paper ledger, which stores no per-fill result', () => {
    expect(realisedTotal([fill('a', null), fill('b', null)])).toBe(0)
  })
})

describe('contributionRows', () => {
  it('ends its running sum exactly on the realised total', () => {
    const fills = [fill('a', 10), fill('b', null), fill('c', -4), fill('d', 7)]
    const rows = contributionRows(fills)
    expect(rows.map((row) => row.running)).toEqual([10, 6, 13])
    expect(rows[rows.length - 1]?.running).toBe(realisedTotal(fills))
  })

  it('skips fills with no reported result entirely', () => {
    expect(contributionRows([fill('a', null)])).toEqual([])
  })
})

describe('fillShare', () => {
  it('divides by the realised total', () => {
    expect(fillShare(fill('a', 50), 200)).toBe(25)
  })

  it('is null when a share would mislead', () => {
    expect(fillShare(fill('a', null), 200)).toBeNull()
    expect(fillShare(fill('a', 50), 0)).toBeNull()
  })
})

describe('fillsToCsv', () => {
  it('covers every detail-panel field and quotes hostile cells', () => {
    const csv = fillsToCsv(
      [fill('a', 5, { product: 'Paper, intraday', orderId: 'ord-"7"' })],
      realisedTotal([fill('a', 5)])
    )
    const [header, body] = csv.trim().split('\n')
    expect(header).toBe(
      'symbol,side,quantity,avg_price,trade_value,fee,slippage,realised_pnl,pnl_share_pct,product,venue,order_id,timestamp'
    )
    expect(body).toContain('"Paper, intraday"')
    expect(body).toContain('"ord-""7"""')
  })

  it('leaves unreported columns blank rather than zero', () => {
    const csv = fillsToCsv([fill('a', null, { fee: null, slippage: null })], 0)
    // fee, slippage, realised and share are all unreported, so all four are
    // empty cells rather than zeros.
    expect(csv).toContain('844.5,,,,,Paper,')
  })

  it('writes the stored timestamp as ISO rather than the engine epoch', () => {
    const csv = fillsToCsv([fill('a', null)], 0)
    expect(csv.trim().split('\n')[1]).toContain('2026-10-02T20:35:53.964Z')
  })
})

describe('fillsCsvFilename', () => {
  it('names the file with the UTC date', () => {
    expect(fillsCsvFilename(new Date('2026-03-04T12:00:00.000Z'))).toBe(
      'pranavpay-fills-2026-03-04.csv'
    )
  })
})
