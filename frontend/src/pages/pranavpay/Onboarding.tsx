import { useCallback, useEffect, useState } from 'react'
import {
  ACCOUNT_LABEL,
  NOT_REPORTED,
  PAPER_COMMAND,
  type PaperAccount,
  SANDBOX_LABEL,
  TWO_ACCOUNTS,
} from './account'
import { money } from './derive'
import type { WalletSnapshot } from './useWalletSnapshot'

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

/** The live figure, or the words for its absence. Never a zero. */
function read(value: number | null): string {
  return value === null ? NOT_REPORTED : money(value)
}

/**
 * First-visit overlay. It opens on the two accounts, because that is the one
 * thing a reader cannot work out from the screen: every figure below is virtual
 * money from the paper ledger, and the terminal they may click through to is a
 * Binance testnet sandbox that holds none of it.
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

  const account: PaperAccount | null = snapshot.account.figures

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
            <strong>Two accounts, and only one is yours</strong>
            <p>{TWO_ACCOUNTS}</p>
          </article>
          <article className="pp-onboarding-step">
            <span className="pp-onboarding-index">2</span>
            <strong>Your account is the virtual one</strong>
            <p>
              The {ACCOUNT_LABEL.toLowerCase()} is a real ledger running real market mechanics.
              Cash, equity, realized and unrealized all come from it. Where it has not reported a
              figure, this surface says so rather than showing a zero.
            </p>
            {account ? (
              <small>
                Right now: {read(account.cash)} cash, {read(account.equity)} equity,{' '}
                {account.virgin ? 'no trades yet' : `${account.positions.length} open positions`}.
              </small>
            ) : null}
            {account?.virgin ? (
              <small>
                The ledger is empty. Run <code>{PAPER_COMMAND}</code> to start the worker.
              </small>
            ) : null}
          </article>
          <article className="pp-onboarding-step">
            <span className="pp-onboarding-index">3</span>
            <strong>The sandbox is not your money</strong>
            <p>
              {SANDBOX_LABEL}. The OpenAlgo terminal is useful for charts, order books and
              strategies, but nothing it shows belongs to this account and nothing in it can be
              withdrawn.
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
