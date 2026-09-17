import {
  Activity,
  ArrowRight,
  ChevronRight,
  Coins,
  Layers,
  PiggyBank,
  RefreshCw,
  TrendingDown,
  TrendingUp,
  Wallet,
  Zap,
} from 'lucide-react'
import { useCallback, useEffect, useRef, useState } from 'react'
import { Link } from 'react-router'
import { MonetraBarChart } from '@/components/dashboard/MonetraBarChart'
import DashboardPnLChart from '@/components/dashboard/DashboardPnLChart'
import { useOrderEventRefresh } from '@/hooks/useOrderEventRefresh'
import { cn } from '@/lib/utils'
import { useAuthStore } from '@/stores/authStore'
import { onModeChange } from '@/stores/themeStore'

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
  positions?: BinancePosition[]
}

function formatCurrency(value: string | number, isUsd: boolean = true): string {
  const num = typeof value === 'string' ? parseFloat(value) : value
  if (Number.isNaN(num)) return isUsd ? '$0.00' : '₹0.00'
  const isNegative = num < 0
  const absNum = Math.abs(num)
  const sym = isUsd ? '$' : '₹'

  const formatted = absNum.toLocaleString('en-US', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })
  return isNegative ? `-${sym}${formatted}` : `${sym}${formatted}`
}

