import { useMemo } from 'react'
import { EmptyNote, LoadingNote } from '../components'
import { formatTime, money, percent, qty, relativeTime, signedMoney } from '../derive'
import type { PaperPlan, PaperStrategy } from '../usePaperState'
import { latestPlan, metricOf, parseMetrics, usePaperState } from '../usePaperState'

/** Epoch seconds from the engine, as a Date the formatters accept. */
const at = (ts: number | null | undefined): Date | null =>
  typeof ts === 'number' && Number.isFinite(ts) ? new Date(ts * 1000) : null

/** Shown wherever the engine stored nothing, so a blank never reads as a zero. */
const NOT_REPORTED = 'not reported'

/**
 * The three backtest figures the panel reads, each independently nullable.
 *
 * A strategy that stored a net P&L but no drawdown shows the P&L and says the
 * drawdown was not reported. Nothing here substitutes a default, because a
 * default drawdown of zero is a claim about the strategy that was never made.
 */
interface StrategyMetrics {
  trades: number | null
  net: number | null
  drawdown: number | null
}

function metricsOf(strategy: PaperStrategy): StrategyMetrics {
  const metrics = parseMetrics(strategy.metrics)
  return {
    trades: metricOf(metrics, 'trades'),
    net: metricOf(metrics, 'net_pnl'),
    drawdown: metricOf(metrics, 'max_drawdown'),
  }
}

/** One metric cell: the stored number, or an explicit "not reported". */
function metricCell(value: number | null, format: (n: number) => string): string {
  return value === null ? NOT_REPORTED : format(value)
}

/** A count, written plainly so a fractional trade count is not hidden. */
function countText(value: number | null): string {
  return value === null ? NOT_REPORTED : qty(value)
}

/** Risk and reward are dollars on a plan; a ratio is shown to two decimals. */
function ratio(value: number | null): string {
  return value === null ? NOT_REPORTED : `${value.toFixed(2)}R`
}

/**
 * p(take) as the analyst stated it: a probability when it stored one, and
 * whatever scale it used left alone rather than rescaled into a guess.
 */
function probabilityText(value: number | null): string {
  if (value === null) return NOT_REPORTED
  if (value > 0 && value <= 1) return `${(value * 100).toFixed(1)}%`
  return `${value.toFixed(1)}%`
}

/** The direction of a plan, from the side it recorded. */
function directionText(plan: PaperPlan): string {
  if (plan.side === null) return NOT_REPORTED
  const side = plan.side.toUpperCase()
  if (side === 'BUY') return 'Long'
  if (side === 'SELL') return 'Short'
  return side
}

/**
 * Paper trading.
 *
 * A simulation engine, not a wallet. Nothing here moves real money, so the page
 * says that at the top rather than in a footnote, and every figure is read from
 * the engine's ledger — the exchange balance is a separate account and is never
 * mixed into these numbers.
 */
