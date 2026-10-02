import { useEffect, useState } from 'react'
import type { Position, Trade } from '@/types/trading'
import type { Funds, WalletSnapshot } from './useWalletSnapshot'
import { toAmounts } from './useWalletSnapshot'

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

/** Base asset from a Binance symbol: BTCUSDT -> BTC. */
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

/** Human label for a Binance symbol, e.g. "BTCUSDT" -> "Bitcoin". */
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

export interface PositionRow {
  symbol: string
  asset: string
  name: string
  side: 'Long' | 'Short'
  quantity: number
  averagePrice: number
  markPrice: number
  value: number
  pnl: number
  pnlPercent: number
}

export function toPositionRows(positions: Position[]): PositionRow[] {
  return positions.map((position) => {
    const asset = baseAsset(position.symbol)
    const mark = Number.isFinite(position.ltp) ? position.ltp : position.average_price
    const value = Math.abs(position.quantity) * mark
    return {
      symbol: position.symbol,
      asset,
      name: assetName(asset),
      side: position.quantity < 0 ? 'Short' : 'Long',
      quantity: Math.abs(position.quantity),
      averagePrice: position.average_price,
      markPrice: mark,
      value,
      pnl: position.pnl,
      pnlPercent: position.pnlpercent,
    }
  })
}

export interface FillRow {
  id: string
  symbol: string
  asset: string
  action: 'BUY' | 'SELL'
  quantity: number
  price: number
  value: number
  pnl: number | null
  product: string
  venue: string
  at: Date | null
  raw: Trade
}

export function toFillRows(trades: Trade[]): FillRow[] {
  return trades.map((trade, index) => {
    const asset = baseAsset(trade.symbol)
    return {
      id: `${trade.orderid ?? 'trade'}-${trade.timestamp ?? index}-${index}`,
      symbol: trade.symbol,
      asset,
      action: trade.action,
      quantity: trade.quantity,
      price: trade.average_price,
      value: Math.abs(trade.trade_value ?? 0),
      pnl: typeof trade.pnl === 'number' ? trade.pnl : null,
      product: trade.product,
      venue: trade.exchange,
      at: parseBrokerTime(trade.timestamp),
      raw: trade,
    }
  })
}

export interface LedgerRow {
  id: string
  kind: 'fill'
  title: string
  detail: string
  amount: number
  at: Date | null
  pnl: number | null
}

/** Realised P&L summed per calendar day, for the P&L calendar. */
export function dailyRealised(fills: FillRow[]): Map<string, number> {
  const byDay = new Map<string, number>()
  for (const fill of fills) {
    if (fill.pnl === null || !fill.at) continue
    const key = fill.at.toISOString().slice(0, 10)
    byDay.set(key, (byDay.get(key) ?? 0) + fill.pnl)
  }
  return byDay
}

export interface AllocationSlice {
  label: string
  value: number
  share: number
}

/** A bucket before its share of equity is known. */
type AllocationBucket = { label: string; value: number }

/**
 * Where the money actually is, from the funds endpoint. The prototype showed
 * invented equities/crypto/cash percentages; these are the real buckets.
 */
export function allocation(funds: Funds | null, positions: Position[]): AllocationSlice[] {
  const amounts = toAmounts(funds)
  if (!amounts) return []
  const exposure = toPositionRows(positions).reduce((sum, row) => sum + row.value, 0)
  const spotValue = (funds?.spot_balances ?? [])
    .filter((balance) => balance.asset?.toUpperCase() !== 'USDC')
    .reduce((sum, balance) => sum + num(balance.total ?? balance.free ?? 0), 0)
  const total = amounts.equity
  if (total <= 0) return []
  const buckets: AllocationBucket[] = [
    { label: 'Protected savings', value: amounts.savings },
    { label: 'Tradable cash', value: amounts.tradable },
    { label: 'Open positions', value: exposure },
  ]
  if (spotValue > 0) buckets.push({ label: 'Spot assets', value: spotValue })
  return buckets
    .filter((bucket) => bucket.value > 0)
    .map((bucket) => ({ ...bucket, share: (bucket.value / total) * 100 }))
}

function num(value: number | string | undefined | null): number {
  const parsed = typeof value === 'number' ? value : Number.parseFloat(String(value ?? ''))
  return Number.isFinite(parsed) ? parsed : 0
}

