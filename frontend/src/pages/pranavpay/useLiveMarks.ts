import { useEffect, useMemo, useRef, useState } from 'react'
import { tradingApi } from '@/api/trading'
import { useMarketData } from '@/hooks/useMarketData'
import { useAuthStore } from '@/stores/authStore'
import type { Position } from '@/types/trading'
import { baseAsset } from './derive'
import type { WalletSnapshot } from './useWalletSnapshot'

const POLL_MS = 10_000

/** The markets worth streaming: held positions, then what is held in spot. */
export function useWatchedMarkets(snapshot: {
  positions: Position[]
  funds: { spot_balances?: Array<{ asset?: string }> } | null
}): string[] {
  return useMemo(() => {
    const unique = new Set<string>()
    for (const position of snapshot.positions) unique.add(position.symbol)
    for (const balance of snapshot.funds?.spot_balances ?? []) {
      const asset = balance.asset?.toUpperCase()
      // A stablecoin is not a market: USDT would become "USDTUSDT" and ask the
      // exchange for a pair that does not exist.
      if (!asset || STABLES.has(asset)) continue
      unique.add(`${asset}USDT`)
    }
    // No fallback symbol: this list is labelled "What you hold", so an
    // account holding nothing must return nothing rather than borrow BTC.
    return [...unique].filter((symbol) => /^[A-Z0-9]{4,20}$/.test(symbol)).slice(0, 12)
  }, [snapshot.positions, snapshot.funds])
}

const STABLES = new Set(['USDT', 'USDC', 'BUSD', 'FDUSD', 'TUSD', 'USDP', 'DAI'])

export interface LiveMarks {
  prices: Map<string, number>
  /** The WebSocket is delivering ticks. */
  isStreaming: boolean
  /** Prices are updating, by whatever means. */
  isLive: boolean
  lastTickAt: Date | null
}

/**
 * Live marks for whatever this account is actually exposed to.
 *
 * The snapshot on its own is a 30-second poll, so a position sat frozen behind
 * a page that looked live. Two sources feed this, in the order the OpenAlgo
 * Positions page already uses:
 *
 *   1. the shared WebSocket feed, when this broker is streaming;
 *   2. the MultiQuotes REST endpoint, which keeps prices moving when it is not.
 *
 * It annotates the snapshot rather than mutating it: one poll owns the books,
 * this only supplies marks, so the two can never fight over the same state.
 * `isStreaming` is reported separately from `isLive` so the interface can say
 * which one is actually happening instead of implying a stream it does not have.
 */
export function useLiveMarks(snapshot: {
  positions: Position[]
  funds: { spot_balances?: Array<{ asset?: string }> } | null
}): LiveMarks {
  const apiKey = useAuthStore((state) => state.apiKey)
  const symbols = useWatchedMarkets(snapshot)

  const { data, isConnected } = useMarketData({
    symbols: symbols.map((symbol) => ({ symbol, exchange: 'CRYPTO' })),
    mode: 'LTP',
    enabled: symbols.length > 0,
    autoReconnect: true,
  })

  const [polled, setPolled] = useState<Map<string, number>>(new Map())
  const [lastTickAt, setLastTickAt] = useState<Date | null>(null)
  const symbolsKey = symbols.join(',')
  const aliveRef = useRef(true)

  useEffect(() => {
    aliveRef.current = true
    return () => {
      aliveRef.current = false
    }
  }, [])

  // The symbol list is rebuilt on every poll of the snapshot, so depending on
  // its identity would restart the interval continuously. symbolsKey is the
  // stable value that actually changes the request.
  // biome-ignore lint/correctness/useExhaustiveDependencies: symbolsKey stands in for symbols
  useEffect(() => {
    if (!apiKey || symbolsKey.length === 0) return
    let cancelled = false

    const pull = async () => {
      // A hidden tab is not a reason to keep asking the exchange.
      if (typeof document !== 'undefined' && document.hidden) return
      try {
        const response = await tradingApi.getMultiQuotes(
          apiKey,
          symbols.map((symbol) => ({ symbol, exchange: 'CRYPTO' }))
        )
        if (cancelled || response.status !== 'success' || !response.results) return
        const next = new Map<string, number>()
        for (const result of response.results) {
          const ltp = result.data?.ltp
          if (result.symbol && typeof ltp === 'number' && ltp > 0) {
            next.set(result.symbol, ltp)
          }
        }
        if (next.size > 0 && aliveRef.current) {
          setPolled(next)
          setLastTickAt(new Date())
        }
      } catch {
        // A failed poll leaves the previous prices in place; the footer reports
        // staleness rather than blanking the page.
      }
    }

    pull()
    const timer = setInterval(pull, POLL_MS)
    return () => {
      cancelled = true
      clearInterval(timer)
    }
  }, [apiKey, symbolsKey])

  const socketPrices = useMemo(() => {
    const next = new Map<string, number>()
    for (const [key, entry] of data.entries()) {
      // The mark lives under entry.data.ltp, and only a websocket-sourced
      // update proves the stream is fresh - a cached REST value would make a
      // stale price look live.
      const ltp = entry?.data?.ltp
      if (entry?.updateSource !== 'rest' && typeof ltp === 'number' && ltp > 0) {
        next.set(key.split(':')[1] ?? key, ltp)
      }
    }
    return next
  }, [data])

  const prices = useMemo(() => {
    const merged = new Map(polled)
    for (const [symbol, price] of socketPrices) merged.set(symbol, price)
    return merged
  }, [polled, socketPrices])

  return {
    prices,
    isStreaming: isConnected && socketPrices.size > 0,
    isLive: prices.size > 0,
    lastTickAt,
  }
}

export interface MarkedPosition {
  ltp: number
  value: number
  pnl: number
  pnlPercent: number
  isLive: boolean
}

/**
 * Recompute a position from the live mark, so the figures on screen are what a
 * close would produce right now rather than what the last poll said.
 */
export function markPosition(
  position: { symbol: string; quantity: number; average_price: number; ltp?: number },
  prices: Map<string, number>
): MarkedPosition {
  const live = prices.get(position.symbol)
  const ltp = live ?? position.ltp ?? position.average_price
  const value = Math.abs(position.quantity) * ltp
  const pnl = (ltp - position.average_price) * position.quantity
  const costBasis = Math.abs(position.average_price * position.quantity)
  return {
    ltp,
    value,
    pnl,
    pnlPercent: costBasis > 0 ? (pnl / costBasis) * 100 : 0,
    isLive: typeof live === 'number',
  }
}

/** Equity restated with the live marks, for the headline balance. */
export function liveEquity(snapshot: WalletSnapshot, prices: Map<string, number>): number | null {
  const equity = Number.parseFloat(snapshot.funds?.equity_usd ?? '')
  if (!Number.isFinite(equity)) return null
  let delta = 0
  for (const position of snapshot.positions) {
    const live = prices.get(position.symbol)
    if (typeof live !== 'number') continue
    const previous = position.ltp || position.average_price
    delta += (live - previous) * position.quantity
  }
  return equity + delta
}

/** Unrealised P&L restated with the live marks. */
export function liveUnrealised(positions: Position[], prices: Map<string, number>): number | null {
  if (positions.length === 0) return 0
  let total = 0
  let marked = 0
  for (const position of positions) {
    const mark = markPosition(position, prices)
    total += mark.pnl
    if (mark.isLive) marked += 1
  }
  // With nothing streaming, the poll's own figure is the honest one.
  return marked > 0 ? total : null
}

export { baseAsset }
