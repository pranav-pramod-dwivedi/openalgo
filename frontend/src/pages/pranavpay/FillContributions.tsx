import { EmptyNote } from './components'
import type { FillRow } from './derive'
import { assetName, signedMoney } from './derive'
import { contributionRows, realisedTotal } from './fills'

/**
 * Every closed fill's P&L contribution with a running sum beside it. The
 * running sum and the header figure both come from `realisedTotal` over the
 * same fills array, so the final running value equals the header by
 * construction - the note below states that instead of asking for trust.
 */
export default function FillContributions({ fills }: { fills: FillRow[] }) {
  const rows = contributionRows(fills)
  const total = realisedTotal(fills)

  return (
    <section
      className="transaction-table-card surface-card"
      aria-label="Per-fill realised contributions"
    >
      <div className="section-heading compact-heading">
        <div>
          <span className="card-label">Attribution</span>
          <h2>What each fill contributed</h2>
        </div>
        {rows.length > 0 ? (
          <span className="wallet-status">
            {rows.length} closed {rows.length === 1 ? 'fill' : 'fills'}
          </span>
        ) : null}
      </div>
      {rows.length === 0 ? (
        <EmptyNote
          title="No realised results yet"
          detail="Fills appear here once they carry a realised result. Open fills contribute nothing until they close."
        />
      ) : (
        <div className="pp-facts">
          {rows.map(({ fill, running }) => (
            <div className="pp-facts-row" key={fill.id}>
              <span>
                {fill.action === 'BUY' ? 'Bought' : 'Sold'} {assetName(fill.asset)} · {fill.symbol}
              </span>
              <strong>{signedMoney(fill.pnl)}</strong>
              <small>Running total {signedMoney(running)}</small>
            </div>
          ))}
        </div>
      )}
      {rows.length > 0 ? (
        <div className="pp-note pp-note-quiet">
          Running total ends at {signedMoney(total)} across {rows.length}{' '}
          {rows.length === 1 ? 'fill' : 'fills'} - the same sum as the Realised P&amp;L figure
          above, because both are summed from the same fills.
        </div>
      ) : null}
    </section>
  )
}
