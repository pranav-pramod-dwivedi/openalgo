import { Link, useLocation } from 'react-router'
import { LayoutDashboard, FileText, TrendingUp, Grid } from 'lucide-react'
import { cn } from '@/lib/utils'

export function MobileBottomNav() {
  const location = useLocation()

  return (
    <div className="md:hidden fixed bottom-5 left-0 right-0 z-50 px-6 flex items-center justify-between pointer-events-none safe-area-bottom">
      {/* Floating Pill Nav Island */}
      <nav className="pointer-events-auto flex items-center gap-1 px-3 py-1.5 monetra-nav-island">
        <Link
          to="/dashboard"
          className={cn(
            'flex items-center gap-1.5 px-3 py-1.5 rounded-full text-xs font-semibold transition-all',
            location.pathname === '/dashboard' || location.pathname === '/'
              ? 'bg-[#18191D] text-white dark:bg-white dark:text-[#18191D] shadow-sm'
              : 'text-muted-foreground hover:text-foreground'
          )}
        >
          <LayoutDashboard className="h-4 w-4" />
          <span>Home</span>
        </Link>

        <Link
          to="/trading"
          className={cn(
            'flex items-center gap-1.5 px-3 py-1.5 rounded-full text-xs font-semibold transition-all',
            location.pathname === '/trading'
              ? 'bg-[#18191D] text-white dark:bg-white dark:text-[#18191D] shadow-sm'
              : 'text-muted-foreground hover:text-foreground'
          )}
        >
          <TrendingUp className="h-4 w-4" />
          <span>Trade</span>
        </Link>

        <Link
          to="/orderbook"
          className={cn(
            'flex items-center gap-1.5 px-3 py-1.5 rounded-full text-xs font-semibold transition-all',
            location.pathname === '/orderbook'
              ? 'bg-[#18191D] text-white dark:bg-white dark:text-[#18191D] shadow-sm'
              : 'text-muted-foreground hover:text-foreground'
          )}
        >
          <FileText className="h-4 w-4" />
          <span>Orders</span>
        </Link>
      </nav>

      {/* Monetra Keypad FAB */}
      <Link
        to="/platforms"
        className="pointer-events-auto monetra-fab"
        title="More Modules"
      >
        <Grid className="h-5 w-5 text-white" />
      </Link>
    </div>
  )
}
