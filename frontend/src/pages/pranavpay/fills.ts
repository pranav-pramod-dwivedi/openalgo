import type { FillRow } from './derive'

/**
 * Fills that actually closed something. Only these carry a realised result;
 * anything with a null P&L is still open and contributes nothing to the total.
 *
 * The paper ledger reports realised P&L as one account total rather than per
 * fill, so its rows are all null here and every per-fill sum is legitimately
 * empty rather than wrong.
 */
export function closedFills(fills: FillRow[]): FillRow[] {
  return fills.filter((fill) => fill.pnl !== null)
}

/**
 * The realised total, summed from the same closed-fill array the breakdown
 * renders. The header figure and the breakdown total both call this with the
 * same fills, so they agree by construction rather than by coincidence.
 */
export function realisedTotal(fills: FillRow[]): number {
  return closedFills(fills).reduce((sum, fill) => sum + (fill.pnl ?? 0), 0)
}

/**
 * One fill's share of the realised total, in percent. Null when the share
 * would mislead: the fill is still open, or the total is zero.
 */
export function fillShare(fill: FillRow, total: number): number | null {
  if (fill.pnl === null || total === 0) return null
  return (fill.pnl / total) * 100
}

export interface ContributionRow {
  fill: FillRow
  /** Sum of every closed fill up to and including this one. */
  running: number
}

/**
 * Each closed fill beside its running sum. The last row's running value is
 * the realised total itself, accumulated in one pass, so the breakdown cannot
 * drift from the header figure.
 */
export function contributionRows(fills: FillRow[]): ContributionRow[] {
  let running = 0
  return closedFills(fills).map((fill) => {
    running += fill.pnl ?? 0
    return { fill, running }
  })
}

const CSV_HEADERS = [
  'symbol',
  'side',
  'quantity',
  'avg_price',
  'trade_value',
  'fee',
  'slippage',
  'realised_pnl',
  'pnl_share_pct',
  'product',
  'venue',
  'order_id',
  'timestamp',
]

function csvCell(value: string): string {
  return /[",\n]/.test(value) ? `"${value.replace(/"/g, '""')}"` : value
}

function cell(value: number | null | undefined): string {
  return typeof value === 'number' && Number.isFinite(value) ? String(value) : ''
}

/**
 * Every fill as CSV, with the same fields the detail panel shows. All values
 * come from the fills themselves; nothing is invented for the export, and a
 * figure the source did not report is an empty cell rather than a zero.
 */
export function fillsToCsv(fills: FillRow[], total: number): string {
  const lines = [CSV_HEADERS.join(',')]
  for (const fill of fills) {
    const share = fillShare(fill, total)
    lines.push(
      [
        fill.symbol,
        fill.action,
        String(fill.quantity),
        String(fill.price),
        String(fill.value),
        cell(fill.fee),
        cell(fill.slippage),
        fill.pnl === null ? '' : String(fill.pnl),
        share === null ? '' : String(share),
        fill.product,
        fill.venue,
        fill.orderId,
        fill.timestamp === null ? '' : new Date(fill.timestamp).toISOString(),
      ]
        .map(csvCell)
        .join(',')
    )
  }
  return `${lines.join('\n')}\n`
}

export function fillsCsvFilename(now = new Date()): string {
  return `pranavpay-fills-${now.toISOString().slice(0, 10)}.csv`
}

export interface FillsDownload {
  filename: string
  count: number
}

/**
 * Download the fills as CSV. Returns null when there is nothing to export so
 * the caller can stay disabled with an explanation instead of writing an
 * empty file. The anchor is appended to the document and the object URL is
 * revoked on a delay: revoking synchronously can cancel the download in
 * Safari.
 */
export function downloadFillsCsv(fills: FillRow[]): FillsDownload | null {
  if (fills.length === 0) return null
  const blob = new Blob([fillsToCsv(fills, realisedTotal(fills))], {
    type: 'text/csv;charset=utf-8',
  })
  const url = URL.createObjectURL(blob)
  const filename = fillsCsvFilename()
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = filename
  document.body.appendChild(anchor)
  anchor.click()
  document.body.removeChild(anchor)
  window.setTimeout(() => URL.revokeObjectURL(url), 1000)
  return { filename, count: fills.length }
}
