import { money, percent, portfolioStats, signedMoney, type FillRow } from './derive'
import { EmptyNote } from './components'

/**
 * The portfolio in numbers.
 *
 * Everything here is a count, a sum or a ratio of two real figures taken from
 * the fills. There is deliberately no composite score: a single "health" number
 * hides which of its inputs is bad, and the inputs are the part a trader acts
 * on.
 */
export function PortfolioFacts({
  fills,
  byDay,
  capitalAtRisk,
  equity,
  floor,
  tradable,
}: {
  fills: FillRow[]
  byDay: Map<string, number>
  capitalAtRisk: number
  equity: number | null
  floor: number | null
  tradable: number | null
}) {
  const stats = portfolioStats(fills, byDay, capitalAtRisk)

  if (stats.closedFills === 0) {
    return (
      <EmptyNote
        title="No closed trades yet"
        detail="Win rate, profit factor and averages appear once a position has been closed."
      />
    )
  }

  const gainAboveFloor = equity !== null && floor !== null ? equity - floor : null

  return (
    <div className="pp-facts">
      <div className="pp-facts-row">
        <span>Win rate</span>
        <strong>{stats.winRate.toFixed(0)}%</strong>
        <small>
          {stats.wins} of {stats.closedFills} closed
        </small>
      </div>

      <div className="pp-facts-row">
        <span>Profit factor</span>
        <strong>{stats.profitFactor === null ? '—' : stats.profitFactor.toFixed(2)}</strong>
        <small>
          {stats.profitFactor === null
            ? 'No losing trades yet'
            : `${money(stats.grossProfit)} won vs ${money(stats.grossLoss)} lost`}
        </small>
      </div>

      <div className="pp-facts-row">
        <span>Average win / loss</span>
        <strong>
          {signedMoney(stats.averageWin)} / {signedMoney(-stats.averageLoss)}
        </strong>
        <small>
          {stats.averageWin > 0 && stats.averageLoss > 0
            ? `Wins are ${(stats.averageWin / stats.averageLoss).toFixed(2)}x losses`
            : 'Only one side has closed so far'}
        </small>
      </div>

      <div className="pp-facts-row">
        <span>Largest win</span>
        <strong>{stats.largestWin ? signedMoney(stats.largestWin.pnl) : '—'}</strong>
        <small>
          {stats.largestWin
            ? `${stats.largestWin.asset} · ${stats.largestWin.quantity} @ ${money(stats.largestWin.price)}`
            : '—'}
        </small>
      </div>

      <div className="pp-facts-row">
        <span>Largest loss</span>
        <strong>{stats.largestLoss ? signedMoney(stats.largestLoss.pnl) : '—'}</strong>
        <small>
          {stats.largestLoss
            ? `${stats.largestLoss.asset} · ${stats.largestLoss.quantity} @ ${money(stats.largestLoss.price)}`
            : 'Nothing has lost money'}
        </small>
      </div>

      <div className="pp-facts-row">
        <span>Trading days</span>
        <strong>
          {stats.daysGreen} / {stats.daysGreen + stats.daysRed}
        </strong>
        <small>
          {stats.daysGreen + stats.daysRed === 0
            ? 'No day closed yet'
            : `${stats.daysGreen} up, ${stats.daysRed} down`}
        </small>
      </div>

      {gainAboveFloor !== null && (
        <div className="pp-facts-row">
          <span>Trading added</span>
          <strong>{signedMoney(gainAboveFloor)}</strong>
          <small>
            Everything above the {money(floor)} savings floor
            {tradable !== null && equity !== null && equity > 0
              ? ` · ${percent((gainAboveFloor / equity) * 100)} of capital`
              : ''}
          </small>
        </div>
      )}
    </div>
  )
}