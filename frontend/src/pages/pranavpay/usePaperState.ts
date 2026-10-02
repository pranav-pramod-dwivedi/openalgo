import { useCallback, useEffect, useRef, useState } from 'react'

/** One open paper position, marked to market. */
export interface PaperPosition {
  symbol: string
  side: 'BUY' | 'SELL'
  qty: number
  entry: number
  mark: number
  /** False when the mark is the last known price rather than a live one. */
  mark_live?: boolean
  unrealized: number
  strategy_id: string | null
  opened: number | null
}

/** One executed paper fill. */
export interface PaperFill {
  order_id: string
  symbol: string
  side: string
  qty: number
  price: number
  fee: number
  slippage: number
  ts: number
}

export interface PaperEquityPoint {
  ts: number
  cash: number
  equity: number
  realized: number
  unrealized: number
  fees: number
  slippage: number
  drawdown: number
}

export interface PaperStrategy {
  id: string
  family: string
  params: string
  metrics: string
  status: string
  created: number
  version: number
}

export interface PaperWorker {
  ts: number
  status: string
  error: string | null
  cycle: number
}

export interface PaperDecision {
  ts: number
  kind: string
  payload: unknown
}

export interface PaperState {
  starting_cash: number
  /** Live balance, moved by every simulated fill. */
  cash: number
  /** Same figure as `cash`; kept for the page that already reads it. */
  virtual_balance: number
  /** Notional posted as margin against open shorts, not spendable. */
  short_margin_locked: number
  equity: number
  realized: number
  unrealized: number
  fees: number
  peak_equity: number
  drawdown: number
  positions: PaperPosition[]
  fills: PaperFill[]
  equity_curve: PaperEquityPoint[]
  strategies: PaperStrategy[]
  experiment_count: number
  worker: PaperWorker | null
  decisions: PaperDecision[]
  jev_verdicts: number
  generated_at: number
}

/**
 * Backtest metrics as the research cycle stored them: a flat map of the JSON
 * fields that were actually written. Parsed defensively — a stored blob that is
 * not JSON, or a JSON array, yields null rather than an empty object, so the
 * page can say "not reported" instead of printing zeros it invented.
 */
export type PaperMetrics = Record<string, number | string>

