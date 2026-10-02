import { useCallback, useEffect, useState } from 'react'
import { money } from './derive'
import type { WalletSnapshot } from './useWalletSnapshot'
import { toAmounts } from './useWalletSnapshot'

export const ONBOARDING_STORAGE_KEY = 'pp-onboarded-v1'
export const REOPEN_ONBOARDING_EVENT = 'pp-reopen-onboarding'

export function isOnboarded(): boolean {
  try {
    return window.localStorage.getItem(ONBOARDING_STORAGE_KEY) === '1'
  } catch {
    return true
  }
}

export function markOnboarded(): void {
  try {
    window.localStorage.setItem(ONBOARDING_STORAGE_KEY, '1')
  } catch {
    // Storage blocked: the overlay simply shows again next visit.
  }
}

export function requestReopenOnboarding(): void {
  window.dispatchEvent(new CustomEvent(REOPEN_ONBOARDING_EVENT))
}

/**
 * First-visit overlay. Three plain-word cards, no invented numbers: the only
 * figures shown are the protected/tradable amounts from the live snapshot,
 * and only when the snapshot has actually loaded.
 */
export function Onboarding({ snapshot }: { snapshot: WalletSnapshot }) {
  const [open, setOpen] = useState(false)

  useEffect(() => {
    if (!isOnboarded()) setOpen(true)
    const reopen = () => setOpen(true)
    window.addEventListener(REOPEN_ONBOARDING_EVENT, reopen)
    return () => window.removeEventListener(REOPEN_ONBOARDING_EVENT, reopen)
  }, [])

  const dismiss = useCallback(() => {
    markOnboarded()
    setOpen(false)
  }, [])

  useEffect(() => {
    if (!open) return
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') dismiss()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open, dismiss])

  if (!open) return null

  const amounts = toAmounts(snapshot.funds)
  const hasAmounts = amounts !== null

  return (
    <div
      className="modal-backdrop pp-onboarding-backdrop"
      role="dialog"
      aria-modal="true"
      aria-labelledby="pp-onboarding-title"
      onClick={dismiss}
    >
      <div className="modal-card pp-onboarding-card" onClick={(event) => event.stopPropagation()}>
        <div>
          <span className="card-label">First visit</span>
          <h2 id="pp-onboarding-title">How PranavPay works</h2>
          <p>Three things worth knowing before anything else.</p>
        </div>
        <div className="pp-onboarding-grid">
          <article className="pp-onboarding-step">
            <span className="pp-onboarding-index">1</span>
            <strong>Protected stays put, tradable moves</strong>
            <p>
              Protected savings is money no order can touch. Tradable is the most any new order may
              cost.
            </p>
            {hasAmounts ? (
              <small>
                Right now: {money(amounts.savings)} protected, {money(amounts.tradable)} tradable.
              </small>
            ) : null}
          </article>
          <article className="pp-onboarding-step">
            <span className="pp-onboarding-index">2</span>
            <strong>This is practice money</strong>
            <p>
              This surface reads the testnet, so every figure is for learning. Nothing here moves
              real funds.
            </p>
          </article>
          <article className="pp-onboarding-step">
            <span className="pp-onboarding-index">3</span>
            <strong>The AI is not connected yet</strong>
            <p>
              The agent places nothing on this account. It is being wired up, and until then every
              order is yours.
            </p>
          </article>
        </div>
        <div className="modal-actions pp-onboarding-actions">
          <button type="button" className="modal-secondary" onClick={dismiss}>
            Skip tour
          </button>
          <button type="button" className="modal-primary" onClick={dismiss}>
            Got it
          </button>
        </div>
      </div>
    </div>
  )
}
