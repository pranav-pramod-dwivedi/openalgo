import { useMemo } from 'react'
import { EmptyNote, LoadingNote } from '../components'
import { formatTime, money, percent, qty, relativeTime, signedMoney } from '../derive'
import type { PaperStrategy } from '../usePaperState'
import { usePaperState } from '../usePaperState'

/** Epoch seconds from the engine, as a Date the formatters accept. */
const at = (ts: number | null | undefined): Date | null =>
  typeof ts === 'number' && Number.isFinite(ts) ? new Date(ts * 1000) : null

/**
 * Backtest metrics are stored as the JSON the research cycle wrote. Shown only
 * when they parse; a strategy row never displays a number the engine did not
 * produce.
 */
function metricsOf(strategy: PaperStrategy): { trades: number; net: number; dd: number } | null {
  try {
    const parsed = JSON.parse(strategy.metrics) as Record<string, unknown>
    const num = (key: string): number | null => {
      const value = parsed[key]
      return typeof value === 'number' && Number.isFinite(value) ? value : null
    }
    const trades = num('trades')
    const net = num('net_pnl')
    const dd = num('max_drawdown')
    if (trades === null && net === null && dd === null) return null
    return { trades: trades ?? 0, net: net ?? 0, dd: dd ?? 0 }
  } catch {
    return null
  }
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
          <strong className="pp-paper-amount">{money(state.virtual_balance)}</strong>
          <small>Starting capital {money(state.starting_cash)}, less fees paid.</small>
        </article>
        <article className="surface-card pp-paper-balance">
          <span className="card-label">Equity</span>
          <strong className="pp-paper-amount">{money(state.equity)}</strong>
          <small>
            Balance plus {signedMoney(state.unrealized)} of open P&amp;L. Peak{' '}
            {money(state.peak_equity)}, drawdown {percent(state.drawdown)}.
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
                    <td>{money(position.mark)}</td>
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
                      {m
                        ? `${m.trades} backtest trades, ${signedMoney(m.net)} net, ${percent(m.dd)} max drawdown`
                        : 'No stored backtest metrics for this strategy.'}
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
