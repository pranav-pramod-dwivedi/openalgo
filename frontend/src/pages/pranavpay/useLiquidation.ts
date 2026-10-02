import { useMemo } from 'react'
import type { Position } from '@/types/trading'
import type { RawPosition, WalletSnapshot } from './useWalletSnapshot'
import { toAmounts } from './useWalletSnapshot'

/**
 * Risk fields the exchange reports on each futures position. They arrive on
 * both the positionbook rows and the funds positions after the backend
 * started passing `/fapi/v2/positionRisk` through verbatim; each stays
 * optional because Binance omits them for spot rows and reports nulls for
 * anything it does not estimate (cross-margin legs, flat legs).
 */
interface ReportedRisk {
  liquidation_price?: unknown
  margin_used?: unknown
  leverage?: unknown
}

function nullableNumber(value: unknown): number | null {
  const parsed = typeof value === 'number' ? value : Number.parseFloat(String(value ?? ''))
  return Number.isFinite(parsed) ? parsed : null
}

function riskOf(record: Position | RawPosition | undefined): {
  liquidationPrice: number | null
  marginUsed: number | null
  leverage: number | null
} {
  const source = (record ?? {}) as ReportedRisk
  const reported = nullableNumber(source.liquidation_price)
  return {
    // Binance sends 0 when it reports no liquidation price, which is not a
    // price and must never render as one.
    liquidationPrice: reported !== null && reported > 0 ? reported : null,
    marginUsed: nullableNumber(source.margin_used),
    leverage: nullableNumber(source.leverage),
  }
}

export interface LiquidationRow {
  symbol: string
  side: 'Long' | 'Short'
  quantity: number
  markPrice: number
  /** As reported by Binance, or null when the exchange did not report one. */
  liquidationPrice: number | null
  /** As reported by Binance, or null when the exchange did not report one. */
  marginUsed: number | null
  /** As reported by Binance, or null when the exchange did not report one. */
  leverage: number | null
  /**
   * Percentage move from the reported mark to the reported liquidation
   * price. This is the only number derived here, computed from those two
   * reported figures; null while either is unreported.
   */
  distancePercent: number | null
}

export interface LiquidationSummary {
  rows: LiquidationRow[]
  hasPositions: boolean
  /** Sum of the reported per-position margins, or null when none reported. */
  totalMarginCommitted: number | null
  tradable: number
}

/**
 * Liquidation and margin usage from the books the shell already fetched.
 *
 * Reads positionRisk-derived data through the existing snapshot: the
 * positionbook rows for marks and the funds positions for the reported
 * risk fields. No new fetch, no new backend route. Liquidation prices and
 * margins are displayed exactly as Binance reported them; only the
 * distance percentage is derived, from the two reported prices.
 */
export function useLiquidation(snapshot: WalletSnapshot): LiquidationSummary {
  return useMemo(() => {
    const rawBySymbol = new Map<string, RawPosition>()
    for (const raw of snapshot.funds?.positions ?? []) rawBySymbol.set(raw.symbol, raw)
    const bookBySymbol = new Map<string, Position>()
    for (const position of snapshot.positions) {
      if (position.product === 'FUTURES') bookBySymbol.set(position.symbol, position)
    }

    const rows: LiquidationRow[] = []
    for (const symbol of new Set([...rawBySymbol.keys(), ...bookBySymbol.keys()])) {
      const book = bookBySymbol.get(symbol)
      const raw = rawBySymbol.get(symbol)
      const quantity = book?.quantity ?? raw?.amount ?? 0
      if (!Number.isFinite(quantity) || quantity === 0) continue

      const bookMark = book && Number.isFinite(book.ltp) ? book.ltp : null
      const rawMark = raw ? nullableNumber(raw.mark_price) : null
      const markPrice = bookMark ?? rawMark ?? 0

      const bookRisk = riskOf(book)
      const rawRisk = riskOf(raw)
      const liquidationPrice = bookRisk.liquidationPrice ?? rawRisk.liquidationPrice
      const marginUsed = bookRisk.marginUsed ?? rawRisk.marginUsed
      const leverage = bookRisk.leverage ?? rawRisk.leverage

      rows.push({
        symbol,
        side: quantity < 0 ? 'Short' : 'Long',
        quantity,
        markPrice,
        liquidationPrice,
        marginUsed,
        leverage,
        distancePercent:
          markPrice > 0 && liquidationPrice !== null
            ? ((liquidationPrice - markPrice) / markPrice) * 100
            : null,
      })
    }
    rows.sort((a, b) => a.symbol.localeCompare(b.symbol))

    const reportedMargins = rows
      .map((row) => row.marginUsed)
      .filter((value): value is number => value !== null)
    return {
      rows,
      hasPositions: rows.length > 0,
      totalMarginCommitted:
        reportedMargins.length > 0 ? reportedMargins.reduce((sum, value) => sum + value, 0) : null,
      tradable: toAmounts(snapshot.funds)?.tradable ?? 0,
    }
  }, [snapshot.funds, snapshot.positions])
}
