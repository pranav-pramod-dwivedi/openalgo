import { useCallback, useEffect, useState } from 'react'

/**
 * One row of GET /agent/api/audit.
 *
 * Field-for-field what `database/agent_db.py` `audit_to_dict` serialises: id,
 * ts (a UTC ISO string, null when never stamped), phase, tool,
 * conversation_id, run_id, args, risk_verdict, ok, response and order_ids.
 * There is deliberately no reasoning key here: the model's reason-before and
 * reason-after live on `ag_message.reasoning`, which this endpoint never joins
 * in, so a journal entry renders the audit row it was given and nothing else.
 */
export interface JournalEntryData {
  id: number
  ts: string | null
  phase: string
  tool: string
  conversation_id: number | null
  run_id: string | null
  args: unknown
  risk_verdict: string | null
  ok: boolean | null
  response: unknown
  order_ids: string[]
}

export interface JournalState {
  entries: JournalEntryData[]
  /** True only while the first fetch is in flight. */
  loading: boolean
  /** A fetch failure. Null when the last fetch landed, whatever it carried. */
  error: string | null
  /**
   * True when a fetch landed with zero rows: the agent has never made a
   * mutating tool call. Distinct from `error`, which is a fetch that failed.
   */
  empty: boolean
  refresh: () => void
}

/** Read one unknown value the way the endpoint's contract promises it. */
function asString(value: unknown): string | null {
  return typeof value === 'string' ? value : null
}

function asNumber(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

/**
 * One envelope row narrowed to the contract, or null when it is not an audit
 * row at all. Absent optional keys stay null rather than becoming guesses;
 * `order_ids` falls back to an empty list exactly as the serializer does.
 */
function toEntry(raw: unknown): JournalEntryData | null {
  if (typeof raw !== 'object' || raw === null) return null
  const row = raw as Record<string, unknown>
  const id = asNumber(row.id)
  if (id === null) return null
  if (typeof row.tool !== 'string' || typeof row.phase !== 'string') return null
  const orderIds = Array.isArray(row.order_ids)
    ? row.order_ids.filter((orderId): orderId is string => typeof orderId === 'string')
    : []
  return {
    id,
    ts: asString(row.ts),
    phase: row.phase,
    tool: row.tool,
    conversation_id: asNumber(row.conversation_id),
    run_id: asString(row.run_id),
    args: (row.args ?? null) as unknown,
    risk_verdict: asString(row.risk_verdict),
    ok: typeof row.ok === 'boolean' ? row.ok : null,
    response: (row.response ?? null) as unknown,
    order_ids: orderIds,
  }
}

/**
 * Newest first, the way the endpoint already orders them (ts desc, id desc).
 * Rows with no parseable timestamp sort as the oldest, never as the newest.
 */
export function byNewestFirst(a: JournalEntryData, b: JournalEntryData): number {
  const first = Date.parse(a.ts ?? '')
  const second = Date.parse(b.ts ?? '')
  const firstMs = Number.isNaN(first) ? Number.NEGATIVE_INFINITY : first
  const secondMs = Number.isNaN(second) ? Number.NEGATIVE_INFINITY : second
  if (secondMs !== firstMs) return secondMs - firstMs
  return b.id - a.id
}

/**
 * True when the risk layer refused the call. Derived only from the row's own
 * `risk_verdict`: `services/agent/safety/audit.py` writes `block:{code}` for a
 * refused attempt and result pair, and `decision:rejected` for a human refusal.
 */
export function isBlocked(entry: JournalEntryData): boolean {
  const verdict = entry.risk_verdict
  return verdict !== null && (verdict.startsWith('block:') || verdict === 'decision:rejected')
}

/**
 * True when the risk layer or the operator approved the call. Derived only
 * from the row's own `risk_verdict`: `allow:{code}` for a guard pass and
 * `decision:approved` for a human approval.
 */
export function isApproved(entry: JournalEntryData): boolean {
  const verdict = entry.risk_verdict
  return verdict !== null && (verdict.startsWith('allow:') || verdict === 'decision:approved')
}

/**
 * True when the call reached the service and succeeded. Derived only from the
 * row's own `phase` and `ok`: only a `result` row carries an outcome, and only
 * `ok: true` is a success.
 */
export function isExecuted(entry: JournalEntryData): boolean {
  return entry.phase === 'result' && entry.ok === true
}

/**
 * The agent's audit trail, newest call last fetched.
 *
 * Fetches GET /agent/api/audit (limit 50) with the session, the same way the
 * other agent routes are read. Loading, failure and "the agent never ran" are
 * three different states: `loading` is only the first fetch, `error` is only a
 * fetch that failed, and `empty` is only a fetch that landed with zero rows.
 */
export function useJournal(): JournalState {
  const [entries, setEntries] = useState<JournalEntryData[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [nonce, setNonce] = useState(0)

  const refresh = useCallback(() => setNonce((value) => value + 1), [])

  useEffect(() => {
    void nonce
    let cancelled = false
    setLoading(true)

    fetch('/agent/api/audit?limit=50', { credentials: 'include' })
      .then(async (response) => {
        const body: unknown = await response.json().catch(() => null)
        if (!response.ok) {
          const message =
            typeof body === 'object' && body !== null && 'message' in body
              ? asString((body as Record<string, unknown>).message)
              : null
          if (response.status === 401) {
            throw new Error('Your session has expired. Sign in again to read the journal.')
          }
          throw new Error(message ?? 'The journal could not be read. Try again in a moment.')
        }
        return body
      })
      .then((body: unknown) => {
        if (cancelled) return
        const rows =
          typeof body === 'object' && body !== null && 'data' in body
            ? (body as Record<string, unknown>).data
            : null
        if (!Array.isArray(rows)) {
          throw new Error('The journal answered in a shape this page does not understand.')
        }
        const parsed = rows
          .map(toEntry)
          .filter((entry): entry is JournalEntryData => entry !== null)
        setEntries(parsed)
        setError(null)
        setLoading(false)
      })
      .catch((cause: unknown) => {
        if (cancelled) return
        setEntries([])
        setError(
          cause instanceof Error
            ? cause.message
            : 'The journal could not be reached. Check your connection and try again.'
        )
        setLoading(false)
      })

    return () => {
      cancelled = true
    }
  }, [nonce])

  return {
    entries,
    loading,
    error,
    empty: !loading && error === null && entries.length === 0,
    refresh,
  }
}
