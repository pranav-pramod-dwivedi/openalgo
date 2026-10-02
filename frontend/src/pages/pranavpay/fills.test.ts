import { describe, expect, it } from 'vitest'
import type { Trade } from '@/types/trading'
import type { FillRow } from './derive'
import { contributionRows, fillShare, fillsCsvFilename, fillsToCsv, realisedTotal } from './fills'

function trade(overrides: Partial<Trade> = {}): Trade {
  return {
    symbol: 'BTCUSDT',
    exchange: 'BINANCE',
    action: 'SELL',
    quantity: 1,
    average_price: 2,
    trade_value: 2,
    product: 'SPOT',
    orderid: 'order-1',
    timestamp: '2026-09-18 03:53:31',
    ...overrides,
  }
}

function fill(id: string, pnl: number | null, overrides: Partial<FillRow> = {}): FillRow {
  const raw = trade()
  return {
    id,
    symbol: raw.symbol,
    asset: 'BTC',
    action: raw.action,
    quantity: raw.quantity,
    price: raw.average_price,
    value: Math.abs(raw.trade_value),
    pnl,
    product: raw.product,
    venue: raw.exchange,
    at: new Date('2026-09-18T03:53:31.000Z'),
    raw,
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
})

describe('contributionRows', () => {
  it('ends its running sum exactly on the realised total', () => {
    const fills = [fill('a', 10), fill('b', null), fill('c', -4), fill('d', 7)]
    const rows = contributionRows(fills)
    expect(rows.map((row) => row.running)).toEqual([10, 6, 13])
    expect(rows[rows.length - 1]?.running).toBe(realisedTotal(fills))
  })

  it('skips open fills entirely', () => {
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
    const fills = [
      fill('a', 5, {
        product: 'MIS, intraday',
        raw: trade({ orderid: 'ord-"7"', timestamp: '2026-09-18 03:53:31' }),
      }),
    ]
    const csv = fillsToCsv(fills, realisedTotal(fills))
    const [header, body] = csv.trim().split('\n')
    expect(header).toBe(
      'symbol,side,quantity,avg_price,trade_value,realised_pnl,pnl_share_pct,product,venue,order_id,timestamp'
    )
    expect(body).toContain('"MIS, intraday"')
    expect(body).toContain('"ord-""7"""')
  })

  it('leaves realised columns blank for open fills', () => {
    const csv = fillsToCsv([fill('a', null)], 0)
    expect(csv).toContain(',,,SPOT,')
  })
})

describe('fillsCsvFilename', () => {
  it('names the file with the UTC date', () => {
    expect(fillsCsvFilename(new Date('2026-03-04T12:00:00.000Z'))).toBe(
      'pranavpay-fills-2026-03-04.csv'
    )
  })
})
