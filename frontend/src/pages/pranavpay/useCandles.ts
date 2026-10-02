import { useEffect, useMemo, useState } from 'react'

export interface Candle {
  timestamp: number
  open: number
  high: number
  low: number
  close: number
  volume: number
}

export type RangeKey = '1D' | '1W' | '1M' | '3M'

const RANGES: Record<RangeKey, { interval: string; limit: number; label: string }> = {
  '1D': { interval: '15m', limit: 96, label: 'Today' },
  '1W': { interval: '1h', limit: 168, label: 'Past week' },
  '1M': { interval: '4h', limit: 180, label: 'Past month' },
  '3M': { interval: '1d', limit: 90, label: 'Past quarter' },
}

export interface SeriesPoint {
  x: number
  y: number
}

/**
 * Real candles for one crypto market, read from the exchange.
 *
 * The prototype shipped five hardcoded SVG paths with invented values. This
 * fetches the actual series and returns points on a 0-100 vertical scale, so
 * the line is the market's and not the design's. An unreachable or empty feed
 * reports `error` and the caller renders an empty state rather than a curve.
 */
export function useCandles(symbol: string, range: RangeKey) {
  const [candles, setCandles] = useState<Candle[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    const config = RANGES[range]
    let cancelled = false
    setLoading(true)
    setError(null)

    fetch(
      `/historify/api/candles?symbol=${encodeURIComponent(symbol)}&interval=${config.interval}&limit=${config.limit}`,
      { credentials: 'include' }
    )
      .then(async (response) => {
        if (!response.ok) {
          const body = await response.json().catch(() => null)
          throw new Error(body?.message ?? 'The exchange returned no candles')
        }
        return response.json()
      })
      .then((body) => {
        if (cancelled) return
        const data = (body?.data ?? []) as Candle[]
        if (data.length < 2) throw new Error('Not enough candles to draw a line')
        setCandles(data)
        setLoading(false)
      })
      .catch((cause: Error) => {
        if (cancelled) return
        setCandles([])
        setError(cause.message)
        setLoading(false)
      })

    return () => {
      cancelled = true
    }
  }, [symbol, range])

  const points = useMemo(() => toPoints(candles), [candles])
  const change = useMemo(() => {
    if (candles.length < 2) return null
    const first = candles[0].close
    const last = candles[candles.length - 1].close
    if (!first) return null
    const delta = last - first
    return { absolute: delta, percent: (delta / first) * 100 }
  }, [candles])

  return { candles, points, change, loading, error, caption: RANGES[range].label }
}

/** Map a candle series onto the chart's 0-100 box, x across the width. */
function toPoints(candles: Candle[]): SeriesPoint[] {
  if (candles.length < 2) return []
  const closes = candles.map((candle) => candle.close)
  const min = Math.min(...closes)
  const max = Math.max(...closes)
  const span = max - min
  const lastIndex = candles.length - 1

  return closes.map((close, index) => ({
    x: (index / lastIndex) * 100,
    // A flat series would divide by zero; park it mid-height instead.
    y: span === 0 ? 50 : 100 - ((close - min) / span) * 100,
  }))
}

/**
 * A straight polyline through the points.
 *
 * The prototype smoothed its line with Bezier curves, which is a reasonable
 * flourish for a decorative squiggle but a distortion for a price series: on a
 * 3-4% single-interval jump the control points overshoot and the curve loops
 * past the real high, drawing a high the market never printed. A price line
 * should pass through the prices and nothing else.
 */
export function toPath(points: SeriesPoint[]): string {
  if (points.length < 2) return ''
  return points
    .map((point, index) => `${index === 0 ? 'M' : 'L'} ${point.x.toFixed(2)} ${point.y.toFixed(2)}`)
    .join(' ')
}

/** A short, dense sparkline for a card footer. */
export function sparkPath(closes: number[]): string {
  return toPath(
    toPoints(
      closes.map((close) => ({
        timestamp: 0,
        open: close,
        high: close,
        low: close,
        close,
        volume: 0,
      }))
    )
  )
}

export { RANGES }
