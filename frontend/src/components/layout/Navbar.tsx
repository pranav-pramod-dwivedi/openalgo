import { BarChart3, BookOpen, LogOut, Menu, Moon, Sun, Zap } from 'lucide-react'
import { useState } from 'react'
import { Link, useLocation, useNavigate } from 'react-router'
import { authApi } from '@/api/auth'
import { LogoutConfirmDialog } from '@/components/auth/LogoutConfirmDialog'
import { Badge } from '@/components/ui/badge'
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
import { useProfileMenuItems } from '@/hooks/useProfileMenuItems'
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
  const { mode, appMode, toggleMode, toggleAppMode, isTogglingMode } = useThemeStore()
  const { user, logout } = useAuthStore()
  const filteredProfileMenuItems = useProfileMenuItems()

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

  const handleModeToggle = async () => {
    const result = await toggleAppMode()
    if (result.success) {
      const newMode = useThemeStore.getState().appMode
      showToast.success(`Switched to ${newMode === 'live' ? 'Live' : 'Analyze'} mode`)
      if (newMode === 'analyzer') {
        setTimeout(() => {
          showToast.warning('Analyzer (Sandbox) mode is for testing purposes only', undefined, {
            duration: 10000,
          })
        }, 2000)
      }
    } else {
      showToast.error(result.message || 'Failed to toggle mode')
    }
  }

  const isActive = (href: string) => isActiveRoute(location.pathname, href)

  return (
    <nav className="sticky top-0 z-50 w-full border-b border-border/50 glass bg-background/80">
      <div
        className={cn(
          'px-4 sm:px-6 flex h-12 items-center gap-1',
          fluid ? 'w-full' : 'container mx-auto max-w-7xl'
        )}
      >
        {/* Mobile Menu */}
        <Sheet open={mobileOpen} onOpenChange={setMobileOpen}>
          <SheetTrigger asChild className="md:hidden">
            <Button
              variant="ghost"
              size="icon"
              className="mr-1 min-h-[44px] min-w-[44px] rounded-xl"
            >
              <Menu className="h-5 w-5" />
              <span className="sr-only">Toggle menu</span>
            </Button>
          </SheetTrigger>
          <SheetContent side="left" className="w-72 overflow-y-auto">
            <SheetHeader className="sr-only">
              <SheetTitle>Navigation Menu</SheetTitle>
              <SheetDescription>Main navigation and quick access links</SheetDescription>
            </SheetHeader>
            <div className="flex flex-col gap-5 py-4">
              <Link
                to="/dashboard"
                className="flex items-center gap-2.5 px-2"
                onClick={() => setMobileOpen(false)}
              >
                <img src="/logo.png" alt="OpenAlgo" className="h-8 w-8 rounded-lg" />
                <span className="font-semibold tracking-tight">OpenAlgo</span>
              </Link>

              <nav className="flex flex-col gap-0.5">
                <div className="px-3 py-2 text-[10px] font-semibold text-muted-foreground uppercase tracking-widest">
                  Navigation
                </div>
                {mobileSheetItems.map((item) => {
                  const active = isActive(item.href)
                  const cls = cn(
                    'flex items-center gap-3 rounded-xl px-3 py-2.5 text-sm transition-all duration-200 min-h-[44px] touch-manipulation',
                    active
                      ? 'bg-primary text-primary-foreground font-medium'
                      : 'text-muted-foreground hover:bg-muted hover:text-foreground'
                  )
                  const inner = (
                    <>
                      <item.icon className="h-[18px] w-[18px]" />
                      {item.label}
                    </>
                  )
                  return item.external ? (
                    <a
                      key={item.href}
                      href={item.href}
                      onClick={() => setMobileOpen(false)}
                      className={cls}
                      aria-current={active ? 'page' : undefined}
                    >
                      {inner}
                    </a>
                  ) : (
                    <Link
                      key={item.href}
                      to={item.href}
                      onClick={() => setMobileOpen(false)}
                      className={cls}
                      aria-current={active ? 'page' : undefined}
                    >
                      {inner}
                    </Link>
                  )
                })}
              </nav>

              <nav className="flex flex-col gap-0.5 border-t border-border/50 pt-4">
                <div className="px-3 py-2 text-[10px] font-semibold text-muted-foreground uppercase tracking-widest">
                  Quick Access
                </div>
                {filteredProfileMenuItems.map((item) => {
                  const active = isActive(item.href)
                  return (
                    <Link
                      key={item.href}
                      to={item.href}
                      onClick={() => setMobileOpen(false)}
                      className={cn(
                        'flex items-center gap-3 rounded-xl px-3 py-2.5 text-sm transition-all duration-200 min-h-[44px] touch-manipulation',
                        active
                          ? 'bg-primary text-primary-foreground font-medium'
                          : 'text-muted-foreground hover:bg-muted hover:text-foreground'
                      )}
                      aria-current={active ? 'page' : undefined}
                    >
                      <item.icon className="h-[18px] w-[18px]" />
                      {item.label}
                    </Link>
                  )
                })}
                <a
                  href="https://docs.openalgo.in"
                  target="_blank"
                  rel="noopener noreferrer"
                  className="flex items-center gap-3 rounded-xl px-3 py-2.5 text-sm transition-all duration-200 min-h-[44px] touch-manipulation text-muted-foreground hover:bg-muted hover:text-foreground"
                  onClick={() => setMobileOpen(false)}
                >
                  <BookOpen className="h-[18px] w-[18px]" />
                  Docs
                </a>
              </nav>
            </div>
          </SheetContent>
        </Sheet>

        {/* Logo & Terminal Status */}
        <Link to="/dashboard" className="flex items-center gap-2.5 mr-5 group">
          <div className="relative flex items-center justify-center">
            <img src="/logo.png" alt="OpenAlgo" className="h-7 w-7 rounded-lg ring-1 ring-border/60 group-hover:ring-emerald-500/50 transition-all" />
          </div>
          <div className="hidden sm:flex items-center gap-2">
            <span className="font-bold tracking-tight text-sm text-foreground">
              OpenAlgo
            </span>
            <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-mono font-medium bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 border border-emerald-500/25">
              <span className="h-1.5 w-1.5 rounded-full bg-emerald-500 animate-pulse" />
              TERMINAL
            </span>
          </div>
        </Link>

        {/* Desktop Navigation — high-craft terminal tabs */}
        <nav className="hidden md:flex items-center gap-1">
          {navItems.map((item) => {
            const active = isActive(item.href)
            const className = cn(
              'flex items-center gap-1.5 rounded-md px-2.5 py-1.5 text-xs font-medium transition-all duration-150 relative',
              active
                ? 'bg-primary text-primary-foreground font-semibold shadow-xs'
                : 'text-muted-foreground hover:text-foreground hover:bg-muted/60'
            )
            const content = (
              <>
                <item.icon className="h-3.5 w-3.5 shrink-0" />
                <span className="hidden xl:inline tracking-tight">{item.label}</span>
              </>
            )
            return item.external ? (
              <a
                key={item.href}
                href={item.href}
                title={item.label}
                className={className}
                aria-current={active ? 'page' : undefined}
              >
                {content}
              </a>
            ) : (
              <Link
                key={item.href}
                to={item.href}
                title={item.label}
                className={className}
                aria-current={active ? 'page' : undefined}
              >
                {content}
              </Link>
            )
          })}
        </nav>

        {/* Right Side */}
        <div className="ml-auto flex items-center gap-2">
          {/* Account Switcher — Tactile Multi-Market Control */}
          <div className="flex items-center p-0.5 bg-muted/60 dark:bg-muted/40 rounded-lg border border-border/60 text-xs">
            <button
              type="button"
              onClick={() => {
                window.location.href = '/auth/switch-account?account=inr'
              }}
              className={cn(
                'flex items-center gap-1 px-2 py-1 rounded-md transition-all duration-150 cursor-pointer text-xs',
                !user?.username?.toLowerCase().includes('usd') &&
                user?.broker !== 'binance_demo' &&
                !user?.username?.toLowerCase().includes('binance')
                  ? 'bg-primary text-primary-foreground font-semibold shadow-xs'
                  : 'text-muted-foreground hover:text-foreground'
              )}
              title="Indian Markets (INR Sandbox)"
            >
              <span>🇮🇳</span>
              <span className="hidden xl:inline">INR</span>
            </button>
            <button
              type="button"
              onClick={() => {
                window.location.href = '/auth/switch-account?account=usd'
              }}
              className={cn(
                'flex items-center gap-1 px-2 py-1 rounded-md transition-all duration-150 cursor-pointer text-xs',
                user?.username?.toLowerCase().includes('usd') &&
                user?.broker !== 'binance_demo' &&
                !user?.username?.toLowerCase().includes('binance')
                  ? 'bg-emerald-600 text-white font-semibold shadow-xs'
                  : 'text-muted-foreground hover:text-foreground'
              )}
              title="Forex & Crypto (USD Sandbox)"
            >
              <span>🌐</span>
              <span className="hidden xl:inline">USD</span>
            </button>
            <button
              type="button"
              onClick={() => {
                window.location.href = '/auth/switch-account?account=binance'
              }}
              className={cn(
                'flex items-center gap-1 px-2 py-1 rounded-md transition-all duration-150 cursor-pointer text-xs',
                user?.broker === 'binance_demo' || user?.username?.toLowerCase().includes('binance')
                  ? 'bg-amber-500 text-black font-semibold shadow-xs'
                  : 'text-muted-foreground hover:text-foreground'
              )}
              title="Binance Official Demo"
            >
              <span>🟡</span>
              <span className="hidden xl:inline">Binance</span>
            </button>
          </div>

          {/* Broker Badge */}
          {user?.broker && (
            <Badge
              variant="outline"
              className="hidden lg:flex text-[10px] font-mono uppercase tracking-wider font-semibold border-border/80 bg-card/60 rounded px-1.5 py-0.5"
            >
              {user.broker}
            </Badge>
          )}

          {/* Mode Badge */}
          <Badge
            variant={appMode === 'live' ? 'default' : 'secondary'}
            className={cn(
              'text-[10px] font-medium rounded-md px-1.5 py-0',
              appMode === 'analyzer' && 'bg-purple-500 hover:bg-purple-600 text-white'
            )}
          >
            <span className="hidden lg:inline">{appMode === 'live' ? 'Live Mode' : 'Analyze'}</span>
            <span className="lg:hidden">{appMode === 'live' ? 'Live' : 'Analyze'}</span>
          </Badge>

          {/* Mode Toggle */}
          <Button
            variant="ghost"
            size="icon"
            className="h-7 w-7 rounded-lg"
            onClick={handleModeToggle}
            disabled={isTogglingMode}
            title={`Switch to ${appMode === 'live' ? 'Analyze' : 'Live'} mode`}
          >
            {isTogglingMode ? (
              <div className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-current border-t-transparent" />
            ) : appMode === 'live' ? (
              <Zap className="h-3.5 w-3.5" />
            ) : (
              <BarChart3 className="h-3.5 w-3.5" />
            )}
          </Button>

          {/* Theme Toggle */}
          <Button
            variant="ghost"
            size="icon"
            className="h-7 w-7 rounded-lg"
            onClick={toggleMode}
            disabled={appMode !== 'live'}
            title={mode === 'light' ? 'Switch to dark mode' : 'Switch to light mode'}
          >
            {mode === 'light' ? <Sun className="h-3.5 w-3.5" /> : <Moon className="h-3.5 w-3.5" />}
          </Button>

          {/* Profile Dropdown */}
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button
                variant="ghost"
                size="icon"
                className="h-7 w-7 rounded-full bg-foreground text-background text-xs font-semibold"
                aria-label="Open user menu"
              >
                {user?.username?.[0]?.toUpperCase() || 'O'}
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end" className="w-56 rounded-xl">
              {filteredProfileMenuItems.map((item) =>
                item.external ? (
                  <DropdownMenuItem key={item.href} asChild className="cursor-pointer rounded-lg">
                    <a href={item.href} className="flex items-center">
                      <item.icon className="h-4 w-4 mr-2" />
                      {item.label}
                    </a>
                  </DropdownMenuItem>
                ) : (
                  <DropdownMenuItem
                    key={item.href}
                    onSelect={() => navigate(item.href)}
                    className="cursor-pointer rounded-lg"
                  >
                    <item.icon className="h-4 w-4 mr-2" />
                    {item.label}
                  </DropdownMenuItem>
                )
              )}
              <DropdownMenuItem asChild className="rounded-lg">
                <a
                  href="https://docs.openalgo.in"
                  target="_blank"
                  rel="noopener noreferrer"
                  className="flex items-center gap-2"
                >
                  <BookOpen className="h-4 w-4" />
                  Docs
                </a>
              </DropdownMenuItem>
              <DropdownMenuSeparator />
              <DropdownMenuItem
                onClick={() => setShowLogoutDialog(true)}
                className="text-destructive focus:text-destructive rounded-lg"
              >
                <LogOut className="h-4 w-4 mr-2" />
                Logout
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
