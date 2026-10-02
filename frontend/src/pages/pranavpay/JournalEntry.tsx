import { parseBrokerTime, relativeTime } from './derive'
import type { JournalEntryData } from './useJournal'

/**
 * One audit row, stated as four facts: what the agent proposed, what the risk
 * layer decided, what the service returned, and where the row came from.
 *
 * A reason-after is deliberately absent. The model writes its reasoning trace
 * onto `ag_message`, and GET /agent/api/audit never joins those rows in, so
 * there is no endpoint key to render it from and this component invents none.
 * Every optional key is guarded: an absent value reads as "not recorded".
 */
export default function JournalEntry({ entry }: { entry: JournalEntryData }) {
  const at = parseBrokerTime(entry.ts ?? undefined)
  const orderIds = Array.isArray(entry.order_ids)
    ? entry.order_ids.filter((orderId): orderId is string => typeof orderId === 'string')
    : []
  const argsText = toJsonText(entry.args)
  const responseText = toJsonText(entry.response)

  return (
    <article className="pp-jentry">
      <div className="pp-jentry-head">
        <div className="pp-jentry-title">
          <strong>{entry.tool}</strong>
          <span className="pp-jchip is-phase">{phaseLabel(entry.phase)}</span>
          {entry.risk_verdict !== null && (
            <span className={`pp-jchip${verdictTone(entry.risk_verdict)}`}>
              {verdictLabel(entry.risk_verdict)}
            </span>
          )}
        </div>
        {typeof entry.ts === 'string' && at !== null ? (
          <time className="pp-jtime" title={entry.ts}>
            {relativeTime(at)}
          </time>
        ) : (
          <span className="pp-jtime">Time not recorded</span>
        )}
      </div>

      <div className="pp-jsections">
        <section className="pp-jsection">
          <span>Proposed</span>
          {argsText !== null ? (
            <pre className="pp-jpre">{argsText}</pre>
          ) : (
            <p className="pp-jquiet">The request arguments were not recorded on this row.</p>
          )}
        </section>

        <section className="pp-jsection">
          <span>Risk decision</span>
          {entry.risk_verdict !== null ? (
            <p className="pp-jverdict">{entry.risk_verdict}</p>
          ) : (
            <p className="pp-jquiet">No verdict was recorded on this row.</p>
          )}
        </section>

        <section className="pp-jsection">
          <span>Result</span>
          <p className="pp-joutcome">{outcomeLabel(entry)}</p>
          {responseText !== null && <pre className="pp-jpre">{responseText}</pre>}
          {orderIds.length > 0 && (
            <ul className="pp-jorders">
              {orderIds.map((orderId) => (
                <li key={orderId}>{orderId}</li>
              ))}
            </ul>
          )}
        </section>

        {(typeof entry.conversation_id === 'number' || typeof entry.run_id === 'string') && (
          <p className="pp-jmeta">
            {typeof entry.conversation_id === 'number' && (
              <span>Conversation #{entry.conversation_id}</span>
            )}
            {typeof entry.run_id === 'string' && <span>Run {entry.run_id}</span>}
          </p>
        )}
      </div>
    </article>
  )
}

/** The phase, in words. Unknown phases render verbatim rather than as a guess. */
function phaseLabel(phase: string): string {
  if (phase === 'attempt') return 'Attempt'
  if (phase === 'result') return 'Result'
  if (phase === 'decision') return 'Decision'
  if (phase === 'transcript') return 'Transcript'
  return phase
}

/**
 * The chip tone, derived from the verdict's own prefix: `allow:{code}` and
 * `decision:approved` read as approvals, `block:{code}` and
 * `decision:rejected` as refusals, anything else as neutral.
 */
function verdictTone(verdict: string): string {
  if (verdict.startsWith('allow:') || verdict === 'decision:approved') return ' is-allow'
  if (verdict.startsWith('block:') || verdict === 'decision:rejected') return ' is-block'
  return ''
}

/** The chip text, derived from the verdict's own prefix and code. */
function verdictLabel(verdict: string): string {
  if (verdict.startsWith('allow:')) {
    const code = verdict.slice('allow:'.length).trim()
    return code ? `Allowed · ${code}` : 'Allowed'
  }
  if (verdict.startsWith('block:')) {
    const code = verdict.slice('block:'.length).trim()
    return code ? `Blocked · ${code}` : 'Blocked'
  }
  if (verdict === 'decision:approved') return 'Approved'
  if (verdict === 'decision:rejected') return 'Rejected'
  return verdict
}

/**
 * The outcome line, from the row's own `ok` and `phase`. Only a result row
 * carries an outcome; an attempt row with no `ok` has none yet, which is a
 * fact about the row rather than a missing success.
 */
function outcomeLabel(entry: JournalEntryData): string {
  if (entry.ok === true) return 'Succeeded'
  if (entry.ok === false) return 'Failed'
  if (entry.phase === 'attempt') return 'No outcome on this row — the attempt only.'
  return 'No outcome recorded on this row.'
}

/** Redacted-at-the-source JSON as text, or null when there is nothing to show. */
function toJsonText(value: unknown): string | null {
  if (value === null || value === undefined) return null
  try {
    const text = JSON.stringify(value, null, 2)
    return typeof text === 'string' ? text : null
  } catch {
    return null
  }
}
