import { expect, test } from '@playwright/test'
import {
  emptyScenario,
  fundedScenario,
  mockPranavPayNetwork,
  type PranavPayScenario,
  servePranavPayShell,
} from './pranavpay-helpers'

/**
 * The paper page's two honesty sections.
 *
 * The checks that run before an order is placed, and what the execution model
 * did with the fills. Both are read off keys the running engine may not send
 * yet, so every case below pins one of three states: reported, absent, or
 * reported-as-empty. The third is the one a page gets wrong most easily, by
 * rendering an absent key as a clean row of zeroes.
 */

/** The funded ledger with a verification layer and priced execution attached. */
const CHECKED_PAPER: Record<string, unknown> = {
  ...fundedScenario.paper,
  allowed_count: 41,
  denied_count: 3,
  verdicts: [
    {
      ts: 1790974611,
      symbol: 'BTCUSDT',
      side: 'BUY',
      allow: false,
      reasons: ['price_deviation'],
      checks: [
        { name: 'price_deviation', passed: false },
        { name: 'fresh_quote', passed: true, detail: 'the quote was two seconds old' },
        { name: 'spread', passed: true },
        { name: 'analyst_available', passed: null },
      ],
    },
    {
      ts: 1790974500,
      symbol: 'ETHUSDT',
      side: 'SELL',
      allow: true,
      checks: [{ name: 'fresh_quote', passed: true }],
    },
  ],
  rejections: [
    {
      ts: 1790974600,
      symbol: 'SOLUSDT',
      side: 'BUY',
      qty: 12,
      reason: 'there was not enough trading in that coin to fill the order',
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
      fill_basis: 'bid',
      spread: 6.4,
      latency_ms: 14,
      unfilled_qty: 0,
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
      fill_basis: 'ask',
      spread: 3.2,
      latency_ms: 42,
      filled_qty: 0.0041,
    },
  ],
}

const checkedScenario: PranavPayScenario = { ...fundedScenario, paper: CHECKED_PAPER }

/** Zero-based column index in the execution table, used to pin one cell. */
const COLUMN = {
  basis: 6,
  spread: 7,
  latency: 8,
  result: 9,
  remainder: 10,
} as const

