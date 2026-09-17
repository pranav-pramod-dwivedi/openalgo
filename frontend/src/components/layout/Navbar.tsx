import { LogOut, Menu, Moon, Sun, Zap } from 'lucide-react'
import { useState } from 'react'
import { Link, useLocation, useNavigate } from 'react-router'
import { authApi } from '@/api/auth'
import { LogoutConfirmDialog } from '@/components/auth/LogoutConfirmDialog'
import { Button } from '@/components/ui/button'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
  SheetTrigger,
} from '@/components/ui/sheet'
import { isActiveRoute, mobileSheetItems, navItems } from '@/config/navigation'
import { cn } from '@/lib/utils'
import { useAuthStore } from '@/stores/authStore'
import { useThemeStore } from '@/stores/themeStore'
import { showToast } from '@/utils/toast'

interface NavbarProps {
  fluid?: boolean
}

export function Navbar({ fluid = false }: NavbarProps = {}) {
  const location = useLocation()
  const navigate = useNavigate()
  const [mobileOpen, setMobileOpen] = useState(false)
  const [showLogoutDialog, setShowLogoutDialog] = useState(false)
  const { mode, toggleMode } = useThemeStore()
  const { user, logout } = useAuthStore()

  const handleLogout = async () => {
    try {
      await authApi.logout()
      logout()
      navigate('/login')
      showToast.success('Logged out successfully')
    } catch {
      logout()
      navigate('/login')
    }
  }

  const isActive = (href: string) => isActiveRoute(location.pathname, href)

  return (
    <nav className="sticky top-0 z-50 w-full bg-background/80 backdrop-blur-md border-b border-border/60 transition-all">
      <div
        className={cn(
          'px-4 sm:px-6 flex h-14 items-center justify-between gap-2',
          fluid ? 'w-full' : 'container mx-auto max-w-7xl'
        )}
      >
        {/* Left Side: Monetra Brand Logo & Nav */}
        <div className="flex items-center gap-6">
          {/* Mobile Menu Trigger */}
          <Sheet open={mobileOpen} onOpenChange={setMobileOpen}>
            <SheetTrigger asChild className="md:hidden">
              <Button
                variant="ghost"
                size="icon"
                className="min-h-[40px] min-w-[40px] rounded-full hover:bg-muted"
              >
                <Menu className="h-5 w-5" />
                <span className="sr-only">Toggle menu</span>
              </Button>
            </SheetTrigger>
            <SheetContent side="left" className="w-72 overflow-y-auto rounded-r-3xl">
              <SheetHeader className="sr-only">
                <SheetTitle>Navigation Menu</SheetTitle>
                <SheetDescription>Main navigation and quick access links</SheetDescription>
              </SheetHeader>
              <div className="flex flex-col gap-5 py-4">
                <Link
                  to="/dashboard"
                  className="flex items-center gap-3 px-2"
                  onClick={() => setMobileOpen(false)}
                >
                  <div className="w-8 h-8 rounded-xl bg-blue-500 flex items-center justify-center text-white shadow-sm shadow-blue-500/30">
                    <Zap className="h-5 w-5 fill-current" />
                  </div>
                  <span className="font-bold text-lg tracking-tight">OpenAlgo</span>
                </Link>

                <nav className="flex flex-col gap-1">
                  <div className="px-3 py-2 text-[11px] font-semibold text-muted-foreground uppercase tracking-wider">
                    Menu
                  </div>
                  {mobileSheetItems.map((item) => {
                    const active = isActive(item.href)
                    const cls = cn(
                      'flex items-center gap-3 rounded-full px-4 py-2.5 text-sm font-medium transition-all duration-150 min-h-[44px]',
                      active
                        ? 'bg-[#18191D] text-white dark:bg-white dark:text-[#18191D] shadow-sm'
                        : 'text-muted-foreground hover:bg-muted hover:text-foreground'
                    )
                    return item.external ? (
                      <a
                        key={item.href}
                        href={item.href}
                        onClick={() => setMobileOpen(false)}
                        className={cls}
                        aria-current={active ? 'page' : undefined}
                      >
                        <item.icon className="h-[18px] w-[18px]" />
                        {item.label}
                      </a>
                    ) : (
                      <Link
                        key={item.href}
                        to={item.href}
                        onClick={() => setMobileOpen(false)}
                        className={cls}
                        aria-current={active ? 'page' : undefined}
                      >
                        <item.icon className="h-[18px] w-[18px]" />
                        {item.label}
                      </Link>
                    )
                  })}
                </nav>
              </div>
            </SheetContent>
          </Sheet>

          {/* Monetra Brand Spark Icon + Name */}
          <Link to="/dashboard" className="flex items-center gap-2.5 group">
            <div className="w-8 h-8 rounded-xl bg-blue-500 flex items-center justify-center text-white shadow-sm shadow-blue-500/20 group-hover:scale-105 transition-transform">
              <Zap className="h-4 w-4 fill-current text-white" />
            </div>
            <span className="font-bold text-base tracking-tight text-foreground">
              OpenAlgo
            </span>
          </Link>

          {/* Desktop Nav Items */}
          <nav className="hidden md:flex items-center gap-1">
            {navItems.slice(0, 5).map((item) => {
              const active = isActive(item.href)
              return (
                <Link
                  key={item.href}
                  to={item.href}
                  className={cn(
                    'flex items-center gap-1.5 px-3.5 py-1.5 rounded-full text-xs font-semibold transition-all',
                    active
                      ? 'bg-[#18191D] text-white dark:bg-white dark:text-[#18191D] shadow-sm'
                      : 'text-muted-foreground hover:text-foreground hover:bg-muted/60'
                  )}
                >
                  <item.icon className="h-3.5 w-3.5" />
                  <span>{item.label}</span>
                </Link>
              )
            })}
          </nav>
        </div>

        {/* Right Side: Monetra Market Switcher Pills & Profile */}
        <div className="flex items-center gap-2">
          {/* Segmented Pill Market Switcher */}
          <div className="flex items-center p-1 bg-muted/60 rounded-full border border-border/80 text-xs">
            <button
              type="button"
              onClick={() => { window.location.href = '/auth/switch-account?account=inr' }}
              className={cn(
                'monetra-pill transition-all text-xs font-semibold',
                !user?.username?.toLowerCase().includes('usd') &&
                user?.broker !== 'binance_demo' &&
                !user?.username?.toLowerCase().includes('binance')
                  ? 'monetra-pill-active'
                  : 'monetra-pill-inactive hover:text-foreground'
              )}
            >
              🇮🇳 INR
            </button>
            <button
              type="button"
              onClick={() => { window.location.href = '/auth/switch-account?account=usd' }}
              className={cn(
                'monetra-pill transition-all text-xs font-semibold',
                user?.username?.toLowerCase().includes('usd') &&
                user?.broker !== 'binance_demo' &&
                !user?.username?.toLowerCase().includes('binance')
                  ? 'monetra-pill-active'
                  : 'monetra-pill-inactive hover:text-foreground'
              )}
            >
              🌐 USD
            </button>
            <button
              type="button"
              onClick={() => { window.location.href = '/auth/switch-account?account=binance' }}
              className={cn(
                'monetra-pill transition-all text-xs font-semibold',
                user?.broker === 'binance_demo' || user?.username?.toLowerCase().includes('binance')
                  ? 'monetra-pill-active'
                  : 'monetra-pill-inactive hover:text-foreground'
              )}
            >
              🟡 Binance
            </button>
          </div>

          {/* Theme Toggle */}
          <Button
            variant="ghost"
            size="icon"
            onClick={toggleMode}
            className="rounded-full w-8 h-8 hover:bg-muted"
            title="Toggle theme"
          >
            {mode === 'dark' ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
          </Button>

          {/* User Profile */}
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button
                variant="ghost"
                size="icon"
                className="rounded-full w-8 h-8 bg-muted hover:bg-muted/80 text-foreground font-bold text-xs"
              >
                {user?.username ? user.username.charAt(0).toUpperCase() : 'U'}
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end" className="w-56 rounded-2xl p-2 shadow-lg">
              <div className="px-2 py-1.5 text-xs text-muted-foreground">
                Signed in as <strong className="text-foreground">{user?.username}</strong>
              </div>
              <DropdownMenuSeparator />
              <DropdownMenuItem
                onClick={toggleMode}
                className="rounded-xl cursor-pointer"
              >
                {mode === 'dark' ? 'Light Theme' : 'Dark Theme'}
              </DropdownMenuItem>
              <DropdownMenuSeparator />
              <DropdownMenuItem
                onClick={() => setShowLogoutDialog(true)}
                className="rounded-xl text-destructive cursor-pointer"
              >
                <LogOut className="h-4 w-4 mr-2" />
                Sign Out
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        </div>
      </div>

      <LogoutConfirmDialog
        open={showLogoutDialog}
        onOpenChange={setShowLogoutDialog}
        onConfirm={handleLogout}
      />
    </nav>
  )
}
