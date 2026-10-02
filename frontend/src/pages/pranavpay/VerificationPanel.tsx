import { NOT_REPORTED } from './account'
import { EmptyNote } from './components'
import { relativeTime } from './derive'
import type { PaperCheck, PaperVerdict, PaperVerification } from './usePaperState'
import { denialText } from './usePaperState'

/** Epoch seconds from the engine, as a Date the formatters accept. */
const at = (ts: number | null): Date | null =>
  typeof ts === 'number' && Number.isFinite(ts) ? new Date(ts * 1000) : null

/** A count, or an explicit gap. An absent count is never shown as a zero. */
function countText(value: number | null): string {
  return value === null ? NOT_REPORTED : String(value)
}

/** The direction a verdict was about, or an explicit gap. */
function sideText(side: string | null): string {
  if (side === null) return NOT_REPORTED
  const word = side.trim().toUpperCase()
  if (word === 'BUY') return 'Long'
  if (word === 'SELL') return 'Short'
  return side
}

/** A stored check name as readable words, never as the handle it arrived as. */
function checkLabel(name: string | null): string {
  if (name === null) return 'Check not named'
  const words = name.replace(/[_-]+/g, ' ').replace(/\s+/g, ' ').trim()
  return words.length === 0 ? 'Check not named' : words
}

/** How a check came out, in one word. Never a code and never a bare number. */
function checkOutcome(check: PaperCheck): string {
  if (check.passed === true) return 'passed'
  if (check.passed === false) return 'failed'
  return 'outcome not reported'
}

/** What one verdict decided, as a word a reader can trust. */
function outcomeWord(verdict: PaperVerdict): string {
  if (verdict.allowed === true) return 'Allowed'
  if (verdict.allowed === false) return 'Denied'
  return 'Outcome not reported'
}

/**
 * The checks that run before an order is placed, in plain words.
 *
 * The panel exists to make one thing unmissable: a trade reaching the paper
 * account has already been through a check that was free to stop it, and a
 * denial is that check doing its job rather than a fault. So a denial is set
 * apart, given its reason in a sentence, and never shown as a raw identifier.
 *
 * Everything is read from what the engine reported. When the verdicts key is
 * absent the panel says the checks have not reported — not that nothing was
 * denied, and not a row of zeroes, which would be the most reassuring thing on
 * the page and the one least supported by the payload.
 */
export function VerificationPanel({ verification }: { verification: PaperVerification }) {
  const { verdicts, verdictsReported, allowedCount, deniedCount } = verification
  const countsReported = allowedCount !== null || deniedCount !== null
  const deniedShown = verdicts.filter((verdict) => verdict.allowed === false).length

  return (
    <section
      className="surface-card pp-paper-section pp-checks"
      aria-labelledby="pp-checks-heading"
    >
      <div className="section-heading compact-heading">
        <div>
          <span className="card-label">Verification</span>
          <h2 id="pp-checks-heading">Checks before trading</h2>
        </div>
        <span className="wallet-status">
          {verdictsReported
            ? `${verdicts.length} most recent ${verdicts.length === 1 ? 'verdict' : 'verdicts'}`
            : 'No verdicts reported'}
        </span>
      </div>

      <dl className="pp-paper-facts pp-check-counts">
        <div>
          <dt>Trades allowed</dt>
          <dd>{countText(allowedCount)}</dd>
        </div>
        <div>
          <dt>Trades denied</dt>
          <dd>{countText(deniedCount)}</dd>
        </div>
      </dl>

      <p className="pp-check-honesty">
        Every allowed trade passed an independent check that could have denied it. A denial is the
        system working, not an error: the order was stopped before it was placed, so nothing was
        bought or sold and no virtual money moved.
      </p>

      {!countsReported && (
        <p className="pp-check-gap">
          The engine has not reported how many trades these checks have seen in total. The verdicts
          below are the only ones it returned.
        </p>
      )}

      {!verdictsReported ? (
        <EmptyNote
          title="The checks have not reported anything yet"
          detail="This engine did not send any verdict, so nothing is known about whether trades have been checked. That is not the same as everything passing: until a verdict arrives, no trade is claimed to have passed."
        />
      ) : verdicts.length === 0 ? (
        <EmptyNote
          title="The checks have run and reported no verdicts"
          detail="The engine sent an empty list, so no trade has been put to the checks yet. The first one will appear here the moment a cycle asks for one."
        />
      ) : (
        <>
          {deniedShown > 0 && (
            <p className="pp-check-gap">
              {deniedShown} of the {verdicts.length} verdicts shown below stopped the trade. Each
              one says which check objected.
            </p>
          )}
          <ul className="pp-verdicts">
            {verdicts.map((verdict, index) => (
              <VerdictRow key={`${verdict.ts ?? 'no-time'}-${index}`} verdict={verdict} />
            ))}
          </ul>
        </>
      )}
    </section>
  )
}

