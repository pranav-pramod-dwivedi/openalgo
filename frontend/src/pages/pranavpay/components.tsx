import { useOutletContext } from 'react-router'
import { ACCOUNT_LABEL, NOT_REPORTED, type PaperAccount, SANDBOX_LABEL } from './account'
import { assetGlyph, money, qty, relativeTime } from './derive'
import { type SandboxAmounts, sandboxAmounts, type WalletSnapshot } from './useWalletSnapshot'

/** Every page reads the one snapshot the shell fetched. */
export function useSnapshot(): WalletSnapshot {
  return useOutletContext<WalletSnapshot>()
}

/**
 * The state a panel must show when it has nothing to show. The prototype filled
 * every gap with a plausible number; this is the replacement, so an empty
 * account reads as empty rather than as a healthy one.
 */
export function EmptyNote({ title, detail }: { title: string; detail: string }) {
  return (
    <div className="pp-empty">
      <p className="pp-empty-title">{title}</p>
      <p className="pp-empty-detail">{detail}</p>
    </div>
  )
}

/** Shown while the first fetch is in flight, so nothing flashes a zero. */
export function LoadingNote({ label = 'Reading your virtual account' }: { label?: string }) {
  return (
    <div className="pp-empty" aria-busy="true">
      <p className="pp-empty-title">{label}</p>
      <p className="pp-empty-detail">One moment.</p>
    </div>
  )
}

/**
 * The two-account banner. Stated once, on every page, because the distinction
 * is the whole point: without it a Binance balance and a virtual one read as
 * two halves of the same pot.
 */
export function TwoAccountsNote() {
  return (
    <div className="pp-two-accounts">
      <span className="pp-two-accounts-label">{ACCOUNT_LABEL}</span>
      <span>
        Every figure on this page comes from the paper ledger and is virtual money. The OpenAlgo
        terminal runs separately against a <strong>Binance testnet sandbox</strong>, and none of its
        balances are yours.
      </span>
    </div>
  )
}

export function StaleNote() {
  return (
    <output className="pp-stale">
      The last refresh did not reach the paper ledger. These are the last figures received.
    </output>
  )
}

export function CardLabel({ children }: { children: React.ReactNode }) {
  return <span className="card-label">{children}</span>
}

export function AssetCell({
  asset,
  name,
  symbol,
}: {
  asset: string
  name?: string
  symbol?: string
}) {
  return (
    <div className="asset-cell">
      <span className="asset-icon">{assetGlyph(asset)}</span>
      <span>
        <strong>{name ?? asset}</strong>
        <small>{symbol ?? asset}</small>
      </span>
    </div>
  )
}

/** A ledger figure, or an explicit gap. A missing figure never reads as zero. */
export function figure(value: number | null, format: (n: number) => string): string {
  return value === null ? NOT_REPORTED : format(value)
}

/**
 * The virtual account's capital split, in the order a trader asks for it: what
 * is free to trade, what the account is worth, and what is currently committed
 * to the market.
 *
 * Every row is the paper ledger's own figure. There is no protected savings and
 * no savings floor here, because the engine has no such concept - it reports a
 * cash balance, and inventing a floor for it would be a rule that does not
 * exist.
 */
export function CapitalSplit({ account }: { account: PaperAccount | null }) {
  if (!account) return <LoadingNote />

  return (
    <div className="pp-split">
      <div className="pp-split-row pp-split-tradable">
        <span>Virtual cash</span>
        <strong>{figure(account.cash, (n) => money(n))}</strong>
        <small>The most the next virtual order may cost.</small>
      </div>
      <div className="pp-split-row pp-split-safe">
        <span>Equity</span>
        <strong>{figure(account.equity, (n) => money(n))}</strong>
        <small>Cash, plus open P&amp;L, less margin held against shorts.</small>
      </div>
      <div className="pp-split-row">
        <span>Open positions</span>
        <strong>{figure(account.openPositionValue, (n) => money(n))}</strong>
        <small>
          {account.positions.length === 0
            ? 'Nothing is exposed to the market right now.'
            : `${account.positions.length} open at the marks the ledger reports.`}
        </small>
      </div>
    </div>
  )
}

/**
 * The only panel on the surface allowed to show a Binance number.
 *
 * It is headed by the label in full - `Testnet sandbox (not your money)` - and
 * its figures are the exchange's own practice balances. Nothing here is summed
 * with, or compared against, a figure from the virtual account; the two are
 * separate accounts and this panel is where the sandbox is visible.
 */
export function SandboxPanel({ snapshot }: { snapshot: WalletSnapshot }) {
  const { funds, stale, updatedAt } = snapshot.sandbox
  const amounts: SandboxAmounts | null = sandboxAmounts(funds)

  return (
    <article className="surface-card pp-sandbox-card">
      <div className="section-heading compact-heading">
        <div>
          <span className="card-label pp-sandbox-label">{SANDBOX_LABEL}</span>
          <h2>OpenAlgo terminal</h2>
        </div>
        {stale ? <span className="wallet-status">Last known balances</span> : null}
      </div>

      {amounts ? (
        <div className="settings-list">
          <div>
            <span>
              <strong>Sandbox equity</strong>
              <small>Binance practice funds, on the terminal's own books</small>
            </span>
            <b>{money(amounts.equity)}</b>
          </div>
          <div>
            <span>
              <strong>Sandbox available</strong>
              <small>What a new order on the terminal may cost</small>
            </span>
            <b>{money(amounts.tradable)}</b>
          </div>
          <div>
            <span>
              <strong>Sandbox protected</strong>
              <small>The terminal's savings floor, not yours</small>
            </span>
            <b>{money(amounts.savings)}</b>
          </div>
          <div>
            <span>
              <strong>Mode</strong>
              <small>{funds?.is_live ? 'Live exchange' : 'Binance testnet'}</small>
            </span>
            <b>{funds?.is_live ? 'Live' : 'Testnet'}</b>
          </div>
        </div>
      ) : (
        <EmptyNote
          title="The sandbox did not report a balance"
          detail="The terminal's own figures are unavailable right now. Your virtual account above is unaffected."
        />
      )}

      <div className="pp-note">
        These are Binance practice funds on a testnet sandbox. They are not your money, they are not
        counted anywhere on this page, and nothing here can be withdrawn.
      </div>

      {/* Plain anchor, not a router link: PranavPay is a separate bundle with
          its own router, so a client-side <Link> to an OpenAlgo route would
          resolve against PranavPay's routes and never reach the terminal. */}
      <div className="pp-advanced">
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
        {updatedAt ? `Read ${updatedAt.toLocaleString('en-US')}.` : 'Never read.'}
      </div>
    </article>
  )
}

export function UpdatedAt({ at }: { at: Date | null }) {
  if (!at) return <span>Never refreshed</span>
  return <span>Updated {relativeTime(at)}</span>
}

export function QuantityText({ value, asset }: { value: number; asset: string }) {
  return (
    <>
      {qty(value)} {asset}
    </>
  )
}
