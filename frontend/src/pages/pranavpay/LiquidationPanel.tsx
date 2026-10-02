import { EmptyNote, LoadingNote, useSnapshot } from './components'
import { money, percent, qty } from './derive'
import { useLiquidation } from './useLiquidation'

/**
 * Liquidation panel for the Manage page.
 *
 * Every liquidation price and margin figure here is what Binance reported
 * for the position; a field the exchange did not report renders as
 * "not reported", never as an estimate. The only derived figure is the
 * distance: the percentage move from the reported mark to the reported
 * liquidation price, labelled as such.
 */
export default function LiquidationPanel() {
  const snapshot = useSnapshot()
  const { rows, hasPositions, totalMarginCommitted, tradable } = useLiquidation(snapshot)

  if (snapshot.loading && !snapshot.funds) {
    return (
      <article className="manage-panel surface-card">
        <LoadingNote label="Reading liquidation levels" />
      </article>
    )
  }

  return (
    <article className="manage-panel surface-card">
      <div className="section-heading compact-heading">
        <div>
          <span className="card-label">Liquidation</span>
          <h2>How far from liquidation</h2>
        </div>
      </div>
      {!hasPositions ? (
        <EmptyNote
          title="No open futures positions"
          detail="Nothing is open, so the exchange reports no liquidation price and no margin in use."
        />
      ) : (
        <div className="settings-list">
          {rows.map((row) => (
            <div key={row.symbol}>
              <span>
                <strong>
                  {row.symbol} · {row.side}
                  {row.leverage !== null ? ` · ${row.leverage}x` : ''}
                </strong>
                <small>
                  {qty(Math.abs(row.quantity))} · mark {money(row.markPrice)} · liquidation{' '}
                  {row.liquidationPrice !== null ? money(row.liquidationPrice) : 'not reported'}
                  {' · '}
                  {row.distancePercent !== null
                    ? `${percent(row.distancePercent)} from mark`
                    : 'distance unavailable'}
                </small>
              </span>
              <b>{row.marginUsed !== null ? money(row.marginUsed) : 'not reported'}</b>
            </div>
          ))}
          <div>
            <span>
              <strong>Total margin committed</strong>
              <small>Sum of the reported per-position margins</small>
            </span>
            <b>{totalMarginCommitted !== null ? money(totalMarginCommitted) : 'not reported'}</b>
          </div>
          <div>
            <span>
              <strong>Tradable now</strong>
              <small>The most any new order may cost</small>
            </span>
            <b>{money(tradable)}</b>
          </div>
        </div>
      )}
      <div className="pp-note">
        Liquidation prices and margins are reported by Binance and shown as received. The distance
        is the percentage move between the two reported prices, not an estimate.
      </div>
    </article>
  )
}