export interface DerivedHealth {
  /** Open exposure as a share of equity. */
  exposureShare: number
  /** Tradable cash as a share of equity. */
  cashShare: number
  positionsCount: number
  best: PositionRow | null
  worst: PositionRow | null
  totalUnrealised: number
  totalRealised: number
}

/** Only facts the account can actually produce. No composite "health score". */
export function derive(snapshot: WalletSnapshot): DerivedHealth {
  const amounts = toAmounts(snapshot.funds)
  const rows = toPositionRows(snapshot.positions)
  const equity = amounts?.equity ?? 0
  const exposure = rows.reduce((sum, row) => sum + row.value, 0)
  const sorted = [...rows].sort((a, b) => b.pnlPercent - a.pnlPercent)
  return {
    exposureShare: equity > 0 ? (exposure / equity) * 100 : 0,
    cashShare: equity > 0 ? ((amounts?.tradable ?? 0) / equity) * 100 : 0,
    positionsCount: rows.length,
    best: sorted[0] ?? null,
    worst: sorted.length > 1 ? sorted[sorted.length - 1] : null,
    totalUnrealised: amounts?.unrealised ?? 0,
    totalRealised: amounts?.realised ?? 0,
  }
}

export interface DerivedAlert {
  id: string
  severity: 'info' | 'warning'
  title: string
  detail: string
  at: Date
}

/**
 * Alerts derived from the account's real state. The prototype listed invented
 * alerts against invented thresholds; these fire on facts we can measure, and
 * the list is empty when nothing is wrong rather than padded.
 */
export function alerts(snapshot: WalletSnapshot): DerivedAlert[] {
  const amounts = toAmounts(snapshot.funds)
  if (!amounts) return []
  const now = new Date()
  const found: DerivedAlert[] = []
  if (amounts.tradable <= 0 && snapshot.positions.length > 0) {
    found.push({
      id: 'no-trading-power',
      severity: 'warning',
      title: 'No trading power left',
      detail: 'Every dollar above the savings floor is committed to open positions.',
      at: now,
    })
  } else if (snapshot.positions.length > 0 && amounts.tradable < amounts.equity * 0.05) {
    found.push({
      id: 'low-trading-power',
      severity: 'info',
      title: 'Trading power is low',
      detail: `Only ${money(amounts.tradable)} of ${money(amounts.equity)} is free to trade.`,
      at: now,
    })
  }
  if (snapshot.stale) {
    found.push({
      id: 'stale',
      severity: 'warning',
      title: 'Figures may be out of date',
      detail:
        'The last refresh did not reach the exchange. Values shown are the last ones received.',
      at: now,
    })
  }
  if (amounts.marginLocked > 0) {
    found.push({
      id: 'margin',
      severity: 'info',
      title: 'Margin is in use',
      detail: `${money(amounts.marginLocked)} of margin is locked by open positions.`,
      at: now,
    })
  }
  return found
}

/** Wall-clock, ticking once a minute so the greeting stays honest. */
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
  if (hour < 12) return 'Your capital is resting. Nothing needs your attention.'
  if (hour < 18) {
    return hasPositions
      ? 'Positions are open and marked to market continuously.'
      : 'The market is moving. You have no open positions.'
  }
  return hasPositions ? 'The day is closing with positions still open.' : 'The day is closing flat.'
}

export function shortNumber(value: number): string {
  return numberFormat.format(value)
}

/**
 * Who this workspace belongs to.
 *
 * The kiosk session is a single local account called binance_demo, so deriving
 * the display name from the session produced "Binance account" in the rail and
 * "Binance Demo" on the account page - the account's own label, not the
 * operator's. This deployment is one person, so the owner is stated here and
 * the session is only used to decide whether an exchange is connected.
 */
export const OWNER = {
  name: 'Pranav Dwivedi',
  firstName: 'pranav',
  initials: 'PD',
  subtitle: 'Personal account',
} as const

/**
 * Portfolio equity, reconstructed from the fills.
 *
 * There is no stored equity history, so this is derived rather than sampled:
 * starting from the live equity now, each fill's realised result is subtracted
 * for every fill that came after it, which puts the account's value at each
 * moment it traded. Where the account was flat between fills the path is flat,
 * which is true - and the last point is the live figure, so the head of the
 * curve moves with the marks.
 *
 * The caller must say this is reconstructed. It is not a broker statement.
 */
export interface EquityPoint {
  t: number
  equity: number
  /** True for the point that carries the live mark rather than a fill. */
  live: boolean
}

