import {
  Activity,
  ArrowUpRight,
  BookOpen,
  Coins,
  ExternalLink,
  FileText,
  RefreshCw,
  ShieldCheck,
  TrendingUp,
  Zap,
} from 'lucide-react'
import { useCallback, useEffect, useRef, useState } from 'react'
import { Link } from 'react-router'
import DashboardPnLChart from '@/components/dashboard/DashboardPnLChart'
import { useSocketContext } from '@/components/socket/SocketProvider'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { DataFreshness } from '@/components/ui/data-freshness'
import { useOrderEventRefresh } from '@/hooks/useOrderEventRefresh'
import { useSocketOnline } from '@/hooks/useSocketOnline'
import { cn } from '@/lib/utils'
import { useAuthStore } from '@/stores/authStore'
import { onModeChange } from '@/stores/themeStore'

interface BinanceBalance {
  asset: string
  free?: number
  locked?: number
  total?: number
  balance?: number
  available?: number
}

interface BinancePosition {
  symbol: string
  amount: number
  side: 'LONG' | 'SHORT'
  entry_price: number
  mark_price: number
  unrealized_pnl: number
}

interface MarginData {
  availablecash: string
  collateral: string
  m2munrealized: string
  m2mrealized: string
  utiliseddebits: string
  is_binance?: boolean
  is_live?: boolean
  trading_floor?: string
  savings_usdt?: string
  tradable_usdt?: string
  wallet_total_usd?: string
  equity_usd?: string
  open_notional_usd?: string
  spot_usdt?: string
  futures_usdt?: string
  futures_wallet_usd?: string
  spot_wallet_usd?: string
  total_balance_usd?: string
  spot_balances?: BinanceBalance[]
  futures_balances?: BinanceBalance[]
  positions?: BinancePosition[]
}

interface MasterContractStatus {
  status: 'pending' | 'downloading' | 'success' | 'error'
  message?: string
  total_symbols?: number
}

// Format number with proper currency and notation (USD vs INR)
function formatAccountCurrency(value: string | number, isUsd: boolean = false): string {
  const num = typeof value === 'string' ? parseFloat(value) : value
  if (Number.isNaN(num)) return isUsd ? '$0.00' : '₹0.00'

  const isNegative = num < 0
  const absNum = Math.abs(num)
  const sym = isUsd ? '$' : '₹'

  if (isUsd) {
    const formatted = absNum.toLocaleString('en-US', {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    })
    return isNegative ? `-${sym}${formatted}` : `${sym}${formatted}`
  }

  let formatted: string
  if (absNum >= 10000000) {
    formatted = `${(absNum / 10000000).toFixed(2)}Cr`
  } else if (absNum >= 100000) {
    formatted = `${(absNum / 100000).toFixed(2)}L`
  } else {
    formatted = absNum.toFixed(2)
  }

  return isNegative ? `-${sym}${formatted}` : `${sym}${formatted}`
}

// Get color class based on P&L value
function getPnLColor(value: string | number): string {
  const num = typeof value === 'string' ? parseFloat(value) : value
  if (num > 0) return 'text-green-600 dark:text-green-400'
  if (num < 0) return 'text-red-600 dark:text-red-400'
  return 'text-foreground'
}

function getPnLBadgeVariant(value: string | number): 'default' | 'destructive' | 'secondary' {
  const num = typeof value === 'string' ? parseFloat(value) : value
  if (num > 0) return 'default'
  if (num < 0) return 'destructive'
  return 'secondary'
}

function num(value: string | number | null | undefined): number {
  const n = typeof value === 'number' ? value : Number(value)
  return Number.isFinite(n) ? n : 0
}

