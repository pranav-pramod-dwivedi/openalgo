import {
  ACCOUNT_LABEL,
  NOT_REPORTED,
  type PaperAccount,
  SANDBOX_LABEL,
  VIRGIN_DETAIL,
  VIRGIN_TITLE,
} from '../account'
import {
  EmptyNote,
  LoadingNote,
  SandboxPanel,
  StaleNote,
  TwoAccountsNote,
  useSnapshot,
} from '../components'
import { money, percent, qty, signedMoney } from '../derive'

/**
 * Manage.
 *
 * Two accounts, two sections, and no card that touches both.
 *
 * The virtual account's limits are the paper engine's, and `/api/paper/state`
 * does not report the configuration behind them: the caps it enforces live in
 * the ledger's settings, not in its state payload. So this page does not print
 * the numbers it cannot read. Each limit reads "not reported" until something
 * reports it, which is a truthful row rather than a wrong one.
 *
 * The order-path limits below it belong to the Binance sandbox and say so.
 */
export default function ManagePage() {
  const snapshot = useSnapshot()
  const account = snapshot.account.figures

  if (snapshot.account.loading && !account) {
    return (
      <div className="content-wrap page-view is-visible">
        <LoadingNote label="Reading your virtual account" />
      </div>
    )
  }

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
            Each limit below is checked before the paper worker places a virtual order. Where the
            engine does not report its configuration, this page says so rather than showing a number
            it did not read.
          </p>
        </div>
      </section>

      <TwoAccountsNote />

      {snapshot.account.stale && <StaleNote />}

      <section className="manage-grid">
        <article className="manage-hero ai-card">
          <div className="ai-card-header">
            <div>
              <span className="card-label inverse-label">{ACCOUNT_LABEL}</span>
              <h2>Your virtual account</h2>
            </div>
            <span className="live-label">
              <span className="live-dot" /> Read from the ledger
            </span>
          </div>
          <div className="manage-status-line">
            <div className="manage-status-number">
              {account?.equity === null || account?.equity === undefined
                ? '—'
                : money(account.equity).replace('$', '')}
            </div>
            <div>
              <strong>dollars of virtual equity</strong>
              <small>Cash plus open P&amp;L, less margin held against shorts</small>
            </div>
          </div>
          <div className="manage-rule-list">
            <div>
              <span>Virtual cash</span>
              <strong>{read(account?.cash ?? null, money)}</strong>
            </div>
            <div>
              <span>Margin held against shorts</span>
              <strong>{read(account?.shortMarginLocked ?? null, money)}</strong>
            </div>
            <div>
              <span>Open positions</span>
              <strong>{account?.positions.length ?? 0}</strong>
            </div>
          </div>
          <div className="pp-note pp-note-inverse">
            The paper engine's own configuration is not part of the state it publishes, so no limit
            is quoted here. The ledger holds it.
          </div>
        </article>

        <article className="manage-panel surface-card">
          <div className="section-heading compact-heading">
            <div>
              <span className="card-label">Spending cap</span>
              <h2>How much one virtual order may cost</h2>
            </div>
          </div>
          {account?.virgin ? (
            <EmptyNote title={VIRGIN_TITLE} detail={VIRGIN_DETAIL} />
          ) : (
            <div className="settings-list">
              <div>
                <span>
                  <strong>Per-order cap</strong>
                  <small>Enforced by the engine, value not published in its state</small>
                </span>
                <b>{NOT_REPORTED}</b>
              </div>
              <div>
                <span>
                  <strong>Total exposure cap</strong>
                  <small>Enforced by the engine, value not published in its state</small>
                </span>
                <b>{NOT_REPORTED}</b>
              </div>
              <div>
                <span>
                  <strong>Daily loss cap</strong>
                  <small>Enforced by the engine, value not published in its state</small>
                </span>
                <b>{NOT_REPORTED}</b>
              </div>
              <div>
                <span>
                  <strong>Largest position size</strong>
                  <small>Enforced by the engine, value not published in its state</small>
                </span>
                <b>{NOT_REPORTED}</b>
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
          <VirtualExposure account={account} />
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
                <small>
                  The virtual account has no exchange behind it, so there is nowhere to move it
                </small>
              </span>
              <b>Not available</b>
            </div>
          </div>
        </article>
      </section>

      {/* The sandbox is a different account, so it is a different section, and
          it comes last: the virtual account is what this page is about. */}
      <section className="pp-sandbox-section">
        <div className="section-heading compact-heading">
          <div>
            <span className="card-label">A different account</span>
            <h2>{SANDBOX_LABEL}</h2>
          </div>
        </div>
        <SandboxPanel snapshot={snapshot} />
      </section>

      <section className="pp-note pp-note-quiet">
        The limits on this page apply to the virtual account only. The Binance sandbox enforces its
        own order path rules, and that is the one behind anything you see in the OpenAlgo terminal.
      </section>
    </div>
  )
}

/** Every open virtual position with its marked result, or the honest gap. */
function VirtualExposure({ account }: { account: PaperAccount | null }) {
  if (!account) return <LoadingNote />
  if (account.positions.length === 0) {
    return (
      <EmptyNote
        title="No virtual exposure"
        detail="Nothing is open, so no virtual capital can move against you right now."
      />
    )
  }
  return (
    <div className="settings-list">
      {account.positions.map((position) => (
        <div key={position.symbol}>
          <span>
            <strong>{position.symbol}</strong>
            <small>
              {qty(position.qty)} ·{' '}
              {String(position.side).toUpperCase() === 'SELL' ? 'Short' : 'Long'}
              {position.mark_live === false ? ' · mark is the last known price' : ''}
            </small>
          </span>
          <b>{signedMoney(position.unrealized)}</b>
        </div>
      ))}
      <div>
        <span>
          <strong>Open position value</strong>
          <small>Sum of the marks the ledger reports</small>
        </span>
        <b>{read(account.openPositionValue, money)}</b>
      </div>
      <div>
        <span>
          <strong>Share of equity</strong>
          <small>What that value represents of the account</small>
        </span>
        <b>{read(account.exposureShare, percent)}</b>
      </div>
    </div>
  )
}

/** The ledger figure, or the words for its absence. Never a zero. */
function read(value: number | null, format: (n: number) => string): string {
  return value === null ? NOT_REPORTED : format(value)
}
