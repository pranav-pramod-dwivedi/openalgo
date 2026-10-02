import { useEffect, useMemo, useState } from 'react'
import { NavLink, Outlet, useLocation } from 'react-router'
import { OWNER } from './derive'
import { Onboarding } from './Onboarding'
import { useTheme } from './theme'
import { useWalletSnapshot } from './useWalletSnapshot'

const SECTIONS = [
  { to: '/', label: 'Quick overview', icon: 'grid', end: true },
  { to: '/wallet', label: 'Wallet', icon: 'wallet', end: false },
  { to: '/transactions', label: 'Transactions', icon: 'list', end: false },
  { to: '/manage', label: 'Manage', icon: 'gear', end: false },
  { to: '/paper', label: 'Paper', icon: 'flask', end: false },
] as const

/**
 * Every section the breadcrumb can name, including the two that are not in the
 * rail. The account page is reached from the profile card, so without it here
 * the breadcrumb fell back to "Quick overview" and every page after Wallet
 * reported the wrong name.
 */
const CRUMB_LABELS: Array<[string, string]> = [
  ['/wallet', 'Wallet'],
  ['/transactions', 'Transactions'],
  ['/manage', 'Manage'],
  ['/account', 'Account settings'],
  ['/paper', 'Paper trading'],
]

const ICONS: Record<string, React.ReactNode> = {
  grid: (
    <>
      <rect x="4" y="4" width="6" height="6" rx="1" />
      <rect x="14" y="4" width="6" height="6" rx="1" />
      <rect x="4" y="14" width="6" height="6" rx="1" />
      <rect x="14" y="14" width="6" height="6" rx="1" />
    </>
  ),
  wallet: (
    <>
      <path d="M3.5 7.5A2.5 2.5 0 0 1 6 5h13a1.5 1.5 0 0 1 1.5 1.5v10A2.5 2.5 0 0 1 18 19H6a2.5 2.5 0 0 1-2.5-2.5v-9Z" />
      <path d="M4 8h15.5M16 13h4.5" />
      <circle cx="16" cy="13" r="1" fill="currentColor" stroke="none" />
    </>
  ),
  list: (
    <>
      <path d="M5 7h14M5 12h14M5 17h9" />
      <path d="m16 15 3 2.5-3 2.5" />
    </>
  ),
  flask: (
    <>
      <path d="M9 3h6M10 3v5.2L5.6 17A2 2 0 0 0 7.4 20h9.2a2 2 0 0 0 1.8-3L14 8.2V3" />
      <path d="M7.6 14h8.8" />
    </>
  ),
  gear: (
    <>
      <path d="M12 3v3M12 18v3M3 12h3M18 12h3M5.6 5.6l2.1 2.1M16.3 16.3l2.1 2.1M18.4 5.6l-2.1 2.1M7.7 16.3l-2.1 2.1" />
      <circle cx="12" cy="12" r="4" />
    </>
  ),
}

const MOBILE_LABELS: Record<string, string> = {
  'Quick overview': 'Quick view',
  Wallet: 'Wallet',
  Transactions: 'Activity',
  Manage: 'Manage',
  Paper: 'Paper',
}

/**
 * Chrome for the PranavPay surface: the left rail, the topbar, and the mobile
 * equivalents. Holds the single snapshot every page reads, so the balance in
 * the rail and the balance on the overview can never disagree.
 */
