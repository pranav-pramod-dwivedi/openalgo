import { useMemo, useState } from 'react'
import { Link } from 'react-router'
import {
  ACCOUNT_LABEL,
  accountAlerts,
  NOT_REPORTED,
  PAPER_COMMAND,
  type PaperAccount,
  paperEquitySeries,
  paperFillRows,
  realisedByDay,
  SANDBOX_LABEL,
  VIRGIN_DETAIL,
  VIRGIN_POSITIONS_DETAIL,
  VIRGIN_POSITIONS_TITLE,
  VIRGIN_TITLE,
} from '../account'
import {
  CapitalSplit,
  EmptyNote,
  LoadingNote,
  SandboxPanel,
  StaleNote,
  TwoAccountsNote,
  UpdatedAt,
  useSnapshot,
} from '../components'
import {
  assetName,
  baseAsset,
  greetingFor,
  introFor,
  money,
  OWNER,
  percent,
  qty,
  relativeTime,
  signedMoney,
  useNow,
} from '../derive'
import { PnlCalendar } from '../PnlCalendar'
import { PortfolioChart, type RangeKey } from '../PortfolioChart'
import { PortfolioFacts } from '../PortfolioFacts'
import { PriceTile } from '../PriceChart'
import {
  livePaperEquity,
  liveUnrealised,
  markPosition,
  useLiveMarks,
  useWatchedMarkets,
} from '../useLiveMarks'