export default function Dashboard() {
  const { user } = useAuthStore()
  const { socket } = useSocketContext()
  const isSocketOnline = useSocketOnline(socket)
  const [marginData, setMarginData] = useState<MarginData | null>(null)
  const username = (user?.username || '').toLowerCase()
  const isBinance =
    username.includes('binance') ||
    user?.broker === 'binance_demo' ||
    Boolean(marginData?.is_binance)
  const isUsdSandbox = !isBinance && username.includes('usd')
  const isIndianSandbox = !isBinance && !isUsdSandbox
  const isUsd = isBinance || isUsdSandbox
  const [isLoading, setIsLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [lastUpdated, setLastUpdated] = useState<string | null>(null)
  const [isRefreshing, setIsRefreshing] = useState(false)
  const [masterContract, setMasterContract] = useState<MasterContractStatus>({
    status: 'pending',
  })
  const [isAuthenticated, setIsAuthenticated] = useState(true) // Assume authenticated initially
  // Broker token revoked/expired while the app session is still valid
  // (daily token rollover). Routes the user to /broker, not /login (#1400).
  const [brokerExpired, setBrokerExpired] = useState(false)
  const autoRefreshRef = useRef<ReturnType<typeof setInterval> | null>(null)

  // Fetch dashboard funds data. Foreground load drives the skeleton cards;
  // background refreshes (poll, socket events, mode changes) must not flash
  // them back to placeholders.
  const fetchFundsData = useCallback(async (background = false) => {
    try {
      if (background) {
        setIsRefreshing(true)
      } else {
        setIsLoading(true)
      }
      const response = await fetch('/auth/dashboard-data', {
        credentials: 'include',
      })

      if (response.status === 401) {
        const body = await response.json().catch(() => null)
        if (body?.code === 'BROKER_SESSION_EXPIRED') {
          setBrokerExpired(true)
        } else {
          setIsAuthenticated(false)
        }
        setIsLoading(false)
        return
      }

      const data = await response.json()

      if (data.status === 'success' && data.data) {
        setMarginData(data.data)
        setError(null)
        setLastUpdated(new Date().toISOString())
      } else {
        setError(data.message || 'Failed to fetch margin data')
      }
    } catch (_err) {
      setError('Failed to fetch margin data')
    } finally {
      setIsLoading(false)
      setIsRefreshing(false)
    }
  }, [])

  useEffect(() => {
    fetchFundsData()
  }, [fetchFundsData])

  const refreshFunds = useCallback(() => {
    fetchFundsData(true)
  }, [fetchFundsData])

  // Auto-refresh dashboard every 60s (less aggressive than trading pages)
  useEffect(() => {
    autoRefreshRef.current = setInterval(() => {
      refreshFunds()
    }, 60_000)
    return () => {
      if (autoRefreshRef.current) clearInterval(autoRefreshRef.current)
    }
  }, [refreshFunds])

  // Refresh funds when an order is placed (via SocketIO event)
  useOrderEventRefresh(refreshFunds, {
    events: ['order_event', 'analyzer_update', 'close_position_event'],
  })

  // Listen for mode changes and refresh data
  useEffect(() => {
    const unsubscribe = onModeChange(() => {
      // Refresh funds data when mode changes
      refreshFunds()
    })
    return () => unsubscribe()
  }, [refreshFunds])

  // Check master contract status
  const checkMasterContractStatus = useCallback(async () => {
    try {
      const response = await fetch('/api/master-contract/status', {
        credentials: 'include',
        headers: { Accept: 'application/json' },
      })

      if (response.status === 401) {
        return
      }

      const data = await response.json()
      setMasterContract(data)
    } catch (_err) {
      setMasterContract({ status: 'error', message: 'Failed to check status' })
    }
  }, [])

  useEffect(() => {
    // Indian symbology download is meaningless on Binance: never poll it.
    if (isBinance) return
    checkMasterContractStatus()
  }, [checkMasterContractStatus, isBinance])

  // Poll every 5 seconds until successful, then stop. The interval is owned by
  // the effect (not a setState updater) so StrictMode double-invoke cannot
  // multiply it and success tears it down instead of polling forever.
  useEffect(() => {
    if (isBinance || masterContract.status === 'success') return
    const interval = setInterval(() => {
      checkMasterContractStatus()
    }, 5000)
    return () => clearInterval(interval)
  }, [checkMasterContractStatus, isBinance, masterContract.status])

  // Master Contract LED color
  const getMasterContractLedColor = () => {
    switch (masterContract.status) {
      case 'success':
        return 'bg-green-500'
      case 'downloading':
        return 'bg-yellow-500 animate-pulse'
      case 'error':
        return 'bg-red-500'
      default:
        return 'bg-gray-400 animate-pulse'
    }
  }

  const getMasterContractStatusText = () => {
    switch (masterContract.status) {
      case 'success':
        return masterContract.total_symbols
          ? `Ready (${masterContract.total_symbols} symbols)`
          : 'Ready'
      case 'downloading':
        return 'Downloading...'
      case 'error':
        return 'Error'
      default:
        return 'Checking...'
    }
  }

  const getMasterContractTextColor = () => {
    switch (masterContract.status) {
      case 'success':
        return 'text-green-600 dark:text-green-400'
      case 'downloading':
        return 'text-yellow-600 dark:text-yellow-400'
      case 'error':
        return 'text-red-600 dark:text-red-400'
      default:
        return 'text-muted-foreground'
    }
  }

  const quickAccessCards = [
    {
      href: '/trading',
      label: 'Chart Terminal',
      description: 'Live Binance charts, order placement & position control',
      icon: TrendingUp,
      tag: 'LIVE',
    },
    {
      href: '/agent',
      label: 'AI Trading Agent',
      description: 'Ask the agent to analyze, chart and propose trades',
      icon: Zap,
      tag: 'AI',
    },
    {
      href: '/positions',
      label: 'Positions',
      description: 'Open exposure with live mark prices',
      icon: Activity,
      tag: 'RISK',
    },
    {
      href: '/orderbook',
      label: 'Orderbook',
      description: 'Open orders, modify and cancel',
      icon: FileText,
      tag: 'ORDERS',
    },
    {
      href: '/tradebook',
      label: 'Tradebook',
      description: 'Filled trades and realized performance',
      icon: BookOpen,
      tag: 'FILLS',
    },
    {
      href: '/apikey',
      label: 'API Key',
      description: 'Key backing charts, webhooks and external tools',
      icon: ShieldCheck,
      tag: 'ACCESS',
    },
  ]

  // Broker token expired but the app session is fine: send the user to the
  // broker reconnect flow, not /login (which would bounce back) — #1400.
  if (brokerExpired) {
    return (
      <div className="flex flex-col items-center justify-center min-h-[50vh] space-y-4">
        <h1 className="text-2xl font-bold">Broker Session Expired</h1>
        <p className="text-muted-foreground">
          The trading session needs a refresh. Reload to continue trading.
        </p>
        <Link
          to="/dashboard"
          className="inline-flex items-center rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground hover:bg-primary/90"
        >
          Reload Dashboard
        </Link>
      </div>
    )
  }

  // If not authenticated, show login prompt
  if (!isAuthenticated) {
    return (
      <div className="flex flex-col items-center justify-center min-h-[50vh] space-y-4">
        <h1 className="text-2xl font-bold">Session Expired</h1>
        <p className="text-muted-foreground">Reload the dashboard to re-establish the session.</p>
        <Link to="/dashboard" className="text-primary hover:underline">
          Go to Dashboard
        </Link>
      </div>
    )
  }

  return (
    <div className="space-y-6 md:space-y-8">
      {/* Terminal Header & Telemetry */}
      <div className="flex flex-col lg:flex-row lg:items-center justify-between gap-4 pb-4 border-b border-border/60">
        <div>
          <div className="flex items-center gap-3">
            <h1 className="text-2xl md:text-3xl font-extrabold tracking-tight text-foreground">
              Trading Terminal
            </h1>
            <span className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-xs font-mono font-semibold bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 border border-emerald-500/25">
              <span className="h-2 w-2 rounded-full bg-emerald-500 animate-pulse" />
              LIVE ENGINE
            </span>
          </div>
          <p className="text-muted-foreground mt-1 text-xs md:text-sm tracking-tight">
            Real-time portfolio telemetry, market depth & automated execution engine
          </p>
        </div>

        <div className="flex items-center gap-2.5 flex-wrap">
          {/* Master Contract Status Badge (Indian symbology; hidden on Binance) */}
          {!isBinance && (
          <div className="inline-flex items-center gap-2 px-2.5 py-1.5 rounded-md bg-muted/50 border border-border/60 text-xs">
            <span className="text-muted-foreground font-medium">Contract:</span>
            <div className="flex items-center gap-1.5">
              <div className={cn('w-2 h-2 rounded-full', getMasterContractLedColor())} />
              <span
                className={cn('font-mono font-medium', getMasterContractTextColor())}
                title={masterContract.message}
              >
                {getMasterContractStatusText()}
              </span>
            </div>
          </div>
          )}

          <DataFreshness
            lastUpdated={lastUpdated}
            isRefreshing={isRefreshing || isLoading}
            isConnected={isSocketOnline}
          />

          <Button
            variant="outline"
            size="sm"
            className="h-8 px-2.5 text-xs font-medium border-border/80 hover:bg-muted/80"
            onClick={() => {
              fetchFundsData(true)
            }}
            disabled={isRefreshing || isLoading}
          >
            <RefreshCw
              className={cn('h-3.5 w-3.5 mr-1.5', (isRefreshing || isLoading) && 'animate-spin')}
            />
            Refresh
          </Button>
        </div>
      </div>

      {/* Account Switcher & Equity Hero */}
      <div className="terminal-panel p-3 md:p-4 space-y-3">
        <div className="flex flex-col md:flex-row md:items-center justify-between gap-3">
          {/* Segmented Market Controls — Swiss Monochrome */}
          <div className="inline-flex p-1 bg-muted/50 rounded-full border border-border/80">
            <button
              type="button"
              onClick={() => {
                window.location.href = '/auth/switch-account?account=inr'
              }}
              className={cn(
                'flex items-center gap-2 px-3.5 py-1.5 rounded-full text-xs font-semibold transition-all cursor-pointer',
                isIndianSandbox
                  ? 'bg-foreground text-background font-semibold shadow-xs'
                  : 'text-muted-foreground hover:text-foreground'
              )}
            >
              <span>🇮🇳</span>
              <span>Indian Markets</span>
              <span
                className={cn(
                  'text-[10px] px-2 py-0.5 rounded-full font-mono font-bold',
                  isIndianSandbox
                    ? 'bg-background/20 text-background'
                    : 'bg-muted text-muted-foreground'
                )}
              >
                {isIndianSandbox && marginData
                  ? formatAccountCurrency(marginData.availablecash, false)
                  : '₹10k'}
              </span>
            </button>

            <button
              type="button"
              onClick={() => {
                window.location.href = '/auth/switch-account?account=usd'
              }}
              className={cn(
                'flex items-center gap-2 px-3.5 py-1.5 rounded-full text-xs font-semibold transition-all cursor-pointer',
                isUsdSandbox
                  ? 'bg-foreground text-background font-semibold shadow-xs'
                  : 'text-muted-foreground hover:text-foreground'
              )}
            >
              <span>🌐</span>
              <span>USD Sandbox</span>
              <span
                className={cn(
                  'text-[10px] px-2 py-0.5 rounded-full font-mono font-bold',
                  isUsdSandbox ? 'bg-background/20 text-background' : 'bg-muted text-muted-foreground'
                )}
              >
                {isUsdSandbox && marginData
                  ? formatAccountCurrency(marginData.availablecash, true)
                  : '$100'}
              </span>
            </button>

            <button
              type="button"
              onClick={() => {
                window.location.href = '/auth/switch-account?account=binance'
              }}
              className={cn(
                'flex items-center gap-2 px-3.5 py-1.5 rounded-full text-xs font-semibold transition-all cursor-pointer',
                isBinance
                  ? 'bg-foreground text-background font-semibold shadow-xs'
                  : 'text-muted-foreground hover:text-foreground'
              )}
            >
              <span>🟡</span>
              <span>Binance Demo</span>
              <span
                className={cn(
                  'text-[10px] px-2 py-0.5 rounded-full font-mono font-bold',
                  isBinance ? 'bg-background/20 text-background' : 'bg-muted text-muted-foreground'
                )}
              >
                {isBinance && marginData
                  ? formatAccountCurrency(
                      marginData.total_balance_usd || marginData.availablecash,
                      true
                    )
                  : '$100'}
              </span>
            </button>
          </div>

          {/* Account Telemetry Status */}
          <div className="flex items-center gap-3 text-xs font-mono text-muted-foreground flex-wrap">
            <span className="flex items-center gap-1.5">
              <span className="h-1.5 w-1.5 rounded-full bg-emerald-500" />
              Active Target:{' '}
              <strong className="text-foreground font-semibold">
                {isBinance
                  ? 'Binance Official Demo (REST API)'
                  : isUsdSandbox
                    ? 'Crypto Sandbox (USD)'
                    : 'Indian Equities (INR)'}
              </strong>
            </span>
            {isBinance && (
              <>
                <span className="text-border">|</span>
                <span>
                  Base Capital: <strong className="text-emerald-500 font-semibold">$100.00</strong>
                </span>
                <span className="text-border">|</span>
                <span className="text-amber-500/90 font-medium">
                  15k USDC Collateral Excluded
                </span>
              </>
            )}
          </div>
        </div>
      </div>

      {/* 5-Column High-Density Precision Telemetry Cards */}
      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-3 md:gap-4">
        {/* Available Balance */}
        <div className="terminal-panel card-terminal p-4">
          <div className="flex items-center justify-between text-muted-foreground mb-1">
            <span className="text-[11px] font-mono uppercase tracking-wider font-semibold">
              {isBinance ? 'Cash Balance' : 'Available Cash'}
            </span>
            <Coins className="h-3.5 w-3.5 text-primary" />
          </div>
          <p className="terminal-stat-value text-2xl font-bold text-foreground mt-1">
            {isLoading
              ? '—'
              : marginData
                ? formatAccountCurrency(marginData.availablecash, isUsd)
                : isUsd
                  ? '$100.00'
                  : '₹0.00'}
          </p>
          <div className="mt-2.5 flex items-center gap-1.5">
            <span className="inline-flex items-center text-[10px] font-mono px-1.5 py-0.5 rounded bg-muted text-muted-foreground font-medium">
              {isBinance ? 'Base: $100.00' : 'Unencumbered'}
            </span>
          </div>
        </div>

        {/* Starting Capital or Collateral */}
        <div className="terminal-panel card-terminal p-4">
          <div className="flex items-center justify-between text-muted-foreground mb-1">
            <span className="text-[11px] font-mono uppercase tracking-wider font-semibold">
              {isBinance ? 'Starting Capital' : 'Collateral'}
            </span>
            <ShieldCheck className="h-3.5 w-3.5 text-emerald-500" />
          </div>
          <p className="terminal-stat-value text-2xl font-bold text-foreground mt-1">
            {isBinance
              ? '$100.00'
              : isLoading
                ? '—'
                : marginData
                  ? formatAccountCurrency(marginData.collateral, isUsd)
                  : isUsd
                    ? '$0.00'
                    : '₹0.00'}
          </p>
          <div className="mt-2.5 flex items-center gap-1.5">
            <span className="inline-flex items-center text-[10px] font-mono px-1.5 py-0.5 rounded bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 font-medium border border-emerald-500/20">
              {isBinance ? '15k Collateral Hidden' : 'Secured'}
            </span>
          </div>
        </div>

        {/* Unrealized P&L */}
        <div className="terminal-panel card-terminal p-4">
          <div className="flex items-center justify-between text-muted-foreground mb-1">
            <span className="text-[11px] font-mono uppercase tracking-wider font-semibold">
              Unrealized MTM
            </span>
            <Activity className="h-3.5 w-3.5 text-muted-foreground" />
          </div>
          <p
            className={cn(
              'terminal-stat-value text-2xl font-bold mt-1',
              marginData ? getPnLColor(marginData.m2munrealized) : 'text-foreground'
            )}
          >
            {isLoading
              ? '—'
              : marginData
                ? formatAccountCurrency(marginData.m2munrealized, isUsd)
                : isUsd
                  ? '$0.00'
                  : '₹0.00'}
          </p>
          <div className="mt-2.5 flex items-center gap-1.5">
            <Badge
              variant={marginData ? getPnLBadgeVariant(marginData.m2munrealized) : 'secondary'}
              className="text-[10px] font-mono h-5 px-1.5 font-medium rounded"
            >
              Live Positions
            </Badge>
          </div>
        </div>

        {/* Realized P&L */}
        <div className="terminal-panel card-terminal p-4">
          <div className="flex items-center justify-between text-muted-foreground mb-1">
            <span className="text-[11px] font-mono uppercase tracking-wider font-semibold">
              Booked P&L
            </span>
            <TrendingUp className="h-3.5 w-3.5 text-muted-foreground" />
          </div>
          <p
            className={cn(
              'terminal-stat-value text-2xl font-bold mt-1',
              marginData ? getPnLColor(marginData.m2mrealized) : 'text-foreground'
            )}
          >
            {isLoading
              ? '—'
              : marginData
                ? formatAccountCurrency(marginData.m2mrealized, isUsd)
                : isUsd
                  ? '$0.00'
                  : '₹0.00'}
          </p>
          <div className="mt-2.5 flex items-center gap-1.5">
            <Badge
              variant={marginData ? getPnLBadgeVariant(marginData.m2mrealized) : 'secondary'}
              className="text-[10px] font-mono h-5 px-1.5 font-medium rounded"
            >
              Session Closed
            </Badge>
          </div>
        </div>

        {/* Utilised Margin */}
        <div className="terminal-panel card-terminal p-4 col-span-2 sm:col-span-1">
          <div className="flex items-center justify-between text-muted-foreground mb-1">
            <span className="text-[11px] font-mono uppercase tracking-wider font-semibold">
              Margin Utilised
            </span>
            <Zap className="h-3.5 w-3.5 text-cyan-500" />
          </div>
          <p className="terminal-stat-value text-2xl font-bold text-foreground mt-1">
            {isLoading
              ? '—'
              : marginData
                ? formatAccountCurrency(marginData.utiliseddebits, isUsd)
                : isUsd
                  ? '$0.00'
                  : '₹0.00'}
          </p>
          <div className="mt-2.5 flex items-center gap-1.5">
            <span className="inline-flex items-center text-[10px] font-mono px-1.5 py-0.5 rounded bg-cyan-500/10 text-cyan-600 dark:text-cyan-400 font-medium border border-cyan-500/20">
              Active Margin
            </span>
          </div>
        </div>
      </div>

      {/* Official Binance Connection & Portfolio (Shown when Binance is active) */}
      {isBinance && (
        <div className="space-y-4 md:space-y-5">
          {/* Connection Banner — the venue is stated here because a live key
              trades real funds and a demo key does not. */}
          <div className="terminal-panel p-3.5 md:p-4 border-border bg-card flex flex-col md:flex-row md:items-center justify-between gap-3">
            <div className="flex items-center gap-3">
              <div className="relative flex h-2 w-2">
                <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-500 opacity-75" />
                <span className="relative inline-flex rounded-full h-2 w-2 bg-emerald-500" />
              </div>
              <div>
                <div className="flex items-center gap-2">
                  <span className="font-bold text-foreground text-sm tracking-tight">
                    {marginData?.is_live
                      ? 'Binance Live Account Connected'
                      : 'Binance Demo Engine Active'}
                  </span>
                  <span
                    className={cn(
                      'px-1.5 py-0.2 rounded-sm text-[10px] font-mono font-semibold uppercase tracking-wider border',
                      marginData?.is_live
                        ? 'bg-amber-500/10 text-amber-600 dark:text-amber-400 border-amber-500/30'
                        : 'bg-muted text-foreground border-border'
                    )}
                  >
                    {marginData?.is_live ? 'REAL FUNDS' : 'TESTNET'}
                  </span>
                </div>
                <p className="text-[11px] font-mono text-muted-foreground mt-0.5">
                  Spot:{' '}
                  <span className="text-foreground">
                    {marginData?.is_live ? 'api.binance.com' : 'demo-api.binance.com'}
                  </span>{' '}
                  &bull; Futures:{' '}
                  <span className="text-foreground">
                    {marginData?.is_live ? 'fapi.binance.com' : 'testnet.binancefuture.com'}
                  </span>{' '}
                  &bull; HMAC-SHA256
                </p>
              </div>
            </div>
            <div className="flex items-center gap-2 text-xs">
              <a
                href={marginData?.is_live ? 'https://www.binance.com/en/trade' : 'https://demo.binance.com/en-IN/trade'}
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-sm bg-background hover:bg-muted border border-border text-foreground transition-colors font-medium text-xs"
              >
                <span>Spot Web</span>
                <ExternalLink className="h-3 w-3" />
              </a>
              <a
                href={marginData?.is_live ? 'https://www.binance.com/en/futures' : 'https://demo.binance.com/en-IN/futures'}
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-sm bg-background hover:bg-muted border border-border text-foreground transition-colors font-medium text-xs"
              >
                <span>Futures Web</span>
                <ExternalLink className="h-3 w-3" />
              </a>
            </div>
          </div>

          {/* Spot & Futures Wallets Grid */}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3 md:gap-4">
            {/* Spot Wallet */}
            <div className="terminal-panel p-4 space-y-3">
              <div className="flex items-center justify-between pb-2 border-b border-border/50">
                <div className="flex items-center gap-2 text-sm font-semibold text-foreground">
                  <Coins className="h-4 w-4 text-foreground" />
                  <span>Spot Demo Assets</span>
                </div>
                <span className="font-mono text-xs font-semibold px-2 py-0.5 rounded-sm bg-muted text-foreground border border-border/60">
                  ${marginData?.spot_usdt || '0.00'} On Exchange
                </span>
              </div>
              <div className="grid grid-cols-3 gap-2">
                <div className="p-2 bg-muted/40 rounded border border-border/50 text-center">
                  <p className="text-[10px] font-mono uppercase text-muted-foreground">USDT On Exchange</p>
                  <p className="font-mono font-bold text-sm text-foreground mt-0.5">
                    ${marginData?.spot_usdt || '0.00'}
                  </p>
                </div>
                {marginData?.spot_balances && marginData.spot_balances.length > 0 ? (
                  marginData.spot_balances.slice(0, 2).map((b, i) => (
                    <div
                      key={b.asset || i}
                      className="p-2 bg-muted/40 rounded border border-border/50 text-center"
                    >
                      <p className="text-[10px] font-mono uppercase text-muted-foreground">{b.asset}</p>
                      <p className="font-mono font-bold text-sm text-foreground mt-0.5">
                        {typeof b.total === 'number' ? b.total.toFixed(4) : b.free || '0'}
                      </p>
                    </div>
                  ))
                ) : (
                  <>
                    <div className="p-2 bg-muted/40 rounded border border-border/50 text-center">
                      <p className="text-[10px] font-mono uppercase text-muted-foreground">BTC</p>
                      <p className="font-mono font-bold text-sm text-foreground mt-0.5">0.0200</p>
                    </div>
                    <div className="p-2 bg-muted/40 rounded border border-border/50 text-center">
                      <p className="text-[10px] font-mono uppercase text-muted-foreground">USDC</p>
                      <p className="font-mono font-bold text-sm text-foreground mt-0.5">Hidden</p>
                    </div>
                  </>
                )}
              </div>
            </div>

            {/* Futures Wallet */}
            <div className="terminal-panel p-4 space-y-3">
              <div className="flex items-center justify-between pb-2 border-b border-border/50">
                <div className="flex items-center gap-2 text-sm font-semibold text-foreground">
                  <TrendingUp className="h-4 w-4 text-emerald-500" />
                  <span>Tradable Balance</span>
                </div>
                <span className="font-mono text-xs font-semibold px-2 py-0.5 rounded bg-muted">
                  ${marginData?.tradable_usdt ?? marginData?.futures_usdt ?? '0.00'} Available
                </span>
              </div>
              <div className="grid grid-cols-2 gap-2">
                <div className="p-2 bg-muted/40 rounded border border-border/50 text-center">
                  <p className="text-[10px] font-mono uppercase text-muted-foreground">Tradable</p>
                  <p className="font-mono font-bold text-sm text-foreground mt-0.5">
                    ${marginData?.tradable_usdt ?? marginData?.futures_usdt ?? '0.00'}
                  </p>
                </div>
                <div className="p-2 bg-muted/40 rounded border border-border/50 text-center">
                  <p className="text-[10px] font-mono uppercase text-muted-foreground">Savings (protected)</p>
                  <p className="font-mono font-bold text-sm text-emerald-500 mt-0.5">
                    ${marginData?.savings_usdt ?? '0.00'}
                  </p>
                </div>
              </div>
              <p className="text-[11px] font-mono text-muted-foreground">
                Floor ${marginData?.trading_floor ?? '14880.00'} is never traded. The bot
                may only use what sits above it.
              </p>
            </div>
          </div>

          {/* Active Live Positions on Binance Futures */}
          <div className="terminal-panel overflow-hidden">
            <div className="p-3.5 border-b border-border/60 flex items-center justify-between">
              <div className="flex items-center gap-2 text-sm font-semibold text-foreground">
                <Activity className="h-4 w-4 text-primary" />
                <span>Live Futures Positions</span>
              </div>
              <span className="font-mono text-xs px-2 py-0.5 rounded bg-muted font-medium">
                {marginData?.positions?.length ?? 0} Active Position{(marginData?.positions?.length ?? 0) !== 1 ? 's' : ''}
              </span>
            </div>
            <div className="overflow-x-auto">
              <table className="w-full text-xs text-left">
                <thead className="text-[11px] font-mono uppercase text-muted-foreground bg-muted/40 border-b border-border/60">
                  <tr>
                    <th className="py-2.5 px-3.5 font-medium">Symbol</th>
                    <th className="py-2.5 px-3.5 font-medium">Side</th>
                    <th className="py-2.5 px-3.5 font-medium">Quantity</th>
                    <th className="py-2.5 px-3.5 font-medium">Entry Price</th>
                    <th className="py-2.5 px-3.5 font-medium">Mark Price</th>
                    <th className="py-2.5 px-3.5 font-medium text-right">Unrealized P&L</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-border/60">
                  {marginData?.positions && marginData.positions.length > 0 ? (
                    marginData.positions.map((p, idx) => (
                      <tr
                        key={idx}
                        className="hover:bg-muted/30 transition-colors font-mono"
                      >
                        <td className="py-3 px-3.5 font-semibold text-foreground">{p.symbol}</td>
                        <td className="py-3 px-3.5">
                          {p.side === 'LONG' ? (
                            <span className="badge-long">▲ LONG</span>
                          ) : (
                            <span className="badge-short">▼ SHORT</span>
                          )}
                        </td>
                        <td className="py-3 px-3.5">{Math.abs(num(p.amount))}</td>
                        <td className="py-3 px-3.5">${num(p.entry_price).toFixed(2)}</td>
                        <td className="py-3 px-3.5">${num(p.mark_price).toFixed(2)}</td>
                        <td className={cn('py-3 px-3.5 text-right font-bold', getPnLColor(num(p.unrealized_pnl)))}>
                          {num(p.unrealized_pnl) >= 0
                            ? `+$${num(p.unrealized_pnl).toFixed(4)}`
                            : `-$${Math.abs(num(p.unrealized_pnl)).toFixed(4)}`}
                        </td>
                      </tr>
                    ))
                  ) : (
                    <tr>
                      <td
                        colSpan={6}
                        className="py-6 text-center text-muted-foreground font-mono"
                      >
                        No active futures positions open
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      )}

      {/* Intraday PnL Graph */}
      <DashboardPnLChart />

      {/* Error Alert */}
      {error && (
        <div className="terminal-panel p-4 border-destructive/40 bg-destructive/5">
          <p className="text-destructive text-sm font-medium">{error}</p>
        </div>
      )}

      {/* Quick Access Tools — Editorial Command Grid */}
      <div className="space-y-3 pt-2">
        <div className="flex items-center justify-between">
          <h2 className="text-base md:text-lg font-bold tracking-tight text-foreground">
            Platform Modules
          </h2>
          <span className="text-xs font-mono text-muted-foreground uppercase">
            Fast Execution Commands
          </span>
        </div>

        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3 md:gap-4">
          {quickAccessCards.map((card) => {
            const cardClasses =
              'terminal-panel card-terminal block p-4 group transition-all duration-150'

            const cardContent = (
              <div className="space-y-2">
                <div className="flex items-center justify-between">
                  <div className="p-2 rounded-md bg-muted/60 border border-border/50 text-foreground group-hover:text-primary transition-colors">
                    <card.icon className="h-4 w-4" />
                  </div>
                  <div className="flex items-center gap-1.5">
                    <span className="px-1.5 py-0.5 rounded text-[10px] font-mono font-semibold tracking-wider text-muted-foreground bg-muted">
                      {card.tag}
                    </span>
                    <ArrowUpRight className="h-3.5 w-3.5 text-muted-foreground group-hover:text-foreground group-hover:translate-x-0.5 group-hover:-translate-y-0.5 transition-all" />
                  </div>
                </div>
                <div>
                  <h3 className="font-semibold text-sm tracking-tight text-foreground group-hover:text-primary transition-colors">
                    {card.label}
                  </h3>
                  <p className="text-xs text-muted-foreground mt-0.5 line-clamp-2">
                    {card.description}
                  </p>
                </div>
              </div>
            )

            return (
              <Link key={card.href} to={card.href} className={cardClasses}>
                {cardContent}
              </Link>
            )
          })}
        </div>
      </div>
    </div>
  )
}
