# PranavPay UI workspace (superseded prototype)

This folder is the original static design prototype. It is kept as the design
reference only - **do not ship from it**.

Everything it showed was sample data: a `$148,240.80` balance, four invented
positions (AAPL, BTC, ETH, NVDA), a Chase bank account and a Coinbase wallet,
an "AI accuracy 82%" score, an "18 markets monitored" autopilot, a health score
of 86/100, and deposit/withdraw buttons with no implementation behind them.

## The live version

The working surface is in the app:

| Piece | Path |
| --- | --- |
| Entry point | `frontend/pranavpay.html` -> `frontend/src/pranavpay/main.tsx` |
| Shell and sections | `frontend/src/pages/pranavpay/` |
| Data layer | `frontend/src/pages/pranavpay/useWalletSnapshot.ts` |
| Formatting and derivations | `frontend/src/pages/pranavpay/derive.ts` |
| Scoped stylesheet | `frontend/src/pages/pranavpay/pranavpay.css` |

Served at `/` (plus `/wallet`, `/transactions`, `/manage`, `/account`) by
`blueprints/react_app.py:serve_pranavpay`. OpenAlgo is untouched at
`/dashboard` and its other routes, and the bridge between the two is the
"Advanced view" control on PranavPay's account page.

## What replaced the sample data

Every figure is read from the Binance account at request time:

- Balance, protected savings, tradable cash, margin: `/auth/dashboard-data`,
  which returns the floor split the order path enforces.
- Open positions: `POST /api/v1/positionbook`.
- Fills and realised P&L: `POST /api/v1/tradebook`.
- Working orders: `POST /api/v1/orderbook`.
- Spot and futures balances: the same funds response.

Where the account has nothing to report, the panel renders an explicit empty
state. Nothing is filled in with a plausible-looking number.
