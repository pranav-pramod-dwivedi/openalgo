import { useMemo, useState } from 'react'
import { type Candle, type RangeKey, toPath, useCandles } from './useCandles'
import { EmptyNote } from './components'
import { baseAsset, money, percent, signedMoney } from './derive'

const RANGE_LABELS: RangeKey[] = ['1D', '1W', '1M', '3M']

/**
 * A real price line for one market.
 *
 * The prototype drew five fixed SVG paths with invented values and captions
 * like "since Sep 02, 2026". This plots the exchange's own candles for the
 * chosen range and states the real change over that window.
 */
export function PriceChart({
  symbol,
  defaultRange = '1M',
  height = 250,
}: {
  symbol: string
  defaultRange?: RangeKey
  height?: number
}) {
  const [range, setRange] = useState<RangeKey>(defaultRange)
  const { points, change, loading, error, caption, candles } = useCandles(symbol, range)
  const path = useMemo(() => toPath(points), [points])
  const areaPath = points.length
    ? `${path} L 100 100 L 0 100 Z`
    : ''
  const labels = useMemo(() => axisLabels(candles), [candles])

  return (
    <div className="pp-chart">
      <div className="section-heading">
        <div>
          <span className="card-label">Market</span>
          <h2>
            {symbol} <span className="heading-count">{baseAsset(symbol)}</span>
          </h2>
        </div>
        <div className="range-switcher">
          {RANGE_LABELS.map((option) => (
            <button
              key={option}
              type="button"
              className={`range-button${range === option ? ' active' : ''}`}
              aria-pressed={range === option}
              onClick={() => setRange(option)}
            >
              {option}
            </button>
          ))}
        </div>
      </div>

      <div className="chart-summary">
        {loading ? (
          <span className="chart-value">Reading the market</span>
        ) : error || !change ? (
          <span className="chart-value">—</span>
        ) : (
          <>
            <span className="chart-value">{signedMoney(change.absolute)}</span>
            <span className="chart-caption">
              {percent(change.percent)} · {caption}
            </span>
          </>
        )}
      </div>

      {error ? (
        <EmptyNote
          title="No candles for that range"
          detail="The market feed did not return data, so nothing is drawn rather than a made-up line."
        />
      ) : (
        <div className="chart-wrap" style={{ height }}>
          <svg
            className="performance-chart"
            viewBox="0 0 100 100"
            preserveAspectRatio="none"
            role="img"
            aria-label={`${symbol} price over the ${caption.toLowerCase()}`}
          >
            <path className="chart-grid-line" d="M0 0H100M0 25H100M0 50H100M0 75H100M0 100H100" />
            {areaPath ? <path className="chart-area" d={areaPath} /> : null}
            {path ? <path className="chart-line" d={path} /> : null}
            {points.length ? (
              <circle
                className="chart-point"
                cx={points[points.length - 1].x}
                cy={points[points.length - 1].y}
                r="0.7"
              />
            ) : null}
          </svg>
          {labels.length ? (
            <div className="x-axis">
              {labels.map((label) => (
                <span key={label}>{label}</span>
              ))}
            </div>
          ) : null}
        </div>
      )}
    </div>
  )
}

/** Three or five real timestamps along the bottom of the chart. */
function axisLabels(candles: Candle[]): string[] {
  if (candles.length < 2) return []
  const count = Math.min(4, candles.length)
  const step = Math.floor((candles.length - 1) / (count - 1))
  const picked: string[] = []
  for (let i = 0; i < count; i += 1) {
    const candle = candles[i * step]
    const date = new Date(candle.timestamp * 1000)
    picked.push(
      date.toLocaleDateString('en-US', { month: 'short', day: 'numeric' })
    )
  }
  return picked
}

/** A compact price tile for a grid of markets. */
export function PriceTile({ symbol, label }: { symbol: string; label: string }) {
  const { candles, change, loading, error } = useCandles(symbol, '1D')
  const closes = candles.map((candle) => candle.close)
  const path = useMemo(() => toPath(
    closes.map((close) => ({ x: 0, y: 0, timestamp: 0, open: close, high: close, low: close, close, volume: 0 }))
  ), [closes])

  return (
    <div className="pp-tile">
      <div className="pp-tile-head">
        <span className="card-label">{label}</span>
        <strong>{symbol}</strong>
      </div>
      {error ? (
        <span className="pp-tile-value">—</span>
      ) : (
        <>
          <span className="pp-tile-value">
            {loading || !closes.length ? '—' : money(closes[closes.length - 1])}
          </span>
          <span className="pp-tile-change">
            {change ? percent(change.percent) : '—'}
          </span>
        </>
      )}
      {path ? (
        <svg className="pp-spark" viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden="true">
          <path d={path} vectorEffect="non-scaling-stroke" />
        </svg>
      ) : null}
    </div>
  )
}
