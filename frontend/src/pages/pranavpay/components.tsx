import { useOutletContext } from 'react-router'
import { assetGlyph, money, qty, relativeTime } from './derive'
import type { WalletSnapshot } from './useWalletSnapshot'
import { toAmounts } from './useWalletSnapshot'

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
export function LoadingNote({ label = 'Reading your account' }: { label?: string }) {
  return (
    <div className="pp-empty" aria-busy="true">
      <p className="pp-empty-title">{label}</p>
      <p className="pp-empty-detail">One moment.</p>
    </div>
  )
}

export function StaleNote() {
  return (
    <output className="pp-stale">
      The last refresh did not reach the exchange. These are the last figures received.
    </output>
  )
}

export function CardLabel({ children }: { children: React.ReactNode }) {
  return <span className="card-label">{children}</span>
}

export function AssetCell({ asset, name, symbol }: { asset: string; name?: string; symbol?: string }) {
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

/**
 * The protected/tradable split, stated in the order a trader cares about:
 * what is safe, what is spendable, and what is currently committed.
 */
export function CapitalSplit({ compact = false }: { compact?: boolean }) {
  const { funds, positions, loading } = useSnapshot()
  if (loading && !funds) return <LoadingNote />
  const amounts = toAmounts(funds)
  if (!amounts) return <EmptyNote title="No account data" detail="The exchange has not reported a balance yet." />

  return (
    <div className={compact ? 'pp-split pp-split-compact' : 'pp-split'}>
      <div className="pp-split-row pp-split-safe">
        <span>Protected savings</span>
        <strong>{money(amounts.savings)}</strong>
        <small>No order can touch this.</small>
      </div>
      <div className="pp-split-row pp-split-tradable">
        <span>Tradable now</span>
        <strong>{money(amounts.tradable)}</strong>
        <small>The most any new order may cost.</small>
      </div>
      <div className="pp-split-row">
        <span>Open positions</span>
        <strong>{positions.length === 0 ? 'None' : money(amounts.openNotional)}</strong>
        <small>
          {positions.length === 0
            ? 'Nothing is currently exposed.'
            : `${money(amounts.marginLocked)} margin locked.`}
        </small>
      </div>
    </div>
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
