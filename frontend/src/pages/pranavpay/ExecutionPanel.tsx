import { NOT_REPORTED } from './account'
import { EmptyNote } from './components'
import { formatTime, money, qty, relativeTime } from './derive'
import type { PaperFill, PaperVerification } from './usePaperState'
import { type PaperExecution, parseFillExecution } from './usePaperState'

/** Epoch seconds from the engine, as a Date the formatters accept. */
const at = (ts: number): Date | null =>
  typeof ts === 'number' && Number.isFinite(ts) ? new Date(ts * 1000) : null

/**
 * Which side of the book the fill was priced on, in the words a trader uses.
 *
 * A buy lifts the offer, so it fills at the ask, and a sell hits the bid. The
 * model may store any of the usual spellings; anything it names that is not
 * recognisable is passed through untouched rather than guessed at, because a
 * wrong side here would misstate what a fill cost.
 */
function basisText(basis: string | null): string | null {
  if (basis === null) return null
  const word = basis.trim().toLowerCase()
  if (word.length === 0) return null
  if (word.includes('ask') || word.includes('offer')) return 'at the ask'
  if (word.includes('bid')) return 'at the bid'
  if (word.includes('mid')) return 'at the midprice'
  return basis.trim()
}

/**
 * The gap between the best buy and the best sell price when the fill happened.
 *
 * Stored as a fraction of the price by most models and as a percentage by some,
 * and a fraction cannot be told from a percentage by looking at it alone, so a
 * value that could be either is read as a fraction. One whole percent would be
 * printed as 1.00% either way, which is the only case the reading can be wrong.
 */
function spreadText(execution: PaperExecution): string {
  if (execution.spread === null) return NOT_REPORTED
  return execution.spreadIsPercent
    ? `${(execution.spread * 100).toFixed(2)}%`
    : money(execution.spread)
}

/** Modelled latency, in the unit it was stored in: milliseconds. */
function latencyText(latencyMs: number | null): string {
  if (latencyMs === null) return NOT_REPORTED
  if (latencyMs < 0) return NOT_REPORTED
  return latencyMs < 10 ? `${latencyMs.toFixed(1)} ms` : `${Math.round(latencyMs)} ms`
}

/** Whether the whole order traded, or only part of it. */
function fillText(execution: PaperExecution): string {
  if (execution.fill === 'complete') return 'Complete'
  if (execution.fill === 'partial') return 'Partial'
  return NOT_REPORTED
}

/**
 * The untraded remainder, in units.
 *
 * A zero here is a real result — the order traded in full — and only an absent
 * figure reads as `not reported`. The two are never collapsed.
 */
function remainderText(execution: PaperExecution): string {
  if (execution.remainderQty === null) return NOT_REPORTED
  if (execution.remainderQty === 0) return '0, nothing left untraded'
  return `${qty(execution.remainderQty)} left untraded`
}

/**
 * What the execution model actually did with each fill.
 *
 * A simulated fill is only honest if it says what it was priced against. A price
 * on its own is a number the engine chose, so the basis, the spread it saw, the
 * delay it added and whether the order traded in full are shown beside it. A
 * model that reports none of that is reported as not reporting any of it, which
 * is the truth and is more useful than a table of plausible fills.
 */