/** A finite number, or null. A missing figure is never a zero. */
function num(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

/** Non-empty text, or null. Booleans are readable as words, not as truthiness. */
function text(value: unknown): string | null {
  if (typeof value === 'string') {
    const trimmed = value.trim()
    return trimmed.length > 0 ? trimmed : null
  }
  if (typeof value === 'number' && Number.isFinite(value)) return String(value)
  if (typeof value === 'boolean') return value ? 'yes' : 'no'
  return null
}

/**
 * The metrics field is JSON text in the ledger and an object on the wire, so
 * both are accepted. Anything else — a bare number, an array, unparseable text —
 * is reported as absent.
 */
export function parseMetrics(raw: unknown): PaperMetrics | null {
  let source: unknown = raw
  if (typeof raw === 'string') {
    const trimmed = raw.trim()
    if (trimmed.length === 0) return null
    try {
      source = JSON.parse(trimmed)
    } catch {
      return null
    }
  }
  if (source === null || typeof source !== 'object' || Array.isArray(source)) return null
  const out: PaperMetrics = {}
  for (const [key, value] of Object.entries(source as Record<string, unknown>)) {
    const parsed = num(value) ?? text(value)
    if (parsed !== null) out[key] = parsed
  }
  return Object.keys(out).length > 0 ? out : null
}

/** One number out of a metrics map, or null when the engine did not store it. */
export function metricOf(metrics: PaperMetrics | null, key: string): number | null {
  if (!metrics) return null
  return num(metrics[key])
}

/**
 * The trade plan a planner decision carries, flattened for display.
 *
 * Every field is nullable because the engine may not have stored it, and the
 * page renders "not reported" for a null rather than filling the gap. `raw` is
 * kept so a fact this shape does not name is still readable.
 */
export interface PaperPlan {
  ts: number | null
  kind: string
  symbol: string | null
  side: string | null
  qty: number | null
  entry_price: number | null
  stop_loss: number | null
  take_profit: number | null
  risk_usd: number | null
  reward_usd: number | null
  rr: number | null
  strategy_id: string | null
  jev_verdict: string | null
  /** The analyst's probability of reaching the target, when the plan states one. */
  p_take: number | null
  /** False when the plan was built without the analyst. Null when not stated. */
  analyst_available: boolean | null
  reasoning: string | null
  refusal_reason: string | null
  metrics: PaperMetrics | null
  refused: boolean
}

/** Keys that make a logged payload a plan rather than some other event. */
const PLAN_KEYS = new Set([
  'qty',
  'entry_price',
  'stop_loss',
  'take_profit',
  'risk_usd',
  'reward_usd',
  'rr',
  'strategy_id',
  'metrics',
  'jev_verdict',
  'reasoning',
  'refusal_reason',
  'analyst_available',
])

/**
 * Events the ledger also logs that share a plan's vocabulary without being one.
 *
 * `jev` rows carry a symbol, a state and a verdict; `paper_refused` carries a
 * symbol, a side and a reason. Left to the key test alone they would be read as
 * a plan with nothing in it and would take over the panel from the real one, so
 * they are named here and skipped whatever they carry.
 */
const NOT_A_PLAN = new Set([
  'jev',
  'worker_skipped',
  'worker_error',
  'research_start',
  'strategy_registered',
  'paper_order',
  'paper_close',
  'paper_refused',
  'equity',
])

/** Probability fields the analyst may write, most specific first. */
const P_TAKE_KEYS = ['p_take', 'p_win', 'p_take_probability', 'probability', 'p']

/** Verdict labels a verdict object may carry, most specific first. */
const VERDICT_KEYS = ['verdict', 'action', 'label', 'decision', 'outcome']

/** The JEV verdict is either a word or an object with a word and a probability. */
function verdictOf(raw: unknown): { verdict: string | null; pTake: number | null } {
  if (raw === null || raw === undefined) return { verdict: null, pTake: null }
  if (typeof raw !== 'object' || Array.isArray(raw)) {
    return { verdict: text(raw), pTake: null }
  }
  const source = raw as Record<string, unknown>
  let verdict: string | null = null
  for (const key of VERDICT_KEYS) {
    const found = text(source[key])
    if (found !== null) {
      verdict = found
      break
    }
  }
  let pTake: number | null = null
  for (const key of P_TAKE_KEYS) {
    const found = num(source[key])
    if (found !== null) {
      pTake = found
      break
    }
  }
  return { verdict, pTake }
}

/**
 * Flatten one decision's payload into a plan, or null when it is not one.
 *
 * A plan may sit at the top of the payload or nested under `plan`, so both are
 * accepted. `kind` alone never makes a payload a plan — a payload with none of
 * the plan keys is some other event and is left alone, and the events listed in
 * `NOT_A_PLAN` are skipped by name.
 */
export function parsePlan(decision: PaperDecision): PaperPlan | null {
  if (NOT_A_PLAN.has(decision.kind)) return null
  const payload = decision.payload
  if (payload === null || typeof payload !== 'object' || Array.isArray(payload)) return null
  const outer = payload as Record<string, unknown>

  const nested = outer.plan
  const nestedIsObject =
    nested !== null && typeof nested === 'object' && !Array.isArray(nested)
      ? (nested as Record<string, unknown>)
      : null
  const candidates = [outer, nestedIsObject].filter(Boolean) as Record<string, unknown>[]
  const body = candidates.find((candidate) =>
    Object.keys(candidate).some((key) => PLAN_KEYS.has(key))
  )
  if (!body) return null

  const jev = verdictOf(body.jev_verdict)
  const refusal = text(body.refusal_reason)
  const metrics = parseMetrics(body.metrics)
  const pTake =
    num(body.p_take) ??
    num(body.p_take_probability) ??
    jev.pTake ??
    (metrics ? metricOf(metrics, 'p_take') : null)

  return {
    ts: num(decision.ts),
    kind: decision.kind,
    symbol: text(body.symbol),
    side: text(body.side),
    qty: num(body.qty),
    entry_price: num(body.entry_price),
    stop_loss: num(body.stop_loss),
    take_profit: num(body.take_profit),
    risk_usd: num(body.risk_usd),
    reward_usd: num(body.reward_usd),
    rr: num(body.rr),
    strategy_id: text(body.strategy_id),
    jev_verdict: jev.verdict,
    p_take: pTake,
    analyst_available: typeof body.analyst_available === 'boolean' ? body.analyst_available : null,
    reasoning: text(body.reasoning),
    refusal_reason: refusal,
    metrics,
    refused: refusal !== null,
  }
}

/**
 * The most recent plan in the audit trail.
 *
 * The API returns decisions newest first, but ordering is re-established here
 * from the timestamps so a payload list that arrives the other way round still
 * resolves to the newest plan. Null when no decision carries a plan, which is a
 * different thing from a plan that was refused — the page tells them apart.
 */
export function latestPlan(decisions: PaperDecision[] | undefined): PaperPlan | null {
  if (!decisions || decisions.length === 0) return null
  let best: PaperPlan | null = null
  let bestTs = Number.NEGATIVE_INFINITY
  for (const decision of decisions) {
    const plan = parsePlan(decision)
    if (!plan) continue
    const ts = plan.ts ?? Number.NEGATIVE_INFINITY
    // `>=` keeps the later-listed entry on a tie, which is the newer row when
    // two plans share a timestamp.
    if (ts >= bestTs) {
      best = plan
      bestTs = ts
    }
  }
  return best
}

export interface PaperSnapshot {
  state: PaperState | null
  loading: boolean
  /** True once a fetch has failed; a previously loaded state keeps showing. */
  stale: boolean
  error: string | null
  updatedAt: Date | null
  refresh: () => void
}

/**
 * The paper trading engine's own ledger.
 *
 * Every account figure on the surface comes from here, because paper trading
 * is permanent and this portfolio *is* the user's account. Nothing is derived
 * from the exchange balance and nothing is invented locally: an empty engine
 * returns zeros and empty lists, and the pages say so rather than filling the
 * gap.
 */
export function usePaperState(): PaperSnapshot {
  const [state, setState] = useState<PaperState | null>(null)
  const [loading, setLoading] = useState(true)
  const [stale, setStale] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [updatedAt, setUpdatedAt] = useState<Date | null>(null)
  const [nonce, setNonce] = useState(0)
  const aliveRef = useRef(true)

  useEffect(() => {
    aliveRef.current = true
    return () => {
      aliveRef.current = false
    }
  }, [])

  const refresh = useCallback(() => setNonce((n) => n + 1), [])

  useEffect(() => {
    void nonce
    let cancelled = false

    const load = async () => {
      try {
        const response = await fetch('/api/paper/state', { credentials: 'include' })
        if (!response.ok) throw new Error(String(response.status))
        const body = await response.json()
        if (cancelled) return
        if (body?.status === 'success' && body.data) {
          setState(body.data as PaperState)
          setError(null)
          setStale(false)
          setUpdatedAt(new Date())
        } else {
          setError(body?.message ?? 'The paper ledger did not answer.')
        }
      } catch {
        if (!cancelled) {
          setStale(true)
          setError('The paper trading engine did not answer.')
        }
      }
      if (aliveRef.current) setLoading(false)
    }

    void load()
    const timer = setInterval(load, 15_000)
    return () => {
      cancelled = true
      clearInterval(timer)
    }
  }, [nonce])

  return { state, loading, stale, error, updatedAt, refresh }
}
