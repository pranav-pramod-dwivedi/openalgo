import { expect, test } from '@playwright/test'
import {
  emptyScenario,
  fundedScenario,
  mockPranavPayNetwork,
  servePranavPayShell,
} from './pranavpay-helpers'

test.describe('PranavPay sections', () => {
  test.beforeEach(async ({ page }) => {
    await servePranavPayShell(page)
    await page.addInitScript(() => window.localStorage.setItem('pp-onboarded-v1', '1'))
  })

  test('wallet shows the virtual account and keeps the sandbox in its own section', async ({
    page,
  }) => {
    await mockPranavPayNetwork(page, fundedScenario)
    await page.goto('/wallet')

    await expect(page.getByText('Virtual cash').first()).toBeVisible()
    await expect(page.locator('.wallet-balance-number').first()).toContainText('$999.47')
    await expect(page.getByText('Equity').first()).toBeVisible()

    // The Binance balances that used to be listed as "venues" of this account
    // are gone from here, and appear only under the sandbox label.
    const sandbox = page.locator('.pp-sandbox-section article.pp-sandbox-card')
    await expect(sandbox).toContainText('Testnet sandbox (not your money)')
    await expect(sandbox).toContainText('$29,999.12')
  })

  test('wallet shows an empty ledger as no virtual trades yet', async ({ page }) => {
    await mockPranavPayNetwork(page, emptyScenario)
    await page.goto('/wallet')

    await expect(page.getByText('No virtual trades yet').first()).toBeVisible()
    // Deliberately no balance figure while the ledger is empty.
    await expect(page.locator('.wallet-balance-number').first()).toContainText('not reported')
  })

  test('transactions page lists virtual fills with the ledger figures', async ({ page }) => {
    await mockPranavPayNetwork(page, fundedScenario)
    await page.goto('/transactions')

    const realized = page
      .locator('article.metric-card')
      .filter({ has: page.getByText('Realized P&L', { exact: true }) })
    await expect(realized).toContainText('+$0.28')
    await expect(realized).toContainText('across 2 fills')

    await expect(page.getByText('Sold Bitcoin')).toBeVisible()
    await expect(page.getByText('Bought Bitcoin')).toBeVisible()

    await page.getByRole('button', { name: 'Buys' }).click()
    await expect(page.getByText('Bought Bitcoin')).toBeVisible()
    await expect(page.getByText('Sold Bitcoin')).toHaveCount(0)

    await page.getByRole('button', { name: 'All' }).click()
    await expect(page.getByText('Sold Bitcoin')).toBeVisible()

    // The engine stores no per-fill realized result, and the page says so
    // rather than distributing the account total across the fills.
    await expect(page.getByText('Not reported').first()).toBeVisible()
  })

  test('manage page reports the ledger and marks the unpublished limits', async ({ page }) => {
    await mockPranavPayNetwork(page, fundedScenario)
    await page.goto('/manage')

    const hero = page.locator('article.manage-hero')
    await expect(hero).toContainText('Virtual account')
    await expect(hero).toContainText('999.89')
    await expect(hero).toContainText('$999.47')

    const cap = page.locator('article.manage-panel').filter({ hasText: 'Spending cap' })
    await expect(cap).toContainText('Per-order cap')
    await expect(cap).toContainText('not reported')

    const exposure = page
      .locator('article.manage-panel')
      .filter({ has: page.getByRole('heading', { name: 'What is at risk' }) })
    await expect(exposure).toContainText('BTCUSDT')
    await expect(exposure).toContainText('+$0.41')
  })

  test('account page shows the Advanced link as a testnet sandbox', async ({ page }) => {
    await mockPranavPayNetwork(page, fundedScenario)
    await page.goto('/account')

    const advanced = page.getByRole('link', { name: /open the testnet sandbox/i })
    await expect(advanced).toBeVisible()
    await expect(advanced).toHaveAttribute('href', '/dashboard')
  })

  test('sections are reachable at /, /wallet, /transactions, /manage, /account', async ({
    page,
  }) => {
    await mockPranavPayNetwork(page, fundedScenario)

    const routes: Array<[string, string]> = [
      ['/', 'Virtual equity'],
      ['/wallet', 'Virtual cash'],
      ['/transactions', 'Fees charged'],
      ['/manage', 'Spending cap'],
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
    await expect(page.getByText('Virtual cash').first()).toBeVisible()
  })
})