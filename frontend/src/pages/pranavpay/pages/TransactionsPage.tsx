import { useMemo, useState } from 'react'
import {
  ACCOUNT_LABEL,
  NOT_REPORTED,
  PAPER_COMMAND,
  paperFillRows,
  VIRGIN_DETAIL,
  VIRGIN_TITLE,
} from '../account'
import {
  EmptyNote,
  LoadingNote,
  StaleNote,
  TwoAccountsNote,
  UpdatedAt,
  useSnapshot,
} from '../components'
import { assetName, money, qty, relativeTime, signedMoney } from '../derive'
import ExportFillsButton from '../ExportFillsButton'
import FillContributions from '../FillContributions'
import FillDetailModal from '../FillDetailModal'

type Filter = 'all' | 'buy' | 'sell'

/**
 * Transactions. What it can show is every virtual fill the paper ledger
 * recorded, with the size, price and fee the engine charged. The prototype's
 * monthly total and "AI accuracy" score had nothing behind them, and there is
 * no per-fill realized result here either: the engine reports realized as one
 * account total, so the page reports that total and does not distribute it
 * across fills that never carried it.
 */
export default function TransactionsPage() {
  const snapshot = useSnapshot()
  const account = snapshot.account.figures
  const [filter, setFilter] = useState<Filter>('all')
  const fills = useMemo(() => paperFillRows(account?.fills ?? []), [account?.fills])

  const visible = useMemo(() => {
    if (filter === 'all') return fills
    return fills.filter((fill) => fill.action.toLowerCase() === filter)
  }, [fills, filter])

  const realized = account?.realized ?? null
  const unrealized = account?.unrealized ?? null
  const fees = account?.fees ?? null
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const selectedFill = fills.find((fill) => fill.id === selectedId) ?? null

  if (snapshot.account.loading && !account) {
    return (
      <div className="content-wrap page-view is-visible">
        <LoadingNote label="Reading your virtual trade history" />
      </div>
    )
  }

  return (
    <div className="content-wrap page-view is-visible" data-page="Transactions">
      <section className="page-intro page-intro-inner">
        <div>
          <p className="eyebrow">
            Transactions <span className="eyebrow-line" /> {ACCOUNT_LABEL}
          </p>
          <h1>
            Every virtual move,
            <br />
            <span className="period">made legible.</span>
          </h1>
          <p className="intro-copy">
            Every simulated order the paper worker executed, with the size, price and fee it
            charged.
          </p>
        </div>
      </section>

      <TwoAccountsNote />

      {snapshot.account.stale && <StaleNote />}

      <section className="transaction-summary">
        <article className="metric-card surface-card">
          <span className="card-label">Realized P&amp;L</span>
          <div className="metric-value">{read(realized, signedMoney)}</div>
          <div className="metric-foot">
            <span>Account total, across {fills.length} fills</span>
            <span className="metric-glyph">↗</span>
          </div>
        </article>
        <article className="metric-card surface-card">
          <span className="card-label">Unrealized P&amp;L</span>
          <div className="metric-value">{read(unrealized, signedMoney)}</div>
          <div className="metric-foot">
            <span>
              On {account?.positions.length ?? 0} open virtual position
              {account?.positions.length === 1 ? '' : 's'}
            </span>
            <span className="metric-glyph">↗</span>
          </div>
        </article>
        <article className="metric-card surface-card">
          <span className="card-label">Fees charged</span>
          <div className="metric-value">{read(fees, money)}</div>
          <div className="metric-foot">
            <span>Every simulated fill pays a fee</span>
            <span className="metric-glyph">→</span>
          </div>
        </article>
      </section>

      <section className="transaction-table-card surface-card">
        <div className="section-heading compact-heading">
          <div>
            <span className="card-label">All activity</span>
            <h2>Virtual fills</h2>
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
            title={fills.length === 0 ? VIRGIN_TITLE : 'Nothing in this filter'}
            detail={
              fills.length === 0
                ? VIRGIN_DETAIL
                : 'Try a different filter to see the rest of your virtual fills.'
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
                  {qty(fill.quantity)} {fill.asset} at {money(fill.price)}
                  {fill.fee === null || fill.fee === undefined ? '' : ` · fee ${money(fill.fee)}`}
                </span>
                <strong>{money(fill.value)}</strong>
                <time title={fill.at?.toISOString()}>{relativeTime(fill.at)}</time>
              </button>
            ))}
          </div>
        )}

        {fills.length > 0 ? (
          <div className="pp-note pp-note-quiet">
            The paper ledger records realized P&amp;L as one account total, so it is not attributed
            to individual fills. <UpdatedAt at={snapshot.account.updatedAt} />
          </div>
        ) : null}
      </section>

      <FillContributions fills={fills} realized={realized} />

      <FillDetailModal
        fill={selectedFill}
        totalRealised={realized ?? 0}
        onClose={() => setSelectedId(null)}
      />

      <div className="pp-note pp-note-quiet">
        Nothing on this page came from the Binance sandbox. To see that account,{' '}
        <code>{PAPER_COMMAND} status</code> shows the worker's own view of it.
      </div>
    </div>
  )
}

/** The ledger figure, or the words for its absence. Never a zero. */
function read(value: number | null, format: (n: number) => string): string {
  return value === null ? NOT_REPORTED : format(value)
}
