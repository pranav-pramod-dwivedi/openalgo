import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { type PaperAccount, paperAccount } from './account'
import { type PaperSnapshot, usePaperState } from './usePaperState'

/**
 * The two accounts this surface shows, kept apart on purpose.
 *
 * `account` is the user's virtual trading account: the paper ledger at
 * `/api/paper/state`, which is the only source an account card may read.
 * `sandbox` is the Binance testnet the OpenAlgo terminal runs against. It is
 * not the user's money, it is never added to an account figure, and it is only
 * ever rendered inside a panel that names it.
 */

/** What Binance's account endpoint sends. Practice funds, never the user's. */
export interface SandboxFunds {
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
  positions: SandboxPosition[]
  spot_balances: SandboxBalance[]
  futures_balances: SandboxBalance[]
}

export interface SandboxPosition {
  symbol: string
  amount: number
  side: 'LONG' | 'SHORT'
  entry_price: number
  mark_price: number
  unrealized_pnl: number
}

export interface SandboxBalance {
  asset: string
  free?: number
  locked?: number
  total?: number
  balance?: number
  available?: number
}

/** The testnet books, as figures with the exchange's own names kept intact. */
export interface SandboxAmounts {
  equity: number
  tradable: number
  savings: number
  floor: number
  openNotional: number
  marginLocked: number
}

const sandboxNum = (value: string | number | undefined | null): number => {
  const parsed = typeof value === 'number' ? value : Number.parseFloat(String(value ?? ''))
  return Number.isFinite(parsed) ? parsed : 0
}

/**
 * The sandbox's split, in the exchange's own vocabulary.
 *
 * Named for what it is so no caller can present it as the user's balance: the
 * protected/tradable split belongs to a Binance testnet wallet, and a floor
 * there says nothing whatever about the virtual account.
 */
export function sandboxAmounts(funds: SandboxFunds | null): SandboxAmounts | null {
  if (!funds) return null
  return {
    equity: sandboxNum(funds.equity_usd),
    tradable: sandboxNum(funds.tradable_usdt),
    savings: sandboxNum(funds.savings_usdt),
    floor: sandboxNum(funds.trading_floor),
    openNotional: sandboxNum(funds.open_notional_usd),
    marginLocked: sandboxNum(funds.utiliseddebits),
  }
}

export interface SandboxSnapshot {
  funds: SandboxFunds | null
  /** Wall-clock of the last successful fetch, or null if it never landed. */
  updatedAt: Date | null
  loading: boolean
  /** True once a fetch has failed; the last good balances keep showing. */
  stale: boolean
  error: string | null
  refresh: () => void
}

export interface AccountSnapshot extends PaperSnapshot {
  /** The ledger as the account cards read it. Null when there is no ledger. */
  figures: PaperAccount | null
}

export interface WalletSnapshot {
  account: AccountSnapshot
  sandbox: SandboxSnapshot
}

/**
 * The single snapshot every PranavPay panel reads.
 *
 * The account half is the paper ledger, fetched once by the shell and handed
 * down, so the balance in the rail and the balance on the overview can never
 * disagree. The sandbox half is fetched separately and stays separate; it is
 * only ever shown inside a panel that says what it is.
 */
export function useWalletSnapshot(): WalletSnapshot {
  const paper = usePaperState()
  const sandbox = useSandboxFunds()

  const account = useMemo<AccountSnapshot>(
    () => ({ ...paper, figures: paperAccount(paper.state) }),
    [paper]
  )

  return useMemo(() => ({ account, sandbox }), [account, sandbox])
}

/**
 * The Binance testnet balances, and nothing else.
 *
 * Only `/auth/dashboard-data` is read. The positions, trade book and order book
 * are deliberately not fetched: every account panel used to reach for them, and
 * the fix is that no account panel can any more.
 */
export function useSandboxFunds(): SandboxSnapshot {
  const [funds, setFunds] = useState<SandboxFunds | null>(null)
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

  useEffect(() => {
    void nonce
    let cancelled = false

    const load = async () => {
      try {
        const response = await fetch('/auth/dashboard-data', { credentials: 'include' })
        if (!response.ok) throw new Error(String(response.status))
        const body = await response.json()
        if (cancelled) return
        if (body?.status === 'success' && body.data) {
          setFunds(body.data as SandboxFunds)
          setError(null)
          setStale(false)
          setUpdatedAt(new Date())
        } else {
          setError('The testnet sandbox did not report a balance.')
        }
      } catch {
        if (!cancelled) {
          setStale(true)
          setError('The testnet sandbox did not answer.')
        }
      }
      if (aliveRef.current) setLoading(false)
    }

    void load()
    const timer = setInterval(load, 60_000)
    return () => {
      cancelled = true
      clearInterval(timer)
    }
  }, [nonce])

  return { funds, updatedAt, loading, stale, error, refresh }
}
