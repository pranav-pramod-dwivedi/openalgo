import { ArrowUpRight, BarChart3, RefreshCw, TrendingDown, TrendingUp } from 'lucide-react'
import { useCallback, useEffect, useRef, useState } from 'react'
import { Link } from 'react-router'
import {
  BaselineSeries,
  ColorType,
  CrosshairMode,
  createChart,
  type IChartApi,
  type ISeriesApi,
} from 'lightweight-charts'
import { fetchCSRFToken } from '@/api/client'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { cn } from '@/lib/utils'
import { useAuthStore } from '@/stores/authStore'
import { useThemeStore } from '@/stores/themeStore'

interface PnLDataPoint {
  time: number
  value: number
}

interface PnLData {
  current_mtm: number
  max_mtm: number
  max_mtm_time: string
  min_mtm: number
  min_mtm_time: string
  max_drawdown: number
  pnl_series: PnLDataPoint[]
  drawdown_series: PnLDataPoint[]
}

const COLOR_PROFIT = '#22c55e' // green-500
const COLOR_LOSS = '#ef4444' // red-500

function formatValue(num: number, isUsd: boolean): string {
  const sym = isUsd ? '$' : '₹'
  const isNeg = num < 0
  const abs = Math.abs(num).toFixed(2)
  return isNeg ? `-${sym}${abs}` : `+${sym}${abs}`
}

