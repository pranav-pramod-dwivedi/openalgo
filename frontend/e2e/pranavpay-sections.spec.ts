import { expect, test } from '@playwright/test'
import { fundedScenario, mockPranavPayNetwork, servePranavPayShell } from './pranavpay-helpers'

test.describe('PranavPay sections', () => {
  test.beforeEach(async ({ page }) => {
    await servePranavPayShell(page)
    await mockPranavPayNetwork(page, fundedScenario)
  })

  test('transactions page lists mocked fills with correct sums', async ({ page }) => {
    await page.goto('/transactions')

    // Closed P&L is the 150 carried by the SELL fill; traded value is
    // 5000 + 3000 across the two fills.
    const realised = page.locator('article.metric-card').filter({ hasText: 'Realised P' })
    await expect(realised).toContainText('+$150.00')
    await expect(realised).toContainText('Across 2 fills')

    const traded = page.locator('article.metric-card').filter({ hasText: 'Traded value' })
    await expect(traded).toContainText('$8,000.00')
    await expect(traded).toContainText('0 orders still working')

    await expect(page.getByText('Bought Bitcoin')).toBeVisible()
    await expect(page.getByText('Sold Ethereum')).toBeVisible()

    await page.getByRole('button', { name: 'Buys' }).click()
    await expect(page.getByText('Bought Bitcoin')).toBeVisible()
    await expect(page.getByText('Sold Ethereum')).toHaveCount(0)

    await page.getByRole('button', { name: 'All' }).click()
    await expect(page.getByText('Sold Ethereum')).toBeVisible()
  })

  test('manage page shows the floor and cap from mocked funds', async ({ page }) => {
    await page.goto('/manage')

    // Floor 20000 renders without the dollar sign in the hero figure.
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
    await page.goto('/account')

    const advanced = page.getByRole('link', { name: /open advanced view/i })
    await expect(advanced).toBeVisible()
    await expect(advanced).toHaveAttribute('href', '/dashboard')
  })

  test('sections are reachable at /, /wallet, /transactions, /manage, /account', async ({
    page,
  }) => {
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
