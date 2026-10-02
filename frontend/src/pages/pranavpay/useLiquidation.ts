import { useMemo } from 'react'
import { SANDBOX_LABEL } from './account'
import { money, percent, qty } from './derive'
import { type SandboxAmounts, type SandboxFunds, sandboxAmounts } from './useWalletSnapshot'

/**
 * Risk fields the exchange reports on each futures position, for the sandbox
 * only.
 *
 * They arrive on both the positionbook rows and the funds positions after the
 * backend started passing `/fapi/v2/positionRisk` through verbatim; each stays
 * optional because Binance omits them for spot rows and reports nulls for
 * anything it does not estimate (cross-margin legs, flat legs).
 *
 * Nothing here describes the virtual account. The paper engine has no
 * liquidation price, no leverage and no margin mode, so a panel that offered
 * one would be inventing a risk the account does not carry.
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

function riskOf(record: SandboxFunds['positions'][number] | undefined): {
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
   * Percentage move from the reported mark to the reported liquidation price.
   * The only number derived here, computed from those two reported figures;
   * null while either is unreported.
   */
  distancePercent: number | null
}

export interface LiquidationSummary {
  rows: LiquidationRow[]
  hasPositions: boolean
  /** Sum of the reported per-position margins, or null when none reported. */
  totalMarginCommitted: number | null
  tradable: SandboxAmounts | null
}

/**
 * Liquidation and margin usage on the Binance sandbox.
 *
 * Reads the sandbox funds payload only, and is scoped to it: these are figures
 * about practice funds in a testnet wallet, and they say nothing about the
 * virtual account. Liquidation prices and margins are displayed exactly as
 * Binance reported them; only the distance percentage is derived, from the two
 * reported prices.
 */
export function useLiquidation(funds: SandboxFunds | null): LiquidationSummary {
  return useMemo(() => {
    const rows: LiquidationRow[] = []
    for (const raw of funds?.positions ?? []) {
      const quantity = nullableNumber(raw.amount) ?? 0
      if (quantity === 0) continue
      const markPrice = nullableNumber(raw.mark_price) ?? 0
      const risk = riskOf(raw)
      rows.push({
        symbol: raw.symbol,
        side: quantity < 0 ? 'Short' : 'Long',
        quantity,
        markPrice,
        liquidationPrice: risk.liquidationPrice,
        marginUsed: risk.marginUsed,
        leverage: risk.leverage,
        distancePercent:
          markPrice > 0 && risk.liquidationPrice !== null
            ? ((risk.liquidationPrice - markPrice) / markPrice) * 100
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
      tradable: sandboxAmounts(funds),
    }
  }, [funds])
}

/** The label every sandbox-only panel carries. Exported so it cannot drift. */
export const SANDBOX_PANEL_LABEL = SANDBOX_LABEL

/** The sandbox's own available balance, or null. Never the virtual account's. */
export function sandboxTradable(funds: SandboxFunds | null): number | null {
  return sandboxAmounts(funds)?.tradable ?? null
}

export { money, percent, qty }
