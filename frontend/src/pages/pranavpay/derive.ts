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
      detail: 'The last refresh did not reach the exchange. Values shown are the last ones received.',
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
