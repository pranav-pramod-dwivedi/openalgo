import { useMemo } from 'react'
import { Link } from 'react-router'
import {
  alerts,
  allocation,
  assetName,
  dailyRealised,
  derive,
  greetingFor,
  introFor,
  money,
  parseBrokerTime,
  percent,
  qty,
  relativeTime,
  signedMoney,
  toFillRows,
  toPositionRows,
  useNow,
} from '../derive'
import { AssetCell, CapitalSplit, EmptyNote, LoadingNote, StaleNote, UpdatedAt, useSnapshot } from '../components'
import { toAmounts } from '../useWalletSnapshot'

/**
 * Quick overview. The prototype showed a fabricated balance, four invented
 * positions, an invented activity feed and a hand-drawn chart. Every number
 * here is read from the exchange, and where the account has nothing to report
 * the panel says so.
 */
export default function OverviewPage() {
  const snapshot = useSnapshot()
  const now = useNow()
  const amounts = toAmounts(snapshot.funds)
  const rows = useMemo(() => toPositionRows(snapshot.positions), [snapshot.positions])
  const fills = useMemo(() => toFillRows(snapshot.trades), [snapshot.trades])
  const health = useMemo(() => derive(snapshot), [snapshot])
  const slices = useMemo(() => allocation(snapshot.funds, snapshot.positions), [snapshot])
  const notices = useMemo(() => alerts(snapshot), [snapshot])
  const realisedByDay = useMemo(() => dailyRealised(fills), [fills])

  if (snapshot.loading && !snapshot.funds) {
    return (
      <div className="content-wrap page-view is-visible" id="quickOverviewPage">
        <LoadingNote label="Reading your Binance account" />
      </div>
    )
  }

  const totalBalance = amounts?.equity ?? null
  const dayChange = health.totalRealised + health.totalUnrealised

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
            <span>{greetingFor(now)}</span>
            <span className="period">.</span>
          </h1>
          <p className="intro-copy">{introFor(now, rows.length > 0)}</p>
        </div>
        <Link className="ghost-button" to="/transactions">
          <span>See activity</span>
          <svg viewBox="0 0 24 24" aria-hidden="true">
            <path d="M12 4v11M8 11l4 4 4-4M5 19h14" />
          </svg>
        </Link>
      </section>

      {snapshot.stale && <StaleNote />}

      <section className="control-strip" aria-label="Quick actions">
        <div className="control-strip-intro">
          <span className="card-label">Control center</span>
          <strong>
            {rows.length === 0
              ? 'No open positions. Your capital is untouched.'
              : `${rows.length} open position${rows.length === 1 ? '' : 's'}, marked to market.`}
          </strong>
        </div>
        <div className="control-actions">
          <Link className="control-button command-button" to="/manage">
            <span>›</span> Guardrails
          </Link>
        </div>
      </section>

      <section className="overview-grid" aria-label="Account overview">
        <article className="balance-card surface-card">
          <div className="card-topline">
            <span className="card-label">Total balance</span>
          </div>
          <div className="balance-value">{totalBalance === null ? '—' : money(totalBalance).replace('$', '$').replace(/\.\d+$/, '')}</div>
          <div className="balance-change">
            <span className="change-symbol">{dayChange > 0 ? '↗' : dayChange < 0 ? '↘' : '→'}</span>
            <strong>{signedMoney(dayChange)}</strong>
            <span>realised plus unrealised</span>
            <span className="change-period">All time</span>
          </div>
          <div className="balance-footer">
            <UpdatedAt at={snapshot.updatedAt} />
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
            <span>
              {health.exposureShare > 0 ? 'Of equity is in open positions' : 'No capital at risk'}
            </span>
            <span className="metric-glyph">→</span>
          </div>
        </article>
      </section>

      <section className="primary-grid">
        <article className="chart-card surface-card">
          <div className="section-heading">
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
            <div className="pp-allocation">
              {slices.map((slice) => (
                <div className="pp-allocation-row" key={slice.label}>
                  <span className="card-label">{slice.label}</span>
                  <strong>{money(slice.value)}</strong>
                  <div className="pp-allocation-track">
                    <i style={{ width: `${Math.min(100, Math.max(0, slice.share))}%` }} />
                  </div>
                  <span className="pp-allocation-share">{slice.share.toFixed(1)}%</span>
                </div>
              ))}
            </div>
          )}
        </article>

        <article className="ai-card">
          <div className="ai-card-header">
            <div>
              <span className="card-label inverse-label">Autopilot</span>
              <h2>AI trading</h2>
            </div>
          </div>
          <div className="ai-orbit" aria-hidden="true">
            <div className="orbit-ring ring-one" />
            <div className="orbit-ring ring-two" />
            <div className="orbit-core">
              <span className="core-glyph">P</span>
            </div>
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
              <strong>None</strong>
              <small>No agent orders placed</small>
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
                Open positions <span className="heading-count">{rows.length}</span>
              </h2>
            </div>
            <Link className="text-button" to="/transactions">
              Activity <span>→</span>
            </Link>
          </div>
          {rows.length === 0 ? (
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
                  {rows.map((row) => (
                    <tr key={row.symbol}>
                      <td>
                        <AssetCell asset={row.asset} name={row.name} symbol={row.symbol} />
                      </td>
                      <td>
                        <strong>
                          {qty(row.quantity)} {row.asset}
                        </strong>
                        <small className="muted-line">{row.side}</small>
                      </td>
                      <td>{money(row.averagePrice)}</td>
                      <td>{money(row.value)}</td>
                      <td>
                        <strong>{percent(row.pnlPercent)}</strong>
                        <small className="muted-line">{signedMoney(row.pnl)}</small>
                      </td>
                      <td />
                    </tr>
                  ))}
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
                  <div className="activity-icon system-activity">{fill.action === 'BUY' ? '↓' : '↑'}</div>
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

      <section className="feature-grid" aria-label="Guardrails and status">
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
          </div>
          <div className="signal-list">
            <div className="signal-row">
              <span>Tradable share</span>
              <strong>{amounts && amounts.equity > 0 ? `${health.cashShare.toFixed(1)}%` : '—'}</strong>
              <span className="signal-meter">
                <i style={{ width: `${Math.min(100, health.cashShare)}%` }} />
              </span>
            </div>
            <div className="signal-row">
              <span>Exposure</span>
              <strong>{health.exposureShare > 0 ? percent(health.exposureShare) : 'Flat'}</strong>
              <span className="signal-meter">
                <i style={{ width: `${Math.min(100, health.exposureShare)}%` }} />
              </span>
            </div>
            <div className="signal-row">
              <span>Unrealised P&amp;L</span>
              <strong>{signedMoney(health.totalUnrealised)}</strong>
              <span className="signal-meter">
                <i style={{ width: `${Math.min(100, Math.abs(health.totalUnrealised))}%` }} />
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
            <strong>
              {health.best ? `${health.best.symbol} ${percent(health.best.pnlPercent)}` : 'No positions'}
            </strong>
            <small>
              {health.worst
                ? `Weakest is ${health.worst.symbol} at ${percent(health.worst.pnlPercent)}.`
                : 'Nothing to rank until a position is open.'}
            </small>
          </div>
        </article>

        <article className="feature-card surface-card insights-card">
          <div className="section-heading compact-heading">
            <div>
              <span className="card-label">Realised results</span>
              <h2>P&amp;L by day</h2>
            </div>
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
                      <strong>{parseBrokerTime(`${day} 00:00:00`)?.toLocaleDateString('en-US', { month: 'short', day: '2-digit' }) ?? day}</strong>
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
      </section>

      <footer className="page-footer">
        <span>
          PranavPay <span className="footer-separator">·</span> Your money, in motion.
        </span>
      </footer>
    </div>
  )
}
