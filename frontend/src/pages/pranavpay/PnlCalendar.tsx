import { money, signedMoney } from './derive'

const WEEKDAY = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat']

export interface CalendarDay {
  key: string
  day: number
  weekday: string
  pnl?: number
  traded: boolean
  today: boolean
  future: boolean
}

/**
 * The trailing 30 days, seven boxes per row, with every day accounted for.
 *
 * Three states, because a trader needs all three: a day that made money, a day
 * that lost money, and a day nothing happened. The prototype's month grid hid
 * the third one, and a flat grid of identical boxes cannot be told apart from
 * a broken one.
 */
export function buildCalendar(byDay: Map<string, number>, days = 30): CalendarDay[] {
  const today = new Date()
  today.setHours(0, 0, 0, 0)
  const out: CalendarDay[] = []
  for (let offset = days - 1; offset >= 0; offset -= 1) {
    const date = new Date(today)
    date.setDate(date.getDate() - offset)
    const key = date.toISOString().slice(0, 10)
    out.push({
      key,
      day: date.getDate(),
      weekday: WEEKDAY[date.getDay()],
      pnl: byDay.get(key),
      traded: byDay.has(key),
      today: offset === 0,
      future: false,
    })
  }
  return out
}

export function PnlCalendar({ byDay }: { byDay: Map<string, number> }) {
  const days = buildCalendar(byDay)
  const traded = days.filter((day) => day.traded)
  const green = days.filter((day) => (day.pnl ?? 0) > 0).length
  const red = days.filter((day) => (day.pnl ?? 0) < 0).length

  return (
    <>
      <div
        className="pp-calendar"
        role="img"
        aria-label="Realised result for each of the last 30 days"
      >
        {days.map((day) => {
          const state = !day.traded ? 'quiet' : (day.pnl ?? 0) >= 0 ? 'gain' : 'loss'
          return (
            <div
              key={day.key}
              className={`pp-day is-${state}${day.today ? ' is-today' : ''}`}
              title={
                day.traded ? `${day.key}: ${signedMoney(day.pnl)}` : `${day.key}: no closed fills`
              }
            >
              <span className="pp-day-num">{day.day}</span>
              <span className="pp-day-mark">
                {day.traded ? ((day.pnl ?? 0) >= 0 ? '+' : '−') : '·'}
              </span>
            </div>
          )
        })}
      </div>

      <p className="pp-calendar-note">
        {traded.length === 0
          ? 'No closed fills in the last 30 days.'
          : `${green} day${green === 1 ? '' : 's'} up, ${red} down, ${days.length - traded.length} no trade.`}
      </p>

      <div className="calendar-legend">
        <span>
          <i className="positive-key" /> Day made money
        </span>
        <span>
          <i className="negative-key" /> Day lost money
        </span>
        <span>
          <i className="quiet-key" /> No trade
        </span>
      </div>
    </>
  )
}

/** The per-day money figure, so a day can be read without hovering. */
export function dayValue(day: CalendarDay): string {
  return day.traded ? signedMoney(day.pnl) : '—'
}

export { money }
