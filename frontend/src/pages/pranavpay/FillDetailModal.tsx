import { useEffect, useRef } from 'react'
import type { FillRow } from './derive'
import { assetName, money, percent, qty, signedMoney } from './derive'
import { fillShare } from './fills'

interface FillDetailModalProps {
  /** Null renders nothing, so the page owns open state with one nullable id. */
  fill: FillRow | null
  /** The same realised total the header shows, so the share divides by it. */
  totalRealised: number
  onClose: () => void
}

/**
 * The full story of one virtual fill: every field the paper ledger stored about
 * it, plus that fill's share of the realized total where the ledger reports
 * both. A modal rather than an expanding row, so it reads the same on a phone
 * as on a desktop - the existing modal card already scrolls within small
 * viewports.
 */
export default function FillDetailModal({ fill, totalRealised, onClose }: FillDetailModalProps) {
  const closeRef = useRef<HTMLButtonElement>(null)
  useEffect(() => {
    if (!fill) return
    closeRef.current?.focus()
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose()
    }
    document.addEventListener('keydown', onKey)
    const previous = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => {
      document.removeEventListener('keydown', onKey)
      document.body.style.overflow = previous
    }
  }, [fill, onClose])

  if (!fill) return null

  const share = fillShare(fill, totalRealised)
  const exact = fill.at
    ? fill.at.toLocaleString('en-US', {
        month: 'short',
        day: '2-digit',
        year: 'numeric',
        hour: 'numeric',
        minute: '2-digit',
        second: '2-digit',
      })
    : null

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div
        className="modal-card"
        role="dialog"
        aria-modal="true"
        aria-label={`${fill.action === 'BUY' ? 'Bought' : 'Sold'} ${fill.symbol} details`}
        onClick={(event) => event.stopPropagation()}
      >
        <button
          type="button"
          className="modal-close"
          onClick={onClose}
          aria-label="Close fill details"
          ref={closeRef}
        >
          ×
        </button>
        <span className="card-label">Fill detail</span>
        <h2>
          {fill.action === 'BUY' ? 'Bought' : 'Sold'} {assetName(fill.asset)}
        </h2>
        <p>
          {qty(fill.quantity)} {fill.asset} at {money(fill.price)}.
        </p>
        <div className="modal-body">
          <div className="pp-facts">
            <div className="pp-facts-row">
              <span>Symbol</span>
              <strong>{fill.symbol}</strong>
            </div>
            <div className="pp-facts-row">
              <span>Side</span>
              <strong>{fill.action}</strong>
            </div>
            <div className="pp-facts-row">
              <span>Quantity</span>
              <strong>
                {qty(fill.quantity)} {fill.asset}
              </strong>
            </div>
            <div className="pp-facts-row">
              <span>Average price</span>
              <strong>{money(fill.price)}</strong>
            </div>
            <div className="pp-facts-row">
              <span>Trade value</span>
              <strong>{money(fill.value)}</strong>
            </div>
            <div className="pp-facts-row">
              <span>Fee charged</span>
              <strong>
                {fill.fee === null || fill.fee === undefined ? 'Not reported' : money(fill.fee)}
              </strong>
              {fill.fee === null || fill.fee === undefined ? (
                <small>The ledger stored no fee for this fill.</small>
              ) : null}
            </div>
            <div className="pp-facts-row">
              <span>Slippage charged</span>
              <strong>
                {fill.slippage === null || fill.slippage === undefined
                  ? 'Not reported'
                  : money(fill.slippage)}
              </strong>
            </div>
            <div className="pp-facts-row">
              <span>Realized P&amp;L</span>
              <strong>{fill.pnl === null ? 'Not reported' : signedMoney(fill.pnl)}</strong>
              {fill.pnl === null ? (
                <small>
                  The paper ledger keeps realized P&amp;L as one account total and stores none on a
                  fill, so there is no per-fill figure to show.
                </small>
              ) : null}
            </div>
            <div className="pp-facts-row">
              <span>Share of realized total</span>
              <strong>{share === null ? 'Not reported' : percent(share)}</strong>
              {share === null ? (
                <small>
                  {fill.pnl === null
                    ? 'This fill carries no realized result to divide.'
                    : 'The realized total is zero, so a share would divide by nothing.'}
                </small>
              ) : null}
            </div>
            <div className="pp-facts-row">
              <span>Venue</span>
              <strong>{fill.venue}</strong>
            </div>
            <div className="pp-facts-row">
              <span>Order id</span>
              <strong>{fill.orderId || 'Not reported'}</strong>
            </div>
            <div className="pp-facts-row">
              <span>Exact time</span>
              <strong title={fill.at ? fill.at.toISOString() : undefined}>
                {exact ??
                  (fill.timestamp === null
                    ? 'Not reported'
                    : new Date(fill.timestamp).toISOString())}
              </strong>
              {fill.at ? <small>{fill.at.toISOString()}</small> : null}
            </div>
          </div>
        </div>
        <div className="modal-actions">
          <button type="button" className="modal-secondary" onClick={onClose}>
            Close
          </button>
        </div>
      </div>
    </div>
  )
}