export default function DashboardPnLChart() {
  const { mode } = useThemeStore()
  const isDarkMode = mode === 'dark'
  const { user } = useAuthStore()
  const uname = (user?.username || '').toLowerCase()
  const isUsd = uname.includes('usd') || uname.includes('binance') || user?.broker === 'binance_demo'

  const [isLoading, setIsLoading] = useState(false)
  const [hasData, setHasData] = useState(false)
  const [metrics, setMetrics] = useState({
    currentMtm: 0,
    maxMtm: 0,
    minMtm: 0,
    maxDrawdown: 0,
  })

  const chartContainerRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<IChartApi | null>(null)
  const pnlSeriesRef = useRef<ISeriesApi<'Baseline'> | null>(null)

  const initChart = useCallback(() => {
    if (!chartContainerRef.current) return

    if (chartRef.current) {
      chartRef.current.remove()
      chartRef.current = null
    }

    const container = chartContainerRef.current
    const chart = createChart(container, {
      width: container.offsetWidth,
      height: 240,
      layout: {
        background: { type: ColorType.Solid, color: 'transparent' },
        textColor: isDarkMode ? '#a6adbb' : '#64748b',
      },
      grid: {
        vertLines: {
          color: isDarkMode ? 'rgba(255, 255, 255, 0.05)' : 'rgba(0, 0, 0, 0.05)',
          style: 1,
        },
        horzLines: {
          color: isDarkMode ? 'rgba(255, 255, 255, 0.05)' : 'rgba(0, 0, 0, 0.05)',
          style: 1,
        },
      },
      rightPriceScale: {
        borderColor: isDarkMode ? 'rgba(255, 255, 255, 0.1)' : 'rgba(0, 0, 0, 0.1)',
        scaleMargins: { top: 0.15, bottom: 0.15 },
      },
      timeScale: {
        borderColor: isDarkMode ? 'rgba(255, 255, 255, 0.1)' : 'rgba(0, 0, 0, 0.1)',
        timeVisible: true,
        secondsVisible: false,
        tickMarkFormatter: (time: number) => {
          const date = new Date(time * 1000)
          const istOffset = 5.5 * 60 * 60 * 1000
          const istDate = new Date(date.getTime() + istOffset)
          const hours = istDate.getUTCHours().toString().padStart(2, '0')
          const minutes = istDate.getUTCMinutes().toString().padStart(2, '0')
          return `${hours}:${minutes}`
        },
      },
      crosshair: {
        mode: CrosshairMode.Normal,
      },
    })

    const pnlSeries = chart.addSeries(BaselineSeries, {
      baseValue: { type: 'price', price: 0 },
      topLineColor: COLOR_PROFIT,
      topFillColor1: 'rgba(34, 197, 94, 0.28)',
      topFillColor2: 'rgba(34, 197, 94, 0.02)',
      bottomLineColor: COLOR_LOSS,
      bottomFillColor1: 'rgba(239, 68, 68, 0.02)',
      bottomFillColor2: 'rgba(239, 68, 68, 0.28)',
      lineWidth: 2,
      priceFormat: {
        type: 'custom',
        formatter: (price: number) => {
          const sym = isUsd ? '$' : '₹'
          return `${price >= 0 ? '+' : ''}${sym}${price.toFixed(2)}`
        },
      },
    })

    chartRef.current = chart
    pnlSeriesRef.current = pnlSeries

    const handleResize = () => {
      if (container && chart) {
        chart.applyOptions({ width: container.offsetWidth })
      }
    }

    window.addEventListener('resize', handleResize)
    return () => {
      window.removeEventListener('resize', handleResize)
    }
  }, [isDarkMode, isUsd])

  const loadData = useCallback(async () => {
    setIsLoading(true)
    try {
      const csrfToken = await fetchCSRFToken()
      const response = await fetch('/pnltracker/api/pnl', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-CSRFToken': csrfToken,
        },
        credentials: 'include',
      })

      if (!response.ok) throw new Error('Failed to fetch PnL')
      const result = await response.json()

      if (result.status === 'success' && result.data) {
        const data: PnLData = result.data
        setMetrics({
          currentMtm: data.current_mtm,
          maxMtm: data.max_mtm,
          minMtm: data.min_mtm,
          maxDrawdown: data.max_drawdown,
        })

        const hasNonZero = (data.pnl_series || []).some((pt) => pt.value !== 0)
        setHasData(hasNonZero || data.current_mtm !== 0)

        if (pnlSeriesRef.current && data.pnl_series && Array.isArray(data.pnl_series)) {
          const points = data.pnl_series
            .map((p) => ({
              time: Math.floor(p.time / 1000) as import('lightweight-charts').UTCTimestamp,
              value: p.value,
            }))
            .sort((a, b) => a.time - b.time)

          if (points.length > 0) {
            pnlSeriesRef.current.setData(points)
            chartRef.current?.timeScale().fitContent()
          }
        }
      }
    } catch (_e) {
      // Ignored
    } finally {
      setIsLoading(false)
    }
  }, [])

  useEffect(() => {
    initChart()
    loadData()

    return () => {
      if (chartRef.current) {
        chartRef.current.remove()
        chartRef.current = null
      }
    }
  }, [initChart, loadData])

  const pnlIsPositive = metrics.currentMtm >= 0

  return (
    <Card className="overflow-hidden border shadow-sm">
      <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-3">
        <div className="flex items-center gap-3">
          <div
            className={cn(
              'p-2 rounded-lg',
              pnlIsPositive
                ? 'bg-green-500/10 text-green-600 dark:text-green-400'
                : 'bg-red-500/10 text-red-600 dark:text-red-400'
            )}
          >
            {pnlIsPositive ? (
              <TrendingUp className="h-5 w-5" />
            ) : (
              <TrendingDown className="h-5 w-5" />
            )}
          </div>
          <div>
            <CardTitle className="text-base md:text-lg flex items-center gap-2">
              Intraday P&L Curve
              <Badge variant={pnlIsPositive ? 'default' : 'destructive'} className="font-mono text-xs">
                {formatValue(metrics.currentMtm, isUsd)}
              </Badge>
            </CardTitle>
            <p className="text-xs text-muted-foreground mt-0.5">
              Live mark-to-market performance for {isUsd ? 'Forex & Crypto (USD)' : 'Indian Markets (INR)'}
            </p>
          </div>
        </div>

        <div className="flex items-center gap-2">
          <Button
            variant="ghost"
            size="icon"
            onClick={loadData}
            disabled={isLoading}
            className="h-8 w-8 text-muted-foreground hover:text-foreground"
            title="Refresh P&L Curve"
          >
            <RefreshCw className={cn('h-4 w-4', isLoading && 'animate-spin')} />
          </Button>

          <Button asChild variant="outline" size="sm" className="h-8 gap-1 text-xs">
            <Link to="/pnl-tracker">
              Full Tracker
              <ArrowUpRight className="h-3.5 w-3.5" />
            </Link>
          </Button>
        </div>
      </CardHeader>

      <CardContent className="pt-2">
        {/* Quick Metrics Bar */}
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 mb-3 p-2 bg-muted/30 rounded-lg border text-xs">
          <div>
            <span className="text-muted-foreground">Current MTM: </span>
            <span
              className={cn(
                'font-mono font-semibold',
                metrics.currentMtm > 0
                  ? 'text-green-600 dark:text-green-400'
                  : metrics.currentMtm < 0
                    ? 'text-red-600 dark:text-red-400'
                    : ''
              )}
            >
              {formatValue(metrics.currentMtm, isUsd)}
            </span>
          </div>

          <div>
            <span className="text-muted-foreground">Day High: </span>
            <span className="font-mono font-semibold text-green-600 dark:text-green-400">
              {formatValue(metrics.maxMtm, isUsd)}
            </span>
          </div>

          <div>
            <span className="text-muted-foreground">Day Low: </span>
            <span className="font-mono font-semibold text-red-600 dark:text-red-400">
              {formatValue(metrics.minMtm, isUsd)}
            </span>
          </div>

          <div>
            <span className="text-muted-foreground">Max Drawdown: </span>
            <span className="font-mono font-semibold text-amber-600 dark:text-amber-400">
              {formatValue(metrics.maxDrawdown, isUsd)}
            </span>
          </div>
        </div>

        {/* Chart Canvas */}
        <div className="relative w-full">
          <div ref={chartContainerRef} className="w-full h-[240px]" />

          {!hasData && (
            <div className="absolute inset-0 flex flex-col items-center justify-center bg-background/60 backdrop-blur-[1px] rounded-lg pointer-events-none">
              <BarChart3 className="h-8 w-8 text-muted-foreground/40 mb-2" />
              <p className="text-sm font-medium text-muted-foreground">No trades executed today</p>
              <p className="text-xs text-muted-foreground/70">
                Execute a trade or scalp to stream your intraday P&L curve in real time.
              </p>
            </div>
          )}
        </div>
      </CardContent>
    </Card>
  )
}
