/**
 * DataFreshness — inline indicator showing when data was last refreshed,
 * whether the connection is live, and how stale the data is.
 *
 * Designed for trading pages (OrderBook, TradeBook, Positions, Dashboard)
 * where traders need to trust that what's on screen reflects reality.
 */

import { AlertTriangle, Clock, Wifi, WifiOff } from 'lucide-react'
import { useEffect, useState } from 'react'
import { Badge } from '@/components/ui/badge'
import { cn } from '@/lib/utils'

interface DataFreshnessProps {
  /** ISO timestamp or Date of last successful fetch */
  lastUpdated: string | Date | null
  /** Whether a refresh is currently in progress */
  isRefreshing?: boolean
  /** Whether the WebSocket (or SocketIO) connection is active */
  isConnected?: boolean
  /** Threshold in ms after which data is considered "stale" (default 60s) */
  staleThreshold?: number
  /** Additional CSS classes */
  className?: string
}

function timeAgo(date: Date): string {
  const seconds = Math.floor((Date.now() - date.getTime()) / 1000)
  if (seconds < 5) return 'just now'
  if (seconds < 60) return `${seconds}s ago`
  const minutes = Math.floor(seconds / 60)
  if (minutes < 60) return `${minutes}m ago`
  const hours = Math.floor(minutes / 60)
  return `${hours}h ago`
}

export function DataFreshness({
  lastUpdated,
  isRefreshing = false,
  isConnected,
  staleThreshold = 60_000,
  className,
}: DataFreshnessProps) {
  const [, setTick] = useState(0)

  // Re-render every 10s to keep the "Xs ago" label accurate
  useEffect(() => {
    const id = setInterval(() => setTick((n) => n + 1), 10_000)
    return () => clearInterval(id)
  }, [])

  const date = lastUpdated ? new Date(lastUpdated) : null
  const isStale = date ? Date.now() - date.getTime() > staleThreshold : true

  return (
    <div className={cn('flex items-center gap-2 text-xs text-muted-foreground', className)}>
      {/* Connection indicator */}
      {isConnected !== undefined && (
        <Badge
          variant="outline"
          className={cn(
            'gap-1 text-[10px] px-1.5 py-0',
            isConnected
              ? 'border-emerald-500/40 text-emerald-600 dark:text-emerald-400'
              : 'border-red-500/40 text-red-600 dark:text-red-400'
          )}
        >
          {isConnected ? <Wifi className="h-2.5 w-2.5" /> : <WifiOff className="h-2.5 w-2.5" />}
          {isConnected ? 'Live' : 'Offline'}
        </Badge>
      )}

      {/* Live pulse when data is fresh */}
      {!isStale && !isRefreshing && (
        <span className="relative flex h-2 w-2">
          <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75" />
          <span className="relative inline-flex rounded-full h-2 w-2 bg-emerald-500" />
        </span>
      )}

      {/* Stale warning */}
      {isStale && date && <AlertTriangle className="h-3 w-3 text-amber-500" />}

      {/* Last updated text */}
      {date ? (
        <span className={cn(isStale && 'text-amber-600 dark:text-amber-400')}>
          {isRefreshing ? 'Refreshing…' : `Updated ${timeAgo(date)}`}
        </span>
      ) : (
        <span className="flex items-center gap-1">
          <Clock className="h-3 w-3" />
          {isRefreshing ? 'Loading…' : 'No data yet'}
        </span>
      )}
    </div>
  )
}
