import { EmptyNote, LoadingNote, StaleNote, useSnapshot } from '../components'
import { money, percent, qty, signedMoney } from '../derive'
import { toAmounts } from '../useWalletSnapshot'

/**
 * Manage.
 *
 * The prototype showed a risk profile with editable loss guards, volatility
 * filters, approval thresholds and a "patient momentum" strategy. None of those
 * existed behind the buttons, so a trader could have read a guardrail that was
 * never enforced. This page lists only what is genuinely enforced in the order
 * path, and states plainly what is not implemented yet.
 */
export default function ManagePage() {
  const snapshot = useSnapshot()
  const amounts = toAmounts(snapshot.funds)

  if (snapshot.loading && !snapshot.funds) {
    return (
      <div className="content-wrap page-view is-visible">
        <LoadingNote label="Reading your guardrails" />
      </div>
    )
  }

  const equity = amounts?.equity ?? 0
  const maxOrder = amounts?.tradable ?? 0

  return (
    <div className="content-wrap page-view is-visible" data-page="Manage">
      <section className="page-intro page-intro-inner">
        <div>
          <p className="eyebrow">
            Manage <span className="eyebrow-line" /> Your rules
          </p>
          <h1>
            The rules that
            <br />
            <span className="period">actually hold.</span>
          </h1>
          <p className="intro-copy">
            Each limit below is checked on the order path before anything is sent to the exchange.
          </p>
        </div>
      </section>

      {snapshot.stale && <StaleNote />}

      <section className="manage-grid">
        <article className="manage-hero ai-card">
          <div className="ai-card-header">
            <div>
              <span className="card-label inverse-label">Hard limit</span>
              <h2>Savings floor</h2>
            </div>
            <span className="live-label">
              <span className="live-dot" /> Enforced
            </span>
          </div>
          <div className="manage-status-line">
            <div className="manage-status-number">
              {amounts ? money(amounts.floor).replace('$', '') : '—'}
            </div>
            <div>
              <strong>dollars are untouchable</strong>
              <small>The first part of your balance no order can spend</small>
            </div>
          </div>
          <div className="manage-rule-list">
            <div>
              <span>Protected savings</span>
              <strong>{amounts ? money(amounts.savings) : '—'}</strong>
            </div>
            <div>
              <span>Free to trade</span>
              <strong>{amounts ? money(amounts.tradable) : '—'}</strong>
            </div>
            <div>
              <span>Equity in total</span>
              <strong>{equity > 0 ? money(equity) : '—'}</strong>
            </div>
          </div>
          <div className="pp-note pp-note-inverse">
            The floor is set on the server. It cannot be lowered from this page, and it survives
            restarts.
          </div>
        </article>

        <article className="manage-panel surface-card">
          <div className="section-heading compact-heading">
            <div>
              <span className="card-label">Spending cap</span>
              <h2>How much one order may cost</h2>
            </div>
          </div>
          {maxOrder <= 0 ? (
            <EmptyNote
              title="Nothing is free to trade"
              detail="The whole balance is at or below the savings floor, so no new order can be placed."
            />
          ) : (
            <div className="settings-list">
              <div>
                <span>
                  <strong>Largest new order right now</strong>
                  <small>Checked against tradable balance before sending</small>
                </span>
                <b>{money(maxOrder)}</b>
              </div>
              <div>
                <span>
                  <strong>Share of equity</strong>
                  <small>What that cap represents of your balance</small>
                </span>
                <b>{equity > 0 ? percent((maxOrder / equity) * 100) : '—'}</b>
              </div>
              <div>
                <span>
                  <strong>Margin already committed</strong>
                  <small>Locked by open positions, deducted from the cap</small>
                </span>
                <b>{amounts ? money(amounts.marginLocked) : '—'}</b>
              </div>
            </div>
          )}
        </article>

        <article className="manage-panel surface-card">
          <div className="section-heading compact-heading">
            <div>
              <span className="card-label">Exposure</span>
              <h2>What is at risk</h2>
            </div>
          </div>
          {snapshot.positions.length === 0 ? (
            <EmptyNote
              title="No exposure"
              detail="Nothing is open, so no capital can move against you right now."
            />
          ) : (
            <div className="settings-list">
              {snapshot.positions.map((position) => (
                <div key={position.symbol}>
                  <span>
                    <strong>{position.symbol}</strong>
                    <small>
                      {qty(Math.abs(position.quantity))} ·{' '}
                      {position.quantity < 0 ? 'Short' : 'Long'}
                    </small>
                  </span>
                  <b>{signedMoney(position.pnl)}</b>
                </div>
              ))}
            </div>
          )}
        </article>
      </section>

      <section className="manage-bottom-row">
        <article className="surface-card preference-card">
          <span className="card-label">Not built yet</span>
          <h2>Named here so you are not misled</h2>
          <div className="settings-list">
            <div>
              <span>
                <strong>AI trading controls</strong>
                <small>Pause, resume and manual approval arrive with the agent</small>
              </span>
              <b>Not available</b>
            </div>
            <div>
              <span>
                <strong>Loss and volatility guards</strong>
                <small>Percentage drawdown and volatility filters are not implemented</small>
              </span>
              <b>Not available</b>
            </div>
            <div>
              <span>
                <strong>Deposits and withdrawals</strong>
                <small>Move funds on the exchange itself</small>
              </span>
              <b>Not available</b>
            </div>
          </div>
        </article>

        <article className="surface-card preference-card">
          <span className="card-label">Venue</span>
          <h2>Where orders go</h2>
          <p>
            {snapshot.funds?.is_live
              ? 'This account is trading the real Binance exchange. Real funds are at risk.'
              : 'This account is trading Binance testnet. The balances are practice funds.'}
          </p>
          <div className="settings-list">
            <div>
              <span>
                <strong>Mode</strong>
                <small>Testnet and live differ in what the money is worth</small>
              </span>
              <b>{snapshot.funds?.is_live ? 'Live' : 'Testnet'}</b>
            </div>
            <div>
              <span>
                <strong>Blocked above the cap</strong>
                <small>New exposure only; closing a position is always allowed</small>
              </span>
              <b>Yes</b>
            </div>
          </div>
        </article>
      </section>
    </div>
  )
}