/**
 * One verdict: what was decided, why, and how each individual check came out.
 *
 * A denial leads with its reason in a sentence, because the reason is the whole
 * content of the row. A reason the engine stored as an identifier is looked up
 * and rewritten in words; one it never explained is marked as unexplained rather
 * than dressed up as a cause.
 */
function VerdictRow({ verdict }: { verdict: PaperVerdict }) {
  const denied = verdict.allowed === false
  const outcome = denied ? 'denied' : verdict.allowed === true ? 'allowed' : 'unstated'
  const reasons = verdict.reasons.map((reason) => denialText(reason))

  return (
    <li className={`pp-verdict is-${outcome}`} data-verdict={outcome}>
      <div className="pp-verdict-head">
        <strong>{verdict.symbol ?? NOT_REPORTED}</strong>
        <span className={`pp-verdict-outcome is-${outcome}`}>{outcomeWord(verdict)}</span>
        <time>
          {verdict.ts === null ? 'Time not reported' : relativeTime(at(verdict.ts))}
          {verdict.side === null ? '' : ` · ${sideText(verdict.side)}`}
        </time>
      </div>

      {denied &&
        (reasons.length > 0 ? (
          <div className="pp-verdict-why">
            <span className="pp-verdict-why-label">Denied because</span>
            <ul>
              {reasons.map((reason) => (
                <li key={reason.text} className="pp-verdict-reason">
                  {reason.text}
                </li>
              ))}
            </ul>
            <p className="pp-verdict-note">
              This is the check working. No order was placed, so no virtual money moved.
            </p>
          </div>
        ) : (
          <p className="pp-verdict-reason">
            The checks stopped this trade but did not record which one objected. The count above is
            still real; only the cause is missing.
          </p>
        ))}

      {!denied && reasons.length > 0 && (
        <div className="pp-verdict-why">
          <span className="pp-verdict-why-label">Noted while allowing</span>
          <ul>
            {reasons.map((reason) => (
              <li key={reason.text} className="pp-verdict-reason">
                {reason.text}
              </li>
            ))}
          </ul>
        </div>
      )}

      {verdict.checks.length > 0 && (
        <ul className="pp-checks">
          {verdict.checks.map((check, index) => (
            <li
              key={`${checkLabel(check.name)}-${index}`}
              className={`pp-check is-${check.passed === true ? 'passed' : check.passed === false ? 'failed' : 'unstated'}`}
              data-check={
                check.passed === true ? 'passed' : check.passed === false ? 'failed' : 'unstated'
              }
            >
              <span className="pp-check-name">{checkLabel(check.name)}</span>
              <span className="pp-check-outcome">{checkOutcome(check)}</span>
              {check.passed === false && check.detail === null ? (
                <span className="pp-check-detail">{denialText(checkLabel(check.name)).text}</span>
              ) : check.detail !== null ? (
                <span className="pp-check-detail">{check.detail}</span>
              ) : null}
            </li>
          ))}
        </ul>
      )}
    </li>
  )
}
