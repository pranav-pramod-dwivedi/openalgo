import { expect, test } from '@playwright/test'
import {
  emptyScenario,
  fundedScenario,
  mockPranavPayNetwork,
  servePranavPayShell,
} from './pranavpay-helpers'

test.describe('PranavPay surface data', () => {
  test.beforeEach(async ({ page }) => {
    await servePranavPayShell(page)
    // Skip the first-run tour so it never blocks rail clicks.
    await page.addInitScript(() => window.localStorage.setItem('pp-onboarded-v1', '1'))
  })

  test('overview renders Tradable, Protected and Exposure from mocked dashboard data', async ({
    page,
  }) => {
    await mockPranavPayNetwork(page, fundedScenario)
    await page.goto('/')

    // funds: equity 30000, tradable 10000, savings 20000; open 0.1 BTC @ 52000 = 5200,
    // so exposure reads 5200 / 30000 = 17.33%.
    const tradable = page
      .locator('article.metric-card')
      .filter({ hasText: /Trading power|Tradable/ })
    await expect(tradable.first()).toContainText('$10,000.00')

    const protectedCard = page.locator('article.metric-card').filter({ hasText: 'Protected' })
    await expect(protectedCard).toContainText('$20,000.00')

    const exposure = page.locator('article.metric-card').filter({ hasText: 'Exposure' })
    await expect(exposure).toContainText('+17.33%')
  })

  test('empty account shows explicit empty states, never a zero masquerading as data', async ({
    page,
  }) => {
    await mockPranavPayNetwork(page, emptyScenario)
    await page.goto('/')

    for (const copy of [
      'No open positions',
      'No fills yet',
      'Nothing to allocate yet',
      'No realised P&L yet',
      'Nothing needs you',
      'No capital at risk',
    ]) {
      await expect(page.getByText(copy).first()).toBeVisible()
    }

    // Flat exposure reads as Flat, not 0.00%.
    await expect(
      page.locator('article.metric-card').filter({ hasText: 'Exposure' })
    ).toContainText('Flat')

    // A share of nothing is unknown, not 0%.
    await expect(page.locator('.signal-row').filter({ hasText: 'Tradable share' })).toContainText(
      '—'
    )

    // No position table is rendered when there is nothing to tabulate.
    await expect(page.locator('table')).toHaveCount(0)
  })

  test('transactions page lists mocked fills with correct sums', async ({ page }) => {
    await mockPranavPayNetwork(page, fundedScenario)
    await page.goto('/transactions')

    // Closed P&L is the 150 carried by the SELL fill; traded value is
    // 5000 + 3000 across the two fills.
    const realised = page
      .locator('article.metric-card')
      .filter({ has: page.getByText('Realised P&L', { exact: true }) })
    await expect(realised).toContainText('+$150.00')
    await expect(realised).toContainText('Across 2 fills')

    const traded = page.locator('article.metric-card').filter({ hasText: 'Traded value' })
    await expect(traded).toContainText('$8,000.00')
    await expect(traded).toContainText('0 orders still working')

    await expect(page.getByText('Bought Bitcoin').first()).toBeVisible()
    await expect(page.getByText('Sold Ethereum').first()).toBeVisible()
  })

  test('manage page shows the floor and cap from mocked funds', async ({ page }) => {
    await mockPranavPayNetwork(page, fundedScenario)
    await page.goto('/manage')

    const hero = page.locator('article.manage-hero')
    await expect(hero).toContainText('20,000.00')
    await expect(hero).toContainText('$20,000.00')
    await expect(hero).toContainText('$10,000.00')

    // The cap is the tradable balance: 10000 of 30000 equity is 33.33%.
    const cap = page.locator('article.manage-panel').filter({ hasText: 'Spending cap' })
    await expect(cap).toContainText('$10,000.00')
    await expect(cap).toContainText('+33.33%')
  })

  test('account page shows the Advanced link to /dashboard', async ({ page }) => {
    await mockPranavPayNetwork(page, fundedScenario)
    await page.goto('/account')

    const advanced = page.getByRole('link', { name: /open advanced view/i })
    await expect(advanced).toBeVisible()
    await expect(advanced).toHaveAttribute('href', '/dashboard')
  })

  test('sections are reachable at /, /wallet, /transactions, /manage, /account', async ({
    page,
  }) => {
    await mockPranavPayNetwork(page, fundedScenario)

    const routes: Array<[string, string]> = [
      ['/', 'Total balance'],
      ['/wallet', 'Available to trade'],
      ['/transactions', 'Traded value'],
      ['/manage', 'Savings floor'],
      ['/account', 'Advanced view'],
    ]
    for (const [path, marker] of routes) {
      await page.goto(path)
      await expect(page.getByText(marker).first()).toBeVisible()
    }

    // The rail reaches each section client-side too.
    await page.goto('/')
    await page.getByRole('link', { name: 'Wallet' }).first().click()
    await expect(page).toHaveURL(/\/wallet$/)
    await expect(page.getByText('Available to trade').first()).toBeVisible()
  })
})
