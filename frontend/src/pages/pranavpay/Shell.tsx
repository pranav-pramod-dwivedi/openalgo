import { useCallback, useEffect, useMemo, useState } from 'react'
import { NavLink, Outlet, useLocation } from 'react-router'
import { useAuthStore } from '@/stores/authStore'
import { useWalletSnapshot } from './useWalletSnapshot'

const SECTIONS = [
  { to: '/', label: 'Quick overview', icon: 'grid', end: true },
  { to: '/wallet', label: 'Wallet', icon: 'wallet', end: false },
  { to: '/transactions', label: 'Transactions', icon: 'list', end: false },
  { to: '/manage', label: 'Manage', icon: 'gear', end: false },
] as const

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
}

/**
 * Chrome for the PranavPay surface: the left rail, the topbar, and the mobile
 * equivalents. Holds the single snapshot every page reads, so the balance in
 * the rail and the balance on the overview can never disagree.
 */
export default function Shell() {
  const location = useLocation()
  const snapshot = useWalletSnapshot()
  const user = useAuthStore((state) => state.user)
  const [scrolled, setScrolled] = useState(false)

  useEffect(() => {
    const onScroll = () => setScrolled(window.scrollY > 10)
    onScroll()
    window.addEventListener('scroll', onScroll, { passive: true })
    return () => window.removeEventListener('scroll', onScroll)
  }, [])

  const active = useMemo(
    () => SECTIONS.find((section) => (section.end ? location.pathname === section.to : location.pathname.startsWith(section.to)))?.label ?? 'Quick overview',
    [location.pathname]
  )

  const initials = useCallback(() => {
    const name = user?.username?.replace(/[_-]/g, ' ').trim() ?? ''
    if (!name || name === 'binance_demo') return 'PP'
    return name
      .split(/\s+/)
      .slice(0, 2)
      .map((part) => part[0]?.toUpperCase() ?? '')
      .join('')
  }, [user?.username])

  const displayName = useMemo(() => {
    const name = user?.username?.replace(/[_-]/g, ' ').trim() ?? ''
    if (!name) return 'Local account'
    if (name.toLowerCase() === 'binance_demo') return 'Binance account'
    return name.replace(/\b\w/g, (character) => character.toUpperCase())
  }, [user?.username])

  const reachable = snapshot.funds !== null

  return (
    <div className={`pp-root${scrolled ? ' is-scrolled' : ''}`}>
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
            <div className="avatar">{initials()}</div>
            <div className="profile-copy">
              <strong>{displayName}</strong>
              <span>{reachable ? 'Binance account' : 'Connecting'}</span>
            </div>
            <svg viewBox="0 0 24 24" aria-hidden="true">
              <path d="m9 18 6-6-6-6" />
            </svg>
          </NavLink>

          <div className="sidebar-foot">
            <span className="live-dot" style={{ color: reachable ? '#4ade80' : '#f59e0b' }} />
            {reachable ? 'Exchange connected' : 'Reconnecting'}
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
              <div className="market-status">
                <span
                  className="status-pulse"
                  style={{ color: reachable ? '#4ade80' : '#f59e0b' }}
                />
                <span>{reachable ? 'Markets open 24/7' : 'Connecting to exchange'}</span>
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
    </div>
  )
}
