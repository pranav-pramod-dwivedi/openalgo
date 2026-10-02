import { useMemo } from 'react'
import { Link } from 'react-router'
import {
  ACCOUNT_LABEL,
  NOT_REPORTED,
  PAPER_COMMAND,
  paperFillRows,
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
  UpdatedAt,
  useSnapshot,
} from '../components'
import { assetName, money, qty, relativeTime, signedMoney } from '../derive'
import ExportFillsButton from '../ExportFillsButton'

/**
 * Wallet.
 *
 * The prototype listed a Chase account and a Coinbase wallet and offered deposit
 * and withdraw buttons. None of that exists, and there is nothing to connect:
 * the only account this surface holds is virtual, so the page shows what the
 * paper ledger reports about it and nothing else.
 *
 * The Binance testnet wallet is a different account with different money, so it
 * appears in its own panel at the foot of the page rather than as "venues" of
 * this one.
 */
export default function WalletPage() {
  const snapshot = useSnapshot()
  const account = snapshot.account.figures
  const fills = useMemo(() => paperFillRows(account?.fills ?? []), [account?.fills])

  if (snapshot.account.loading && !account) {
    return (
      <div className="content-wrap page-view is-visible">
        <LoadingNote label="Reading your virtual balances" />
      </div>
    )
  }

  const recentFills = fills.slice(0, 5)

  return (
    <div className="content-wrap page-view is-visible" data-page="Wallet">
      <section className="page-intro page-intro-inner">
        <div>
          <p className="eyebrow">
            Wallet <span className="eyebrow-line" /> {ACCOUNT_LABEL}
          </p>
          <h1>
            Your virtual money,
            <br />
            <span className="period">accounted for.</span>
          </h1>
          <p className="intro-copy">
            Every balance below was read from the paper ledger just now, not carried over from a
            previous session. None of it is deposited anywhere and none of it can be withdrawn.
          </p>
        </div>
        <Link className="ghost-button" to="/manage">
          <span>Guardrails</span>
          <span>↗</span>
        </Link>
      </section>

      <TwoAccountsNote />

      {snapshot.account.stale && <StaleNote />}

      {account?.virgin ? (
        <section className="surface-card">
          <EmptyNote title={VIRGIN_TITLE} detail={VIRGIN_DETAIL} />
          <div className="pp-note pp-note-quiet">
            There is deliberately no balance shown above it: an empty ledger reported as a figure
            would read as a real one.
          </div>
        </section>
      ) : null}

      <section className="wallet-page-grid">
        <article className="wallet-balance surface-card">
          <span className="card-label">Virtual cash</span>
          <div className="wallet-balance-number">{read(account?.cash ?? null, money)}</div>
          <div className="wallet-balance-foot">
            <span>Ready for the next virtual order</span>
            <Link className="dark-small-button" to="/paper">
              Paper engine <span>↗</span>
            </Link>
          </div>
        </article>
        <article className="wallet-balance surface-card wallet-invested">
          <span className="card-label">Equity</span>
          <div className="wallet-balance-number">{read(account?.equity ?? null, money)}</div>
          <div className="wallet-balance-foot">
            <span>
              {account?.cashShare === null || account?.cashShare === undefined
                ? 'Share unknown'
                : `${account.cashShare.toFixed(1)}% of equity is still cash`}
            </span>
            <Link className="outline-small-button" to="/manage">
              Where it goes <span>↗</span>
            </Link>
          </div>
        </article>
      </section>

      <section className="wallet-page-grid wallet-lower-grid">
        <article className="wallet-account-card surface-card">
          <div className="section-heading compact-heading">
            <div>
              <span className="card-label">Where it sits</span>
              <h2>The virtual account</h2>
            </div>
          </div>

          {!account ? (
            <LoadingNote />
          ) : (
            <div className="settings-list">
              <div>
                <span>
                  <strong>Cash</strong>
                  <small>What the next order may spend</small>
                </span>
                <b>{read(account.cash, money)}</b>
              </div>
              <div>
                <span>
                  <strong>Open positions</strong>
                  <small>Sum of the marks the ledger reports</small>
                </span>
                <b>{read(account.openPositionValue, money)}</b>
              </div>
              <div>
                <span>
                  <strong>Margin held against shorts</strong>
                  <small>Posted, so not spendable</small>
                </span>
                <b>{read(account.shortMarginLocked, money)}</b>
              </div>
              <div>
                <span>
                  <strong>Worker</strong>
                  <small>The process that places the virtual orders</small>
                </span>
                <b>{account.worker?.status ?? NOT_REPORTED}</b>
              </div>
            </div>
          )}

          <div className="pp-note">
            {ACCOUNT_LABEL} only. Nothing here is held at an exchange, because there is no exchange
            holding it for you: the ledger simulates the fills and keeps the balance itself.
          </div>
        </article>

        <article className="wallet-transfer-card surface-card">
          <div className="section-heading compact-heading">
            <div>
              <span className="card-label">Last movements</span>
              <h2>Recent virtual fills</h2>
            </div>
            <span className="wallet-status">
              <span className="live-dot" /> {fills.length} recorded
            </span>
          </div>
          {recentFills.length === 0 ? (
            <EmptyNote title={VIRGIN_TITLE} detail={VIRGIN_DETAIL} />
          ) : (
            <div className="activity-list">
              {recentFills.map((fill) => (
                <div className="activity-item" key={fill.id}>
                  <div className="activity-icon transfer-activity">
                    {fill.action === 'BUY' ? '↓' : '↑'}
                  </div>
                  <div className="activity-copy">
                    <strong>
                      {fill.action === 'BUY' ? 'Bought' : 'Sold'} {assetName(fill.asset)}
                    </strong>
                    <span>
                      {qty(fill.quantity)} at {money(fill.price)}
                    </span>
                  </div>
                  {/* The value belongs beside the fill, not in the time slot,
                      where a money figure reads as a timestamp. */}
                  <span className="activity-result">{money(fill.value)}</span>
                  <time>{relativeTime(fill.at)}</time>
                </div>
              ))}
            </div>
          )}
        </article>
      </section>

      <section className="wallet-ledger surface-card">
        <div className="section-heading compact-heading">
          <div>
            <span className="card-label">Paper trail</span>
            <h2>Virtual fill history</h2>
          </div>
          <Link className="text-button" to="/transactions">
            Full ledger <span>→</span>
          </Link>
          <ExportFillsButton fills={fills} />
        </div>
        {fills.length === 0 ? (
          <EmptyNote
            title={VIRGIN_TITLE}
            detail={`No virtual order has been executed yet. Run ${PAPER_COMMAND} and they appear here.`}
          />
        ) : (
          <div className="transfer-ledger">
            {fills.slice(0, 8).map((fill) => (
              <div className="transfer-line" key={fill.id}>
                <span className="activity-icon transfer-activity">
                  {fill.action === 'BUY' ? '↓' : '↑'}
                </span>
                <span>
                  <strong>
                    {fill.action === 'BUY' ? 'Bought' : 'Sold'} {assetName(fill.asset)}
                  </strong>
                  <small>
                    {qty(fill.quantity)} {fill.asset} at {money(fill.price)}
                    {fill.fee === null || fill.fee === undefined ? '' : ` · fee ${money(fill.fee)}`}
                  </small>
                </span>
                <strong>{money(fill.value)}</strong>
                <time>
                  {fill.at?.toLocaleString('en-US', {
                    month: 'short',
                    day: '2-digit',
                    hour: 'numeric',
                    minute: '2-digit',
                  }) ?? '—'}
                </time>
              </div>
            ))}
          </div>
        )}
        <div className="pp-note pp-note-quiet">
          <UpdatedAt at={snapshot.account.updatedAt} /> Realized P&amp;L so far:{' '}
          {read(account?.realized ?? null, signedMoney)}.
        </div>
      </section>

      <section className="pp-sandbox-section">
        <div className="section-heading compact-heading">
          <div>
            <span className="card-label">A different account</span>
            <h2>{SANDBOX_LABEL}</h2>
          </div>
        </div>
        <SandboxPanel snapshot={snapshot} />
      </section>
    </div>
  )
}

/** The ledger figure, or the words for its absence. Never a zero. */
function read(value: number | null, format: (n: number) => string): string {
  return value === null ? NOT_REPORTED : format(value)
}
