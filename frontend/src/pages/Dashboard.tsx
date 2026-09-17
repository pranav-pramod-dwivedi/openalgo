import {
  BarChart3,
  BookOpen,
  Coins,
  ExternalLink,
  FileText,
  GraduationCap,
  RefreshCw,
  Search,
  TrendingUp,
  Zap,
} from 'lucide-react'
import { useCallback, useEffect, useRef, useState } from 'react'
import { Link } from 'react-router'
import DashboardPnLChart from '@/components/dashboard/DashboardPnLChart'
import { useSocketContext } from '@/components/socket/SocketProvider'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { DataFreshness } from '@/components/ui/data-freshness'
import { useOrderEventRefresh } from '@/hooks/useOrderEventRefresh'
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

export default function Dashboard() {
  const { user } = useAuthStore()
  const { socket } = useSocketContext()
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

  // Fetch dashboard funds data
  const fetchFundsData = useCallback(async () => {
    try {
      setIsLoading(true)
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

  // Auto-refresh dashboard every 60s (less aggressive than trading pages)
  useEffect(() => {
    autoRefreshRef.current = setInterval(() => {
      fetchFundsData()
    }, 60_000)
    return () => {
      if (autoRefreshRef.current) clearInterval(autoRefreshRef.current)
    }
  }, [fetchFundsData])

  // Refresh funds when an order is placed (via SocketIO event)
  useOrderEventRefresh(fetchFundsData, {
    events: ['order_event', 'analyzer_update', 'close_position_event'],
  })

  // Listen for mode changes and refresh data
  useEffect(() => {
    const unsubscribe = onModeChange(() => {
      // Refresh funds data when mode changes
      fetchFundsData()
    })
    return () => unsubscribe()
  }, [fetchFundsData])

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
    checkMasterContractStatus()

    // Poll every 5 seconds until successful
    const interval = setInterval(() => {
      setMasterContract((prev) => {
        if (prev.status === 'success') {
          return prev // Don't check again if already successful
        }
        checkMasterContractStatus()
        return prev
      })
    }, 5000)

    return () => clearInterval(interval)
  }, [checkMasterContractStatus])

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
      href: '/search',
      label: 'OpenAlgo Symbols',
      description: 'Universal symbology across brokers',
      icon: Search,
      gradient: 'from-primary/10 to-primary/5 hover:from-primary/20 hover:to-primary/10',
      iconBg: 'bg-primary/20',
      iconColor: 'text-primary',
      borderColor: 'border-primary/20 hover:border-primary/40',
    },
    {
      href: '/logs',
      label: 'Live Logs',
      description: 'Real-time trading activity logs',
      icon: FileText,
      gradient:
        'from-violet-500/10 to-violet-500/5 hover:from-violet-500/20 hover:to-violet-500/10',
      iconBg: 'bg-violet-500/20',
      iconColor: 'text-violet-500',
      borderColor: 'border-violet-500/20 hover:border-violet-500/40',
    },
    {
      href: 'https://docs.openalgo.in',
      label: 'Documentation',
      description: 'Tutorials, API docs & features',
      icon: BookOpen,
      gradient: 'from-cyan-500/10 to-cyan-500/5 hover:from-cyan-500/20 hover:to-cyan-500/10',
      iconBg: 'bg-cyan-500/20',
      iconColor: 'text-cyan-500',
      borderColor: 'border-cyan-500/20 hover:border-cyan-500/40',
      external: true,
    },
    {
      href: '/pnl-tracker',
      label: 'P&L Tracker',
      description: 'Live intraday MTM tracker',
      icon: BarChart3,
      gradient: 'from-green-500/10 to-green-500/5 hover:from-green-500/20 hover:to-green-500/10',
      iconBg: 'bg-green-500/20',
      iconColor: 'text-green-500',
      borderColor: 'border-green-500/20 hover:border-green-500/40',
    },
    {
      href: 'https://www.openalgo.in/learn',
      label: 'OpenVarsity',
      description: 'Learn algo trading with OpenAlgo',
      icon: GraduationCap,
      gradient: 'from-blue-500/10 to-blue-500/5 hover:from-blue-500/20 hover:to-blue-500/10',
      iconBg: 'bg-blue-500/20',
      iconColor: 'text-blue-500',
      borderColor: 'border-blue-500/20 hover:border-blue-500/40',
      external: true,
    },
    {
      href: '/logs/latency',
      label: 'Latency Monitor',
      description: 'Monitor order & API latency',
      icon: Zap,
      gradient:
        'from-orange-500/10 to-orange-500/5 hover:from-orange-500/20 hover:to-orange-500/10',
      iconBg: 'bg-orange-500/20',
      iconColor: 'text-orange-500',
      borderColor: 'border-orange-500/20 hover:border-orange-500/40',
    },
  ]

  // Broker token expired but the app session is fine: send the user to the
  // broker reconnect flow, not /login (which would bounce back) — #1400.
  if (brokerExpired) {
    return (
      <div className="flex flex-col items-center justify-center min-h-[50vh] space-y-4">
        <h1 className="text-2xl font-bold">Broker Session Expired</h1>
        <p className="text-muted-foreground">
          Your broker token has expired (brokers roll tokens daily). Reconnect to continue trading.
        </p>
        <Link
          to="/broker"
          className="inline-flex items-center rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground hover:bg-primary/90"
        >
          Reconnect Broker
        </Link>
      </div>
    )
  }

  // If not authenticated, show login prompt
  if (!isAuthenticated) {
    return (
      <div className="flex flex-col items-center justify-center min-h-[50vh] space-y-4">
        <h1 className="text-2xl font-bold">Session Expired</h1>
        <p className="text-muted-foreground">Please log in to access the dashboard.</p>
        <Link to="/login" className="text-primary hover:underline">
          Go to Login
        </Link>
      </div>
    )
  }

  return (
    <div className="space-y-6 md:space-y-12">
      {/* Dashboard Header */}
      <div className="flex flex-col lg:flex-row lg:items-start gap-4">
        <div className="flex-1">
          <h1 className="text-2xl md:text-3xl font-bold">Trading Dashboard</h1>
          <p className="text-muted-foreground mt-1 md:mt-2 text-sm md:text-base">
            Overview of your trading account and market positions
          </p>
        </div>
        <div className="flex items-center gap-3 flex-wrap lg:ml-auto lg:self-start">
          <DataFreshness
            lastUpdated={lastUpdated}
            isRefreshing={isRefreshing || isLoading}
            isConnected={socket?.connected}
          />
          <Button
            variant="outline"
            size="sm"
            onClick={() => {
              setIsRefreshing(true)
              fetchFundsData()
            }}
            disabled={isRefreshing || isLoading}
          >
            <RefreshCw
              className={cn('h-4 w-4 mr-2', (isRefreshing || isLoading) && 'animate-spin')}
            />
            Refresh
          </Button>
        </div>
        {/* Master Contract Status Indicator */}
        <div className="flex items-center gap-2 md:gap-3 bg-muted rounded-lg px-3 md:px-4 py-2 md:py-3 w-fit lg:ml-auto lg:self-start">
          <span className="text-xs md:text-sm font-medium whitespace-nowrap">Master Contract:</span>
          <div className="flex items-center gap-2">
            <div
              className={cn('w-2.5 h-2.5 md:w-3 md:h-3 rounded-full', getMasterContractLedColor())}
            />
            <span
              className={cn('text-xs md:text-sm', getMasterContractTextColor())}
              title={masterContract.message}
            >
              {getMasterContractStatusText()}
            </span>
          </div>
        </div>
      </div>

      {/* Account Switcher Tabs: INR vs USD vs Binance Demo */}
      <div className="flex flex-wrap items-center justify-between gap-4 p-2.5 bg-muted/40 rounded-2xl border">
        <div className="inline-flex p-1 bg-background rounded-xl border shadow-sm">
          <button
            type="button"
            onClick={() => {
              window.location.href = '/auth/switch-account?account=inr'
            }}
            className={cn(
              'flex items-center gap-2 px-4 py-2 rounded-lg text-sm transition-all cursor-pointer',
              isIndianSandbox
                ? 'bg-primary text-primary-foreground font-semibold shadow'
                : 'text-muted-foreground hover:text-foreground font-medium'
            )}
          >
            <span>🇮🇳</span>
            <span>Indian Markets (INR)</span>
            <span
              className={cn(
                'text-xs px-2 py-0.5 rounded-full font-mono font-bold',
                isIndianSandbox
                  ? 'bg-primary-foreground/20 text-primary-foreground'
                  : 'bg-muted text-muted-foreground'
              )}
            >
              {isIndianSandbox && marginData
                ? formatAccountCurrency(marginData.availablecash, false)
                : '₹10,000'}
            </span>
          </button>
          <button
            type="button"
            onClick={() => {
              window.location.href = '/auth/switch-account?account=usd'
            }}
            className={cn(
              'flex items-center gap-2 px-4 py-2 rounded-lg text-sm transition-all cursor-pointer',
              isUsdSandbox
                ? 'bg-emerald-600 text-white font-semibold shadow'
                : 'text-muted-foreground hover:text-foreground font-medium'
            )}
          >
            <span>🌐</span>
            <span>Forex & Crypto Sandbox</span>
            <span
              className={cn(
                'text-xs px-2 py-0.5 rounded-full font-mono font-bold',
                isUsdSandbox ? 'bg-white/20 text-white' : 'bg-muted text-muted-foreground'
              )}
            >
              {isUsdSandbox && marginData
                ? formatAccountCurrency(marginData.availablecash, true)
                : '$100.59'}
            </span>
          </button>
          <button
            type="button"
            onClick={() => {
              window.location.href = '/auth/switch-account?account=binance'
            }}
            className={cn(
              'flex items-center gap-2 px-4 py-2 rounded-lg text-sm transition-all cursor-pointer',
              isBinance
                ? 'bg-amber-500 text-black font-semibold shadow'
                : 'text-muted-foreground hover:text-foreground font-medium'
            )}
          >
            <span className="text-base">🟡</span>
            <span>Binance Official Demo</span>
            <span
              className={cn(
                'text-xs px-2 py-0.5 rounded-full font-mono font-bold',
                isBinance ? 'bg-black/20 text-black' : 'bg-muted text-muted-foreground'
              )}
            >
              {isBinance && marginData
                ? formatAccountCurrency(
                    marginData.total_balance_usd || marginData.availablecash,
                    true
                  )
                : '$100.00'}
            </span>
          </button>
        </div>

        <div className="flex items-center gap-2 text-xs text-muted-foreground px-3 py-1.5 bg-muted/50 rounded-lg border flex-wrap justify-between">
          <div className="flex items-center gap-2">
            <span>Active:</span>
            <span className="font-semibold text-foreground">
              {isBinance
                ? '🟡 Official Binance Demo (Spot & Futures REST API)'
                : isUsdSandbox
                  ? '🌐 Forex & Crypto Sandbox ($ USD - openalgo_usd)'
                  : '🇮🇳 Indian Equities & F&O Sandbox (₹ INR - openalgo_admin)'}
            </span>
          </div>
          {isBinance && (
            <div className="flex items-center gap-3 font-mono text-xs flex-wrap">
              <span>
                Account Equity:{' '}
                <strong className="text-foreground">
                  ${marginData?.total_balance_usd || '100.00'}
                </strong>
              </span>
              <span className="text-muted-foreground hidden sm:inline">|</span>
              <span>
                Starting Capital: <strong className="text-blue-500">$100.00</strong>
              </span>
              <span className="text-muted-foreground hidden sm:inline">|</span>
              <span className="text-emerald-500 font-medium">
                Collateral: Hidden ($15k USDC excluded)
              </span>
            </div>
          )}
        </div>
      </div>

      {/* Stats Grid */}
      <div className="grid grid-cols-1 sm:grid-cols-3 lg:grid-cols-5 gap-4 md:gap-6">
        {/* Available Balance */}
        <Card>
          <CardContent className="pt-6">
            <div className="space-y-1">
              <p className="text-sm text-muted-foreground">
                {isBinance ? 'Cash Balance (USD)' : 'Available Balance'}
              </p>
              <p className="text-2xl font-bold text-primary">
                {isLoading
                  ? '...'
                  : marginData
                    ? formatAccountCurrency(marginData.availablecash, isUsd)
                    : isUsd
                      ? '$100.00'
                      : '₹0.00'}
              </p>
              <Badge variant="secondary" className="mt-2">
                {isBinance ? 'Base: $100.00' : 'Cash Balance'}
              </Badge>
            </div>
          </CardContent>
        </Card>

        {/* Collateral or Starting Capital */}
        {isBinance ? (
          <Card>
            <CardContent className="pt-6">
              <div className="space-y-1">
                <p className="text-sm text-muted-foreground">Starting Capital</p>
                <p className="text-2xl font-bold text-violet-500 dark:text-violet-400">$100.00</p>
                <Badge variant="secondary" className="mt-2 text-xs">
                  15k USDC Collateral Hidden
                </Badge>
              </div>
            </CardContent>
          </Card>
        ) : (
          <Card>
            <CardContent className="pt-6">
              <div className="space-y-1">
                <p className="text-sm text-muted-foreground">Collateral</p>
                <p className="text-2xl font-bold text-violet-500 dark:text-violet-400">
                  {isLoading
                    ? '...'
                    : marginData
                      ? formatAccountCurrency(marginData.collateral, isUsd)
                      : isUsd
                        ? '$0.00'
                        : '₹0.00'}
                </p>
                <Badge variant="secondary" className="mt-2">
                  Total Collateral
                </Badge>
              </div>
            </CardContent>
          </Card>
        )}

        {/* Unrealized P&L */}
        <Card>
          <CardContent className="pt-6">
            <div className="space-y-1">
              <p className="text-sm text-muted-foreground">Unrealized P&L</p>
              <p
                className={cn(
                  'text-2xl font-bold',
                  marginData ? getPnLColor(marginData.m2munrealized) : ''
                )}
              >
                {isLoading
                  ? '...'
                  : marginData
                    ? formatAccountCurrency(marginData.m2munrealized, isUsd)
                    : isUsd
                      ? '$0.00'
                      : '₹0.00'}
              </p>
              <Badge
                variant={marginData ? getPnLBadgeVariant(marginData.m2munrealized) : 'secondary'}
                className="mt-2"
              >
                Mark to Market
              </Badge>
            </div>
          </CardContent>
        </Card>

        {/* Realized P&L */}
        <Card>
          <CardContent className="pt-6">
            <div className="space-y-1">
              <p className="text-sm text-muted-foreground">Realized P&L</p>
              <p
                className={cn(
                  'text-2xl font-bold',
                  marginData ? getPnLColor(marginData.m2mrealized) : ''
                )}
              >
                {isLoading
                  ? '...'
                  : marginData
                    ? formatAccountCurrency(marginData.m2mrealized, isUsd)
                    : isUsd
                      ? '$0.00'
                      : '₹0.00'}
              </p>
              <Badge
                variant={marginData ? getPnLBadgeVariant(marginData.m2mrealized) : 'secondary'}
                className="mt-2"
              >
                Booked P&L
              </Badge>
            </div>
          </CardContent>
        </Card>

        {/* Utilised Margin */}
        <Card>
          <CardContent className="pt-6">
            <div className="space-y-1">
              <p className="text-sm text-muted-foreground">Utilised Margin</p>
              <p className="text-2xl font-bold text-cyan-500 dark:text-cyan-400">
                {isLoading
                  ? '...'
                  : marginData
                    ? formatAccountCurrency(marginData.utiliseddebits, isUsd)
                    : isUsd
                      ? '$0.00'
                      : '₹0.00'}
              </p>
              <Badge
                variant="outline"
                className="mt-2 border-cyan-500/50 text-cyan-600 dark:text-cyan-400"
              >
                Used Margin
              </Badge>
            </div>
          </CardContent>
        </Card>
      </div>

      {/* Official Binance Live Connection & Portfolio (Shown when Binance Demo is active) */}
      {isBinance && (
        <div className="space-y-6">
          {/* Live Connection Banner */}
          <div className="p-4 bg-amber-500/10 border border-amber-500/30 rounded-2xl flex flex-col md:flex-row md:items-center justify-between gap-4">
            <div className="flex items-start md:items-center gap-3">
              <div className="relative flex h-3 w-3 mt-1 md:mt-0">
                <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75"></span>
                <span className="relative inline-flex rounded-full h-3 w-3 bg-emerald-500"></span>
              </div>
              <div>
                <div className="flex items-center gap-2">
                  <span className="font-semibold text-foreground text-base">
                    Official Binance Demo Connected via Live REST API
                  </span>
                  <Badge
                    variant="outline"
                    className="border-amber-500 text-amber-600 dark:text-amber-400 font-mono text-xs"
                  >
                    Non-Simulated
                  </Badge>
                </div>
                <p className="text-xs md:text-sm text-muted-foreground mt-0.5">
                  Spot: <code className="font-mono text-foreground">demo-api.binance.com</code>{' '}
                  &bull; Futures:{' '}
                  <code className="font-mono text-foreground">testnet.binancefuture.com</code>{' '}
                  &bull; HMAC-SHA256 Signed
                </p>
              </div>
            </div>
            <div className="flex items-center gap-2 text-xs">
              <a
                href="https://demo.binance.com/en-IN/trade"
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-background border hover:bg-muted text-foreground transition-colors font-medium"
              >
                <span>Spot Web</span>
                <ExternalLink className="h-3.5 w-3.5" />
              </a>
              <a
                href="https://demo.binance.com/en-IN/futures"
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-background border hover:bg-muted text-foreground transition-colors font-medium"
              >
                <span>Futures Web</span>
                <ExternalLink className="h-3.5 w-3.5" />
              </a>
            </div>
          </div>

          {/* Spot & Futures Wallets Grid */}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            {/* Spot Wallet */}
            <Card className="border">
              <CardHeader className="pb-3">
                <CardTitle className="text-base flex items-center justify-between">
                  <span className="flex items-center gap-2">
                    <Coins className="h-4 w-4 text-amber-500" />
                    Spot Demo Wallet
                  </span>
                  <Badge variant="secondary" className="font-mono text-xs">
                    ${marginData?.spot_usdt || '0.00'} USDT Free
                  </Badge>
                </CardTitle>
              </CardHeader>
              <CardContent className="space-y-2 text-sm">
                <div
                  className={cn(
                    'grid gap-2',
                    marginData?.spot_balances && marginData.spot_balances.length > 0
                      ? `grid-cols-2 sm:grid-cols-${Math.min(marginData.spot_balances.length + 1, 4)}`
                      : 'grid-cols-2 sm:grid-cols-3'
                  )}
                >
                  <div className="p-2.5 bg-muted/50 rounded-lg border text-center">
                    <p className="text-xs text-muted-foreground">USDT Free</p>
                    <p className="font-mono font-bold text-foreground mt-0.5">
                      ${marginData?.spot_usdt || '0.00'}
                    </p>
                  </div>
                  {marginData?.spot_balances && marginData.spot_balances.length > 0 ? (
                    marginData.spot_balances.slice(0, 5).map((b, i) => (
                      <div
                        key={b.asset || i}
                        className="p-2.5 bg-muted/50 rounded-lg border text-center"
                      >
                        <p className="text-xs text-muted-foreground">{b.asset}</p>
                        <p className="font-mono font-bold text-foreground mt-0.5">
                          {typeof b.total === 'number' ? b.total.toFixed(6) : b.free || '0'}
                        </p>
                      </div>
                    ))
                  ) : (
                    <>
                      <div className="p-2.5 bg-muted/50 rounded-lg border text-center">
                        <p className="text-xs text-muted-foreground">SOL</p>
                        <p className="font-mono font-bold text-foreground mt-0.5">—</p>
                      </div>
                      <div className="p-2.5 bg-muted/50 rounded-lg border text-center">
                        <p className="text-xs text-muted-foreground">BTC</p>
                        <p className="font-mono font-bold text-foreground mt-0.5">—</p>
                      </div>
                    </>
                  )}
                </div>
              </CardContent>
            </Card>

            {/* Futures Wallet */}
            <Card className="border">
              <CardHeader className="pb-3">
                <CardTitle className="text-base flex items-center justify-between">
                  <span className="flex items-center gap-2">
                    <TrendingUp className="h-4 w-4 text-emerald-500" />
                    Futures Testnet Wallet
                  </span>
                  <Badge variant="secondary" className="font-mono text-xs">
                    ${marginData?.futures_usdt || '0.00'} USDT Avail
                  </Badge>
                </CardTitle>
              </CardHeader>
              <CardContent className="space-y-2 text-sm">
                <div className="grid grid-cols-2 sm:grid-cols-2 gap-2">
                  <div className="p-2.5 bg-muted/50 rounded-lg border text-center">
                    <p className="text-xs text-muted-foreground">USDT Avail</p>
                    <p className="font-mono font-bold text-foreground mt-0.5">
                      ${marginData?.futures_usdt || '0.00'}
                    </p>
                  </div>
                  {marginData?.futures_balances && marginData.futures_balances.length > 0 ? (
                    marginData.futures_balances.slice(0, 1).map((b, i) => (
                      <div
                        key={b.asset || i}
                        className="p-2.5 bg-muted/50 rounded-lg border text-center"
                      >
                        <p className="text-xs text-muted-foreground">{b.asset} Balance</p>
                        <p className="font-mono font-bold text-foreground mt-0.5">
                          {typeof b.balance === 'number' ? b.balance.toFixed(6) : '0'}
                        </p>
                      </div>
                    ))
                  ) : (
                    <div className="p-2.5 bg-muted/50 rounded-lg border text-center">
                      <p className="text-xs text-muted-foreground">Collateral</p>
                      <p className="font-mono font-bold text-foreground mt-0.5">—</p>
                    </div>
                  )}
                </div>
              </CardContent>
            </Card>
          </div>

          {/* Active Live Positions on Binance Futures */}
          <Card className="border">
            <CardHeader className="pb-3">
              <CardTitle className="text-base flex items-center justify-between">
                <span className="flex items-center gap-2">
                  <TrendingUp className="h-4 w-4 text-primary" />
                  Live Binance Futures Positions
                </span>
                <Badge variant="outline" className="font-mono text-xs">
                  {marginData?.positions?.length ?? 0} Open Position
                  {(marginData?.positions?.length ?? 0) !== 1 ? 's' : ''}
                </Badge>
              </CardTitle>
            </CardHeader>
            <CardContent>
              <div className="overflow-x-auto">
                <table className="w-full text-sm text-left">
                  <thead className="text-xs text-muted-foreground border-b bg-muted/30">
                    <tr>
                      <th className="py-2.5 px-3">Symbol</th>
                      <th className="py-2.5 px-3">Side</th>
                      <th className="py-2.5 px-3">Quantity</th>
                      <th className="py-2.5 px-3">Entry Price</th>
                      <th className="py-2.5 px-3">Mark Price</th>
                      <th className="py-2.5 px-3">Unrealized P&L</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-border">
                    {marginData?.positions && marginData.positions.length > 0 ? (
                      marginData.positions.map((p, idx) => (
                        <tr
                          key={idx}
                          className="hover:bg-muted/30 transition-colors font-mono text-xs"
                        >
                          <td className="py-3 px-3 font-semibold text-foreground">{p.symbol}</td>
                          <td className="py-3 px-3">
                            <Badge
                              variant={p.side === 'LONG' ? 'default' : 'destructive'}
                              className="text-xs"
                            >
                              {p.side}
                            </Badge>
                          </td>
                          <td className="py-3 px-3">{Math.abs(p.amount)}</td>
                          <td className="py-3 px-3">${p.entry_price.toFixed(2)}</td>
                          <td className="py-3 px-3">${p.mark_price.toFixed(2)}</td>
                          <td className={cn('py-3 px-3 font-bold', getPnLColor(p.unrealized_pnl))}>
                            {p.unrealized_pnl >= 0
                              ? `+$${p.unrealized_pnl.toFixed(4)}`
                              : `-$${Math.abs(p.unrealized_pnl).toFixed(4)}`}
                          </td>
                        </tr>
                      ))
                    ) : (
                      <tr>
                        <td
                          colSpan={6}
                          className="py-6 text-center text-muted-foreground font-mono text-xs"
                        >
                          No open futures positions
                        </td>
                      </tr>
                    )}
                  </tbody>
                </table>
              </div>
            </CardContent>
          </Card>
        </div>
      )}

      {/* Intraday PnL Graph */}
      <DashboardPnLChart />

      {/* Error Alert */}
      {error && (
        <Card className="border-destructive bg-destructive/5">
          <CardContent className="pt-6">
            <p className="text-destructive text-sm">{error}</p>
          </CardContent>
        </Card>
      )}

      {/* Quick Access Tools */}
      <div>
        <h2 className="text-xl md:text-2xl font-semibold mb-4 md:mb-6">Quick Access</h2>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 md:gap-5">
          {quickAccessCards.map((card) => {
            const cardClasses = cn(
              'block rounded-lg border transition-all duration-300 hover:shadow-lg',
              `bg-gradient-to-br ${card.gradient}`,
              card.borderColor
            )

            const cardContent = (
              <div className="p-4 md:p-5">
                <div className="flex items-start gap-3 md:gap-4">
                  <div className={cn('p-2.5 md:p-3 rounded-lg flex-shrink-0', card.iconBg)}>
                    <card.icon className={cn('h-5 w-5 md:h-6 md:w-6', card.iconColor)} />
                  </div>
                  <div className="flex-1 min-w-0">
                    <h3 className="font-semibold mb-1 text-base md:text-lg">{card.label}</h3>
                    <p className="text-sm text-muted-foreground">{card.description}</p>
                  </div>
                </div>
              </div>
            )

            if (card.external) {
              return (
                <a
                  key={card.href}
                  href={card.href}
                  target="_blank"
                  rel="noopener noreferrer"
                  className={cardClasses}
                >
                  {cardContent}
                </a>
              )
            }

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