test.describe('Paper page: checks and execution', () => {
  test.beforeEach(async ({ page }) => {
    await servePranavPayShell(page)
    await page.addInitScript(() => window.localStorage.setItem('pp-onboarded-v1', '1'))
  })

  test('a denied verdict is shown as denied, with its reason in words', async ({ page }) => {
    await mockPranavPayNetwork(page, checkedScenario)
    await page.goto('/paper')

    const checks = page.locator('section.pp-checks')
    await expect(checks.getByRole('heading', { name: 'Checks before trading' })).toBeVisible()

    // Counts come from the engine, not from counting the rows on screen.
    await expect(checks.locator('dt', { hasText: 'Trades allowed' })).toBeVisible()
    await expect(checks.locator('dd').nth(0)).toHaveText('41')
    await expect(checks.locator('dd').nth(1)).toHaveText('3')

    const denied = checks.locator('li[data-verdict="denied"]')
    await expect(denied).toHaveCount(1)
    await expect(denied.locator('.pp-verdict-outcome')).toHaveText('Denied')
    await expect(denied.locator('strong')).toHaveText('BTCUSDT')

    // The reason is a sentence a trader can act on, never the stored slug.
    await expect(denied).toContainText(
      'the price was too far from the live market price for the order to be trusted'
    )
    await expect(denied).not.toContainText('price_deviation')

    // Each check is listed with its own outcome, and one that was never scored
    // says so instead of reading as a pass.
    const perCheck = denied.locator('li[data-check]')
    await expect(perCheck).toHaveCount(4)
    await expect(perCheck.nth(0)).toContainText('price deviation')
    await expect(perCheck.nth(0)).toContainText('failed')
    await expect(perCheck.nth(1)).toContainText('passed')
    await expect(perCheck.nth(1)).toContainText('the quote was two seconds old')
    await expect(perCheck.nth(3)).toContainText('outcome not reported')

    // A denial reads as its own thing, distinct from an allowed one.
    const allowed = checks.locator('li[data-verdict="allowed"]')
    await expect(allowed).toHaveCount(1)
    await expect(allowed.locator('.pp-verdict-outcome')).toHaveText('Allowed')
    await expect(allowed).not.toContainText('the price was too far')

    // A rejection from the execution model is stated in the engine's words.
    await expect(checks.locator('li[data-rejection]')).toHaveCount(0)
    await expect(
      page.locator('section.pp-execution li[data-rejection]').first()
    ).toContainText('there was not enough trading in that coin to fill the order')
  })

  test('the copy says what a denial is, and what an allowed trade went through', async ({
    page,
  }) => {
    await mockPranavPayNetwork(page, checkedScenario)
    await page.goto('/paper')

    await expect(page.locator('section.pp-checks')).toContainText(
      'Every allowed trade passed an independent check that could have denied it.'
    )
    await expect(page.locator('section.pp-checks')).toContainText(
      'A denial is the system working, not an error'
    )
    await expect(page.locator('section.pp-checks')).toContainText('No order was placed')
  })

  test('a partial fill renders the untraded remainder', async ({ page }) => {
    await mockPranavPayNetwork(page, checkedScenario)
    await page.goto('/paper')

    const execution = page.locator('section.pp-execution')
    await expect(execution.getByRole('heading', { name: 'How each fill was priced' })).toBeVisible()
    await expect(execution).toContainText('2 of 2 fills report pricing')

    const rows = execution.locator('tbody tr')
    await expect(rows).toHaveCount(2)

    // Newest first in the ledger: the sell lifted the bid and traded in full.
    const complete = rows.nth(0)
    await expect(complete.locator('td').nth(COLUMN.basis)).toHaveText('at the bid')
    await expect(complete.locator('td').nth(COLUMN.spread)).toHaveText('$6.40')
    await expect(complete.locator('td').nth(COLUMN.latency)).toHaveText('14 ms')
    await expect(complete.locator('td').nth(COLUMN.result)).toHaveText('Complete')
    await expect(complete.locator('td').nth(COLUMN.remainder)).toHaveText(
      '0, nothing left untraded'
    )

    // The buy lifted the ask and only part of it traded. 0.0059 - 0.0041.
    const partial = rows.nth(1)
    await expect(partial.locator('td').nth(COLUMN.basis)).toHaveText('at the ask')
    await expect(partial.locator('td').nth(COLUMN.spread)).toHaveText('$3.20')
    await expect(partial.locator('td').nth(COLUMN.latency)).toHaveText('42 ms')
    await expect(partial.locator('td').nth(COLUMN.result)).toHaveText('Partial')
    await expect(partial.locator('td').nth(COLUMN.remainder)).toHaveText('0.0018 left untraded')
  })

  test('an engine that reports no checks at all says so, never a zero', async ({ page }) => {
    // fundedScenario carries fills but none of the new keys.
    await mockPranavPayNetwork(page, fundedScenario)
    await page.goto('/paper')

    // The page itself is intact.
    await expect(page.locator('.pp-paper-banner')).toContainText('VIRTUAL MONEY')

    const checks = page.locator('section.pp-checks')
    await expect(checks.getByRole('heading', { name: 'Checks before trading' })).toBeVisible()
    await expect(checks).toContainText('The checks have not reported anything yet')
    await expect(checks).toContainText(
      'That is not the same as everything passing'
    )

    // Both counts are unreported. A zero here would be the most reassuring
    // number on the page and the one the payload least supports.
    const counts = checks.locator('.pp-check-counts dd')
    await expect(counts).toHaveCount(2)
    for (const cell of await counts.all()) await expect(cell).toHaveText('not reported')
  })

  test('a fill with no reported pricing reads as not reported in every column', async ({
    page,
  }) => {
    await mockPranavPayNetwork(page, fundedScenario)
    await page.goto('/paper')

    const execution = page.locator('section.pp-execution')
    await expect(execution).toContainText('Pricing not reported')

    // A rejected-orders list that never arrived is an open question, not a
    // clean sheet of nothing.
    await expect(execution).toContainText('No rejection list has been reported')
    await expect(execution).toContainText('not a clean sheet')

    const row = execution.locator('tbody tr').first()
    for (const column of [
      COLUMN.basis,
      COLUMN.spread,
      COLUMN.latency,
      COLUMN.result,
      COLUMN.remainder,
    ]) {
      await expect(row.locator('td').nth(column)).toHaveText('not reported')
    }
  })

  test('a ledger with no fills still states the checks, and says nothing was filled', async ({
    page,
  }) => {
    await mockPranavPayNetwork(page, emptyScenario)
    await page.goto('/paper')

    await expect(page.locator('.pp-paper-banner')).toContainText('VIRTUAL MONEY')

    const checks = page.locator('section.pp-checks')
    await expect(checks).toContainText('The checks have not reported anything yet')

    const execution = page.locator('section.pp-execution')
    await expect(execution).toContainText('No fill has happened yet')
    await expect(execution.locator('table')).toHaveCount(0)
  })
})
