import { useMemo, useState } from 'react'
import { EmptyNote, LoadingNote, StaleNote, UpdatedAt, useSnapshot } from '../components'
import { assetName, derive, money, qty, relativeTime, signedMoney, toFillRows } from '../derive'
import ExportFillsButton from '../ExportFillsButton'
import FillContributions from '../FillContributions'
import FillDetailModal from '../FillDetailModal'
import { realisedTotal } from '../fills'

type Filter = 'all' | 'buy' | 'sell'

/**
 * Transactions. The prototype showed a monthly total, an "AI accuracy" score
 * and fee totals, none of which this account can produce. What it can produce
 * is every fill the exchange reported, so that is the whole page, with counts
 * derived from those same fills.
 */
export default function TransactionsPage() {
  const snapshot = useSnapshot()
  const [filter, setFilter] = useState<Filter>('all')
  const fills = useMemo(() => toFillRows(snapshot.trades), [snapshot.trades])
  const orders = snapshot.orders
  const health = useMemo(() => derive(snapshot), [snapshot])

  const visible = useMemo(() => {
    if (filter === 'all') return fills
    return fills.filter((fill) => fill.action.toLowerCase() === filter)
  }, [fills, filter])

  const grossVolume = useMemo(() => fills.reduce((sum, fill) => sum + fill.value, 0), [fills])
  // Same helper the breakdown section totals with, so the header figure and
  // the running sum agree by construction.
  const closedPnl = useMemo(() => realisedTotal(fills), [fills])
  const openOrders = useMemo(
    () => orders.filter((order) => order.order_status === 'open').length,
    [orders]
  )
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const selectedFill = fills.find((fill) => fill.id === selectedId) ?? null

  if (snapshot.loading && !snapshot.funds) {
    return (
      <div className="content-wrap page-view is-visible">
        <LoadingNote label="Reading your trade history" />
      </div>
    )
  }

  return (
    <div className="content-wrap page-view is-visible" data-page="Transactions">
      <section className="page-intro page-intro-inner">
        <div>
          <p className="eyebrow">
            Transactions <span className="eyebrow-line" /> Audit trail
          </p>
          <h1>
            Every move,
            <br />
            <span className="period">made legible.</span>
          </h1>
          <p className="intro-copy">
            Every executed order the exchange reported, with the result it produced.
          </p>
        </div>
      </section>

      {snapshot.stale && <StaleNote />}

      <section className="transaction-summary">
        <article className="metric-card surface-card">
          <span className="card-label">Realised P&amp;L</span>
          <div className="metric-value">{signedMoney(closedPnl)}</div>
          <div className="metric-foot">
            <span>Across {fills.length} fills</span>
            <span className="metric-glyph">↗</span>
          </div>
        </article>
        <article className="metric-card surface-card">
          <span className="card-label">Unrealised P&amp;L</span>
          <div className="metric-value">{signedMoney(health.totalUnrealised)}</div>
          <div className="metric-foot">
            <span>On open positions</span>
            <span className="metric-glyph">↗</span>
          </div>
        </article>
        <article className="metric-card surface-card">
          <span className="card-label">Traded value</span>
          <div className="metric-value">{money(grossVolume)}</div>
          <div className="metric-foot">
            <span>
              {openOrders} order{openOrders === 1 ? '' : 's'} still working
            </span>
            <span className="metric-glyph">→</span>
          </div>
        </article>
      </section>

      <section className="transaction-table-card surface-card">
        <div className="section-heading compact-heading">
          <div>
            <span className="card-label">All activity</span>
            <h2>Fills</h2>
          </div>
          <div className="transaction-filters">
            {(['all', 'buy', 'sell'] as const).map((option) => (
              <button
                key={option}
                type="button"
                className={`filter-chip${filter === option ? ' active' : ''}`}
                onClick={() => setFilter(option)}
                aria-pressed={filter === option}
              >
                {option === 'all' ? 'All' : option === 'buy' ? 'Buys' : 'Sells'}
              </button>
            ))}
            <ExportFillsButton fills={fills} />
          </div>
        </div>

        {visible.length > 0 ? <p className="pp-plain">Tap a fill for its full detail.</p> : null}
        {visible.length === 0 ? (
          <EmptyNote
            title={fills.length === 0 ? 'No fills yet' : 'Nothing in this filter'}
            detail={
              fills.length === 0
                ? 'When an order executes, it appears here with size, price and result.'
                : 'Try a different filter to see the rest of your fills.'
            }
          />
        ) : (
          <div className="full-ledger">
            <div className="ledger-header">
              <span>Activity</span>
              <span>Details</span>
              <span>Amount</span>
              <span>Time</span>
            </div>
            {visible.map((fill) => (
              <button
                type="button"
                className="ledger-row"
                key={fill.id}
                onClick={() => setSelectedId(fill.id)}
                aria-haspopup="dialog"
                style={{ textAlign: 'left', width: '100%' }}
              >
                <span>
                  <i className="ledger-icon">{fill.action === 'BUY' ? '↓' : '↑'}</i>
                  <strong>
                    {fill.action === 'BUY' ? 'Bought' : 'Sold'} {assetName(fill.asset)}
                  </strong>
                </span>
                <span>
                  {qty(fill.quantity)} {fill.asset} at {money(fill.price)} · {fill.product}
                </span>
                <strong>{money(fill.value)}</strong>
                <time title={fill.at?.toISOString()}>{relativeTime(fill.at)}</time>
              </button>
            ))}
          </div>
        )}

        {fills.some((fill) => fill.pnl !== null) && (
          <div className="pp-note pp-note-quiet">
            Closed fills carry a realised result of {signedMoney(closedPnl)} in total.{' '}
            <UpdatedAt at={snapshot.updatedAt} />
          </div>
        )}
      </section>

      <FillContributions fills={fills} />

      <FillDetailModal
        fill={selectedFill}
        totalRealised={closedPnl}
        onClose={() => setSelectedId(null)}
      />
    </div>
  )
}
