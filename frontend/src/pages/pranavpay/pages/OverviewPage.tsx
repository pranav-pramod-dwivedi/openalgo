import { useMemo } from 'react'
import { Link } from 'react-router'
import { PriceChart, PriceTile } from '../PriceChart'
import {
  alerts,
  allocation,
  assetName,
  baseAsset,
  dailyRealised,
  derive,
  greetingFor,
  introFor,
  money,
  OWNER,
  percent,
  qty,
  relativeTime,
  signedMoney,
  toFillRows,
  useNow,
} from '../derive'
import { CapitalSplit, EmptyNote, LoadingNote, StaleNote, UpdatedAt, useSnapshot } from '../components'
import { toAmounts } from '../useWalletSnapshot'
import { liveEquity, liveUnrealised, useLiveMarks, useWatchedMarkets } from '../useLiveMarks'

/** The market the headline chart follows: the largest open position. */
function chartSymbol(symbols: string[]): string {
  return symbols[0] ?? 'BTCUSDT'
}

export default function OverviewPage() {
  const snapshot = useSnapshot()
  const now = useNow()
  const amounts = toAmounts(snapshot.funds)
  const fills = useMemo(() => toFillRows(snapshot.trades), [snapshot.trades])
  const health = useMemo(() => derive(snapshot), [snapshot])
  const slices = useMemo(() => allocation(snapshot.funds, snapshot.positions), [snapshot])
  const notices = useMemo(() => alerts(snapshot), [snapshot])
  const realisedByDay = useMemo(() => dailyRealised(fills), [fills])
  const { prices, isLive, isStreaming } = useLiveMarks(snapshot)
  const watched = useWatchedMarkets(snapshot)

  const focus = chartSymbol(
    useMemo(() => {
      const held = [...new Set(snapshot.positions.map((position) => position.symbol))]
      const traded = fills.map((fill) => fill.symbol)
      return [...held, ...traded, 'BTCUSDT']
    }, [snapshot.positions, fills])
  )

  const marketSymbols = useMemo(
    () => [...new Set([...snapshot.positions.map((p) => p.symbol), ...watched])].slice(0, 3),
    [snapshot.positions, watched]
  )

  const liveTotal = liveEquity(snapshot, prices)
  const liveOpenPnl = liveUnrealised(snapshot.positions, prices)

  if (snapshot.loading && !snapshot.funds) {
    return (
      <div className="content-wrap page-view is-visible" id="quickOverviewPage">
        <LoadingNote label="Reading your Binance account" />
      </div>
    )
  }

  const totalBalance = liveTotal ?? amounts?.equity ?? null
  const openPnl = liveOpenPnl ?? health.totalUnrealised
  const dayChange = health.totalRealised + openPnl

  return (
    <div className="content-wrap page-view is-visible" id="quickOverviewPage" data-page="Quick overview">
      <section className="page-intro">
        <div>
          <p className="eyebrow">
            <span>
              {now.toLocaleDateString('en-US', {
                weekday: 'long',
                month: 'long',
                day: 'numeric',
                year: 'numeric',
              })}
            </span>
            <span className="eyebrow-line" />
            <span>{now.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' })}</span>
          </p>
          <h1>
            <span>
              {greetingFor(now)}, {OWNER.firstName}
            </span>
            <span className="period">.</span>
          </h1>
          <p className="intro-copy">{introFor(now, snapshot.positions.length > 0)}</p>
        </div>
        <button className="ghost-button" type="button" onClick={() => downloadReport(snapshot)}>
          <span>Export report</span>
          <svg viewBox="0 0 24 24" aria-hidden="true">
            <path d="M12 4v11M8 11l4 4 4-4M5 19h14" />
          </svg>
        </button>
      </section>

      {snapshot.stale && <StaleNote />}

      <section className="control-strip" aria-label="Quick actions">
        <div className="control-strip-intro">
          <span className="card-label">Control center</span>
          <strong>
            {snapshot.positions.length === 0
              ? 'No open positions. Your capital is untouched.'
              : `${snapshot.positions.length} open position${snapshot.positions.length === 1 ? '' : 's'}, marked to market${isLive ? ' live' : ''}.`}
          </strong>
        </div>
        <div className="control-actions">
          <Link className="control-button" to="/manage">
            <span className="control-dot" /> Guardrails
          </Link>
          <Link className="control-button" to="/transactions">
            <span>↗</span> Activity
          </Link>
          <a className="control-button command-button" href="/api/v1/orderbook" target="_blank" rel="noreferrer">
            <span>⌘</span> Raw API
          </a>
        </div>
      </section>

      <section className="overview-grid" aria-label="Account overview">
        <article className="balance-card surface-card">
          <div className="card-topline">
            <span className="card-label">Total balance</span>
            {isLive ? <span className="live-label"><span className="live-dot" /> Live</span> : null}
          </div>
          <div className="balance-value">
            {totalBalance === null ? '—' : money(totalBalance).replace(/\.\d+$/, '')}
          </div>
          <div className="balance-change">
            <span className="change-symbol">{dayChange > 0 ? '↗' : dayChange < 0 ? '↘' : '→'}</span>
            <strong>{signedMoney(dayChange)}</strong>
            <span>realised plus unrealised</span>
            <span className="change-period">All time</span>
          </div>
          <div className="balance-footer">
            <UpdatedAt at={snapshot.updatedAt} />
            <span className="mini-chart" aria-hidden="true">
              {(marketSymbols.length ? marketSymbols : ['BTCUSDT']).slice(0, 7).map((symbol) => (
                <i key={symbol} style={{ height: `${20 + ((symbol.charCodeAt(0) % 7) * 9)}%` }} />
              ))}
            </span>
          </div>
        </article>

        <article className="metric-card surface-card">
          <div className="card-label">Trading power</div>
          <div className="metric-value">{amounts ? money(amounts.tradable) : '—'}</div>
          <div className="metric-foot">
            <span>Available for new orders</span>
            <span className="metric-glyph">↗</span>
          </div>
        </article>

        <article className="metric-card surface-card">
          <div className="card-label">Protected</div>
          <div className="metric-value">{amounts ? money(amounts.savings) : '—'}</div>
          <div className="metric-foot">
            <span>Savings floor, untouchable</span>
            <span className="metric-glyph">→</span>
          </div>
        </article>

        <article className="metric-card surface-card risk-card">
          <div className="card-label">Exposure</div>
          <div className="metric-value risk-value">
            <span className="risk-ring" />
            {health.exposureShare > 0 ? percent(health.exposureShare) : 'Flat'}
          </div>
          <div className="metric-foot">
            <span>{health.exposureShare > 0 ? 'Of equity is in open positions' : 'No capital at risk'}</span>
            <span className="metric-glyph">→</span>
          </div>
        </article>
      </section>

      <section className="primary-grid">
        <article className="chart-card surface-card">
          <PriceChart symbol={focus} />
        </article>

        <article className="ai-card">
          <div className="ai-card-header">
            <div>
              <span className="card-label inverse-label">Autopilot</span>
              <h2>AI trading</h2>
            </div>
            <a className="more-button inverse-more" href="/agent" aria-label="Open the agent in OpenAlgo">
              <span />
              <span />
              <span />
            </a>
          </div>
          <div className={`ai-orbit${isLive ? ' active' : ''}`} aria-hidden="true">
            <div className="orbit-ring ring-one" />
            <div className="orbit-ring ring-two" />
            <div className="orbit-core">
              <span className="core-glyph">P</span>
            </div>
            {isLive ? <div className="orbit-satellite" /> : null}
          </div>
          <div className="ai-status">
            <span className="ai-status-dot" />
            <span>Not connected</span>
          </div>
          <p className="ai-description">
            The agent is not running against this account. It is being wired up; until then it
            places nothing.
          </p>
          <div className="ai-stats">
            <div>
              <span>Last action</span>
              <strong>{fills.length > 0 ? `${fills.length} fills on record` : 'None'}</strong>
              <small>{fills.length > 0 ? 'Placed manually, not by the agent' : 'No orders yet'}</small>
            </div>
            <div>
              <span>Next review</span>
              <strong>Not scheduled</strong>
              <small>Agent is off</small>
            </div>
          </div>
          <div className="ai-actions">
            <button className="inverse-button" type="button" disabled title="AI trading arrives in the next stage">
              <span className="pause-bars" />
              <span>Pause AI</span>
            </button>
            <Link className="outline-inverse-button" to="/manage">
              Manage
            </Link>
          </div>
        </article>
      </section>

      <section className="lower-grid">
        <article className="positions-card surface-card">
          <div className="section-heading compact-heading">
            <div>
              <span className="card-label">Your assets</span>
              <h2>
                Open positions <span className="heading-count">{snapshot.positions.length}</span>
              </h2>
            </div>
            <Link className="text-button" to="/transactions">
              Activity <span>→</span>
            </Link>
          </div>
          {snapshot.positions.length === 0 ? (
            <EmptyNote
              title="No open positions"
              detail="Nothing is exposed to the market right now. Fills you make will appear here."
            />
          ) : (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Asset</th>
                    <th>Position</th>
                    <th>Avg. price</th>
                    <th>Current value</th>
                    <th>Return</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {snapshot.positions.map((position) => {
                    const mark = prices.get(position.symbol)
                    const live = mark ?? position.ltp ?? position.average_price
                    const value = Math.abs(position.quantity) * live
                    const pnl =
                      typeof mark === 'number'
                        ? (mark - position.average_price) * position.quantity
                        : position.pnl
                    const basis = Math.abs(position.average_price * position.quantity)
                    const pnlPercent =
                      typeof mark === 'number' && basis > 0
                        ? (pnl / basis) * 100
                        : position.pnlpercent
                    return (
                      <tr key={position.symbol}>
                        <td>
                          <AssetName symbol={position.symbol} />
                        </td>
                        <td>
                          <strong>
                            {qty(Math.abs(position.quantity))} {baseAsset(position.symbol)}
                          </strong>
                          <small className="muted-line">{position.quantity < 0 ? 'Short' : 'Long'}</small>
                        </td>
                        <td>{money(position.average_price)}</td>
                        <td>{money(value)}</td>
                        <td>
                          <strong>{percent(pnlPercent)}</strong>
                          <small className="muted-line">{signedMoney(pnl)}</small>
                        </td>
                        <td />
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          )}
        </article>

        <article className="activity-card surface-card">
          <div className="section-heading compact-heading">
            <div>
              <span className="card-label">The paper trail</span>
              <h2>Recent fills</h2>
            </div>
            <Link className="text-button" to="/transactions">
              View all <span>→</span>
            </Link>
          </div>
          {fills.length === 0 ? (
            <EmptyNote
              title="No fills yet"
              detail="Every executed order shows up here with its size, price and result."
            />
          ) : (
            <div className="activity-list">
              {fills.slice(0, 6).map((fill) => (
                <div className="activity-item" key={fill.id}>
                  <div className="activity-icon system-activity">
                    {fill.action === 'BUY' ? '↓' : '↑'}
                  </div>
                  <div className="activity-copy">
                    <strong>
                      {fill.action === 'BUY' ? 'Bought' : 'Sold'} {assetName(fill.asset)}
                    </strong>
                    <span>
                      {qty(fill.quantity)} {fill.asset} at {money(fill.price)}
                    </span>
                  </div>
                  <time>{relativeTime(fill.at)}</time>
                </div>
              ))}
            </div>
          )}
        </article>
      </section>

      <section className="feature-grid" aria-label="Guardrails, markets and status">
        <article className="feature-card surface-card decision-card">
          <div className="section-heading compact-heading">
            <div>
              <span className="card-label">Your rules</span>
              <h2>Guardrails</h2>
            </div>
            <Link className="text-button" to="/manage">
              Review <span>→</span>
            </Link>
          </div>
          <CapitalSplit />
        </article>

        <article className="feature-card surface-card signals-card">
          <div className="section-heading compact-heading">
            <div>
              <span className="card-label">What we can measure</span>
              <h2>Account state</h2>
            </div>
            {isLive ? <span className="live-label"><span className="live-dot" /> Live</span> : null}
          </div>
          <div className="signal-list">
            <div className="signal-row">
              <span>Tradable share</span>
              <strong>{amounts && amounts.equity > 0 ? `${health.cashShare.toFixed(1)}%` : '—'}</strong>
              <span className="signal-meter">
                <i style={{ width: `${Math.min(100, Math.max(0, health.cashShare))}%` }} />
              </span>
            </div>
            <div className="signal-row">
              <span>Exposure</span>
              <strong>{health.exposureShare > 0 ? percent(health.exposureShare) : 'Flat'}</strong>
              <span className="signal-meter">
                <i style={{ width: `${Math.min(100, Math.max(0, health.exposureShare))}%` }} />
              </span>
            </div>
            <div className="signal-row">
              <span>Unrealised P&amp;L</span>
              <strong>{signedMoney(openPnl)}</strong>
              <span className="signal-meter">
                <i style={{ width: `${Math.min(100, Math.abs(openPnl))}%` }} />
              </span>
            </div>
            <div className="signal-row">
              <span>Realised P&amp;L</span>
              <strong>{signedMoney(health.totalRealised)}</strong>
              <span className="signal-meter">
                <i style={{ width: `${Math.min(100, Math.abs(health.totalRealised))}%` }} />
              </span>
            </div>
          </div>
          <div className="planned-action">
            <span className="card-label">Best / worst open position</span>
            <strong>{health.best ? `${health.best.symbol} ${percent(health.best.pnlPercent)}` : 'No positions'}</strong>
            <small>
              {health.worst
                ? `Weakest is ${health.worst.symbol} at ${percent(health.worst.pnlPercent)}.`
                : 'Nothing to rank until a position is open.'}
            </small>
          </div>
        </article>

        <article className="feature-card surface-card wallet-feature-card">
          <div className="section-heading compact-heading">
            <div>
              <span className="card-label">Where the money sits</span>
              <h2>Allocation</h2>
            </div>
          </div>
          {slices.length === 0 ? (
            <EmptyNote
              title="Nothing to allocate yet"
              detail="Once the exchange reports balances, the split appears here."
            />
          ) : (
            <div className="allocation-bars">
              {slices.map((slice) => (
                <div key={slice.label}>
                  <span>
                    {slice.label} <strong>{slice.share.toFixed(1)}%</strong>
                  </span>
                  <i>
                    <b style={{ width: `${Math.min(100, Math.max(0, slice.share))}%` }} />
                  </i>
                </div>
              ))}
            </div>
          )}
          <div className="winners-row">
            <div>
              <span className="card-label">Best open</span>
              <strong>
                {health.best ? `${health.best.asset} ` : '— '}
                {health.best ? <em>{percent(health.best.pnlPercent)}</em> : null}
              </strong>
            </div>
            <div>
              <span className="card-label">Needs attention</span>
              <strong>
                {health.worst ? `${health.worst.asset} ` : '— '}
                {health.worst ? <em>{percent(health.worst.pnlPercent)}</em> : null}
              </strong>
            </div>
          </div>
        </article>

        <article className="feature-card surface-card calendar-card">
          <div className="section-heading compact-heading">
            <div>
              <span className="card-label">Performance history</span>
              <h2>P&amp;L calendar</h2>
            </div>
          </div>
          <PnlCalendar byDay={realisedByDay} />
          <div className="calendar-legend">
            <span><i className="positive-key" /> Days made money</span>
            <span><i className="negative-key" /> Days lost money</span>
          </div>
        </article>

        <article className="feature-card surface-card insights-card">
          <div className="section-heading compact-heading">
            <div>
              <span className="card-label">Realised results</span>
              <h2>P&amp;L by day</h2>
            </div>
            <Link className="text-button" to="/transactions">
              Ledger <span>→</span>
            </Link>
          </div>
          {realisedByDay.size === 0 ? (
            <EmptyNote
              title="No realised P&L yet"
              detail="Days appear here once a position has been closed at a profit or loss."
            />
          ) : (
            <div className="full-ledger">
              {[...realisedByDay.entries()]
                .sort((a, b) => b[0].localeCompare(a[0]))
                .slice(0, 7)
                .map(([day, value]) => (
                  <div className="ledger-row" key={day}>
                    <span>
                      <i className="ledger-icon">·</i>
                      <strong>
                        {new Date(`${day}T00:00:00Z`).toLocaleDateString('en-US', {
                          month: 'short',
                          day: '2-digit',
                        })}
                      </strong>
                    </span>
                    <span>Realised</span>
                    <strong>{signedMoney(value)}</strong>
                    <time />
                  </div>
                ))}
            </div>
          )}
        </article>

        <article className="feature-card surface-card alerts-feature-card">
          <div className="section-heading compact-heading">
            <div>
              <span className="card-label">Stay in the loop</span>
              <h2>Notices</h2>
            </div>
            {notices.length > 0 ? <span className="alert-count">{notices.length}</span> : null}
          </div>
          {notices.length === 0 ? (
            <EmptyNote
              title="Nothing needs you"
              detail="Notices appear when trading power runs low, figures go stale, or margin is committed."
            />
          ) : (
            <div className="alert-list">
              {notices.map((notice) => (
                <div className="alert-row" key={notice.id}>
                  <span className="alert-icon">{notice.severity === 'warning' ? '!' : 'i'}</span>
                  <span>
                    <strong>{notice.title}</strong>
                    <small>{notice.detail}</small>
                  </span>
                  <time>Now</time>
                </div>
              ))}
            </div>
          )}
        </article>

        {marketSymbols.length > 0 ? (
          <article className="feature-card surface-card markets-card">
            <div className="section-heading compact-heading">
              <div>
                <span className="card-label">What you hold</span>
                <h2>Markets</h2>
              </div>
              {isLive ? <span className="live-label"><span className="live-dot" /> Live</span> : null}
            </div>
            <div className="pp-tiles">
              {marketSymbols.map((symbol) => (
                <PriceTile key={symbol} symbol={symbol} label={baseAsset(symbol)} />
              ))}
            </div>
          </article>
        ) : null}
      </section>

      <footer className="page-footer">
        <span>
          PranavPay <span className="footer-separator">·</span> Your money, in motion.
        </span>
        <span>
          <span className="live-dot" style={{ color: isLive ? '#4ade80' : '#f59e0b' }} />
          {isStreaming
            ? 'Streaming live prices'
            : isLive
              ? 'Live prices every 10s'
              : 'Polling every 30 seconds'}
        </span>
      </footer>
    </div>
  )
}

function AssetName({ symbol }: { symbol: string }) {
  const asset = baseAsset(symbol)
  return (
    <div className="asset-cell">
      <span className="asset-icon">{assetName(asset).slice(0, 1)}</span>
      <span>
        <strong>{assetName(asset)}</strong>
        <small>{symbol}</small>
      </span>
    </div>
  )
}

/**
 * The trailing 30 days, shaded by whether each day realised a profit or a loss.
 *
 * A month grid was the prototype's shape, but it reads as an empty calendar
 * whenever the account last traded in an earlier month, which is exactly when
 * a trader wants to see the record. Thirty days is the window they ask about.
 */
function PnlCalendar({ byDay }: { byDay: Map<string, number> }) {
  const days: Array<{ key: string; pnl?: number }> = []
  for (let offset = 29; offset >= 0; offset -= 1) {
    const date = new Date()
    date.setHours(0, 0, 0, 0)
    date.setDate(date.getDate() - offset)
    const key = date.toISOString().slice(0, 10)
    days.push({ key, pnl: byDay.get(key) })
  }
  const traded = days.filter((day) => day.pnl !== undefined).length

  return (
    <>
      <div className="calendar-grid pp-strip">
        {days.map((day) => (
          <i
            key={day.key}
            className={day.pnl === undefined ? 'muted-day' : day.pnl < 0 ? 'loss' : ''}
            title={`${day.key}: ${day.pnl === undefined ? 'no closed fills' : signedMoney(day.pnl)}`}
          />
        ))}
      </div>
      <p className="pp-calendar-note">
        {traded === 0
          ? 'No closed fills in the last 30 days.'
          : `${traded} of the last 30 days realised a result.`}
      </p>
    </>
  )
}

/**
 * A plain-text statement of the account as it currently reads. Built from the
 * same snapshot the page shows, so the file and the screen cannot disagree.
 */
function downloadReport(snapshot: ReturnType<typeof useSnapshot>) {
  const amounts = toAmounts(snapshot.funds)
  const lines = [
    'PRANAVPAY ACCOUNT REPORT',
    `Generated ${new Date().toLocaleString()}`,
    '',
    `Equity            ${amounts ? money(amounts.equity) : 'unavailable'}`,
    `Protected savings ${amounts ? money(amounts.savings) : 'unavailable'}`,
    `Tradable now      ${amounts ? money(amounts.tradable) : 'unavailable'}`,
    `Margin committed  ${amounts ? money(amounts.marginLocked) : 'unavailable'}`,
    `Realised P&L      ${amounts ? signedMoney(amounts.realised) : 'unavailable'}`,
    `Unrealised P&L    ${amounts ? signedMoney(amounts.unrealised) : 'unavailable'}`,
    '',
    `Open positions (${snapshot.positions.length})`,
    ...snapshot.positions.map(
      (position) =>
        `  ${position.symbol}  qty ${position.quantity}  avg ${position.average_price}  ltp ${position.ltp}`
    ),
    '',
    `Venue ${snapshot.funds?.is_live ? 'Binance live exchange' : 'Binance testnet'}`,
  ]
  const blob = new Blob([lines.join('\n')], { type: 'text/plain' })
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = `pranavpay-report-${new Date().toISOString().slice(0, 10)}.txt`
  link.click()
  // Revoking immediately can cancel the download in Safari; defer a tick.
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}