export default function OverviewPage() {
  const snapshot = useSnapshot()
  const now = useNow()
  const [range, setRange] = useState<RangeKey>('1M')
  const account = snapshot.account.figures
  const fills = useMemo(() => paperFillRows(account?.fills ?? []), [account?.fills])
  const series = useMemo(() => (account ? paperEquitySeries(account) : []), [account])
  const notices = useMemo(
    () => accountAlerts(account, { stale: snapshot.account.stale }),
    [account, snapshot.account.stale]
  )
  const byDay = useMemo(
    () => (account ? realisedByDay(account) : new Map<string, number>()),
    [account]
  )
  const lastSevenDays = useMemo(
    () =>
      [...byDay.entries()]
        .sort((a, b) => a[0].localeCompare(b[0]))
        .slice(-7)
        .map(([day, value]) => ({ day, value })),
    [byDay]
  )
  const { prices, isLive, isStreaming } = useLiveMarks(account)
  const watched = useWatchedMarkets(account)

  const marketSymbols = useMemo(
    () =>
      [...new Set([...(account?.positions ?? []).map((p) => p.symbol), ...watched])].slice(0, 3),
    [account?.positions, watched]
  )

  if (snapshot.account.loading && !account) {
    return (
      <div className="content-wrap page-view is-visible" id="quickOverviewPage">
        <LoadingNote label="Reading your virtual account" />
      </div>
    )
  }

  const liveEquity = livePaperEquity(account, prices)
  const liveOpenPnl = liveUnrealised(account?.positions ?? [], prices)
  const openPnl = liveOpenPnl ?? account?.unrealized ?? null
  const unrealised = account?.unrealized ?? null
  const realized = account?.realized ?? null
  const dayChange = realized !== null && openPnl !== null ? realized + openPnl : null

  return (
    <div
      className="content-wrap page-view is-visible"
      id="quickOverviewPage"
      data-page="Quick overview"
    >
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
          <p className="intro-copy">{introFor(now, (account?.positions.length ?? 0) > 0)}</p>
        </div>
        <button className="ghost-button" type="button" onClick={() => downloadReport(account)}>
          <span>Export report</span>
          <svg viewBox="0 0 24 24" aria-hidden="true">
            <path d="M12 4v11M8 11l4 4 4-4M5 19h14" />
          </svg>
        </button>
      </section>

      <TwoAccountsNote />

      {snapshot.account.stale && <StaleNote />}

      <section className="control-strip" aria-label="Quick actions">
        <div className="control-strip-intro">
          <span className="card-label">{ACCOUNT_LABEL}</span>
          <strong>
            {(account?.positions.length ?? 0) === 0
              ? 'No open virtual positions. Your virtual capital is untouched.'
              : `${account?.positions.length} virtual position${account?.positions.length === 1 ? '' : 's'}, marked to market${isLive ? ' live' : ''}.`}
          </strong>
        </div>
        <div className="control-actions">
          <Link className="control-button" to="/manage">
            <span className="control-dot" /> Guardrails
          </Link>
          <Link className="control-button" to="/transactions">
            <span>↗</span> Activity
          </Link>
          <a
            className="control-button command-button"
            href="/dashboard"
            target="_blank"
            rel="noreferrer"
            title={SANDBOX_LABEL}
          >
            <span>⌘</span> Testnet sandbox
          </a>
        </div>
      </section>

      <section className="overview-grid" aria-label="Virtual account overview">
        <article className="balance-card surface-card">
          <div className="card-topline">
            <span className="card-label">Virtual equity</span>
            {isLive ? (
              <span className="live-label">
                <span className="live-dot" /> Live
              </span>
            ) : null}
          </div>
          <div className="balance-value">
            {liveEquity === null ? NOT_REPORTED : money(liveEquity).replace(/\.\d+$/, '')}
          </div>
          <div className="balance-change">
            <span className="change-symbol">
              {dayChange === null ? '→' : dayChange > 0 ? '↗' : dayChange < 0 ? '↘' : '→'}
            </span>
            <strong>{dayChange === null ? NOT_REPORTED : signedMoney(dayChange)}</strong>
            <span>realized plus unrealized</span>
            <span className="change-period">All time</span>
          </div>
          <div className="balance-footer">
            <UpdatedAt at={snapshot.account.updatedAt} />
            {lastSevenDays.length > 0 ? (
              <span className="mini-chart" aria-hidden="true">
                {lastSevenDays.map((value) => (
                  <i
                    key={value.day}
                    style={{
                      height: `${12 + Math.min(76, Math.abs(value.value) * 900)}%`,
                      background: value.value < 0 ? 'transparent' : undefined,
                      border: value.value < 0 ? '1px solid currentColor' : undefined,
                    }}
                  />
                ))}
              </span>
            ) : null}
          </div>
        </article>

        <article className="metric-card surface-card">
          <div className="card-label">Virtual cash</div>
          <div className="metric-value">{read(account?.cash ?? null, money)}</div>
          <div className="metric-foot">
            <span>Available for the next order</span>
            <span className="metric-glyph">↗</span>
          </div>
        </article>

        <article className="metric-card surface-card">
          <div className="card-label">Fees charged</div>
          <div className="metric-value">{read(account?.fees ?? null, money)}</div>
          <div className="metric-foot">
            <span>Taken on every simulated fill</span>
            <span className="metric-glyph">→</span>
          </div>
        </article>

        <article className="metric-card surface-card risk-card">
          <div className="card-label">Open exposure</div>
          <div className="metric-value risk-value">
            <span className="risk-ring" />
            {account?.exposureShare === null || account?.exposureShare === undefined
              ? NOT_REPORTED
              : account.exposureShare > 0
                ? percent(account.exposureShare)
                : 'Flat'}
          </div>
          <div className="metric-foot">
            <span>
              {account && account.positions.length === 0
                ? 'No capital is at risk'
                : account
                  ? 'Of equity sits in open virtual positions'
                  : NOT_REPORTED}
            </span>
            <span className="metric-glyph">→</span>
          </div>
        </article>
      </section>

      <section className="primary-grid">
        <article className="chart-card surface-card">
          <PortfolioChart
            series={series}
            equity={liveEquity}
            startingCash={account?.startingCash ?? null}
            range={range}
            onRangeChange={setRange}
          />
        </article>

        <article className="ai-card">
          <div className="ai-card-header">
            <div>
              <span className="card-label inverse-label">Autopilot</span>
              <h2>AI trading</h2>
            </div>
            <a
              className="more-button inverse-more"
              href="/agent"
              aria-label="Open the agent in OpenAlgo"
            >
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
            The agent is not running against this account. The paper worker is what places its
            orders, and until the agent is wired up every trade is that worker's own.
          </p>
          <div className="ai-stats">
            <div>
              <span>Last action</span>
              <strong>{fills.length > 0 ? `${fills.length} virtual fills` : 'None'}</strong>
              <small>{fills.length > 0 ? 'Placed by the paper worker' : 'No orders yet'}</small>
            </div>
            <div>
              <span>Next review</span>
              <strong>Not scheduled</strong>
              <small>Agent is off</small>
            </div>
          </div>
          <div className="ai-actions">
            <button
              className="inverse-button"
              type="button"
              disabled
              title="AI trading arrives in the next stage"
            >
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
              <span className="card-label">Your virtual assets</span>
              <h2>
                Open positions{' '}
                <span className="heading-count">{account?.positions.length ?? 0}</span>
              </h2>
            </div>
            <Link className="text-button" to="/transactions">
              Activity <span>→</span>
            </Link>
          </div>
          {(account?.positions.length ?? 0) === 0 ? (
            <EmptyNote
              title={account?.virgin ? VIRGIN_POSITIONS_TITLE : 'No open virtual positions'}
              detail={account?.virgin ? VIRGIN_POSITIONS_DETAIL : 'Nothing is exposed right now.'}
            />
          ) : (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Asset</th>
                    <th>Position</th>
                    <th>Avg. price</th>
                    <th>Mark</th>
                    <th>Return</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {(account?.positions ?? []).map((position) => {
                    const marked = markPosition(position, prices)
                    return (
                      <tr key={position.symbol}>
                        <td>
                          <AssetName symbol={position.symbol} />
                        </td>
                        <td>
                          <strong>
                            {qty(position.qty)} {baseAsset(position.symbol)}
                          </strong>
                          <small className="muted-line">
                            {String(position.side).toUpperCase() === 'SELL' ? 'Short' : 'Long'}
                          </small>
                        </td>
                        <td>{money(position.entry)}</td>
                        <td>
                          {money(marked.ltp)}
                          {marked.isLive ? null : <small className="muted-line"> last known</small>}
                        </td>
                        <td>
                          <strong>{read(marked.pnlPercent, percent)}</strong>
                          <small className="muted-line">{signedMoney(marked.pnl)}</small>
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
              <h2>Recent virtual fills</h2>
            </div>
            <Link className="text-button" to="/transactions">
              View all <span>→</span>
            </Link>
          </div>
          {fills.length === 0 ? (
            <EmptyNote title={VIRGIN_TITLE} detail={VIRGIN_DETAIL} />
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

      <section className="feature-grid" aria-label="Account record, engine and status">
        <article className="feature-card surface-card decision-card">
          <div className="section-heading compact-heading">
            <div>
              <span className="card-label">Your account</span>
              <h2>Capital</h2>
            </div>
            <Link className="text-button" to="/manage">
              Review <span>→</span>
            </Link>
          </div>
          <CapitalSplit account={account} />
        </article>

        <article className="feature-card surface-card signals-card">
          <div className="section-heading compact-heading">
            <div>
              <span className="card-label">What matters</span>
              <h2>Account record</h2>
            </div>
            {isLive ? (
              <span className="live-label">
                <span className="live-dot" /> Live
              </span>
            ) : null}
          </div>
          {account ? <PortfolioFacts account={account} /> : <LoadingNote />}
          <div className="signal-list">
            <div className="signal-row">
              <span>Cash share</span>
              <strong>{read(account?.cashShare ?? null, (n) => `${n.toFixed(1)}%`)}</strong>
              <span className="signal-meter">
                <i style={{ width: `${clamp(account?.cashShare ?? null)}%` }} />
              </span>
            </div>
            <div className="signal-row">
              <span>Open exposure</span>
              <strong>
                {account?.exposureShare === null || account?.exposureShare === undefined
                  ? NOT_REPORTED
                  : account.exposureShare > 0
                    ? percent(account.exposureShare)
                    : 'Flat'}
              </strong>
              <span className="signal-meter">
                <i style={{ width: `${clamp(account?.exposureShare ?? null)}%` }} />
              </span>
            </div>
            <div className="signal-row">
              <span>Unrealized P&amp;L</span>
              <strong>{read(unrealised, signedMoney)}</strong>
              <span className="signal-meter">
                <i style={{ width: `${clamp(Math.abs(unrealised ?? 0))}%` }} />
              </span>
            </div>
            <div className="signal-row">
              <span>Realized P&amp;L</span>
              <strong>{read(realized, signedMoney)}</strong>
              <span className="signal-meter">
                <i style={{ width: `${clamp(Math.abs(realized ?? 0))}%` }} />
              </span>
            </div>
          </div>
          <div className="planned-action">
            <span className="card-label">Best / worst open position</span>
            <strong>{extremes(account)}</strong>
            <small>
              {(account?.positions.length ?? 0) === 0
                ? 'Nothing to rank until the worker opens a position.'
                : 'Ranked on the paper ledger marks.'}
            </small>
          </div>
        </article>

        <article className="feature-card surface-card wallet-feature-card">
          <div className="section-heading compact-heading">
            <div>
              <span className="card-label">Where it sits</span>
              <h2>Allocation</h2>
            </div>
          </div>
          <AllocationBars account={account} />
        </article>

        <article className="feature-card surface-card calendar-card">
          <div className="section-heading compact-heading">
            <div>
              <span className="card-label">Performance history</span>
              <h2>P&amp;L calendar</h2>
            </div>
          </div>
          <PnlCalendar byDay={byDay} />
        </article>

        <article className="feature-card surface-card insights-card">
          <div className="section-heading compact-heading">
            <div>
              <span className="card-label">Realized results</span>
              <h2>P&amp;L by day</h2>
            </div>
            <Link className="text-button" to="/transactions">
              Ledger <span>→</span>
            </Link>
          </div>
          {byDay.size === 0 ? (
            <EmptyNote
              title="No realized P&L recorded yet"
              detail="The ledger stores realized as a running total, so a day appears once a cycle moved it."
            />
          ) : (
            <div className="full-ledger">
              {[...byDay.entries()]
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
                    <span>Realized</span>
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
              detail="Notices appear when the ledger goes stale, the account falls from its peak, or the worker reports a problem."
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
              {isLive ? (
                <span className="live-label">
                  <span className="live-dot" /> Live
                </span>
              ) : null}
            </div>
            <div className="pp-tiles">
              {marketSymbols.map((symbol) => (
                <PriceTile key={symbol} symbol={symbol} label={baseAsset(symbol)} />
              ))}
            </div>
          </article>
        ) : null}
      </section>

      {/* The sandbox is a different account, so it is a different section. It
          sits outside the account grid rather than as another card in it, and
          the heading carries its label in full so the distinction is visible
          before a single figure is read. */}
      <section className="pp-sandbox-section">
        <div className="section-heading compact-heading">
          <div>
            <span className="card-label">A different account</span>
            <h2>{SANDBOX_LABEL}</h2>
          </div>
        </div>
        <SandboxPanel snapshot={snapshot} />
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

/** A ledger figure, or the words for its absence. Never a zero. */
function read(value: number | null, format: (n: number) => string): string {
  return value === null ? NOT_REPORTED : format(value)
}

/** A share into 0-100 for a meter. Null and negative both render no bar. */
function clamp(value: number | null): number {
  if (value === null || !Number.isFinite(value)) return 0
  return Math.min(100, Math.max(0, value))
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
 * Best and worst open virtual position by percent, from the ledger's own marks.
 * Both are absent when the account holds nothing.
 */
function extremes(account: PaperAccount | null): string {
  if (!account || account.positions.length === 0) return 'No positions'
  const ranked = account.positions
    .map((position) => ({ position, marked: markPosition(position, new Map()) }))
    .filter((row) => row.marked.pnlPercent !== null)
    .sort((a, b) => (b.marked.pnlPercent ?? 0) - (a.marked.pnlPercent ?? 0))
  const best = ranked[0]
  const worst = ranked.length > 1 ? ranked[ranked.length - 1] : null
  const head = best
    ? `${best.position.symbol} ${percent(best.marked.pnlPercent ?? 0)}`
    : 'Not reported'
  return worst ? `${head} · worst ${worst.position.symbol}` : head
}

/** Cash, open positions and unrealized P&L as shares of the virtual equity. */
function AllocationBars({ account }: { account: PaperAccount | null }) {
  if (!account || account.virgin) {
    return (
      <EmptyNote
        title="Nothing to allocate yet"
        detail={`Once the paper worker has run, the split between cash, open positions and unrealized P&L appears here. Start it with ${PAPER_COMMAND}.`}
      />
    )
  }
  const equity = account.equity
  const slices: Array<{ label: string; value: number | null }> = [
    { label: 'Virtual cash', value: account.cash },
    { label: 'Open positions', value: account.openPositionValue },
    {
      label: 'Unrealized P&L',
      value: account.unrealized === null ? null : Math.abs(account.unrealized),
    },
  ]
  // A share of nothing is unknown, so a slice with no equity behind it shows
  // its own dollar value and no percentage rather than a fabricated 0%.
  const shareOf = (value: number | null): number | null =>
    value === null || equity === null || equity <= 0 ? null : (value / equity) * 100

  return (
    <div className="allocation-bars">
      {slices.map((slice) => {
        const share = shareOf(slice.value)
        return (
          <div key={slice.label}>
            <span>
              {slice.label} <strong>{read(share, (n) => `${n.toFixed(1)}%`)}</strong>
            </span>
            <i>
              <b style={{ width: `${clamp(share)}%` }} />
            </i>
          </div>
        )
      })}
    </div>
  )
}

/**
 * A plain-text statement of the virtual account as it currently reads. Built
 * from the same ledger the page shows, so the file and the screen cannot
 * disagree.
 */
function downloadReport(account: PaperAccount | null) {
  const lines = [
    'PRANAVPAY VIRTUAL ACCOUNT REPORT',
    `Generated ${new Date().toLocaleString()}`,
    '',
    'Virtual money. Not deposited anywhere and not withdrawable.',
    '',
    `Equity                 ${read(account?.equity ?? null, money)}`,
    `Virtual cash           ${read(account?.cash ?? null, money)}`,
    `Realized P&L           ${read(account?.realized ?? null, signedMoney)}`,
    `Unrealized P&L         ${read(account?.unrealized ?? null, signedMoney)}`,
    `Fees charged           ${read(account?.fees ?? null, money)}`,
    `Starting capital       ${read(account?.startingCash ?? null, money)}`,
    `Peak equity            ${read(account?.peakEquity ?? null, money)}`,
    `Drawdown from peak     ${read(account?.drawdown ?? null, percent)}`,
    `Margin locked (shorts) ${read(account?.shortMarginLocked ?? null, money)}`,
    `Open position value    ${read(account?.openPositionValue ?? null, money)}`,
    '',
    `Open virtual positions (${account?.positions.length ?? 0})`,
    ...(account?.positions ?? []).map(
      (position) =>
        `  ${position.symbol}  ${position.side} ${position.qty}  entry ${position.entry}  mark ${position.mark}`
    ),
    '',
    `Worker: ${account?.worker?.status ?? NOT_REPORTED} (cycle ${account?.worker?.cycle ?? NOT_REPORTED})`,
    '',
    `The OpenAlgo terminal is a separate Binance ${SANDBOX_LABEL},`,
    'and none of its balances appear in this report.',
  ]
  const blob = new Blob([lines.join('\n')], { type: 'text/plain' })
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = `pranavpay-virtual-account-${new Date().toISOString().slice(0, 10)}.txt`
  link.click()
  // Revoking immediately can cancel the download in Safari; defer a tick.
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}
