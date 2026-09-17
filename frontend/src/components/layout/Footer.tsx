import { Github, Monitor } from 'lucide-react'
import { useEffect, useState } from 'react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'
import { useSessionStore } from '@/stores/sessionStore'

interface FooterProps {
  className?: string
}

export function Footer({ className }: FooterProps) {
  const [version, setVersion] = useState<string>('')
  const activeSessionCount = useSessionStore((s) => s.activeSessionCount)

  useEffect(() => {
    const fetchVersion = async () => {
      try {
        const response = await fetch('/auth/app-info')
        const data = await response.json()
        if (data.status === 'success') {
          setVersion(data.version)
        }
      } catch (_error) {}
    }
    fetchVersion()
  }, [])

  return (
    <footer className={cn('mt-auto border-t border-border/50 bg-muted/20', className)}>
      <div className="container mx-auto px-4 sm:px-6 py-4 max-w-7xl">
        <div className="flex flex-col md:flex-row items-center justify-center gap-1.5 md:gap-3 text-xs text-muted-foreground">
          <div className="flex items-center gap-2">
            <span>© 2026</span>
            <a
              href="https://www.openalgo.in"
              className="text-foreground/70 hover:text-foreground transition-colors font-medium"
              target="_blank"
              rel="noopener noreferrer"
            >
              openalgo.in
            </a>
          </div>
          <span className="hidden md:inline text-border">·</span>
          <span>Open Source Algo Trading</span>
          {version && (
            <>
              <span className="hidden md:inline text-border">·</span>
              <Badge
                variant="secondary"
                className="text-[10px] font-mono rounded-md px-1.5 py-0 gap-0.5"
              >
                <span className="opacity-50">v</span>
                {version}
              </Badge>
            </>
          )}
          {activeSessionCount > 0 && (
            <>
              <span className="hidden md:inline text-border">·</span>
              <Badge
                variant="outline"
                className="text-[10px] rounded-md px-1.5 py-0 gap-1 border-border/50"
              >
                <Monitor className="h-2.5 w-2.5" />
                {activeSessionCount} {activeSessionCount === 1 ? 'session' : 'sessions'}
              </Badge>
            </>
          )}
        </div>

        {/* Social Links */}
        <div className="flex justify-center gap-1 mt-3">
          <Button
            variant="ghost"
            size="icon"
            asChild
            className="h-7 w-7 rounded-lg text-muted-foreground hover:text-foreground"
          >
            <a
              href="https://github.com/marketcalls/openalgo"
              target="_blank"
              rel="noopener noreferrer"
              aria-label="GitHub"
            >
              <Github className="h-3.5 w-3.5" />
            </a>
          </Button>
          <Button
            variant="ghost"
            size="icon"
            asChild
            className="h-7 w-7 rounded-lg text-muted-foreground hover:text-foreground"
          >
            <a
              href="https://openalgo.in/discord"
              target="_blank"
              rel="noopener noreferrer"
              aria-label="Discord"
            >
              <svg className="h-3.5 w-3.5" fill="currentColor" viewBox="0 0 24 24">
                <path d="M20.317 4.3698a19.7913 19.7913 0 00-4.8851-1.5152.0741.0741 0 00-.0785.0371c-.211.3753-.4447.8648-.6083 1.2495-1.8447-.2762-3.68-.2762-5.4868 0-.1636-.3933-.4058-.8742-.6177-1.2495a.077.077 0 00-.0785-.037 19.7363 19.7363 0 00-4.8852 1.515.0699.0699 0 00-.0321.0277C.5334 9.0458-.319 13.5799.0992 18.0578a.0824.0824 0 00.0312.0561c2.0528 1.5076 4.0413 2.4228 5.9929 3.0294a.0777.0777 0 00.0842-.0276c.4616-.6304.8731-1.2952 1.226-1.9942a.076.076 0 00-.0416-.1057c-.6528-.2476-1.2743-.5495-1.8722-.8923a.077.077 0 01-.0076-.1277c.1258-.0943.2517-.1923.3718-.2914a.0743.0743 0 01.0776-.0105c3.9278 1.7933 8.18 1.7933 12.0614 0a.0739.0739 0 01.0785.0095c.1202.099.246.1981.3728.2924a.077.077 0 01-.0066.1276 12.2986 12.2986 0 01-1.873.8914.0766.0766 0 00-.0407.1067c.3604.698.7719 1.3628 1.225 1.9932a.076.076 0 00.0842.0286c1.961-.6067 3.9495-1.5219 6.0023-3.0294a.077.077 0 00.0313-.0552c.5004-5.177-.8382-9.6739-3.5485-13.6604a.061.061 0 00-.0312-.0286zM8.02 15.3312c-1.1825 0-2.1569-1.0857-2.1569-2.419 0-1.3332.9555-2.4189 2.157-2.4189 1.2108 0 2.1757 1.0952 2.1568 2.419 0 1.3332-.9555 2.4189-2.1569 2.4189zm7.9748 0c-1.1825 0-2.1569-1.0857-2.1569-2.419 0-1.3332.9554-2.4189 2.1569-2.4189 1.2108 0 2.1757 1.0952 2.1568 2.419 0 1.3332-.946 2.4189-2.1568 2.4189Z" />
              </svg>
            </a>
          </Button>
          <Button
            variant="ghost"
            size="icon"
            asChild
            className="h-7 w-7 rounded-lg text-muted-foreground hover:text-foreground"
          >
            <a
              href="https://x.com/openalgoHQ"
              target="_blank"
              rel="noopener noreferrer"
              aria-label="X"
            >
              <svg className="h-3.5 w-3.5" fill="currentColor" viewBox="0 0 24 24">
                <path d="M18.901 1.153h3.68l-8.04 9.19L24 22.846h-7.406l-5.8-7.584-6.638 7.584H.474l8.6-9.83L0 1.154h7.594l5.243 6.932ZM17.61 20.644h2.039L6.486 3.24H4.298Z" />
              </svg>
            </a>
          </Button>
          <Button
            variant="ghost"
            size="icon"
            asChild
            className="h-7 w-7 rounded-lg text-muted-foreground hover:text-foreground"
          >
            <a
              href="https://www.youtube.com/@openalgo"
              target="_blank"
              rel="noopener noreferrer"
              aria-label="YouTube"
            >
              <svg className="h-3.5 w-3.5" fill="currentColor" viewBox="0 0 24 24">
                <path d="M23.498 6.186a3.016 3.016 0 0 0-2.122-2.136C19.505 3.545 12 3.545 12 3.545s-7.505 0-9.377.505A3.017 3.017 0 0 0 .502 6.186C0 8.07 0 12 0 12s0 3.93.502 5.814a3.016 3.016 0 0 0 2.122 2.136c1.871.505 9.376.505 9.376.505s7.505 0 9.377-.505a3.015 3.015 0 0 0 2.122-2.136C24 15.93 24 12 24 12s0-3.93-.502-5.814zM9.545 15.568V8.432L15.818 12l-6.273 3.568z" />
              </svg>
            </a>
          </Button>
        </div>
      </div>
    </footer>
  )
}