export function portfolioSeries(
  liveEquityNow: number,
  fills: FillRow[],
  openPositions: number,
  floor: number
): EquityPoint[] {
  const closed = fills
    .filter((fill) => fill.at !== null && fill.pnl !== null)
    .sort((a, b) => (a.at?.getTime() ?? 0) - (b.at?.getTime() ?? 0))

  // Equity implied by each fill = today's equity, less everything realised since.
  const points: EquityPoint[] = []
  let running = liveEquityNow
  for (let i = closed.length - 1; i >= 0; i -= 1) {
    const fill = closed[i]
    const pnl = fill.pnl ?? 0
    running -= pnl
    points.push({ t: fill.at!.getTime(), equity: running, live: false })
  }
  points.reverse()

  // No synthetic starting point. The loop already lands on the account's value
  // at its first fill; anchoring the head at the savings floor instead invented
  // a $100 drop on the first day that never happened, and drew the account
  // climbing out of a hole it was never in.
  points.push({ t: Date.now(), equity: liveEquityNow, live: true })
  void openPositions
  void floor
  return points
}

export interface PortfolioStats {
  closedFills: number
  wins: number
  losses: number
  winRate: number
  grossProfit: number
  grossLoss: number
  profitFactor: number | null
  averageWin: number
  averageLoss: number
  largestWin: FillRow | null
  largestLoss: FillRow | null
  bestDay: { day: string; value: number } | null
  worstDay: { day: string; value: number } | null
  daysGreen: number
  daysRed: number
  capitalAtRisk: number
}

/**
 * What actually matters about a portfolio, all of it computed from the fills
 * and the live marks. Every figure here is either a count, a sum or a ratio of
 * two real numbers; nothing is a score or an opinion.
 */
export function portfolioStats(
  fills: FillRow[],
  byDay: Map<string, number>,
  capitalAtRisk: number
): PortfolioStats {
  const closed = fills.filter((fill) => fill.pnl !== null)
  const wins = closed.filter((fill) => (fill.pnl ?? 0) > 0)
  const losses = closed.filter((fill) => (fill.pnl ?? 0) < 0)
  const grossProfit = wins.reduce((sum, fill) => sum + (fill.pnl ?? 0), 0)
  const grossLoss = Math.abs(losses.reduce((sum, fill) => sum + (fill.pnl ?? 0), 0))
  const bySize = (a: FillRow, b: FillRow) => (a.pnl ?? 0) - (b.pnl ?? 0)

  const days = [...byDay.entries()]
  const ranked = days.length ? [...days].sort((a, b) => b[1] - a[1]) : []

  return {
    closedFills: closed.length,
    wins: wins.length,
    losses: losses.length,
    winRate: closed.length > 0 ? (wins.length / closed.length) * 100 : 0,
    grossProfit,
    grossLoss,
    // Only meaningful when there is something to divide by; a ratio with no
    // losses would otherwise read as infinite profit.
    profitFactor: grossLoss > 0 ? grossProfit / grossLoss : null,
    averageWin: wins.length > 0 ? grossProfit / wins.length : 0,
    averageLoss: losses.length > 0 ? grossLoss / losses.length : 0,
    largestWin: wins.length ? [...wins].sort(bySize).reverse()[0] : null,
    largestLoss: losses.length ? [...losses].sort(bySize)[0] : null,
    bestDay: ranked.length ? { day: ranked[0][0], value: ranked[0][1] } : null,
    worstDay: ranked.length
      ? { day: ranked[ranked.length - 1][0], value: ranked[ranked.length - 1][1] }
      : null,
    daysGreen: days.filter(([, value]) => value > 0).length,
    daysRed: days.filter(([, value]) => value < 0).length,
    capitalAtRisk,
  }
}

export interface EquityDomain {
  min: number
  max: number
  /** Where the savings floor sits in 0-100 space, or null when off-scale. */
  floorY: number | null
  /** True when the series moved too little for the vertical scale to mean much. */
  effectivelyFlat: boolean
}

/**
 * The vertical range for the equity curve.
 *
 * Two ways this goes wrong on a quiet account, both of which make the panel
 * lie. Scaling to the series' own min and max turns a $0.17 wiggle on a $30k
 * account into a full-height cliff. Scaling with only a tenth of the range as
 * headroom has the same effect from the other side: $100 of capital above a
 * $29,899 floor fills the box and reads like a large position.
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
