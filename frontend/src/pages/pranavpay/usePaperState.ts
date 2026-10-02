import { useCallback, useEffect, useRef, useState } from 'react'

/** One open paper position, marked to market. */
export interface PaperPosition {
  symbol: string
  side: 'BUY' | 'SELL'
  qty: number
  entry: number
  mark: number
  unrealized: number
  strategy_id: string | null
  opened: number | null
}

/** One executed paper fill. */
export interface PaperFill {
  order_id: string
  symbol: string
  side: string
  qty: number
  price: number
  fee: number
  slippage: number
  ts: number
}

export interface PaperEquityPoint {
  ts: number
  cash: number
  equity: number
  realized: number
  unrealized: number
  fees: number
  slippage: number
  drawdown: number
}

export interface PaperStrategy {
  id: string
  family: string
  params: string
  metrics: string
  status: string
  created: number
  version: number
}

export interface PaperWorker {
  ts: number
  status: string
  error: string | null
  cycle: number
}

export interface PaperDecision {
  ts: number
  kind: string
  payload: unknown
}

export interface PaperState {
  starting_cash: number
  virtual_balance: number
  equity: number
  realized: number
  unrealized: number
  fees: number
  peak_equity: number
  drawdown: number
  positions: PaperPosition[]
  fills: PaperFill[]
  equity_curve: PaperEquityPoint[]
  strategies: PaperStrategy[]
  experiment_count: number
  worker: PaperWorker | null
  decisions: PaperDecision[]
  jev_verdicts: number
  generated_at: number
}

export interface PaperSnapshot {
  state: PaperState | null
  loading: boolean
  /** True once a fetch has failed; a previously loaded state keeps showing. */
  stale: boolean
  error: string | null
  updatedAt: Date | null
  refresh: () => void
}

/**
 * The paper trading engine's own ledger.
 *
 * Every figure on the Paper page comes from here. Nothing is derived from the
 * exchange balance and nothing is invented locally: an empty engine returns
 * zeros and empty lists, and the page says so rather than filling the gap.
 */
export function usePaperState(): PaperSnapshot {
  const [state, setState] = useState<PaperState | null>(null)
  const [loading, setLoading] = useState(true)
  const [stale, setStale] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [updatedAt, setUpdatedAt] = useState<Date | null>(null)
  const [nonce, setNonce] = useState(0)
  const aliveRef = useRef(true)

  useEffect(() => {
    aliveRef.current = true
    return () => {
      aliveRef.current = false
    }
  }, [])

  const refresh = useCallback(() => setNonce((n) => n + 1), [])

  useEffect(() => {
    void nonce
    let cancelled = false

    const load = async () => {
      try {
        const response = await fetch('/api/paper/state', { credentials: 'include' })
        if (!response.ok) throw new Error(String(response.status))
        const body = await response.json()
        if (cancelled) return
        if (body?.status === 'success' && body.data) {
          setState(body.data as PaperState)
          setError(null)
          setStale(false)
          setUpdatedAt(new Date())
        } else {
          setError(body?.message ?? 'The paper ledger did not answer.')
        }
      } catch {
        if (!cancelled) {
          setStale(true)
          setError('The paper trading engine did not answer.')
        }
      }
      if (aliveRef.current) setLoading(false)
    }

    void load()
    const timer = setInterval(load, 15_000)
    return () => {
      cancelled = true
      clearInterval(timer)
    }
  }, [nonce])

  return { state, loading, stale, error, updatedAt, refresh }
}
