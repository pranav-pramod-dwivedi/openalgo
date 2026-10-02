import {
  Bot,
  CandlestickChart,
  ClipboardList,
  FileText,
  FlaskConical,
  Key,
  LayoutDashboard,
  type LucideIcon,
  TrendingUp,
} from 'lucide-react'

export interface NavItem {
  href: string
  label: string
  icon: LucideIcon
  /** Served by Flask (not a React route): render as a full-page link. */
  external?: boolean
}

// Kiosk build: Dashboard + Trading core + AI Agent only.
export const navItems: NavItem[] = [
  { href: '/dashboard', label: 'Dashboard', icon: LayoutDashboard },
  { href: '/trading', label: 'Trading', icon: CandlestickChart },
  { href: '/orderbook', label: 'Orderbook', icon: ClipboardList },
  { href: '/tradebook', label: 'Tradebook', icon: FileText },
  { href: '/positions', label: 'Positions', icon: TrendingUp },
  { href: '/agent', label: 'Agent', icon: Bot },
]

// Items shown in mobile bottom navigation
export const bottomNavItems: NavItem[] = [
  { href: '/dashboard', label: 'Dashboard', icon: LayoutDashboard },
  { href: '/orderbook', label: 'Orderbook', icon: ClipboardList },
  { href: '/tradebook', label: 'Tradebook', icon: FileText },
  { href: '/positions', label: 'Positions', icon: TrendingUp },
]

// Paths in bottom nav (for filtering mobile sheet items)
const bottomNavPaths = bottomNavItems.map((item) => item.href)

// Secondary items for mobile sheet (items not in bottom nav)
export const mobileSheetItems = navItems.filter((item) => !bottomNavPaths.includes(item.href))

// Profile dropdown menu items
export const profileMenuItems: NavItem[] = [
  { href: '/agent', label: 'Agent', icon: Bot },
  { href: '/paper', label: 'Paper Trading', icon: FlaskConical },
  { href: '/apikey', label: 'API Key', icon: Key },
]

// External links
export const externalLinks = {
  docs: { href: 'https://docs.openalgo.in', label: 'Docs', icon: FileText },
}

// Shared utility to check if a route is active.
// Every nav item is a leaf route, so an exact match is all that is needed.
export function isActiveRoute(pathname: string, href: string): boolean {
  return pathname === href
}