export function ExecutionPanel({
  fills,
  verification,
}: {
  fills: PaperFill[]
  verification: PaperVerification
}) {
  const executions = fills.map((fill) => parseFillExecution(fill))
  const detailed = executions.filter((execution) => execution.reported).length

  return (
    <section
      className="surface-card pp-paper-section pp-execution"
      aria-labelledby="pp-exec-heading"
    >
      <div className="section-heading compact-heading">
        <div>
          <span className="card-label">Execution</span>
          <h2 id="pp-exec-heading">How each fill was priced</h2>
        </div>
        <span className="wallet-status">
          {detailed === 0
            ? 'Pricing not reported'
            : `${detailed} of ${fills.length} fills report pricing`}
        </span>
      </div>

      <p className="pp-check-honesty">
        A simulated fill is only worth reading if it says what it was priced against. These rows
        carry the side of the book the order lifted or hit, the gap between the best buy and best
        sell price at that moment, the delay the model added, and how much of the order never
        traded. Where the engine reported none of that, the column says so.
      </p>

      <Rejections rejections={verification.rejections} reported={verification.rejectionsReported} />

      {fills.length === 0 ? (
        <EmptyNote
          title="No fill has happened yet"
          detail="There is nothing to price. A row appears here the moment the engine places its first simulated order."
        />
      ) : (
        <div className="pp-paper-table-wrap">
          <table className="pp-paper-table">
            <thead>
              <tr>
                <th scope="col">Time</th>
                <th scope="col">Symbol</th>
                <th scope="col">Side</th>
                <th scope="col">Qty</th>
                <th scope="col">Price</th>
                <th scope="col">Fee</th>
                <th scope="col">Fill basis</th>
                <th scope="col">Spread</th>
                <th scope="col">Modelled latency</th>
                <th scope="col">Fill result</th>
                <th scope="col">Untraded remainder</th>
              </tr>
            </thead>
            <tbody>
              {fills.map((fill, index) => {
                const execution = executions[index]
                return (
                  <tr key={fill.order_id}>
                    <td>{formatTime(at(fill.ts))}</td>
                    <td>
                      <strong>{fill.symbol}</strong>
                    </td>
                    <td>{fill.side}</td>
                    <td>{qty(fill.qty)}</td>
                    <td>{money(fill.price)}</td>
                    <td>{money(fill.fee)}</td>
                    <td>{basisText(execution.basis) ?? NOT_REPORTED}</td>
                    <td>{spreadText(execution)}</td>
                    <td>{latencyText(execution.latencyMs)}</td>
                    <td className={execution.fill === 'partial' ? 'is-negative' : ''}>
                      {fillText(execution)}
                    </td>
                    <td>{remainderText(execution)}</td>
                  </tr>
                )
              })}
            </tbody>
          </table>
          <small className="pp-exec-note">
            {fills.length === 1
              ? 'The most recent simulated fill, in the order the ledger returned it.'
              : `The ${fills.length} most recent simulated fills, in the order the ledger returned them.`}
          </small>
        </div>
      )}
    </section>
  )
}

/**
 * Orders the execution model refused, kept beside the fills.
 *
 * A rejection and a partial fill are the same story told twice — an order the
 * market did not give us in full — so they sit together rather than stranding
 * the refusal list somewhere the reader has to hunt for it.
 */
function Rejections({
  rejections,
  reported,
}: {
  rejections: PaperVerification['rejections']
  reported: boolean
}) {
  if (!reported) {
    return (
      <div className="pp-rejections">
        <span className="card-label">Rejected orders</span>
        <EmptyNote
          title="No rejection list has been reported"
          detail="The engine did not say whether it refused any order. An unanswered question, not a clean sheet."
        />
      </div>
    )
  }
  if (rejections.length === 0) return null

  return (
    <div className="pp-rejections">
      <span className="card-label">Rejected orders</span>
      <ul className="pp-verdicts">
        {rejections.map((rejection, index) => (
          <li
            key={`${rejection.ts ?? 'no-time'}-${index}`}
            className="pp-verdict is-denied"
            data-rejection="true"
          >
            <div className="pp-verdict-head">
              <strong>{rejection.symbol ?? NOT_REPORTED}</strong>
              <span className="pp-verdict-outcome is-denied">Rejected</span>
              <time>
                {rejection.ts === null ? 'Time not reported' : relativeTime(at(rejection.ts))}
              </time>
            </div>
            <p className="pp-verdict-reason">
              {rejection.reason === null
                ? 'The execution model rejected this order without recording a reason.'
                : rejection.reason}
            </p>
          </li>
        ))}
      </ul>
    </div>
  )
}
