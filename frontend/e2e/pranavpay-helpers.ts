import type { Page } from '@playwright/test'

/**
 * Shared fixtures for the PranavPay surface.
 *
 * Production serves the PranavPay bundle at `/`, `/wallet`, `/transactions`,
 * `/manage`, `/account` and `/paper`. Under the repo's normal e2e command
 * (`npm run dev`) Vite serves the OpenAlgo app at those paths instead, so
 * document requests for them are fulfilled here with the dev server's own
 * `pranavpay.html` shell. Every other request (module scripts, assets) passes
 * through untouched.
 *
 * Two accounts are mocked and they are deliberately on different scales, so a
 * spec that renders one of them as the other fails loudly:
 *
 *   - `/api/paper/state` is the virtual account, roughly $1000.
 *   - `/auth/dashboard-data` is the Binance testnet sandbox, roughly $30,000.
 *
 * No spec may assert that a sandbox figure appears on an account card.
 */

const SHELL_PATHS = new Set(['/', '/wallet', '/transactions', '/manage', '/account', '/paper'])

function isPranavPayShellRequest(url: URL): boolean {
  const local = url.hostname === 'localhost' || url.hostname === '127.0.0.1'
  return local && SHELL_PATHS.has(url.pathname)
}

/** Serve the PranavPay shell for its routes while running under `vite dev`. */
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
  /** The paper ledger: the user's virtual account. */
  paper: Record<string, unknown>
  /** The Binance testnet funds: a sandbox, never the account. */
  sandbox: Record<string, unknown>
}

/** A virtual account that has traded: ~$999.89 equity, one open position. */
const FUNDED_PAPER: Record<string, unknown> = {
  starting_cash: 1000,
  cash: 999.4748,
  virtual_balance: 999.4748,
  short_margin_locked: 0,
  equity: 999.8877,
  realized: 0.2757,
  unrealized: 0.4135,
  fees: 0.4004,
  peak_equity: 999.8877,
  drawdown: 0,
  positions: [
    {
      symbol: 'BTCUSDT',
      side: 'BUY',
      qty: 0.0059,
      entry: 84450,
      mark: 84468.89,
      mark_live: true,
      unrealized: 0.4135,
      strategy_id: 'momentum-v2',
      opened: 1790974294,
    },
  ],
  fills: [
    {
      order_id: 'plan-1',
      symbol: 'BTCUSDT',
      side: 'SELL',
      qty: 0.0059,
      price: 84468.89,
      fee: 0.1999,
      slippage: 16.89,
      ts: 1790974294.01115,
    },
    {
      order_id: 'close-1',
      symbol: 'BTCUSDT',
      side: 'BUY',
      qty: 0.0059,
      price: 84450,
      fee: 0.1999,
      slippage: 16.89,
      ts: 1790973353.96429,
    },
  ],
  equity_curve: [
    {
      ts: 1790973353,
      cash: 999.4,
      equity: 999.7,
      realized: 0.27,
      unrealized: 0.3,
      fees: 0.4,
      slippage: 16.8,
      drawdown: 0,
    },
    {
      ts: 1790974294,
      cash: 999.4748,
      equity: 999.8877,
      realized: 0.2757,
      unrealized: 0.4135,
      fees: 0.4004,
      slippage: 16.89,
      drawdown: 0,
    },
  ],
  strategies: [
    {
      id: 'momentum-v2',
      family: 'momentum',
      params: '{}',
      metrics: JSON.stringify({ trades: 42, net_pnl: 31.2, max_drawdown: 8.4 }),
      status: 'active',
      created: 1790960000,
      version: 2,
    },
  ],
  experiment_count: 105,
  worker: { ts: 1790974611.4, status: 'running', error: null, cycle: 48 },
  decisions: [],
  jev_verdicts: 12,
  generated_at: 1790974611.5,
}

/** A ledger the worker has never written to: no fills, no positions, no curve. */
const EMPTY_PAPER: Record<string, unknown> = {
  starting_cash: 1000,
  cash: 0,
  virtual_balance: 0,
  short_margin_locked: 0,
  equity: 0,
  realized: 0,
  unrealized: 0,
  fees: 0,
  peak_equity: 0,
  drawdown: 0,
  positions: [],
  fills: [],
  equity_curve: [],
  strategies: [],
  experiment_count: 0,
  worker: null,
  decisions: [],
  jev_verdicts: 0,
  generated_at: 1790974611.5,
}

/** Binance testnet: ~$30,000 of practice funds, on a different scale entirely. */
const SANDBOX_FUNDS: Record<string, unknown> = {
  wallet_total_usd: '30000',
  equity_usd: '29999.12',
  trading_floor: '20000',
  savings_usdt: '20000',
  tradable_usdt: '99.99',
  open_notional_usd: '5000',
  availablecash: '99.99',
  m2munrealized: '250.00',
  m2mrealized: '120.50',
  utiliseddebits: '5000',
  spot_usdt: '20000',
  futures_usdt: '9999.99',
  is_live: false,
  positions: [],
  spot_balances: [{ asset: 'USDT', free: 20000, locked: 0, total: 20000 }],
  futures_balances: [{ asset: 'USDT', balance: 9999.99, available: 99.99 }],
}

export const fundedScenario: PranavPayScenario = {
  paper: FUNDED_PAPER,
  sandbox: SANDBOX_FUNDS,
}

export const emptyScenario: PranavPayScenario = {
  paper: EMPTY_PAPER,
  sandbox: SANDBOX_FUNDS,
}

function json(body: unknown): { status: number; contentType: string; body: string } {
  return { status: 200, contentType: 'application/json', body: JSON.stringify(body) }
}

/**
 * Mock every network call the surface makes: session sync, the paper ledger,
 * the sandbox funds, live-mark polls, candle history and the streaming stack.
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
  // The account. Everything the surface calls an account figure comes from here.
  await page.route('**/api/paper/state', (route) =>
    route.fulfill(json({ status: 'success', data: scenario.paper }))
  )
  // The sandbox. Kept on a visibly different scale so a mixed card is obvious.
  await page.route('**/auth/dashboard-data', (route) =>
    route.fulfill(json({ status: 'success', data: scenario.sandbox }))
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
    route.fulfill(json({ status: 'success', data: [] }))
  )
  await page.route('**/api/v1/tradebook', (route) =>
    route.fulfill(json({ status: 'success', data: [] }))
  )
  await page.route('**/api/v1/orderbook', (route) =>
    route.fulfill(
      json({
        status: 'success',
        data: {
          orders: [],
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