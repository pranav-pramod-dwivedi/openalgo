import { BookOpen, LayoutDashboard, LogOut, Menu, Moon, Sun } from 'lucide-react'
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
  const filteredProfileMenuItems = useProfileMenuItems()

  const handleLogout = async () => {
    // Kiosk build: there is no login screen. Logging out clears the server
    // session, and the next UI request re-establishes it, so return to the
    // dashboard rather than a page that no longer exists.
    try {
      await authApi.logout()
    } catch {
      // The kiosk re-creates the session either way; nothing to report.
    }
    logout()
    navigate('/dashboard')
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
            <img src="/logo.png" alt="OpenAlgo" className="h-7 w-7 rounded-sm ring-1 ring-border group-hover:ring-foreground transition-all" />
          </div>
          <div className="hidden sm:flex items-center gap-2">
            <span className="font-extrabold tracking-tight text-sm text-foreground">
              OpenAlgo
            </span>
            <span className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-sm text-[10px] font-mono font-semibold uppercase tracking-wider bg-transparent text-foreground border border-border">
              <span className="h-1.5 w-1.5 rounded-full bg-emerald-500 animate-pulse" />
              TERMINAL
            </span>
          </div>
        </Link>

        {/* Desktop Navigation — Swiss precision tabs */}
        <nav className="hidden md:flex items-center gap-0.5">
          {navItems.map((item) => {
            const active = isActive(item.href)
            const className = cn(
              'flex items-center gap-1.5 rounded-sm px-2.5 py-1 text-xs font-medium transition-all duration-100',
              active
                ? 'bg-foreground text-background font-semibold'
                : 'text-muted-foreground hover:text-foreground hover:bg-muted/50'
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
        <div className="ml-auto flex items-center gap-1.5">
          {/* Kiosk build: single broker (Binance), so the INR/USD sandbox
              account switcher is gone. It used to offer markets this
              deployment no longer loads. */}

          {/* Broker Badge */}
          {user?.broker && (
            <span className="hidden lg:inline-flex text-[10px] font-mono uppercase tracking-wider font-semibold border border-border bg-card rounded-sm px-1.5 py-0.5 text-foreground">
              {user.broker}
            </span>
          )}

          {/* Mode Badge. Kiosk build is live-only: the analyze toggle is
              removed because analyze mode routes every order, including an
              exit for a live position, to the sandbox, which reports success
              while the real position is still open. */}
          <Badge variant="default" className="text-[10px] font-medium rounded-md px-1.5 py-0">
            <span className="hidden lg:inline">Live Mode</span>
            <span className="lg:hidden">Live</span>
          </Badge>

          {/* Theme Toggle */}
          <Button
            variant="ghost"
            size="icon"
            className="h-7 w-7 rounded-lg"
            onClick={toggleMode}
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
              {/* PranavPay is the calm, beginner-facing surface and a separate
                  bundle, so this is a full page load rather than a route
                  change. Kept in the profile menu, not the main nav, so the
                  advanced view stays the default here. */}
              <DropdownMenuItem asChild className="rounded-lg">
                <a href="/" className="flex items-center gap-2">
                  <LayoutDashboard className="h-4 w-4" />
                  Simple view
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