export default function Shell() {
  const location = useLocation()
  const snapshot = useWalletSnapshot()
  const [scrolled, setScrolled] = useState(false)
  const { pref, cycle } = useTheme()

  useEffect(() => {
    const onScroll = () => setScrolled(window.scrollY > 10)
    onScroll()
    window.addEventListener('scroll', onScroll, { passive: true })
    return () => window.removeEventListener('scroll', onScroll)
  }, [])

  const active = useMemo(() => {
    const match = CRUMB_LABELS.find(([path]) => location.pathname.startsWith(path))
    if (match) return match[1]
    return (
      SECTIONS.find((section) =>
        section.end ? location.pathname === section.to : location.pathname.startsWith(section.to)
      )?.label ?? 'Quick overview'
    )
  }, [location.pathname])

  const initials = OWNER.initials
  const displayName = OWNER.name

  // The rail reports on the virtual account, which is the paper ledger. The
  // sandbox has its own state and is never summarised here: a green dot beside
  // "connected" used to mean the Binance testnet answered, which reads as
  // "your broker is connected" when no broker holds the user's money.
  const ledgerLive = snapshot.account.state !== null

  return (
    <div className={`pp-root${scrolled ? ' is-scrolled' : ''}`} data-theme={pref}>
      <div className="app-shell">
        <aside className="sidebar" aria-label="Primary navigation">
          <div className="brand-lockup">
            <div className="brand-mark" aria-hidden="true">
              <span />
              <i />
            </div>
            <div className="brand-name">PranavPay</div>
          </div>

          <div className="rail-label">Workspace</div>
          <nav className="nav-stack" aria-label="Workspace">
            {SECTIONS.map((section) => (
              <NavLink
                key={section.to}
                to={section.to}
                end={section.end}
                className={({ isActive }) => `nav-item${isActive ? ' active' : ''}`}
              >
                <svg viewBox="0 0 24 24" aria-hidden="true">
                  {ICONS[section.icon]}
                </svg>
                <span>{section.label}</span>
              </NavLink>
            ))}
          </nav>

          <div className="sidebar-spacer" />

          <div className="rail-label">Account</div>
          <NavLink to="/account" className="profile-card">
            <div className="avatar">{initials}</div>
            <div className="profile-copy">
              <strong>{displayName}</strong>
              <span>{OWNER.subtitle}</span>
            </div>
            <svg viewBox="0 0 24 24" aria-hidden="true">
              <path d="m9 18 6-6-6-6" />
            </svg>
          </NavLink>

          <div className="sidebar-foot">
            <span className="live-dot" style={{ color: ledgerLive ? '#4ade80' : '#f59e0b' }} />
            {ledgerLive ? 'Virtual account live' : 'Reading your ledger'}
          </div>
        </aside>

        <main className="main-content">
          <header className="topbar">
            <div className="mobile-brand">
              <div className="brand-mark small" aria-hidden="true">
                <span />
                <i />
              </div>
              <span>PranavPay</span>
            </div>
            <div className="breadcrumb">
              <span>Workspace</span>
              <span className="slash">/</span>
              <strong>{active}</strong>
            </div>
            <div className="topbar-actions">
              <button
                type="button"
                className="pp-theme-toggle"
                onClick={cycle}
                aria-label={`Color theme is ${pref}. Switch theme.`}
                title={`Theme: ${pref} (system follows your device)`}
              >
                <span className="pp-theme-dot" aria-hidden="true" />
                <span>{pref === 'system' ? 'System' : pref === 'light' ? 'Light' : 'Dark'}</span>
              </button>
              <div className="market-status">
                <span
                  className="status-pulse"
                  style={{ color: ledgerLive ? '#4ade80' : '#f59e0b' }}
                />
                <span>{ledgerLive ? 'Paper worker ledger' : 'Reading your ledger'}</span>
              </div>
            </div>
          </header>

          <nav className="mobile-toolbar" aria-label="Mobile workspace navigation">
            <div className="mobile-toolbar-track">
              {SECTIONS.map((section) => (
                <NavLink
                  key={section.to}
                  to={section.to}
                  end={section.end}
                  className={({ isActive }) => `mobile-toolbar-item${isActive ? ' active' : ''}`}
                >
                  {MOBILE_LABELS[section.label] ?? section.label}
                </NavLink>
              ))}
            </div>
          </nav>

          <Outlet context={snapshot} />
        </main>
      </div>

      <nav className="mobile-nav" aria-label="Mobile navigation">
        {SECTIONS.map((section) => (
          <NavLink
            key={section.to}
            to={section.to}
            end={section.end}
            className={({ isActive }) => `mobile-nav-item${isActive ? ' active' : ''}`}
          >
            <svg viewBox="0 0 24 24" aria-hidden="true">
              {ICONS[section.icon]}
            </svg>
            <span>{MOBILE_LABELS[section.label] ?? section.label}</span>
          </NavLink>
        ))}
      </nav>

      <Onboarding snapshot={snapshot} />
    </div>
  )
}
