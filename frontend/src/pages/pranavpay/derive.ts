import { useEffect, useState } from 'react'

/**
 * Formatting and the generic row shapes every panel renders.
 *
 * Nothing here knows where a number came from. Which endpoint a figure is read
 * from, and whether it is the user's virtual account or the Binance sandbox, is
 * decided in `account.ts` and `useWalletSnapshot.ts`; this file only turns an
 * already-sourced figure into text.
 */

/** Money, always two decimals, tabular. Never a silent zero for missing data. */
export function money(value: number | null | undefined, currency = '$'): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return '—'
  return `${currency}${value.toLocaleString('en-US', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })}`
}

/** Crypto quantities need more precision than money. */
export function qty(value: number | null | undefined, max = 6): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return '—'
  if (value === 0) return '0'
  return value.toLocaleString('en-US', { maximumFractionDigits: max })
}

export function signedMoney(value: number | null | undefined, currency = '$'): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return '—'
  const sign = value > 0 ? '+' : value < 0 ? '-' : ''
  return `${sign}${money(Math.abs(value), currency)}`
}

export function percent(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return '—'
  const sign = value > 0 ? '+' : value < 0 ? '-' : ''
  return `${sign}${Math.abs(value).toFixed(2)}%`
}

const numberFormat = new Intl.NumberFormat('en-US', { maximumFractionDigits: 2 })

/** Binance sends "2026-09-18 03:53:31" in UTC with no zone marker. */
export function parseBrokerTime(value: string | null | undefined): Date | null {
  if (!value) return null
  const iso = /Z|[+-]\d{2}:?\d{2}$/.test(value) ? value : `${value.replace(' ', 'T')}Z`
  const parsed = new Date(iso)
  return Number.isNaN(parsed.getTime()) ? null : parsed
}

export function formatTime(value: string | Date | null | undefined): string {
  const date = value instanceof Date ? value : parseBrokerTime(String(value ?? ''))
  if (!date) return '—'
  return date.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' })
}

export function formatDate(value: string | Date | null | undefined): string {
  const date = value instanceof Date ? value : parseBrokerTime(String(value ?? ''))
  if (!date) return '—'
  return date.toLocaleDateString('en-US', { month: 'short', day: '2-digit' })
}

