import { useMemo } from 'react'
import { useAuthStore } from '@/stores/authStore'
import {
  ACCOUNT_LABEL,
  NOT_REPORTED,
  PAPER_COMMAND,
  SANDBOX_LABEL,
  VIRGIN_DETAIL,
  VIRGIN_TITLE,
} from '../account'
import { EmptyNote, LoadingNote, TwoAccountsNote, UpdatedAt, useSnapshot } from '../components'
import { formatDate, money, OWNER } from '../derive'
import { requestReopenOnboarding } from '../Onboarding'

/**
 * Account.
 *
 * Facts only, and the most important one is which account this is: the virtual
 * paper portfolio. The Binance broker session is named in the second panel,
 * where it is labelled as a testnet sandbox, because it is a separate account
 * with separate (practice) money and calling it "the venue" is what let the two
 * read as one pot.
 *
 * The prototype's "member since", account ID, email and passkey status were all
 * invented, so none of them appear.
 */
export default function AccountPage() {
  const snapshot = useSnapshot()
  const user = useAuthStore((state) => state.user)
  const account = snapshot.account.figures

  const identity = useMemo(
    () => ({ title: OWNER.name, initials: OWNER.initials, subtitle: OWNER.subtitle }),
    []
  )

  if (snapshot.account.loading && !account) {
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
          <p className="intro-copy">
            What this workspace is connected to, and nothing more. There are two connections, and
            only one of them holds your account.
          </p>
        </div>
      </section>

      <TwoAccountsNote />

      <section className="account-settings-grid">
        <article className="account-identity-card ai-card">
          <div className="account-identity-top">
            <div className="account-avatar-large">{identity.initials}</div>
            <div>
              <span className="card-label inverse-label">{identity.subtitle}</span>
              <h2>{identity.title}</h2>
              <p>{ACCOUNT_LABEL} - paper trading, no real funds</p>
            </div>
          </div>
          <div className="account-identity-meta">
            <span>Account</span>
            <strong>{ACCOUNT_LABEL}</strong>
            <span>Ledger</span>
            <strong>data/paper.db</strong>
          </div>
        </article>

        <article className="account-settings-panel surface-card">
          <div className="section-heading compact-heading">
            <div>
              <span className="card-label">Your account</span>
              <h2>Virtual account</h2>
            </div>
          </div>
          {account?.virgin ? (
            <EmptyNote title={VIRGIN_TITLE} detail={VIRGIN_DETAIL} />
          ) : (
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
                  <strong>Equity reported</strong>
                  <small>Read from the paper ledger on the last refresh</small>
                </span>
                <b>{account?.equity ? money(account.equity) : NOT_REPORTED}</b>
              </div>
              <div>
                <span>
                  <strong>Realized P&amp;L</strong>
                  <small>The ledger's own running total</small>
                </span>
                <b>
                  {account?.realized === null || account?.realized === undefined
                    ? NOT_REPORTED
                    : money(account.realized)}
                </b>
              </div>
              <div>
                <span>
                  <strong>Worker</strong>
                  <small>The process that places the virtual orders</small>
                </span>
                <b>{account?.worker?.status ?? NOT_REPORTED}</b>
              </div>
            </div>
          )}
        </article>

        <article className="account-settings-panel surface-card pp-account-sandbox-panel">
          <div className="section-heading compact-heading">
            <div>
              <span className="card-label pp-sandbox-label">{SANDBOX_LABEL}</span>
              <h2>The other account</h2>
            </div>
          </div>
          <div className="settings-list">
            <div>
              <span>
                <strong>Broker plugin</strong>
                <small>Loaded by this OpenAlgo deployment</small>
              </span>
              <b>{user?.broker ?? 'Unknown'}</b>
            </div>
            <div>
              <span>
                <strong>Mode</strong>
                <small>{SANDBOX_LABEL}</small>
              </span>
              <b>{snapshot.sandbox.funds?.is_live ? 'Live' : 'Testnet'}</b>
            </div>
            <div>
              <span>
                <strong>Sandbox equity</strong>
                <small>Practice funds. Not yours, and not part of your account.</small>
              </span>
              <b>
                {snapshot.sandbox.funds
                  ? money(Number(snapshot.sandbox.funds.equity_usd))
                  : NOT_REPORTED}
              </b>
            </div>
          </div>
          <div className="pp-note pp-note-quiet">
            This connection exists so the OpenAlgo terminal can chart, stream and place sandbox
            orders. It holds no money of yours.
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
                {snapshot.account.updatedAt
                  ? snapshot.account.updatedAt.toLocaleString('en-US', {
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
                <strong>Virtual fills recorded</strong>
                <small>Simulated orders the ledger has stored</small>
              </span>
              <b>{account?.fills.length ?? 0}</b>
            </div>
            <div>
              <span>
                <strong>Open virtual positions</strong>
                <small>Positions the worker is managing</small>
              </span>
              <b>{account?.positions.length ?? 0}</b>
            </div>
            <div>
              <span>
                <strong>Worker cycle</strong>
                <small>How many cycles the engine has reported</small>
              </span>
              <b>{account?.worker?.cycle ?? NOT_REPORTED}</b>
            </div>
          </div>
          <div className="pp-note pp-note-quiet">
            <UpdatedAt at={snapshot.account.updatedAt} /> Figures refresh every 15 seconds.
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
                <small>Values are quoted in USD</small>
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
            <div>
              <span>
                <strong>Intro tour</strong>
                <small>The two accounts, and what the virtual one holds</small>
              </span>
              <button
                type="button"
                className="pp-reopen-onboarding"
                onClick={requestReopenOnboarding}
              >
                Replay intro
              </button>
            </div>
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
            PranavPay is the simple surface and reads your virtual account. The full OpenAlgo
            terminal - charting, order books, strategies and the agent - is one click away, and it
            runs on the
            <strong> {SANDBOX_LABEL}</strong> instead. Its balances are not yours.
          </p>
          <div className="pp-advanced">
            {/* Plain anchors, not router links: PranavPay is a separate bundle
                with its own router, so a client-side <Link> to an OpenAlgo
                route would resolve against PranavPay's routes and never
                reach the terminal. */}
            <a className="ghost-button" href="/dashboard">
              <span>Open the testnet sandbox</span>
              <svg viewBox="0 0 24 24" aria-hidden="true">
                <path d="M5 12h14M13 6l6 6-6 6" />
              </svg>
            </a>
            <a className="text-button" href="/trading">
              Charting terminal <span>→</span>
            </a>
          </div>
          <div className="pp-note pp-note-quiet">
            Ledger last read {formatDate(snapshot.account.updatedAt)}. To inspect the engine itself,{' '}
            <code>{PAPER_COMMAND} status</code>.
          </div>
        </article>
      </section>
    </div>
  )
}
