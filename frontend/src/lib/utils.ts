import { type ClassValue, clsx } from 'clsx'
import { twMerge } from 'tailwind-merge'

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

/**
 * Sanitize a value for CSV export to prevent formula injection.
 * Prefixes dangerous characters (=, +, -, @) with a single quote.
 */
export function sanitizeCSV(value: string | number | null | undefined): string {
  if (value === null || value === undefined) return ''
  const str = String(value)
  // Prefix dangerous formula characters with a single quote
  if (/^[=+\-@]/.test(str)) {
    return `'${str}`
  }
  // Escape quotes and wrap in quotes if contains comma
  if (str.includes(',') || str.includes('"') || str.includes('\n')) {
    return `"${str.replace(/"/g, '""')}"`
  }
  return str
}

import { useAuthStore } from '@/stores/authStore'

/**
 * Returns a currency formatter bound to the active broker and account.
 * - deltaexchange or openalgo_usd → USD ($)
 * - all other brokers / openalgo_admin  → INR (₹)
 */
export function makeFormatCurrency(broker?: string | null): (value: number) => string {
  const user = useAuthStore.getState().user
  const uname = user?.username ? user.username.toLowerCase() : ''
  const isUSD =
    broker === 'deltaexchange' ||
    broker === 'binance_demo' ||
    uname.includes('usd') ||
    uname.includes('binance')
  return (value: number) =>
    isUSD
      ? new Intl.NumberFormat('en-US', {
          style: 'currency',
          currency: 'USD',
          minimumFractionDigits: 2,
        }).format(value)
      : new Intl.NumberFormat('en-IN', {
          style: 'currency',
          currency: 'INR',
          minimumFractionDigits: 2,
        }).format(value)
}