/** "2m", "3h", "Sep 28" - the ledger's relative time column. */
export function relativeTime(value: string | Date | null | undefined, now = Date.now()): string {
  const date = value instanceof Date ? value : parseBrokerTime(String(value ?? ''))
  if (!date) return '—'
  const seconds = Math.max(0, Math.round((now - date.getTime()) / 1000))
  if (seconds < 60) return `${seconds}s`
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m`
  if (seconds < 86_400) return `${Math.floor(seconds / 3600)}h`
  if (seconds < 604_800) return `${Math.floor(seconds / 86_400)}d`
  return formatDate(date)
}

/** Base asset from a market symbol: BTCUSDT -> BTC. */
export function baseAsset(symbol: string): string {
  const match = symbol.match(/^([A-Z0-9]+?)(USDT|USDC|BUSD|FDUSD|USD)$/)
  return match ? match[1] : symbol.replace(/(USDT|USDC|BUSD)$/, '')
}

const ASSET_GLYPHS: Record<string, string> = {
  BTC: '₿',
  ETH: 'Ξ',
  SOL: 'S',
  BNB: 'B',
  XRP: 'X',
  DOGE: 'Ð',
  USDT: '$',
  USDC: '$',
}

export function assetGlyph(asset: string): string {
  return ASSET_GLYPHS[asset?.toUpperCase()] ?? (asset?.[0] ?? '?').toUpperCase()
}

/** Human label for a market symbol, e.g. "BTCUSDT" -> "Bitcoin". */
const ASSET_NAMES: Record<string, string> = {
  BTC: 'Bitcoin',
  ETH: 'Ethereum',
  SOL: 'Solana',
  BNB: 'BNB',
  XRP: 'XRP',
  ADA: 'Cardano',
  DOGE: 'Dogecoin',
  USDT: 'Tether',
  USDC: 'USD Coin',
}

export function assetName(asset: string): string {
  return ASSET_NAMES[asset?.toUpperCase()] ?? asset
}

/**
 * One executed fill, as a row.
 *
 * Deliberately carries no exchange payload: the same shape serves the paper
 * ledger's fills and the sandbox's, so a panel cannot be pointed at one and
 * quietly keep reading the other's fields.
 */
export interface FillRow {
  id: string
  symbol: string
  asset: string
  action: 'BUY' | 'SELL'
  quantity: number
  price: number
  value: number
  /** Null when nothing reported a realised result for this fill. */
  pnl: number | null
  /** Cost charged on the fill, when the source reported one. */
  fee?: number | null
  /** Price slippage charged on the fill, when the source reported one. */
  slippage?: number | null
  orderId: string
  /** Epoch milliseconds the source stored, or null. */
  timestamp: number | null
  product: string
  venue: string
  at: Date | null
}

/**
 * Portfolio equity, as one point on a line.
 *
 * `live` marks the point that carries the figure read just now, so a chart can
 * draw it differently from the stored history behind it.
 */
export interface EquityPoint {
  t: number
  equity: number
  live: boolean
}

export interface EquityDomain {
  min: number
  max: number
  /** Where a baseline sits in 0-100 space, or null when off-scale. */
  floorY: number | null
  /** True when the series moved too little for the vertical scale to mean much. */
  effectivelyFlat: boolean
}

/**
 * The vertical range for the equity curve.
 *
 * Two ways this goes wrong on a quiet account, both of which make the panel
 * lie. Scaling to the series' own min and max turns a $0.17 wiggle on a $1000
 * account into a full-height cliff. Scaling with only a tenth of the range as
 * headroom has the same effect from the other side.
 *
 * So the domain is the data's own range, widened to at least a readable span -
 * a fraction of the account - with the movement centred. `effectivelyFlat`
 * reports when the change is too small for that span to resolve, so the caller
 * can say so instead of letting a straight line imply a story.
 */
export function equityDomain(series: EquityPoint[], scaleHint: number | null): EquityDomain | null {
  if (series.length < 2) return null
  const values = series.map((point) => point.equity)
  const dataMin = Math.min(...values)
  const dataMax = Math.max(...values)
  const raw = dataMax - dataMin

  // A span of 0.4% of the account, so a quiet series still has room to be read
  // as quiet rather than as noise.
  const hint = scaleHint !== null && Number.isFinite(scaleHint) ? Math.abs(scaleHint) : 0
  const minSpan = Math.max(raw * 1.5, hint * 0.004)
  const half = Math.max(minSpan - raw, 1) / 2
  const min = dataMin - half
  const max = dataMax + half
  return {
    min,
    max,
    floorY: null,
    effectivelyFlat: raw < minSpan * 0.15,
  }
}

/** Scale equity points onto the chart's 0-100 box within a fixed domain. */
export function equityToPoints(
  series: EquityPoint[],
  domain: EquityDomain
): Array<{ x: number; y: number; live?: boolean }> {
  if (series.length < 2) return []
  const span = Math.max(1e-9, domain.max - domain.min)
  const first = series[0].t
  const spanMs = Math.max(1, series[series.length - 1].t - first)
  return series.map((point) => ({
    x: ((point.t - first) / spanMs) * 100,
    y: 100 - ((point.equity - domain.min) / span) * 100,
    live: point.live,
  }))
}

/** Wall-clock, ticking so the greeting stays honest. */
export function useNow(intervalMs = 30_000): Date {
  const [now, setNow] = useState(() => new Date())
  useEffect(() => {
    const timer = setInterval(() => setNow(new Date()), intervalMs)
    return () => clearInterval(timer)
  }, [intervalMs])
  return now
}

export function greetingFor(date: Date): string {
  const hour = date.getHours()
  if (hour < 12) return 'Good morning'
  if (hour < 18) return 'Good afternoon'
  return 'Good evening'
}

export function introFor(date: Date, hasPositions: boolean): string {
  const hour = date.getHours()
  if (hour < 12) return 'Your virtual account is resting. Nothing needs your attention.'
  if (hour < 18) {
    return hasPositions
      ? 'Virtual positions are open and marked to market continuously.'
      : 'The market is moving. Your virtual account has no open positions.'
  }
  return hasPositions
    ? 'The day is closing with virtual positions still open.'
    : 'The day is closing flat.'
}

export function shortNumber(value: number): string {
  return numberFormat.format(value)
}

/**
 * Who this workspace belongs to.
 *
 * The kiosk session is a single local account called binance_demo, so deriving
 * the display name from the session produced "Binance account" in the rail and
 * "Binance Demo" on the account page - the sandbox's own label, not the
 * operator's. This deployment is one person, so the owner is stated here and
 * the session is only used to decide whether a broker is connected.
 */
export const OWNER = {
  name: 'Pranav Dwivedi',
  firstName: 'pranav',
  initials: 'PD',
  subtitle: 'Personal account',
} as const
