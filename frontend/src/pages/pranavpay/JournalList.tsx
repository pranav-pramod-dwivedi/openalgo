import { useMemo, useState } from 'react'
import { EmptyNote, LoadingNote } from './components'
import JournalEntry from './JournalEntry'
import type { JournalEntryData } from './useJournal'
import { byNewestFirst, isApproved, isBlocked, isExecuted, useJournal } from './useJournal'

type JournalFilter = 'all' | 'approved' | 'blocked' | 'executed'

const FILTERS: Array<{ key: JournalFilter; label: string }> = [
  { key: 'all', label: 'All' },
  { key: 'approved', label: 'Approved' },
  { key: 'blocked', label: 'Blocked' },
  { key: 'executed', label: 'Executed' },
]

function matches(entry: JournalEntryData, filter: JournalFilter): boolean {
  if (filter === 'approved') return isApproved(entry)
  if (filter === 'blocked') return isBlocked(entry)
  if (filter === 'executed') return isExecuted(entry)
  return true
}

/**
 * The agent's trade journal: every mutating tool call the audit trail holds,
 * newest first, with a filter derived from each row's own verdict fields.
 * Every count on the chips is a count of rows just rendered; an empty trail
 * reads as "the agent has not acted", which is different from a failed fetch.
 */
export default function JournalList() {
  const { entries, loading, error, empty, refresh } = useJournal()
  const [filter, setFilter] = useState<JournalFilter>('all')

  const counts = useMemo(() => {
    const approved = entries.filter(isApproved).length
    const blocked = entries.filter(isBlocked).length
    const executed = entries.filter(isExecuted).length
    return { all: entries.length, approved, blocked, executed }
  }, [entries])

  const visible = useMemo(
    () => entries.filter((entry) => matches(entry, filter)).sort(byNewestFirst),
    [entries, filter]
  )

  if (loading) {
    return (
      <section className="surface-card pp-journal-card" aria-label="Agent trade journal">
        <LoadingNote label="Reading the agent journal" />
      </section>
    )
  }

  if (error !== null) {
    return (
      <section className="surface-card pp-journal-card" aria-label="Agent trade journal">
        <div className="pp-empty">
          <p className="pp-empty-title">The journal could not be read</p>
          <p className="pp-empty-detail">{error}</p>
        </div>
        <button type="button" className="outline-small-button pp-jretry" onClick={refresh}>
          Try again
        </button>
      </section>
    )
  }

  if (empty) {
    return (
      <section className="surface-card pp-journal-card" aria-label="Agent trade journal">
        <EmptyNote
          title="No agent activity yet"
          detail="The agent has not made a mutating tool call, so there is nothing to audit. When it proposes an order, the proposal, the risk decision and the result appear here."
        />
      </section>
    )
  }

  return (
    <section className="surface-card pp-journal-card" aria-label="Agent trade journal">
      <div className="section-heading compact-heading">
        <div>
          <span className="card-label">Audit trail</span>
          <h2>Trade journal</h2>
        </div>
        <div className="pp-journal-filters">
          {FILTERS.map((option) => (
            <button
              key={option.key}
              type="button"
              className={`pp-jfilter${filter === option.key ? ' active' : ''}`}
              onClick={() => setFilter(option.key)}
              aria-pressed={filter === option.key}
            >
              {option.label}
              <span className="pp-jfilter-count">{counts[option.key]}</span>
            </button>
          ))}
        </div>
      </div>

      {visible.length === 0 ? (
        <EmptyNote
          title="Nothing in this filter"
          detail="No journal rows carry that verdict. Try a different filter to see the rest of the trail."
        />
      ) : (
        <div className="pp-journal-list">
          {visible.map((entry) => (
            <JournalEntry key={entry.id} entry={entry} />
          ))}
        </div>
      )}

      <p className="pp-journal-foot">
        Newest first · {visible.length} of {entries.length} rows
      </p>
    </section>
  )
}