export default function Dashboard() {
  const { user } = useAuthStore()
  const [marginData, setMarginData] = useState<MarginData | null>(null)
  const [isLoading, setIsLoading] = useState(true)
  const [isRefreshing, setIsRefreshing] = useState(false)
  const [isAuthenticated, setIsAuthenticated] = useState(true)
  const [activeAccountTab, setActiveAccountTab] = useState<'Checking' | 'Savings' | 'Investments'>('Investments')
  const autoRefreshRef = useRef<ReturnType<typeof setInterval> | null>(null)

  const isBinance = user?.broker === 'binance_demo' || user?.username?.toLowerCase().includes('binance')
  const isUsdSandbox = user?.username?.toLowerCase().includes('usd')
  const isUsd = isBinance || isUsdSandbox

  const fetchFundsData = useCallback(async () => {
    try {
      const response = await fetch('/api/funds', {
        credentials: 'include',
        headers: { Accept: 'application/json' },
      })
      if (response.status === 401) {
        setIsAuthenticated(false)
        setIsLoading(false)
        return
      }
      const data = await response.json()
      if (data.status === 'success' && data.data) {
        setMarginData(data.data)
      }
    } catch (_err) {
      // ignore
    } finally {
      setIsLoading(false)
      setIsRefreshing(false)
    }
  }, [])

  useEffect(() => {
    fetchFundsData()
    autoRefreshRef.current = setInterval(() => {
      fetchFundsData()
    }, 60_000)
    return () => {
      if (autoRefreshRef.current) clearInterval(autoRefreshRef.current)
    }
  }, [fetchFundsData])

  useOrderEventRefresh(fetchFundsData, {
    events: ['order_event', 'analyzer_update', 'close_position_event'],
  })

  useEffect(() => {
    const unsubscribe = onModeChange(() => {
      fetchFundsData()
    })
    return () => unsubscribe()
  }, [fetchFundsData])

  if (!isAuthenticated) {
    return (
      <div className="flex flex-col items-center justify-center min-h-[50vh] text-center p-6">
        <h1 className="text-2xl font-bold">Session Expired</h1>
        <p className="text-muted-foreground mt-2">Please log in to access your portfolio.</p>
        <Link to="/login" className="mt-4 monetra-btn-primary">
          Go to Login
        </Link>
      </div>
    )
  }

  // Calculate live portfolio values
  const availableCash = marginData ? marginData.availablecash : (isUsd ? '100.00' : '10000.00')

  return (
    <div className="space-y-6 max-w-5xl mx-auto pb-20">
      {/* ─────────────────────────────────────────────────────────────
          UPPER HERO SECTION (Monetra Light Minimalist Architecture)
          - Credit/Portfolio score style hero
          - Smooth rounded bar chart
          - Account type pill selector: [Checking] [Savings] [Investments]
          ───────────────────────────────────────────────────────────── */}
      <div className="monetra-card p-6 md:p-8 bg-card relative overflow-hidden">
        <div className="flex flex-col md:flex-row md:items-center justify-between gap-4">
          <div>
            <div className="flex items-center gap-2 text-muted-foreground text-xs font-semibold uppercase tracking-wider">
              <span>Available Trading Capital</span>
              <span className="inline-flex items-center gap-1 text-emerald-600 dark:text-emerald-400 bg-emerald-50 dark:bg-emerald-950/40 px-2 py-0.5 rounded-full text-xs font-bold">
                <TrendingUp className="h-3 w-3" /> +15.4%
              </span>
            </div>
            <div className="flex items-baseline gap-3 mt-1.5">
              <h1 className="text-4xl md:text-5xl font-extrabold tracking-tight text-foreground">
                {isLoading ? '...' : formatCurrency(availableCash, isUsd)}
              </h1>
              <span className="text-xs font-medium text-muted-foreground">
                {isBinance ? 'Demo Base ($100)' : 'Active Margin'}
              </span>
            </div>
          </div>

          {/* Quick Action Top Right Pill */}
          <div className="flex items-center gap-2">
            <button
              onClick={() => {
                setIsRefreshing(true)
                fetchFundsData()
              }}
              disabled={isRefreshing || isLoading}
              className="monetra-pill monetra-pill-inactive flex items-center gap-1.5 hover:bg-muted"
            >
              <RefreshCw className={cn("h-3.5 w-3.5", (isRefreshing || isLoading) && "animate-spin")} />
              <span>Refresh</span>
            </button>
            <Link
              to="/trading"
              className="monetra-pill monetra-pill-active flex items-center gap-1.5"
            >
              <span>Trade Now</span>
              <ArrowRight className="h-3.5 w-3.5" />
            </Link>
          </div>
        </div>

        {/* Monetra Bar Chart Visualization */}
        <div className="mt-4">
          <MonetraBarChart />
        </div>

        {/* View Details Banner link */}
        <div className="mt-4 pt-4 border-t border-border/60 flex items-center justify-between text-sm cursor-pointer hover:opacity-80 transition-opacity">
          <div className="flex items-center gap-2 font-semibold text-foreground">
            <div className="w-6 h-6 rounded-lg bg-blue-50 dark:bg-blue-950/50 flex items-center justify-center text-blue-600">
              <Activity className="h-3.5 w-3.5" />
            </div>
            <span>View Full Performance Telemetry</span>
          </div>
          <ChevronRight className="h-4 w-4 text-muted-foreground" />
        </div>

        {/* Segmented Account Pills: Checking / Savings / Investments */}
        <div className="mt-5 flex items-center gap-2 pt-2">
          {(['Checking', 'Savings', 'Investments'] as const).map((tab) => (
            <button
              key={tab}
              onClick={() => setActiveAccountTab(tab)}
              className={cn(
                'monetra-pill flex items-center gap-2 text-xs font-semibold',
                activeAccountTab === tab
                  ? 'monetra-pill-active'
                  : 'monetra-pill-inactive hover:text-foreground'
              )}
            >
              {tab === 'Checking' && <Wallet className="h-3.5 w-3.5" />}
              {tab === 'Savings' && <PiggyBank className="h-3.5 w-3.5" />}
              {tab === 'Investments' && <TrendingUp className="h-3.5 w-3.5" />}
              <span>{tab}</span>
            </button>
          ))}
        </div>
      </div>

      {/* ─────────────────────────────────────────────────────────────
          MIDDLE TIER: Monetra Action Buttons & Calendar Strip
          - Black quick action buttons: [Add Order] [Track Trades] [Alerts]
          - Calendar week strip with date circles & status dots
          ───────────────────────────────────────────────────────────── */}
      <div className="monetra-card p-6 bg-[#18191D] text-white">
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 pb-5 border-b border-white/10">
          <div>
            <div className="flex items-center gap-2">
              <span className="text-xs font-semibold uppercase tracking-wider text-slate-400">
                Execution Scheduler
              </span>
            </div>
            <p className="text-base font-bold text-white mt-1">
              You have <span className="text-blue-400 font-extrabold">{marginData?.positions?.length || 0} active orders</span> running in this cycle
            </p>
          </div>

          <div className="flex items-center gap-2">
            <Link
              to="/trading"
              className="flex items-center gap-2 px-4 py-2 rounded-full bg-white/10 hover:bg-white/20 text-white text-xs font-semibold transition-all"
            >
              <Zap className="h-3.5 w-3.5" />
              <span>Fast Order</span>
            </Link>
            <Link
              to="/orderbook"
              className="flex items-center gap-2 px-4 py-2 rounded-full bg-white/10 hover:bg-white/20 text-white text-xs font-semibold transition-all"
            >
              <span>Orderbook</span>
            </Link>
          </div>
        </div>

        {/* Monetra Horizontal Week Strip */}
        <div className="pt-5 flex items-center justify-between gap-2 overflow-x-auto">
          {[
            { day: 'M', date: '9', icon: null, active: false },
            { day: 'T', date: '10', icon: '🟢', active: true },
            { day: 'W', date: '11', icon: '☁️', active: false },
            { day: 'T', date: '12', icon: null, active: false },
            { day: 'F', date: '13', icon: null, active: false },
            { day: 'S', date: '14', icon: null, active: false },
            { day: 'S', date: '15', icon: '⚡', active: false },
          ].map((item, idx) => (
            <div
              key={idx}
              className={cn(
                'flex flex-col items-center gap-2 p-2.5 rounded-2xl min-w-[48px] cursor-pointer transition-all',
                item.active
                  ? 'bg-white text-[#18191D] font-bold shadow-md'
                  : 'text-slate-400 hover:bg-white/5'
              )}
            >
              <span className="text-[10px] font-semibold">{item.day}</span>
              <span className="text-sm font-extrabold">{item.date}</span>
              <div className="w-2 h-2 rounded-full flex items-center justify-center">
                {item.icon ? (
                  <span className="text-[10px]">{item.icon}</span>
                ) : (
                  <span className="w-1 h-1 rounded-full bg-slate-600" />
                )}
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* ─────────────────────────────────────────────────────────────
          LOWER CONTAINER: Active Positions & Recent Charges List
          (Replicating Monetra category review & transaction tiles)
          ───────────────────────────────────────────────────────────── */}
      <div className="monetra-card p-6 bg-card">
        {/* Category Review Header Tile */}
        <Link
          to="/positions"
          className="flex items-center justify-between p-4 rounded-2xl bg-muted/50 hover:bg-muted/80 transition-colors border border-border/50"
        >
          <div className="flex items-center gap-3">
            <div className="monetra-icon-tile monetra-icon-blue">
              <Layers className="h-5 w-5" />
            </div>
            <div>
              <h3 className="font-bold text-sm text-foreground">Active Positions Review</h3>
              <p className="text-xs text-muted-foreground mt-0.5">
                Review latest fills, unrealized MTM & risk leverage
              </p>
            </div>
          </div>
          <ChevronRight className="h-4 w-4 text-muted-foreground" />
        </Link>

        {/* Live Positions / Transactions List */}
        <div className="mt-6 space-y-4">
          <div className="flex items-center justify-between">
            <h4 className="font-bold text-base text-foreground">Open Positions</h4>
            <span className="text-xs font-semibold text-muted-foreground">
              {marginData?.positions?.length || 0} Open
            </span>
          </div>

          {marginData?.positions && marginData.positions.length > 0 ? (
            <div className="space-y-3">
              {marginData.positions.map((pos, idx) => {
                const isLong = pos.side === 'LONG'
                const isPositive = pos.unrealized_pnl >= 0
                return (
                  <div
                    key={idx}
                    className="flex items-center justify-between p-3.5 rounded-2xl bg-background border border-border/60 hover:shadow-xs transition-all"
                  >
                    <div className="flex items-center gap-3">
                      <div className={cn("monetra-icon-tile", isLong ? "monetra-icon-green" : "monetra-icon-pink")}>
                        {isLong ? <TrendingUp className="h-5 w-5" /> : <TrendingDown className="h-5 w-5" />}
                      </div>
                      <div>
                        <div className="flex items-center gap-2">
                          <span className="font-extrabold text-sm text-foreground">
                            {pos.symbol}
                          </span>
                          <span className={isLong ? "badge-long" : "badge-short"}>
                            {pos.side}
                          </span>
                        </div>
                        <p className="text-xs text-muted-foreground mt-0.5 font-mono">
                          Entry: ${pos.entry_price.toFixed(2)} &bull; Mark: ${pos.mark_price.toFixed(2)}
                        </p>
                      </div>
                    </div>

                    <div className="text-right">
                      <p className={cn("font-bold text-sm font-mono", isPositive ? "text-emerald-600 dark:text-emerald-400" : "text-rose-600 dark:text-rose-400")}>
                        {isPositive ? `+$${pos.unrealized_pnl.toFixed(2)}` : `-$${Math.abs(pos.unrealized_pnl).toFixed(2)}`}
                      </p>
                      <p className="text-[11px] text-muted-foreground font-mono">
                        Size: {Math.abs(pos.amount)}
                      </p>
                    </div>
                  </div>
                )
              })}
            </div>
          ) : (
            /* Fallback Replicated Mock Cards matching Monetra reference */
            <div className="space-y-3">
              <div className="flex items-center justify-between p-3.5 rounded-2xl bg-background border border-border/60">
                <div className="flex items-center gap-3">
                  <div className="monetra-icon-tile monetra-icon-green">
                    <TrendingUp className="h-5 w-5" />
                  </div>
                  <div>
                    <h5 className="font-bold text-sm text-foreground">BTC/USDT Perpetual</h5>
                    <p className="text-xs text-muted-foreground">Demo Futures &bull; 10x Leverage</p>
                  </div>
                </div>
                <div className="text-right">
                  <p className="font-bold text-sm text-emerald-600 dark:text-emerald-400 font-mono">+$18.40</p>
                  <p className="text-[11px] text-muted-foreground font-medium">Unrealized MTM</p>
                </div>
              </div>

              <div className="flex items-center justify-between p-3.5 rounded-2xl bg-background border border-border/60">
                <div className="flex items-center gap-3">
                  <div className="monetra-icon-tile monetra-icon-blue">
                    <Coins className="h-5 w-5" />
                  </div>
                  <div>
                    <h5 className="font-bold text-sm text-foreground">ETH/USDT Spot</h5>
                    <p className="text-xs text-muted-foreground">Cash Balance Demo</p>
                  </div>
                </div>
                <div className="text-right">
                  <p className="font-bold text-sm text-foreground font-mono">$100.00</p>
                  <p className="text-[11px] text-muted-foreground font-medium">Spot Wallet</p>
                </div>
              </div>
            </div>
          )}
        </div>

        {/* Intraday Chart Component */}
        <div className="mt-8 pt-6 border-t border-border/60">
          <DashboardPnLChart />
        </div>
      </div>
    </div>
  )
}
