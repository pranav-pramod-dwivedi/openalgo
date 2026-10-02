import { SANDBOX_LABEL } from './account'
import { EmptyNote } from './components'
import { money, percent, qty } from './derive'
import { useLiquidation } from './useLiquidation'

/**
 * Liquidation panel for the Binance sandbox.
 *
 * Every liquidation price and margin figure here is what Binance reported for
 * that testnet position; a field the exchange did not report renders as
 * "not reported", never as an estimate. The only derived figure is the
 * distance: the percentage move from the reported mark to the reported
 * liquidation price, labelled as such.
 *
 * This belongs to the sandbox and to nothing else. The virtual paper account
 * has no liquidation price and no margin mode, so no figure here may be read
 * against it.
 */
export default function LiquidationPanel({
  funds,
}: {
  funds: Parameters<typeof useLiquidation>[0]
}) {
  const { rows, hasPositions, totalMarginCommitted, tradable } = useLiquidation(funds)

  return (
    <article className="manage-panel surface-card">
      <div className="section-heading compact-heading">
        <div>
          <span className="card-label pp-sandbox-label">{SANDBOX_LABEL}</span>
          <h2>How far from liquidation</h2>
        </div>
      </div>
      {!hasPositions ? (
        <EmptyNote
          title="No open sandbox positions"
          detail="Nothing is open on the testnet, so the exchange reports no liquidation price and no margin in use. Your virtual account is unaffected."
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
              <strong>Total sandbox margin committed</strong>
              <small>Sum of the reported per-position margins</small>
            </span>
            <b>{totalMarginCommitted !== null ? money(totalMarginCommitted) : 'not reported'}</b>
          </div>
          <div>
            <span>
              <strong>Sandbox available</strong>
              <small>The most any new sandbox order may cost</small>
            </span>
            <b>{tradable ? money(tradable.tradable) : 'not reported'}</b>
          </div>
        </div>
      )}
      <div className="pp-note">
        Liquidation prices and margins are reported by Binance for its testnet wallet and shown as
        received. They are practice funds, and none of this applies to your virtual account.
      </div>
    </article>
  )
}
