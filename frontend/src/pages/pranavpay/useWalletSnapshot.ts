import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { tradingApi } from '@/api/trading'
import { useAuthStore } from '@/stores/authStore'
import type { Order, Position, Trade } from '@/types/trading'

/**
 * Funds as the Binance service reports them. The floor fields are what make
 * the protected/tradable split honest: `tradable_usdt` is what the order path
 * will actually accept, and `savings_usdt` is the part no order can touch.
 */
export interface Funds {
  wallet_total_usd: string
  equity_usd: string
  trading_floor: string
  savings_usdt: string
  tradable_usdt: string
  open_notional_usd: string
  availablecash: string
  m2munrealized: string
  m2mrealized: string
  utiliseddebits: string
  spot_usdt: string
  futures_usdt: string
  is_live: boolean
  positions: RawPosition[]
  spot_balances: AssetBalance[]
  futures_balances: AssetBalance[]
}

export interface RawPosition {
  symbol: string
  amount: number
  side: 'LONG' | 'SHORT'
  entry_price: number
  mark_price: number
  unrealized_pnl: number
}

export interface AssetBalance {
  asset: string
  free?: number
  locked?: number
  total?: number
  balance?: number
  available?: number
}

export interface WalletSnapshot {
  funds: Funds | null
  positions: Position[]
  trades: Trade[]
  orders: Order[]
  /** Wall-clock of the last successful fetch, or null if it has never landed. */
  updatedAt: Date | null
  loading: boolean
  /** True when a fetch has failed at least once; the last good data still shows. */
  stale: boolean
  error: string | null
  refresh: () => void
}

const num = (value: string | number | undefined | null): number => {
  const parsed = typeof value === 'number' ? value : Number.parseFloat(String(value ?? ''))
  return Number.isFinite(parsed) ? parsed : 0
}

/** Values the funds endpoint sends as strings, as numbers. */
export const toAmounts = (funds: Funds | null) => {
  if (!funds) return null
  return {
    wallet: num(funds.wallet_total_usd),
    equity: num(funds.equity_usd),
    floor: num(funds.trading_floor),
    savings: num(funds.savings_usdt),
    tradable: num(funds.tradable_usdt),
    openNotional: num(funds.open_notional_usd),
    realised: num(funds.m2mrealized),
    unrealised: num(funds.m2munrealized),
    marginLocked: num(funds.utiliseddebits),
    spotUsdt: num(funds.spot_usdt),
    futuresUsdt: num(funds.futures_usdt),
  }
}

/**
 * One fetch of everything the surface shows, shared by every panel.
 *
 * The prototype hardcoded balances, positions and fills. Each of those is now
 * read here, and a panel that has no data renders an explicit empty state
 * rather than a plausible number. Polls on a slow interval and refetches on the
 * order events the socket already emits, so the books stay honest without a
 * request per tick.
 */
export function useWalletSnapshot(): WalletSnapshot {
  const apiKey = useAuthStore((state) => state.apiKey)
  const [funds, setFunds] = useState<Funds | null>(null)
  const [positions, setPositions] = useState<Position[]>([])
  const [trades, setTrades] = useState<Trade[]>([])
  const [orders, setOrders] = useState<Order[]>([])
  const [updatedAt, setUpdatedAt] = useState<Date | null>(null)
  const [loading, setLoading] = useState(true)
  const [stale, setStale] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [nonce, setNonce] = useState(0)
  const aliveRef = useRef(true)

  useEffect(() => {
    aliveRef.current = true
    return () => {
      aliveRef.current = false
    }
  }, [])

  const refresh = useCallback(() => setNonce((n) => n + 1), [])

  // `nonce` is the manual-refresh trigger. It is read inside the effect only to
  // re-run it, which is exactly what it is for, so the dependency is deliberate
  // rather than redundant.
  useEffect(() => {
    void nonce
    if (!apiKey) {
      // The session is still syncing. Staying "loading" is the honest state:
      // an empty book would read as a flat account.
      setLoading(true)
      return
    }
    let cancelled = false

    const load = async () => {
      try {
        const fundsResponse = await fetch('/auth/dashboard-data', {
          credentials: 'include',
        })
        if (fundsResponse.ok) {
          const body = await fundsResponse.json()
          if (body?.status === 'success' && body.data && !cancelled) {
            setFunds(body.data as Funds)
            setError(null)
            setStale(false)
            setUpdatedAt(new Date())
          }
        } else {
          setStale(true)
        }
      } catch {
        setStale(true)
      }

      const [positionResult, tradeResult, orderResult] = await Promise.allSettled([
        tradingApi.getPositions(apiKey),
        tradingApi.getTrades(apiKey),
        tradingApi.getOrders(apiKey),
      ])
      if (cancelled) return

      if (positionResult.status === 'fulfilled' && positionResult.value.data) {
        setPositions(positionResult.value.data)
      }
      if (tradeResult.status === 'fulfilled' && tradeResult.value.data) {
        setTrades(tradeResult.value.data)
      }
      if (orderResult.status === 'fulfilled' && orderResult.value.data) {
        setOrders(orderResult.value.data.orders ?? [])
      }
      if (!aliveRef.current) return
      setLoading(false)
    }

    load()
    const timer = setInterval(load, 30_000)
    return () => {
      cancelled = true
      clearInterval(timer)
    }
  }, [apiKey, nonce])

  return useMemo(
    () => ({ funds, positions, trades, orders, updatedAt, loading, stale, error, refresh }),
    [funds, positions, trades, orders, updatedAt, loading, stale, error, refresh]
  )
}
