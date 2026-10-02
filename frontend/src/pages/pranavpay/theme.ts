import { useCallback, useEffect, useState } from 'react'

/** Manual theme choice, persisted to localStorage. `system` follows the OS. */
export type ThemePref = 'system' | 'light' | 'dark'

export const THEME_STORAGE_KEY = 'pp-theme'

function readStoredTheme(): ThemePref {
  try {
    const raw = window.localStorage.getItem(THEME_STORAGE_KEY)
    if (raw === 'light' || raw === 'dark' || raw === 'system') return raw
  } catch {
    // Private mode or blocked storage: fall back to the OS.
  }
  return 'system'
}

function readSystemDark(): boolean {
  try {
    return window.matchMedia('(prefers-color-scheme: dark)').matches
  } catch {
    return false
  }
}

/**
 * Theme preference for the PranavPay surface. The value is stored as-is
 * (`system` stays `system`) so the CSS `prefers-color-scheme` rule keeps
 * working; the resolved value is only for the toggle label.
 */
export function useTheme() {
  const [pref, setPref] = useState<ThemePref>(() => readStoredTheme())
  const [systemDark, setSystemDark] = useState<boolean>(() => readSystemDark())

  useEffect(() => {
    const query = window.matchMedia('(prefers-color-scheme: dark)')
    const onChange = (event: MediaQueryListEvent) => setSystemDark(event.matches)
    query.addEventListener('change', onChange)
    return () => query.removeEventListener('change', onChange)
  }, [])

  useEffect(() => {
    try {
      window.localStorage.setItem(THEME_STORAGE_KEY, pref)
    } catch {
      // Storage blocked: the choice still applies for this visit.
    }
  }, [pref])

  const cycle = useCallback(() => {
    setPref((current) => (current === 'system' ? 'light' : current === 'light' ? 'dark' : 'system'))
  }, [])

  const resolved = pref === 'system' ? (systemDark ? 'dark' : 'light') : pref
  return { pref, resolved, systemDark, setPref, cycle }
}
