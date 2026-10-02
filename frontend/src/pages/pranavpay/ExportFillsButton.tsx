import { useEffect, useState } from 'react'
import type { FillRow } from './derive'
import { downloadFillsCsv } from './fills'

/**
 * CSV export for the fills, shared by Transactions and Wallet. Disabled with
 * a visible explanation when there is nothing to export, and a toast confirms
 * the file on success so the click has an answer.
 */
export default function ExportFillsButton({ fills }: { fills: FillRow[] }) {
  const [note, setNote] = useState<string | null>(null)
  const empty = fills.length === 0

  useEffect(() => {
    if (!note) return
    const timer = window.setTimeout(() => setNote(null), 4000)
    return () => window.clearTimeout(timer)
  }, [note])

  const onExport = () => {
    const result = downloadFillsCsv(fills)
    if (result) setNote(`Exported ${result.count} fills to ${result.filename}.`)
  }

  return (
    <>
      <button
        type="button"
        className="control-button"
        onClick={onExport}
        disabled={empty}
        title={empty ? 'No fills to export yet' : `Download ${fills.length} fills as CSV`}
      >
        <span>Export CSV</span>
      </button>
      {empty ? (
        <span className="wallet-status" role="note">
          No fills to export yet
        </span>
      ) : null}
      {note ? (
        <output className="toast show">
          <span className="toast-mark">✓</span>
          <span>{note}</span>
        </output>
      ) : null}
    </>
  )
}
