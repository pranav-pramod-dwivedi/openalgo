import { NOT_REPORTED } from './account'
import { EmptyNote } from './components'
import { type FillRow, money, signedMoney } from './derive'

/**
 * Where the account's realized result comes from.
 *
 * The paper engine reports realized P&L as a single running total, not per
 * fill, so this panel cannot attribute it to individual fills without
 * inventing an allocation. It therefore states the account total and says
 * plainly that the ledger does not break it down. A breakdown built by
 * spreading the total across fills would look like a record and be fiction.
 */
export default function FillContributions({
  fills,
  realized,
}: {
  fills: FillRow[]
  realized: number | null
}) {
  return (
    <section
      className="transaction-table-card surface-card"
      aria-label="Where the realized result comes from"
    >
      <div className="section-heading compact-heading">
        <div>
          <span className="card-label">Attribution</span>
          <h2>What each fill contributed</h2>
        </div>
        <span className="wallet-status">
          {fills.length} virtual fill{fills.length === 1 ? '' : 's'}
        </span>
      </div>
      <div className="pp-facts">
        <div className="pp-facts-row">
          <span>Realized, account total</span>
          <strong>{realized === null ? NOT_REPORTED : signedMoney(realized)}</strong>
          <small>One running figure, kept by the paper engine since it began</small>
        </div>
        <div className="pp-facts-row">
          <span>Per fill</span>
          <strong>Not reported</strong>
          <small>
            The ledger stores no realized result on an individual fill, so there is nothing to
            attribute it to.
          </small>
        </div>
        {fills.length > 0 ? (
          <div className="pp-facts-row">
            <span>Largest single fill</span>
            <strong>{money(largestValue(fills))}</strong>
            <small>Value traded, not profit: the ledger reports no per-fill result</small>
          </div>
        ) : null}
      </div>
      {fills.length === 0 ? (
        <EmptyNote
          title="No virtual trades yet"
          detail="Once the paper worker executes an order, this panel shows where the account's realized result sits."
        />
      ) : null}
    </section>
  )
}

/** The biggest fill by value traded. */
function largestValue(fills: FillRow[]): number | null {
  let best: number | null = null
  for (const fill of fills) {
    if (!Number.isFinite(fill.value)) continue
    best = best === null ? fill.value : Math.max(best, fill.value)
  }
  return best
}