export default function PaperPage() {
  const { state, loading, stale, error, updatedAt, refresh } = usePaperState()

  const activeStrategies = useMemo(
    () => (state?.strategies ?? []).filter((s) => s.status === 'active'),
    [state?.strategies]
  )

  /**
   * Every strategy the ledger returned, not only the active ones: a retired or
   * failing strategy is exactly what an operator wants to see ranked against
   * the ones being traded. Sorted by stored net P&L descending, with strategies
   * that reported no net P&L last — an absent figure never sorts as a zero and
   * never outranks a real loss.
   */
  const rankedStrategies = useMemo(() => {
    const rows = (state?.strategies ?? []).map((strategy) => ({
      strategy,
      metrics: metricsOf(strategy),
    }))
    return rows.sort((a, b) => {
      const left = a.metrics.net
      const right = b.metrics.net
      if (left === null && right === null) return 0
      if (left === null) return 1
      if (right === null) return -1
      return right - left
    })
  }, [state?.strategies])

  const plan = useMemo(() => latestPlan(state?.decisions), [state?.decisions])

  /** The strategy a plan named, so its backtest record can sit under the plan. */
  const planStrategy = useMemo(() => {
    if (!plan?.strategy_id) return null
    return (state?.strategies ?? []).find((s) => s.id === plan.strategy_id) ?? null
  }, [plan?.strategy_id, state?.strategies])

  if (loading && !state) {
    return (
      <div className="content-wrap page-view is-visible">
        <LoadingNote label="Reading the paper ledger" />
      </div>
    )
  }

  if (!state) {
    return (
      <div className="content-wrap page-view is-visible" data-page="Paper">
        <section className="page-intro page-intro-inner">
          <div>
            <p className="eyebrow">
              Paper <span className="eyebrow-line" /> Simulation
            </p>
            <h1>
              Nothing to
              <br />
              <span className="period">report yet.</span>
            </h1>
          </div>
        </section>
        <section className="surface-card">
          <EmptyNote
            title="The paper engine is not answering"
            detail={
              error ?? 'The simulation engine has not reported a state. Try again in a moment.'
            }
          />
          <div className="pp-paper-actions">
            <button type="button" className="text-button" onClick={refresh}>
              Try again <span>→</span>
            </button>
          </div>
        </section>
      </div>
    )
  }

  const positions = state.positions

  /**
   * Equity is stored by the engine. If the payload carries none, the page says
   * so — it does not add cash and open P&L together to produce a number the
   * engine never reported, which would also hide a wrong `cash`.
   */
  const equity =
    typeof state.equity === 'number' && Number.isFinite(state.equity) ? state.equity : null
  const drawdown =
    typeof state.drawdown === 'number' && Number.isFinite(state.drawdown) ? state.drawdown : null

  return (
    <div className="content-wrap page-view is-visible" data-page="Paper">
      <section className="page-intro page-intro-inner">
        <div>
          <p className="eyebrow">
            Paper <span className="eyebrow-line" /> Simulation
          </p>
          <h1>
            Practice money,
            <br />
            <span className="period">real mechanics.</span>
          </h1>
          <p className="intro-copy">
            The engine below places simulated orders against live prices. No funds are deposited, no
            exchange account is touched.
          </p>
        </div>
      </section>

      <output className="pp-paper-banner">
        PAPER TRADING — VIRTUAL MONEY. None of these balances exist on any exchange.
      </output>

      {stale && (
        <output className="pp-stale">
          The last refresh did not reach the paper engine. These are the last figures received.
        </output>
      )}

      <section className="pp-paper-grid">
        <article className="surface-card pp-paper-balance">
          <span className="card-label">Virtual balance</span>
          <strong className="pp-paper-amount">{money(state.cash)}</strong>
          <small>
            Cash left from {money(state.starting_cash)} of starting capital, after every simulated
            fill.
          </small>
        </article>
        <article className="surface-card pp-paper-balance">
          <span className="card-label">Equity</span>
          <strong className="pp-paper-amount">
            {equity === null ? 'not computed yet' : money(equity)}
          </strong>
          <small>
            Balance plus {signedMoney(state.unrealized)} of open P&amp;L, less{' '}
            {money(state.short_margin_locked)} margin held against shorts. Peak{' '}
            {money(state.peak_equity)}, drawdown {metricCell(drawdown, percent)}.
          </small>
        </article>
        <article className="surface-card pp-paper-balance">
          <span className="card-label">Realized</span>
          <strong className="pp-paper-amount">{signedMoney(state.realized)}</strong>
          <small>{money(state.fees)} of fees charged across every simulated fill.</small>
        </article>
        <article className="surface-card pp-paper-balance">
          <span className="card-label">Unrealized</span>
          <strong className="pp-paper-amount">{signedMoney(state.unrealized)}</strong>
          <small>
            Marked to the live price of {positions.length}{' '}
            {positions.length === 1 ? 'open position' : 'open positions'}.
          </small>
        </article>
      </section>

      <section className="surface-card pp-paper-section">
        <LatestPlan plan={plan} strategy={planStrategy} />
      </section>

      <section className="surface-card pp-paper-section">
        <div className="section-heading compact-heading">
          <div>
            <span className="card-label">Live book</span>
            <h2>Open positions</h2>
          </div>
          <span className="wallet-status">
            <span className="live-dot" /> {positions.length} open
          </span>
        </div>
        {positions.length === 0 ? (
          <EmptyNote
            title="No open positions"
            detail="The engine holds nothing right now. A position appears here the moment a strategy opens one."
          />
        ) : (
          <div className="pp-paper-table-wrap">
            <table className="pp-paper-table">
              <thead>
                <tr>
                  <th scope="col">Symbol</th>
                  <th scope="col">Side</th>
                  <th scope="col">Qty</th>
                  <th scope="col">Entry</th>
                  <th scope="col">Mark</th>
                  <th scope="col">Unrealized</th>
                  <th scope="col">Strategy</th>
                </tr>
              </thead>
              <tbody>
                {positions.map((position) => (
                  <tr key={position.symbol}>
                    <td>
                      <strong>{position.symbol}</strong>
                    </td>
                    <td>{position.side}</td>
                    <td>{qty(position.qty)}</td>
                    <td>{money(position.entry)}</td>
                    <td>
                      {position.mark_live === false ? (
                        <span title="The exchange feed is not responding, so this is the last known price">
                          {money(position.mark)} (stale)
                        </span>
                      ) : (
                        money(position.mark)
                      )}
                    </td>
                    <td className={position.unrealized < 0 ? 'is-negative' : 'is-positive'}>
                      {signedMoney(position.unrealized)}
                    </td>
                    <td>{position.strategy_id ?? '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <section className="pp-paper-two">
        <article className="surface-card pp-paper-section">
          <div className="section-heading compact-heading">
            <div>
              <span className="card-label">Research</span>
              <h2>Active strategies</h2>
            </div>
            <span className="wallet-status">{state.experiment_count} experiments run</span>
          </div>
          {activeStrategies.length === 0 ? (
            <EmptyNote
              title="No strategy is active"
              detail={`The engine has run ${state.experiment_count} experiments so far and promoted none. A strategy appears here after its backtest validates.`}
            />
          ) : (
            <ul className="pp-paper-strategies">
              {activeStrategies.map((strategy) => {
                const m = metricsOf(strategy)
                return (
                  <li key={strategy.id}>
                    <strong>{strategy.id}</strong>
                    <small>{strategy.family}</small>
                    <span>
                      {m.trades === null && m.net === null && m.drawdown === null
                        ? 'No stored backtest metrics for this strategy.'
                        : `${countText(m.trades)} backtest trades, ${metricCell(m.net, signedMoney)} net, ${metricCell(m.drawdown, percent)} max drawdown`}
                    </span>
                  </li>
                )
              })}
            </ul>
          )}
        </article>

        <article className="surface-card pp-paper-section">
          <div className="section-heading compact-heading">
            <div>
              <span className="card-label">Engine</span>
              <h2>Worker status</h2>
            </div>
            <span className="wallet-status">{state.jev_verdicts} AI verdicts</span>
          </div>
          {state.worker ? (
            <dl className="pp-paper-facts">
              <div>
                <dt>Status</dt>
                <dd>{state.worker.status}</dd>
              </div>
              <div>
                <dt>Cycle</dt>
                <dd>{state.worker.cycle}</dd>
              </div>
              <div>
                <dt>Last beat</dt>
                <dd>
                  {state.worker.ts
                    ? `${formatTime(at(state.worker.ts))} (${relativeTime(at(state.worker.ts))})`
                    : 'Never'}
                </dd>
              </div>
              {state.worker.error ? (
                <div>
                  <dt>Last error</dt>
                  <dd>{state.worker.error}</dd>
                </div>
              ) : null}
            </dl>
          ) : (
            <EmptyNote
              title="No heartbeat recorded"
              detail="The paper worker has not checked in yet. The figures above are whatever the ledger last stored."
            />
          )}
        </article>
      </section>

      <section className="surface-card pp-paper-section">
        <div className="section-heading compact-heading">
          <div>
            <span className="card-label">Research</span>
            <h2>Strategy performance</h2>
          </div>
          <span className="wallet-status">
            {rankedStrategies.length} {rankedStrategies.length === 1 ? 'strategy' : 'strategies'},
            best net first
          </span>
        </div>
        {rankedStrategies.length === 0 ? (
          <EmptyNote
            title="No strategy has been registered"
            detail={`The engine has run ${state.experiment_count} experiments so far and registered none. A row appears here once a backtest validates and is stored.`}
          />
        ) : (
          <div className="pp-paper-table-wrap">
            <table className="pp-paper-table">
              <thead>
                <tr>
                  <th scope="col">Strategy</th>
                  <th scope="col">Family</th>
                  <th scope="col">Status</th>
                  <th scope="col">Trades</th>
                  <th scope="col">Net P&amp;L</th>
                  <th scope="col">Max drawdown</th>
                </tr>
              </thead>
              <tbody>
                {rankedStrategies.map(({ strategy, metrics }) => (
                  <tr key={strategy.id}>
                    <td>
                      <strong>{strategy.id}</strong>
                    </td>
                    <td>{strategy.family || NOT_REPORTED}</td>
                    <td>{strategy.status || NOT_REPORTED}</td>
                    <td>{countText(metrics.trades)}</td>
                    <td
                      className={
                        metrics.net === null ? '' : metrics.net < 0 ? 'is-negative' : 'is-positive'
                      }
                    >
                      {metricCell(metrics.net, signedMoney)}
                    </td>
                    <td>{metricCell(metrics.drawdown, percent)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <section className="surface-card pp-paper-section">
        <div className="section-heading compact-heading">
          <div>
            <span className="card-label">Audit trail</span>
            <h2>Recent decisions</h2>
          </div>
          <span className="wallet-status">
            <span className="live-dot" /> {state.decisions.length} logged
          </span>
        </div>
        {state.decisions.length === 0 ? (
          <EmptyNote
            title="Nothing logged yet"
            detail="Every signal the engine acts on is written here. No entries means no cycles have run."
          />
        ) : (
          <ul className="pp-paper-decisions">
            {state.decisions.map((decision, index) => (
              <li key={`${decision.ts}-${index}`}>
                <span className="pp-paper-kind">{decision.kind}</span>
                <span className="pp-paper-payload">{summarize(decision.payload)}</span>
                <time>{relativeTime(at(decision.ts))}</time>
              </li>
            ))}
          </ul>
        )}
      </section>

      {state.fills.length > 0 && (
        <section className="surface-card pp-paper-section">
          <div className="section-heading compact-heading">
            <div>
              <span className="card-label">Fills</span>
              <h2>Last {state.fills.length} simulated fills</h2>
            </div>
          </div>
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
                </tr>
              </thead>
              <tbody>
                {state.fills.map((fill) => (
                  <tr key={fill.order_id}>
                    <td>{formatTime(at(fill.ts))}</td>
                    <td>
                      <strong>{fill.symbol}</strong>
                    </td>
                    <td>{fill.side}</td>
                    <td>{qty(fill.qty)}</td>
                    <td>{money(fill.price)}</td>
                    <td>{money(fill.fee)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}

      <div className="pp-note pp-note-quiet">
        {updatedAt ? `Read from the engine ${relativeTime(updatedAt)}.` : 'Not yet read.'}
      </div>
    </div>
  )
}

/**
 * The planner's most recent decision, in full.
 *
 * A refusal is the common case and is not a failure of the panel: the reason
 * the planner declined is the whole content of the panel, so it is stated in
 * full rather than left as an empty box that reads as "nothing to report".
 * Every figure is the engine's own; the panel computes nothing.
 */
function LatestPlan({
  plan,
  strategy,
}: {
  plan: PaperPlan | null
  strategy: PaperStrategy | null
}) {
  if (!plan) {
    return (
      <>
        <div className="section-heading compact-heading">
          <div>
            <span className="card-label">Planner</span>
            <h2>Latest plan</h2>
          </div>
          <span className="wallet-status">No plan yet</span>
        </div>
        <EmptyNote
          title="The planner has not produced a plan yet"
          detail="Every cycle asks the planner for a trade, and each plan it returns — taken or refused — is written here. Nothing is shown until one is."
        />
      </>
    )
  }

  const when = at(plan.ts)
  const planMetrics = plan.metrics ?? (strategy ? parseMetrics(strategy.metrics) : null)

  if (plan.refused) {
    return (
      <>
        <div className="section-heading compact-heading">
          <div>
            <span className="card-label">Planner</span>
            <h2>Why no trade</h2>
          </div>
          <span className="wallet-status">
            Refused {when ? relativeTime(when) : 'at an unknown time'}
          </span>
        </div>
        <div className="pp-plan-refusal">
          <p className="pp-plan-refusal-reason">{plan.refusal_reason}</p>
          <p className="pp-plan-refusal-note">
            The planner declined this cycle and no order was placed. It will ask again on the next
            cycle.
          </p>
        </div>
        <dl className="pp-paper-facts pp-plan-facts">
          <div>
            <dt>Symbol considered</dt>
            <dd>{plan.symbol ?? NOT_REPORTED}</dd>
          </div>
          <div>
            <dt>Direction</dt>
            <dd>{directionText(plan)}</dd>
          </div>
          <div>
            <dt>Strategy</dt>
            <dd>{plan.strategy_id ?? NOT_REPORTED}</dd>
          </div>
          <div>
            <dt>Jev verdict</dt>
            <dd>{plan.jev_verdict ?? NOT_REPORTED}</dd>
          </div>
          <div>
            <dt>p(take)</dt>
            <dd>{probabilityText(plan.p_take)}</dd>
          </div>
          <div>
            <dt>Analyst available</dt>
            <dd>
              {plan.analyst_available === null
                ? NOT_REPORTED
                : plan.analyst_available
                  ? 'yes'
                  : 'no — the analyst did not answer'}
            </dd>
          </div>
        </dl>
        <ReasoningBlock reasoning={plan.reasoning} label="Why the planner stopped here" />
      </>
    )
  }

  return (
    <>
      <div className="section-heading compact-heading">
        <div>
          <span className="card-label">Planner</span>
          <h2>Latest plan</h2>
        </div>
        <span className="wallet-status">
          {when ? `${formatTime(when)} · ${relativeTime(when)}` : 'Time not reported'}
        </span>
      </div>

      <div className="pp-plan-head">
        <strong>{plan.symbol ?? NOT_REPORTED}</strong>
        <span className={`pp-plan-side is-${(plan.side ?? 'none').toLowerCase()}`}>
          {directionText(plan)}
        </span>
        {plan.qty === null ? null : <span className="pp-plan-qty">{qty(plan.qty)} units</span>}
      </div>

      <dl className="pp-paper-facts pp-plan-facts">
        <div>
          <dt>Strategy</dt>
          <dd>
            {plan.strategy_id ?? NOT_REPORTED}
            {strategy?.family ? ` · ${strategy.family}` : ''}
          </dd>
        </div>
        <div>
          <dt>Entry</dt>
          <dd>{metricCell(plan.entry_price, money)}</dd>
        </div>
        <div>
          <dt>Stop loss</dt>
          <dd>{metricCell(plan.stop_loss, money)}</dd>
        </div>
        <div>
          <dt>Target</dt>
          <dd>{metricCell(plan.take_profit, money)}</dd>
        </div>
        <div>
          <dt>Risk</dt>
          <dd>{metricCell(plan.risk_usd, money)}</dd>
        </div>
        <div>
          <dt>Reward</dt>
          <dd>{metricCell(plan.reward_usd, money)}</dd>
        </div>
        <div>
          <dt>R:R</dt>
          <dd>{ratio(plan.rr)}</dd>
        </div>
        <div>
          <dt>Jev verdict</dt>
          <dd>{plan.jev_verdict ?? NOT_REPORTED}</dd>
        </div>
        <div>
          <dt>p(take)</dt>
          <dd>{probabilityText(plan.p_take)}</dd>
        </div>
        <div>
          <dt>Analyst available</dt>
          <dd>
            {plan.analyst_available === null ? NOT_REPORTED : plan.analyst_available ? 'yes' : 'no'}
          </dd>
        </div>
      </dl>

      <div className="pp-plan-metrics">
        <span className="card-label">Backtest record</span>
        <div className="pp-paper-table-wrap">
          <table className="pp-paper-table">
            <thead>
              <tr>
                <th scope="col">Trades</th>
                <th scope="col">Net P&amp;L</th>
                <th scope="col">Max drawdown</th>
              </tr>
            </thead>
            <tbody>
              <tr>
                <td>{countText(metricOf(planMetrics, 'trades'))}</td>
                <td>{metricCell(metricOf(planMetrics, 'net_pnl'), signedMoney)}</td>
                <td>{metricCell(metricOf(planMetrics, 'max_drawdown'), percent)}</td>
              </tr>
            </tbody>
          </table>
        </div>
        <small>
          {planMetrics
            ? 'From the backtest the research cycle stored for this strategy.'
            : 'No backtest metrics are stored for this strategy, so there is nothing to rank it against.'}
        </small>
      </div>

      <ReasoningBlock reasoning={plan.reasoning} label="Why this trade" />
    </>
  )
}

/**
 * The planner's own words, collapsed so the panel reads as a summary and opens
 * into the reasoning. Verbatim: it is the audit trail, and paraphrasing it here
 * would make the panel a second opinion rather than a record.
 */
function ReasoningBlock({ reasoning, label }: { reasoning: string | null; label: string }) {
  if (reasoning === null) {
    return <p className="pp-why-missing">The planner stored no reasoning text with this plan.</p>
  }
  return (
    <details className="pp-why">
      <summary>{label}</summary>
      <p className="pp-why-body">{reasoning}</p>
    </details>
  )
}

/** One readable line for a logged payload, whatever shape the engine stored. */
function summarize(payload: unknown): string {
  if (payload === null || payload === undefined) return '—'
  if (typeof payload === 'string') return payload
  if (typeof payload !== 'object') return String(payload)
  const entries = Object.entries(payload as Record<string, unknown>).filter(
    ([key]) => key !== 'state'
  )
  if (entries.length === 0) return '—'
  return entries
    .map(([key, value]) => `${key}: ${typeof value === 'number' ? qty(value) : String(value)}`)
    .join(' · ')
}
