import { NOT_REPORTED, type PaperAccount } from './account'
import { EmptyNote } from './components'
import { money, percent, signedMoney } from './derive'

/**
 * The virtual account's record, in the figures the paper ledger reports.
 *
 * Everything here is read, not scored. There is deliberately no composite
 * "health" number: a single score hides which of its inputs is bad, and the
 * inputs are the part a trader acts on. A figure the engine did not store reads
 * as "not reported" rather than as a zero.
 */
export function PortfolioFacts({ account }: { account: PaperAccount }) {
  if (account.virgin) {
    return (
      <EmptyNote
        title="No virtual trades yet"
        detail="The paper ledger is empty because the worker has not run. Start it with ./paper and every figure here fills itself in."
      />
    )
  }

  const openValue = account.openPositionValue
  const unrealisedShare =
    account.unrealized !== null && account.equity !== null && account.equity > 0
      ? (Math.abs(account.unrealized) / account.equity) * 100
      : null

  return (
    <div className="pp-facts">
      <div className="pp-facts-row">
        <span>Starting capital</span>
        <strong>{read(account.startingCash, money)}</strong>
        <small>What the ledger was seeded with, and it never changes</small>
      </div>

      <div className="pp-facts-row">
        <span>Realized</span>
        <strong>{read(account.realized, signedMoney)}</strong>
        <small>Closed result since the ledger began</small>
      </div>

      <div className="pp-facts-row">
        <span>Unrealized</span>
        <strong>{read(account.unrealized, signedMoney)}</strong>
        <small>
          {unrealisedShare === null
            ? `${account.positions.length} open position${account.positions.length === 1 ? '' : 's'}`
            : `${percent(unrealisedShare)} of equity across ${account.positions.length} open position${account.positions.length === 1 ? '' : 's'}`}
        </small>
      </div>

      <div className="pp-facts-row">
        <span>Fees charged</span>
        <strong>{read(account.fees, money)}</strong>
        <small>Taken on every simulated fill, and never refunded</small>
      </div>

      <div className="pp-facts-row">
        <span>Peak equity</span>
        <strong>{read(account.peakEquity, money)}</strong>
        <small>The best equity the ledger has recorded</small>
      </div>

      <div className="pp-facts-row">
        <span>Drawdown from peak</span>
        <strong>{read(account.drawdown, percent)}</strong>
        <small>How far the account sits below that peak</small>
      </div>

      <div className="pp-facts-row">
        <span>Margin held against shorts</span>
        <strong>{read(account.shortMarginLocked, money)}</strong>
        <small>Posted against open short positions, so not spendable</small>
      </div>

      <div className="pp-facts-row">
        <span>Open position value</span>
        <strong>{read(openValue, money)}</strong>
        <small>Sum of the marks the ledger reports</small>
      </div>

      <div className="pp-facts-row">
        <span>Traded value</span>
        <strong>{read(account.tradedValue, money)}</strong>
        <small>Across the fills the ledger returned</small>
      </div>
    </div>
  )
}

/** The stored figure, or the words for its absence. */
function read(value: number | null, format: (n: number) => string): string {
  return value === null ? NOT_REPORTED : format(value)
}
