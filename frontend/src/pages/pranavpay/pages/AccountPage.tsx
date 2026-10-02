import { useMemo } from 'react'
import { useAuthStore } from '@/stores/authStore'
import { formatDate, money, OWNER } from '../derive'
import { EmptyNote, LoadingNote, UpdatedAt, useSnapshot } from '../components'
import { toAmounts } from '../useWalletSnapshot'

/**
 * Account.
 *
 * Facts only: the account the session is actually signed in as, the venue, and
 * when the figures were last read. The prototype's "member since", account ID,
 * email and passkey status were all invented, so none of them appear.
 */
export default function AccountPage() {
  const snapshot = useSnapshot()
  const user = useAuthStore((state) => state.user)
  const amounts = toAmounts(snapshot.funds)

  const identity = useMemo(
    () => ({ title: OWNER.name, initials: OWNER.initials, subtitle: OWNER.subtitle }),
    []
  )

  if (snapshot.loading && !snapshot.funds) {
    return (
      <div className="content-wrap page-view is-visible">
        <LoadingNote label="Reading your account" />
      </div>
    )
  }

  return (
    <div className="content-wrap page-view is-visible" data-page="Account settings">
      <section className="page-intro page-intro-inner">
        <div>
          <p className="eyebrow">
            Account <span className="eyebrow-line" /> Session
          </p>
          <h1>
            Your account,
            <br />
            <span className="period">as it is.</span>
          </h1>
          <p className="intro-copy">What this workspace is connected to, and nothing more.</p>
        </div>
      </section>

      <section className="account-settings-grid">
        <article className="account-identity-card ai-card">
          <div className="account-identity-top">
            <div className="account-avatar-large">{identity.initials}</div>
            <div>
              <span className="card-label inverse-label">{identity.subtitle}</span>
              <h2>{identity.title}</h2>
              <p>{user?.broker ? `${user.broker} venue` : 'No broker connected'}</p>
            </div>
          </div>
          <div className="account-identity-meta">
            <span>Venue</span>
            <strong>{user?.broker ?? 'Unknown'}</strong>
            <span>Mode</span>
            <strong>{snapshot.funds?.is_live ? 'Live exchange' : 'Testnet'}</strong>
          </div>
        </article>

        <article className="account-settings-panel surface-card">
          <div className="section-heading compact-heading">
            <div>
              <span className="card-label">Profile</span>
              <h2>Connection</h2>
            </div>
          </div>
          <div className="settings-list">
            <div>
              <span>
                <strong>Owner</strong>
                <small>Single local session, no remote account</small>
              </span>
              <b>{OWNER.name}</b>
            </div>
            <div>
              <span>
                <strong>Broker</strong>
                <small>Plugin this deployment loads</small>
              </span>
              <b>{user?.broker ?? 'Unknown'}</b>
            </div>
            <div>
              <span>
                <strong>Equity reported</strong>
                <small>Read from the exchange on the last refresh</small>
              </span>
              <b>{amounts && amounts.equity > 0 ? money(amounts.equity) : '—'}</b>
            </div>
          </div>
        </article>

        <article className="account-settings-panel surface-card">
          <div className="section-heading compact-heading">
            <div>
              <span className="card-label">Preferences</span>
              <h2>Workspace defaults</h2>
            </div>
          </div>
          <div className="settings-list">
            <div>
              <span>
                <strong>Default landing page</strong>
                <small>Where PranavPay opens</small>
              </span>
              <b>Quick overview</b>
            </div>
            <div>
              <span>
                <strong>Currency</strong>
                <small>Values are quoted in USDT</small>
              </span>
              <b>USD</b>
            </div>
            <div>
              <span>
                <strong>Timezone</strong>
                <small>Used for clocks and report times</small>
              </span>
              <b>{Intl.DateTimeFormat().resolvedOptions().timeZone}</b>
            </div>
          </div>
        </article>
      </section>

      <section className="account-lower-grid">
        <article className="account-settings-panel surface-card">
          <div className="section-heading compact-heading">
            <div>
              <span className="card-label">Data</span>
              <h2>Freshness</h2>
            </div>
          </div>
          <div className="settings-list">
            <div>
              <span>
                <strong>Last successful read</strong>
                <small>Every figure on this surface comes from this moment</small>
              </span>
              <b>
                {snapshot.updatedAt
                  ? snapshot.updatedAt.toLocaleString('en-US', {
                      month: 'short',
                      day: '2-digit',
                      hour: 'numeric',
                      minute: '2-digit',
                    })
                  : 'Never'}
              </b>
            </div>
            <div>
              <span>
                <strong>Fills recorded</strong>
                <small>Executed orders the exchange has reported</small>
              </span>
              <b>{snapshot.trades.length}</b>
            </div>
            <div>
              <span>
                <strong>Open orders</strong>
                <small>Working orders not yet filled</small>
              </span>
              <b>{snapshot.orders.filter((order) => order.order_status === 'open').length}</b>
            </div>
          </div>
          <div className="pp-note pp-note-quiet">
            <UpdatedAt at={snapshot.updatedAt} /> Figures refresh every 30 seconds.
          </div>
        </article>

        <article className="account-settings-panel surface-card">
          <div className="section-heading compact-heading">
            <div>
              <span className="card-label">OpenAlgo</span>
              <h2>Advanced view</h2>
            </div>
          </div>
          {user?.broker ? null : (
            <EmptyNote title="No broker" detail="Connect a broker to use the advanced terminal." />
          )}
          <p className="pp-plain">
            PranavPay is the simple surface. The full OpenAlgo terminal - charting, order books,
            strategies and the agent - is one click away, and shares this same account.
          </p>
          <div className="pp-advanced">
            {/* Plain anchors, not router links: PranavPay is a separate bundle
                with its own router, so a client-side <Link> to an OpenAlgo
                route would resolve against PranavPay's routes and never
                reach the terminal. */}
            <a className="ghost-button" href="/dashboard">
              <span>Open advanced view</span>
              <svg viewBox="0 0 24 24" aria-hidden="true">
                <path d="M5 12h14M13 6l6 6-6 6" />
              </svg>
            </a>
            <a className="text-button" href="/trading">
              Charting terminal <span>→</span>
            </a>
          </div>
          <div className="pp-note pp-note-quiet">
            Last checked {formatDate(snapshot.updatedAt)}
          </div>
        </article>
      </section>
    </div>
  )
}
