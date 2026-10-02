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

/**
 * One executed paper fill.
 *
 * The first seven fields are what the ledger has always stored. The ones below
 * them are optional: the execution model may report how a fill was priced and
 * how much of the order was left over, and until it does every one of them
 * reads as `not reported` rather than as a zero.
 */
export interface PaperFill {
  order_id: string
  symbol: string
  side: string
  qty: number
  price: number
  fee: number
  slippage: number
  ts: number
  /** Which side of the book the fill was priced on: `ask`, `bid` or `mid`. */
  fill_basis?: string | null
  /** The gap between the best buy and best sell price when the fill happened. */
  spread?: number | null
  /** The gap in basis points, when the model reports it that way. */
  spread_bps?: number | null
  /** The delay the model added between deciding and filling, in milliseconds. */
  latency_ms?: number | null
  /** How much of the order actually traded. */
  filled_qty?: number | null
  /** How much of the order never traded. */
  unfilled_qty?: number | null
  /** The same remainder under another name, in case the model uses this one. */
  remaining_qty?: number | null
  /** A nested block, when the execution model returns its detail as one object. */
  execution?: Record<string, unknown> | null
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
  /**
   * The verification layer's recent verdicts, one row per trade it checked.
   *
   * Optional. The table that holds them may not exist yet, in which case this
   * key is simply absent and every figure read from it is "not reported".
   */
  verdicts?: PaperVerdictWire[] | null
  /** How many trades the checks have allowed, over all time. */
  allowed_count?: number | null
  /** How many trades the checks have denied, over all time. */
  denied_count?: number | null
  /** Orders the execution model itself refused, most recent first. */
  rejections?: PaperRejectionWire[] | null
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

/* ------------------------------------------------------------------------- *
 * The verification layer and the execution detail.
 *
 * Both arrive on the same payload as the ledger, but neither is guaranteed to
 * be there: the checks that produce a verdict and the execution model that
 * prices a fill are separate pieces of work, and this page may well be running
 * before either of them ships.
 *
 * So every field below is nullable, and the distinction the page depends on is
 * kept explicitly: `reported: false` means the key was not in the payload at
 * all, which is not the same as a key that was there and said zero. A missing
 * key must never render as a clean sheet of zeroes.
 * ------------------------------------------------------------------------- */

/** The wire shape of one verdict row, as it arrives. Nothing is assumed. */
export type PaperVerdictWire = Record<string, unknown>

/** The wire shape of one execution rejection, as it arrives. */
export type PaperRejectionWire = Record<string, unknown>

/** One named check inside a verdict, and how it came out. */
export interface PaperCheck {
  /** The check's own name, as the engine recorded it. Null when unnamed. */
  name: string | null
  /** True or false. Null when the engine did not state the outcome. */
  passed: boolean | null
  /** The engine's own sentence about this check, when it stored one. */
  detail: string | null
}

/** One verdict: did the checks allow this trade, and if not, why not. */
export interface PaperVerdict {
  ts: number | null
  symbol: string | null
  side: string | null
  /** True allowed, false denied, null when the engine stated neither. */
  allowed: boolean | null
  /** Every reason the row gave, whatever shape each one arrived in. */
  reasons: string[]
  checks: PaperCheck[]
}

/** One order the execution model refused. */
export interface PaperRejection {
  ts: number | null
  symbol: string | null
  side: string | null
  qty: number | null
  /** The engine's own words, or null when it recorded none. */
  reason: string | null
}

/** The verification layer as the page reads it. */
export interface PaperVerification {
  /** False when the payload carried no `verdicts` key at all. */
  verdictsReported: boolean
  verdicts: PaperVerdict[]
  /** False when the payload carried no `rejections` key at all. */
  rejectionsReported: boolean
  rejections: PaperRejection[]
  /** Over all time, not just the rows returned. Null when unreported. */
  allowedCount: number | null
  deniedCount: number | null
}

/** Keys a verdict may use to state its outcome, most specific first. */
const ALLOW_KEYS = [
  'allow',
  'allowed',
  'denied',
  'verdict',
  'decision',
  'outcome',
  'result',
  'status',
]

/** Words that mean the trade was allowed through. */
const ALLOW_WORDS = new Set([
  'allow',
  'allowed',
  'allow_ed',
  'pass',
  'passed',
  'ok',
  'accept',
  'accepted',
  'approve',
  'approved',
  'true',
  'yes',
])

/** Words that mean the trade was stopped. */
const DENY_WORDS = new Set([
  'deny',
  'denied',
  'reject',
  'rejected',
  'block',
  'blocked',
  'fail',
  'failed',
  'refuse',
  'refused',
  'stop',
  'stopped',
  'false',
  'no',
])

/** Keys a reason may arrive under, most specific first. */
const REASON_KEYS = ['reason', 'reasons', 'check', 'code', 'slug', 'message', 'detail', 'why']

/** True only when the payload actually carried the key. */
function carried(value: unknown): boolean {
  return value !== undefined && value !== null
}

/**
 * Whether a verdict allowed the trade, or null when it did not say.
 *
 * A boolean is taken at face value and a word is looked up. Anything else — a
 * number, most of all — is treated as unstated rather than guessed at, because
 * a status code is not an outcome and printing one would be worse than saying
 * nothing.
 */
function allowOf(raw: unknown): boolean | null {
  if (typeof raw === 'boolean') return raw
  if (typeof raw === 'string') {
    const word = raw
      .trim()
      .toLowerCase()
      .replace(/[\s-]+/g, '_')
    if (ALLOW_WORDS.has(word)) return true
    if (DENY_WORDS.has(word)) return false
    return null
  }
  return null
}

/** One reason out of whatever shape it arrived in, or null. */
function reasonOf(raw: unknown): string | null {
  if (typeof raw === 'string') {
    const trimmed = raw.trim()
    return trimmed.length > 0 ? trimmed : null
  }
  if (typeof raw === 'number') return null
  if (raw === null || typeof raw !== 'object' || Array.isArray(raw)) return null
  const source = raw as Record<string, unknown>
  for (const key of REASON_KEYS) {
    const found = text(source[key])
    if (found !== null) return found
  }
  return null
}

/** Every reason a row carries, flattened from either a list or a single value. */
function reasonsOf(raw: unknown): string[] {
  const found: string[] = []
  const push = (value: unknown) => {
    const reason = reasonOf(value)
    if (reason !== null && !found.includes(reason)) found.push(reason)
  }
  if (Array.isArray(raw)) raw.forEach(push)
  else push(raw)
  return found
}

/**
 * The checks inside a verdict, if it carries any.
 *
 * Accepted as a list of objects, a list of bare words, or a single object. A
 * check named but never scored is kept, with `passed: null`, so the page can
 * say the check exists and that its outcome was not reported — which is a
 * different thing from a check that passed.
 */
function checksOf(raw: unknown): PaperCheck[] {
  const rows = Array.isArray(raw) ? raw : carried(raw) ? [raw] : []
  const checks: PaperCheck[] = []
  for (const row of rows) {
    if (typeof row === 'string') {
      const name = row.trim()
      if (name.length > 0) checks.push({ name, passed: null, detail: null })
      continue
    }
    if (row === null || typeof row !== 'object' || Array.isArray(row)) continue
    const source = row as Record<string, unknown>
    const name = text(source.name) ?? text(source.check) ?? text(source.id) ?? text(source.label)
    const passed = allowOf(
      source.passed ?? source.pass ?? source.ok ?? source.result ?? source.status
    )
    checks.push({ name, passed, detail: text(source.detail) ?? text(source.message) })
  }
  return checks
}

/** One verdict row, flattened from whatever the payload called it. */
function verdictRow(row: PaperVerdictWire): PaperVerdict {
  let allowed: boolean | null = null
  for (const key of ALLOW_KEYS) {
    if (!(key in row)) continue
    // `denied` is the mirror of `allowed`, so a truthy value here is a stop.
    const found = allowOf(key === 'denied' ? invert(row[key]) : row[key])
    if (found !== null) {
      allowed = found
      break
    }
  }

  const checks = checksOf(row.checks ?? row.results ?? row.verdicts)
  const reasons = [
    ...reasonsOf(row.reasons ?? row.reason),
    // A failed check is itself a reason, and is the only one a verdict with no
    // `reasons` key ever gives. Named so a trader still reads a reason.
    ...checks.filter((check) => check.passed === false && check.name !== null).map((c) => c.name),
  ]
  if (allowed === false && reasons.length === 0) {
    for (const check of checks) {
      if (check.detail !== null) reasons.push(check.detail)
    }
  }

  return {
    ts: num(row.ts),
    symbol: text(row.symbol),
    side: text(row.side),
    allowed,
    reasons: [...new Set(reasons.filter((reason): reason is string => reason !== null))],
    checks,
  }
}

/** True when the engine recorded something that means "no". */
function invert(value: unknown): unknown {
  if (typeof value === 'boolean') return !value
  if (typeof value === 'string') {
    const word = value.trim().toLowerCase()
    if (DENY_WORDS.has(word)) return true
    if (ALLOW_WORDS.has(word)) return false
    return value
  }
  return value
}

/** One rejection row, flattened. */
function rejectionOf(row: PaperRejectionWire): PaperRejection {
  return {
    ts: num(row.ts),
    symbol: text(row.symbol),
    side: text(row.side),
    qty: num(row.qty),
    reason: text(row.reason) ?? text(row.reasons) ?? text(row.message) ?? text(row.detail),
  }
}

/**
 * The verification layer, as far as the payload describes it.
 *
 * `verdictsReported` and `rejectionsReported` are the two flags the page needs:
 * a payload with no `verdicts` key has not told us whether the checks ran, and
 * that is a different message from one that reported zero verdicts.
 */
export function paperVerification(state: PaperState | null): PaperVerification {
  const verdictsRaw = state?.verdicts
  const rejectionsRaw = state?.rejections
  return {
    verdictsReported: Array.isArray(verdictsRaw),
    verdicts: Array.isArray(verdictsRaw)
      ? verdictsRaw
          .filter((row): row is PaperVerdictWire => row !== null && typeof row === 'object')
          .map(verdictRow)
      : [],
    rejectionsReported: Array.isArray(rejectionsRaw),
    rejections: Array.isArray(rejectionsRaw)
      ? rejectionsRaw
          .filter((row): row is PaperRejectionWire => row !== null && typeof row === 'object')
          .map(rejectionOf)
      : [],
    allowedCount: num(state?.allowed_count),
    deniedCount: num(state?.denied_count),
  }
}

/* ------------------------------------------------------------------------- *
 * Execution detail: what a fill actually was priced against.
 * ------------------------------------------------------------------------- */

/** How a single fill was priced, and how much of the order was left over. */
export interface PaperExecution {
  /** False when the payload described no execution detail for this fill. */
  reported: boolean
  /** `ask`, `bid` or `mid`, as the model recorded it. Null when unreported. */
  basis: string | null
  /** The gap between the best buy and best sell price. Null when unreported. */
  spread: number | null
  /** True when the stored spread is already a percentage. */
  spreadIsPercent: boolean
  /** The delay the model added, in milliseconds. Null when unreported. */
  latencyMs: number | null
  /** How much of the order traded. Null when unreported. */
  filledQty: number | null
  /**
   * How much of the order did not trade. Null when unreported — and a reported
   * zero is a complete fill, which is a real result and not a missing one.
   */
  remainderQty: number | null
  /** `complete` or `partial`, or null when the payload did not say. */
  fill: 'complete' | 'partial' | null
}

/** Where a fill basis may be stored, most specific first. */
const BASIS_KEYS = ['fill_basis', 'basis', 'basis_side', 'reference_side', 'price_side']

/** Where an observed spread may be stored, most specific first. */
const SPREAD_KEYS = ['spread', 'observed_spread', 'spread_value']

/** Where a modelled latency may be stored, most specific first. */
const LATENCY_KEYS = ['latency_ms', 'modelled_latency_ms', 'modeled_latency_ms', 'latency']

/** Where the untraded remainder may be stored, most specific first. */
const REMAINDER_KEYS = [
  'unfilled_qty',
  'remaining_qty',
  'untraded_qty',
  'remainder',
  'leftover_qty',
]

/** Where the traded quantity may be stored, most specific first. */
const FILLED_KEYS = ['filled_qty', 'executed_qty', 'fill_qty']

/**
 * Execution detail for one fill.
 *
 * Read from the fill row itself or from a nested `execution` block, because the
 * execution model may report either. A latency given as a bare number is read
 * as milliseconds; a latency given as an object is read from the first key that
 * names one. Nothing is derived into existence: a remainder only appears when
 * the payload stated one, or when it stated both the order size and the traded
 * size and those two disagree — both operands reported, so the difference is a
 * result and not a guess.
 */
export function parseFillExecution(fill: PaperFill): PaperExecution {
  const nested =
    fill.execution !== null && typeof fill.execution === 'object' && !Array.isArray(fill.execution)
      ? (fill.execution as Record<string, unknown>)
      : {}
  const row = { ...nested, ...fill }

  const filledQty = firstNum(row, FILLED_KEYS)
  const stated = firstNum(row, REMAINDER_KEYS)
  const ordered = firstNum(row, ['qty', 'order_qty', 'requested_qty'])
  const remainderQty =
    stated ?? (filledQty !== null && ordered !== null ? ordered - filledQty : null)

  const basis = firstText(row, BASIS_KEYS)
  const spreadBps = num(nested.spread_bps ?? fill.spread_bps)
  const spread = firstNum(row, SPREAD_KEYS) ?? (spreadBps !== null ? spreadBps / 10_000 : null)

  return {
    reported:
      basis !== null ||
      spread !== null ||
      latencyOf(row) !== null ||
      filledQty !== null ||
      remainderQty !== null,
    basis,
    spread,
    spreadIsPercent: spread !== null && Math.abs(spread) <= 1 && spreadBps === null,
    latencyMs: latencyOf(row),
    filledQty,
    remainderQty,
    fill: fillStatus(row, remainderQty),
  }
}

/** The first numeric value stored under any of these keys. */
function firstNum(source: Record<string, unknown>, keys: string[]): number | null {
  for (const key of keys) {
    const found = num(source[key])
    if (found !== null) return found
  }
  return null
}

/** The first non-empty text stored under any of these keys. */
function firstText(source: Record<string, unknown>, keys: string[]): string | null {
  for (const key of keys) {
    const found = text(source[key])
    if (found !== null) return found
  }
  return null
}

/**
 * Modelled latency in milliseconds.
 *
 * A bare number is milliseconds. An object is searched for the first key that
 * names a millisecond figure, and an object that names one in seconds is
 * scaled, because a latency reported as `0.18` with no unit is ambiguous and
 * printing it as 0.18 ms would understate it a thousandfold.
 */
function latencyOf(source: Record<string, unknown>): number | null {
  const raw = firstNum(source, LATENCY_KEYS)
  if (raw !== null) return raw
  for (const key of LATENCY_KEYS) {
    const value = source[key]
    if (value === null || typeof value !== 'object' || Array.isArray(value)) continue
    const block = value as Record<string, unknown>
    const millis = firstNum(block, ['ms', 'millis', 'latency_ms', 'modelled_ms'])
    if (millis !== null) return millis
    const seconds = firstNum(block, ['s', 'seconds', 'sec'])
    if (seconds !== null) return seconds * 1000
  }
  return null
}

/**
 * Whether the fill was complete or partial.
 *
 * Stated by the payload when it states it. Otherwise a reported remainder
 * settles it, and a remainder left over is the definition of a partial fill. No
 * remainder reported at all leaves it unstated, because an absent key is not an
 * order that traded in full.
 */
function fillStatus(
  source: Record<string, unknown>,
  remainderQty: number | null
): 'complete' | 'partial' | null {
  for (const key of ['fill_status', 'fill_state', 'status', 'state']) {
    const raw = source[key]
    if (typeof raw !== 'string') continue
    const word = raw.trim().toLowerCase()
    if (word.startsWith('part')) return 'partial'
    if (word.startsWith('comp') || word.startsWith('full')) return 'complete'
  }
  if (remainderQty === null) return null
  return remainderQty > 0 ? 'partial' : 'complete'
}

/* ------------------------------------------------------------------------- *
 * Saying a denial in words.
 * ------------------------------------------------------------------------- */

/**
 * Every reason the checks are known to give, written the way a trader reads it.
 *
 * The keys are the slugs the verifier stores. A slug is an internal handle and
 * means nothing on its own, so nothing here is ever shown raw: a matched slug
 * becomes the sentence, and an unmatched one is still turned into words and
 * marked as not spelled out by the engine.
 */
const DENIAL_WORDS: Record<string, string> = {
  price_deviation: 'the price was too far from the live market price for the order to be trusted',
  price_too_far: 'the price was too far from the live market price for the order to be trusted',
  deviation: 'the price was too far from the live market price for the order to be trusted',
  stale_quote: 'the quote the order was priced from was too old to trust',
  stale_price: 'the price the order was priced from was too old to trust',
  stale_data: 'the price data behind the order was too old to trust',
  spread: 'the gap between the best buy price and the best sell price was too wide to fill fairly',
  spread_too_wide:
    'the gap between the best buy price and the best sell price was too wide to fill fairly',
  max_spread:
    'the gap between the best buy price and the best sell price was too wide to fill fairly',
  liquidity: 'there was not enough trading in that coin to fill the order',
  min_liquidity: 'there was not enough trading in that coin to fill the order',
  no_liquidity: 'there was not enough trading in that coin to fill the order',
  insufficient_funds: 'the virtual account did not hold enough cash for the order',
  no_cash: 'the virtual account did not hold enough cash for the order',
  margin: 'the virtual account did not hold enough margin for the order',
  exposure: 'the order would have put more money at risk than the account allows',
  exposure_cap: 'the order would have put more money at risk than the account allows',
  max_exposure: 'the order would have put more money at risk than the account allows',
  position_cap: 'the account was already holding as much of that coin as it allows',
  daily_loss_limit: 'the account had already lost more today than it is allowed to lose',
  loss_limit: 'the account had already lost more than it is allowed to lose',
  drawdown_limit: 'the account had fallen further from its best value than it is allowed to',
  kill_switch: 'trading had been switched off, so nothing was allowed through',
  halted: 'trading in that coin was halted at the exchange',
  circuit: 'the exchange had halted trading in that coin',
  invalid_qty: 'the order size was not one the market can fill',
  bad_qty: 'the order size was not one the market can fill',
  zero_qty: 'the order size was not one the market can fill',
  duplicate: 'the same order had already been sent moments earlier',
  duplicate_order: 'the same order had already been sent moments earlier',
  unknown_symbol: 'that coin is not one the market data feed carries',
  bad_symbol: 'that coin is not one the market data feed carries',
  price_band: 'the order was priced outside the band the exchange accepts',
  market_closed: 'the market was closed, so the order could not be filled at a live price',
  no_price: 'there was no live price to fill the order against',
  missing_price: 'there was no live price to fill the order against',
  verify_failed: 'the independent check on this order did not pass',
  verifier_error: 'the independent check on this order could not be completed',
}

/** The outcome of turning one stored reason into words. */
export interface DenialText {
  /** The sentence to show. Never a raw slug on its own. */
  text: string
  /** False when the engine named the reason without explaining it. */
  spelledOut: boolean
}

/**
 * One denial reason, written for a trader.
 *
 * A reason the engine wrote in its own words is used as it stands — that is the
 * audit trail and paraphrasing it would make the panel an opinion. A slug is
 * looked up in the table above. A slug that is not in the table is still turned
 * into words and marked as not spelled out, because a reader is owed something
 * a person wrote, and "the engine named this reason without explaining it" is
 * true where a bare identifier is not.
 */
export function denialText(reason: string): DenialText {
  const slug = reason
    .trim()
    .toLowerCase()
    .replace(/[\s-]+/g, '_')
  const known = DENIAL_WORDS[slug]
  if (known !== undefined) return { text: known, spelledOut: true }
  const words = reason.trim().replace(/[_-]+/g, ' ').replace(/\s+/g, ' ')
  if (words.length === 0) {
    return { text: 'the engine stopped this order without recording why', spelledOut: false }
  }
  return {
    text: `the engine stopped this order for a reason it named but did not spell out: ${words}`,
    spelledOut: false,
  }
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
