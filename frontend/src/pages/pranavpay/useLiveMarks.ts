import { useEffect, useMemo, useRef, useState } from 'react'
import { tradingApi } from '@/api/trading'
import { useMarketData } from '@/hooks/useMarketData'
import { useAuthStore } from '@/stores/authStore'
import type { PaperAccount } from './account'
import { baseAsset } from './derive'
import type { PaperPosition } from './usePaperState'

const POLL_MS = 10_000

/**
 * The markets worth streaming: whatever the virtual account is exposed to.
 *
 * Only open paper positions count. The Binance spot balances are a sandbox's
 * holdings and were what used to put unrelated coins on a panel headed "What you
 * hold", which is an account figure by another name.
 */
export function useWatchedMarkets(account: PaperAccount | null): string[] {
  return useMemo(() => {
    const unique = new Set<string>()
    for (const position of account?.positions ?? []) unique.add(position.symbol)
    // No fallback symbol: this list is labelled "What you hold", so an account
    // holding nothing must return nothing rather than borrow BTC.
    return [...unique].filter((symbol) => /^[A-Z0-9]{4,20}$/.test(symbol)).slice(0, 12)
  }, [account?.positions])
}

export interface LiveMarks {
  prices: Map<string, number>
  /** The WebSocket is delivering ticks. */
  isStreaming: boolean
  /** Prices are updating, by whatever means. */
  isLive: boolean
  lastTickAt: Date | null
}

/**
 * Live marks for the symbols the virtual account is exposed to.
 *
 * The ledger is a 15-second poll, so a position sat frozen behind a page that
 * looked live. Two sources feed this, in the order the OpenAlgo Positions page
 * already uses:
 *
 *   1. the shared WebSocket feed, when this broker is streaming;
 *   2. the MultiQuotes REST endpoint, which keeps prices moving when it is not.
 *
 * It annotates the ledger rather than mutating it: one fetch owns the books,
 * this only supplies marks, so the two can never fight over the same state.
 * `isStreaming` is reported separately from `isLive` so the interface can say
 * which one is actually happening instead of implying a stream it does not have.
 */
export function useLiveMarks(account: PaperAccount | null): LiveMarks {
  const apiKey = useAuthStore((state) => state.apiKey)
  const symbols = useWatchedMarkets(account)

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

  // The symbol list is rebuilt on every poll of the ledger, so depending on
  // its identity would restart the interval continuously. symbolsKey is the
  // stable value that actually changes the request.
  // biome-ignore lint/correctness/useExhaustiveDependencies: symbolsKey stands in for symbols
  useEffect(() => {
    if (!apiKey || symbolsKey.length === 0) return
    let cancelled = false

    const pull = async () => {
      // A hidden tab is not a reason to keep asking.
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
  /** Marked P&L, signed for the side the ledger recorded. */
  pnl: number
  pnlPercent: number | null
  isLive: boolean
}

/**
 * Recompute one paper position from the live mark, so the figures on screen are
 * what a close would produce right now rather than what the last poll said.
 *
 * `pnlPercent` is null when the position reports no entry price, because the
 * ratio would divide by nothing.
 */
export function markPosition(position: PaperPosition, prices: Map<string, number>): MarkedPosition {
  const live = prices.get(position.symbol)
  const mark = typeof live === 'number' ? live : position.mark
  const entry = position.entry
  const signedQty = String(position.side).toUpperCase() === 'SELL' ? -position.qty : position.qty
  const basis = Math.abs(entry * signedQty)
  const pnl = (mark - entry) * signedQty
  return {
    ltp: mark,
    pnl,
    pnlPercent: basis > 0 ? (pnl / basis) * 100 : null,
    isLive: typeof live === 'number',
  }
}

/**
 * The virtual account's equity restated with the live marks.
 *
 * The ledger's own equity is the starting point, so this only carries the move
 * since it last read: the difference between a fresh mark and the mark the
 * ledger stored, applied to the position's size. Null whenever the ledger has
 * not reported equity, so a headline can never be built out of nothing.
 */
export function livePaperEquity(
  account: PaperAccount | null,
  prices: Map<string, number>
): number | null {
  if (!account || account.equity === null) return null
  let delta = 0
  for (const position of account.positions) {
    const live = prices.get(position.symbol)
    if (typeof live !== 'number' || typeof position.mark !== 'number') continue
    const signedQty = String(position.side).toUpperCase() === 'SELL' ? -position.qty : position.qty
    delta += (live - position.mark) * signedQty
  }
  return account.equity + delta
}

/** Unrealised P&L restated with the live marks, or null when none are streaming. */
export function liveUnrealised(
  positions: PaperPosition[],
  prices: Map<string, number>
): number | null {
  if (positions.length === 0) return 0
  let total = 0
  let marked = 0
  for (const position of positions) {
    const mark = markPosition(position, prices)
    total += mark.pnl
    if (mark.isLive) marked += 1
  }
  // With nothing streaming, the ledger's own figure is the honest one.
  return marked > 0 ? total : null
}

export { baseAsset }
