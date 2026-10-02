import type { Page } from '@playwright/test'

/**
 * Shared fixtures for the PranavPay surface.
 *
 * Production serves the PranavPay bundle at `/`, `/wallet`, `/transactions`,
 * `/manage` and `/account`. Under the repo's normal e2e command (`npm run dev`)
 * Vite serves the OpenAlgo app at those paths instead, so document requests for
 * them are fulfilled here with the dev server's own `pranavpay.html` shell.
 * Every other request (module scripts, assets) passes through untouched.
 *
 * All data requests are mocked: the specs never touch a live server or Binance.
 */

const SHELL_PATHS = new Set(['/', '/wallet', '/transactions', '/manage', '/account'])

function isPranavPayShellRequest(url: URL): boolean {
  const local = url.hostname === 'localhost' || url.hostname === '127.0.0.1'
  return local && SHELL_PATHS.has(url.pathname)
}

/** Serve the PranavPay shell for its five routes while running under `vite dev`. */
export async function servePranavPayShell(page: Page): Promise<void> {
  await page.route(isPranavPayShellRequest, async (route) => {
    const requestUrl = new URL(route.request().url())
    const shellResponse = await fetch(`${requestUrl.origin}/pranavpay.html`)
    await route.fulfill({
      status: shellResponse.status,
      contentType: 'text/html',
      body: await shellResponse.text(),
    })
  })
}

export interface PranavPayScenario {
  funds: Record<string, unknown>
  positions: Array<Record<string, unknown>>
  trades: Array<Record<string, unknown>>
  orders: Array<Record<string, unknown>>
}

const FUNDED_FUNDS: Record<string, unknown> = {
  wallet_total_usd: '30000',
  equity_usd: '30000',
  trading_floor: '20000',
  savings_usdt: '20000',
  tradable_usdt: '10000',
  open_notional_usd: '5000',
  availablecash: '10000',
  m2munrealized: '250.00',
  m2mrealized: '120.50',
  utiliseddebits: '5000',
  spot_usdt: '20000',
  futures_usdt: '10000',
  is_live: false,
  positions: [],
  spot_balances: [{ asset: 'USDT', free: 20000, locked: 0, total: 20000 }],
  futures_balances: [{ asset: 'USDT', balance: 10000, available: 5000 }],
}

const FUNDED_POSITIONS: Array<Record<string, unknown>> = [
  {
    symbol: 'BTCUSDT',
    exchange: 'CRYPTO',
    product: 'FUTURES',
    quantity: 0.1,
    average_price: 50000,
    ltp: 52000,
    pnl: 200,
    pnlpercent: 4.0,
  },
]

const FUNDED_TRADES: Array<Record<string, unknown>> = [
  {
    symbol: 'BTCUSDT',
    exchange: 'CRYPTO',
    action: 'BUY',
    quantity: 0.1,
    average_price: 50000,
    trade_value: 5000,
    product: 'FUTURES',
    orderid: 'ord-1',
    timestamp: '2026-09-18 03:53:31',
  },
  {
    symbol: 'ETHUSDT',
    exchange: 'CRYPTO',
    action: 'SELL',
    quantity: 1,
    average_price: 3000,
    trade_value: 3000,
    pnl: 150,
    product: 'FUTURES',
    orderid: 'ord-2',
    timestamp: '2026-09-19 10:00:00',
  },
]

const EMPTY_FUNDS: Record<string, unknown> = {
  wallet_total_usd: '0',
  equity_usd: '0',
  trading_floor: '0',
  savings_usdt: '0',
  tradable_usdt: '0',
  open_notional_usd: '0',
  availablecash: '0',
  m2munrealized: '0',
  m2mrealized: '0',
  utiliseddebits: '0',
  spot_usdt: '0',
  futures_usdt: '0',
  is_live: false,
  positions: [],
  spot_balances: [],
  futures_balances: [],
}

export const fundedScenario: PranavPayScenario = {
  funds: FUNDED_FUNDS,
  positions: FUNDED_POSITIONS,
  trades: FUNDED_TRADES,
  orders: [],
}

export const emptyScenario: PranavPayScenario = {
  funds: EMPTY_FUNDS,
  positions: [],
  trades: [],
  orders: [],
}

function json(body: unknown): { status: number; contentType: string; body: string } {
  return { status: 200, contentType: 'application/json', body: JSON.stringify(body) }
}

/**
 * Mock every network call the surface makes: session sync, dashboard funds,
 * the trading books, live-mark polls, candle history and the streaming stack.
 * The WebSocket config answers with an error so no socket is ever dialled.
 */
export async function mockPranavPayNetwork(page: Page, scenario: PranavPayScenario): Promise<void> {
  await page.route('**/auth/session-status', (route) =>
    route.fulfill(
      json({
        status: 'success',
        logged_in: true,
        broker: 'binance',
        user: 'binance_demo',
        api_key: 'e2e-test-key',
        active_sessions: 1,
      })
    )
  )
  await page.route('**/auth/dashboard-data', (route) =>
    route.fulfill(json({ status: 'success', data: scenario.funds }))
  )
  await page.route('**/auth/analyzer-mode', (route) =>
    route.fulfill(json({ status: 'success', data: { analyze_mode: false } }))
  )
  await page.route('**/auth/csrf-token', (route) =>
    route.fulfill(json({ csrf_token: 'mock-csrf-token' }))
  )
  await page.route('**/api/broker/capabilities', (route) =>
    route.fulfill(
      json({
        status: 'success',
        data: {
          broker_name: 'binance',
          broker_type: 'crypto',
          supported_exchanges: ['CRYPTO'],
          leverage_config: false,
        },
      })
    )
  )
  await page.route('**/api/v1/positionbook', (route) =>
    route.fulfill(json({ status: 'success', data: scenario.positions }))
  )
  await page.route('**/api/v1/tradebook', (route) =>
    route.fulfill(json({ status: 'success', data: scenario.trades }))
  )
  await page.route('**/api/v1/orderbook', (route) =>
    route.fulfill(
      json({
        status: 'success',
        data: {
          orders: scenario.orders,
          statistics: {
            total_buy_orders: 0,
            total_sell_orders: 0,
            total_completed_orders: 0,
            total_open_orders: 0,
            total_rejected_orders: 0,
          },
        },
      })
    )
  )
  await page.route('**/api/v1/multiquotes', (route) =>
    route.fulfill(json({ status: 'success', results: [] }))
  )
  await page.route('**/api/websocket/config', (route) =>
    route.fulfill(json({ status: 'error', message: 'mocked offline' }))
  )
  await page.route('**/api/websocket/apikey', (route) =>
    route.fulfill(json({ status: 'error', message: 'mocked offline' }))
  )
  await page.route((url) => url.pathname === '/historify/api/candles', (route) =>
    route.fulfill(json({ status: 'success', data: [] }))
  )
  await page.route('**/socket.io/**', (route) => route.abort())
}
