import { useMemo } from 'react'
import { Link } from 'react-router'
import { assetGlyph, assetName, money, qty, signedMoney, toFillRows } from '../derive'
import { EmptyNote, LoadingNote, StaleNote, UpdatedAt, useSnapshot } from '../components'
import { toAmounts } from '../useWalletSnapshot'

/**
 * Wallet. The prototype listed a Chase account and a Coinbase wallet and
 * offered deposit and withdraw buttons. None of that exists here, so this page
 * shows the two venues that actually hold the money - Binance Spot and Binance
 * USD-M Futures - read from the exchange, and says plainly that transfers are
 * not wired up rather than pretending a button works.
 */
export default function WalletPage() {
  const snapshot = useSnapshot()
  const amounts = toAmounts(snapshot.funds)
  const fills = useMemo(() => toFillRows(snapshot.trades), [snapshot.trades])

  if (snapshot.loading && !snapshot.funds) {
    return (
      <div className="content-wrap page-view is-visible">
        <LoadingNote label="Reading your balances" />
      </div>
    )
  }

  const spotBalances = (snapshot.funds?.spot_balances ?? []).filter(
    (balance) => (balance.total ?? balance.free ?? 0) > 0
  )
  const futuresBalances = (snapshot.funds?.futures_balances ?? []).filter(
    (balance) => (balance.balance ?? 0) > 0
  )
  const recentFills = fills.slice(0, 5)

  return (
    <div className="content-wrap page-view is-visible" data-page="Wallet">
      <section className="page-intro page-intro-inner">
        <div>
          <p className="eyebrow">
            Wallet <span className="eyebrow-line" /> Connected money
          </p>
          <h1>
            Your money,
            <br />
            <span className="period">accounted for.</span>
          </h1>
          <p className="intro-copy">
            Every balance below was read from the exchange just now, not carried over from a
            previous session.
          </p>
        </div>
        <Link className="ghost-button" to="/manage">
          <span>Guardrails</span>
          <span>↗</span>
        </Link>
      </section>

      {snapshot.stale && <StaleNote />}

      <section className="wallet-page-grid">
        <article className="wallet-balance surface-card">
          <span className="card-label">Available to trade</span>
          <div className="wallet-balance-number">{amounts ? money(amounts.tradable) : '—'}</div>
          <div className="wallet-balance-foot">
            <span>Ready for new orders</span>
            <a className="dark-small-button" href="/trading">
              Open terminal <span>↗</span>
            </a>
          </div>
        </article>
        <article className="wallet-balance surface-card wallet-invested">
          <span className="card-label">Protected savings</span>
          <div className="wallet-balance-number">{amounts ? money(amounts.savings) : '—'}</div>
          <div className="wallet-balance-foot">
            <span>
              {amounts && amounts.equity > 0
                ? `${((amounts.savings / amounts.equity) * 100).toFixed(1)}% of equity`
                : 'Share unknown'}
            </span>
            <Link className="outline-small-button" to="/manage">
              How it is protected <span>↗</span>
            </Link>
          </div>
        </article>
      </section>

      <section className="wallet-page-grid wallet-lower-grid">
        <article className="wallet-account-card surface-card">
          <div className="section-heading compact-heading">
            <div>
              <span className="card-label">Venues</span>
              <h2>Where the balance lives</h2>
            </div>
          </div>

          {spotBalances.length === 0 && futuresBalances.length === 0 ? (
            <EmptyNote
              title="The exchange reported no balances"
              detail="Nothing is held in spot or futures right now."
            />
          ) : (
            <>
              {spotBalances.map((balance) => (
                <div className="connected-row" key={`spot-${balance.asset}`}>
                  <span className="bank-glyph">{assetGlyph(balance.asset)}</span>
                  <span>
                    <strong>
                      {assetName(balance.asset)} <small>· Spot</small>
                    </strong>
                    <small>
                      {qty(balance.total ?? balance.free ?? 0)} {balance.asset}
                      {balance.locked ? ` · ${qty(balance.locked)} locked in orders` : ''}
                    </small>
                  </span>
                  <span className="connected-check">✓</span>
                </div>
              ))}
              {futuresBalances.map((balance) => (
                <div className="connected-row" key={`fut-${balance.asset}`}>
                  <span className="bank-glyph crypto-glyph">{assetGlyph(balance.asset)}</span>
                  <span>
                    <strong>
                      {assetName(balance.asset)} <small>· Futures margin</small>
                    </strong>
                    <small>
                      {qty(balance.available ?? balance.balance ?? 0)} {balance.asset} available
                    </small>
                  </span>
                  <span className="connected-check">✓</span>
                </div>
              ))}
            </>
          )}

          <div className="pp-note">
            Deposits and withdrawals are not connected in this build. Move funds on the exchange
            itself; this page will show the new balance on the next refresh.
          </div>
        </article>

        <article className="wallet-transfer-card surface-card">
          <div className="section-heading compact-heading">
            <div>
              <span className="card-label">Last movements</span>
              <h2>Recent fills</h2>
            </div>
            <span className="wallet-status">
              <span className="live-dot" /> {fills.length} recorded
            </span>
          </div>
          {recentFills.length === 0 ? (
            <EmptyNote
              title="No fills recorded"
              detail="Executed orders from either venue will list here."
            />
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
                      {qty(fill.quantity)} at {money(fill.price)} · {fill.product}
                    </span>
                  </div>
                  <time>{signedMoney(fill.pnl ?? fill.value)}</time>
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
            <h2>Fill history</h2>
          </div>
          <Link className="text-button" to="/transactions">
            Full ledger <span>→</span>
          </Link>
        </div>
        {fills.length === 0 ? (
          <EmptyNote title="Nothing to show" detail="No executed orders have been reported." />
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
                    {qty(fill.quantity)} {fill.asset} at {money(fill.price)} · {fill.venue}
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
          <UpdatedAt at={snapshot.updatedAt} />
        </div>
      </section>
    </div>
  )
}
