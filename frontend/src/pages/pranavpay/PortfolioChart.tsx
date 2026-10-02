import { useMemo } from 'react'
import { EmptyNote } from './components'
import {
  assetName,
  type EquityPoint,
  equityDomain,
  equityToPoints,
  money,
  percent,
  signedMoney,
} from './derive'
import { toPath } from './useCandles'

export type RangeKey = '1D' | '1W' | '1M' | '3M'

const RANGE_MS: Record<RangeKey, number> = {
  '1D': 24 * 60 * 60 * 1000,
  '1W': 7 * 24 * 60 * 60 * 1000,
  '1M': 30 * 24 * 60 * 60 * 1000,
  '3M': 90 * 24 * 60 * 60 * 1000,
}
const RANGE_CAPTION: Record<RangeKey, string> = {
  '1D': 'Past day',
  '1W': 'Past week',
  '1M': 'Past month',
  '3M': 'Past quarter',
}

/**
 * The virtual account's own value over time, not a market's.
 *
 * This plots the ledger the paper engine stored, one point per cycle, plus the
 * live figure at the head so the end of the line follows the current marks.
 * Every point on it was measured by the engine: nothing here is interpolated
 * from fills or back-filled from a balance, which is what the previous
 * reconstruction did.
 */
export function PortfolioChart({
  series,
  equity,
  startingCash,
  range,
  onRangeChange,
  height = 250,
}: {
  series: EquityPoint[]
  equity: number | null
  startingCash: number | null
  range: RangeKey
  onRangeChange: (range: RangeKey) => void
  height?: number
}) {
  const windowed = useMemo(() => {
    if (series.length === 0) return []
    const cutoff = Date.now() - RANGE_MS[range]
    const inside = series.filter((point) => point.t >= cutoff)
    // A range with nothing in it is not an empty chart: keep the most recent
    // points so the account still has a line, rather than blanking the panel.
    return inside.length >= 2 ? inside : series.slice(-2)
  }, [series, range])

  const domain = useMemo(
    () => equityDomain(windowed, equity ?? startingCash),
    [windowed, equity, startingCash]
  )
  const points = useMemo(() => (domain ? equityToPoints(windowed, domain) : []), [windowed, domain])
  const path = useMemo(() => toPath(points), [points])
  /**
   * No fill when there is nothing to fill. A shaded block under a flat line
   * reads as magnitude that is not there - on a quiet account it looked like a
   * large position. The line alone carries the story, and the caption says the
   * movement is below the scale.
   */
  const areaPath =
    points.length && domain && !domain.effectivelyFlat ? `${path} L 100 100 L 0 100 Z` : ''

  const change = useMemo(() => {
    if (windowed.length < 2) return null
    const first = windowed[0].equity
    const last = windowed[windowed.length - 1].equity
    if (first === 0) return null
    const delta = last - first
    return { absolute: delta, percent: (delta / first) * 100, from: first, to: last }
  }, [windowed])

  const labels = useMemo(() => timeLabels(windowed), [windowed])
  const stored = series.filter((point) => !point.live).length

  return (
    <div className="pp-chart">
      <div className="section-heading">
        <div>
          <span className="card-label">Virtual account</span>
          <h2>
            Your equity <span className="heading-count">{RANGE_CAPTION[range]}</span>
          </h2>
        </div>
        <div className="range-switcher">
          {(['1D', '1W', '1M', '3M'] as RangeKey[]).map((option) => (
            <button
              key={option}
              type="button"
              className={`range-button${range === option ? ' active' : ''}`}
              aria-pressed={range === option}
              onClick={() => onRangeChange(option)}
            >
              {option}
            </button>
          ))}
        </div>
      </div>

      <div className="chart-summary">
        {equity === null ? (
          <span className="chart-value">Reading your account</span>
        ) : change ? (
          <>
            <span className="chart-value">{signedMoney(change.absolute)}</span>
            <span className="chart-caption">
              {percent(change.percent)} on {money(change.from)} ·{' '}
              {RANGE_CAPTION[range].toLowerCase()}
            </span>
          </>
        ) : (
          <>
            <span className="chart-value">{money(equity)}</span>
            <span className="chart-caption">Total equity</span>
          </>
        )}
      </div>

      {points.length < 2 ? (
        <EmptyNote
          title="Not enough history yet"
          detail="The paper ledger stores one equity point per worker cycle, so the line begins once the worker has run a few times."
        />
      ) : (
        <div className="chart-wrap" style={{ height }}>
          <svg
            className="performance-chart"
            viewBox="0 0 100 100"
            preserveAspectRatio="none"
            role="img"
            aria-label={`Virtual account equity over the ${RANGE_CAPTION[range].toLowerCase()}`}
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

      {/* Say plainly how the line was produced. A curve that silently
          interpolated would be indistinguishable from a broker statement. */}
      <p className="pp-chart-foot">
        {equity === null
          ? 'Waiting for the paper ledger.'
          : domain?.effectivelyFlat
            ? `Total equity ${money(equity)}, from ${startingCash === null ? 'an unreported' : money(startingCash)} of starting capital. Drawn from ${stored} stored point${stored === 1 ? '' : 's'} and the live figure; the movement is too small for this scale, so the line reads flat because it is.`
            : `Total equity ${money(equity)}. Drawn from ${stored} stored point${stored === 1 ? '' : 's'} and the live figure — the flat stretches are cycles where the worker placed nothing.`}
      </p>
    </div>
  )
}

function timeLabels(series: EquityPoint[]): string[] {
  if (series.length < 2) return []
  const count = Math.min(4, series.length)
  const step = Math.floor((series.length - 1) / (count - 1))
  const picked: string[] = []
  for (let i = 0; i < count; i += 1) {
    const point = series[i * step]
    const date = new Date(point.t)
    picked.push(
      date.toLocaleDateString('en-US', { month: 'short', day: 'numeric' }) +
        (date.getHours()
          ? ` ${date.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' })}`
          : '')
    )
  }
  return picked
}

/** Real market tiles for the assets actually held, kept beside the chart. */
export { assetName }
