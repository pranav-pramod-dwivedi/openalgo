import { expect, test } from '@playwright/test'
import {
  emptyScenario,
  fundedScenario,
  mockPranavPayNetwork,
  servePranavPayShell,
} from './pranavpay-helpers'

test.describe('PranavPay overview', () => {
  test.beforeEach(async ({ page }) => {
    await servePranavPayShell(page)
    // Skip the first-run tour so it never blocks rail clicks.
    await page.addInitScript(() => window.localStorage.setItem('pp-onboarded-v1', '1'))
  })

  test('renders every account card from the paper ledger, not the Binance sandbox', async ({
    page,
  }) => {
    await mockPranavPayNetwork(page, fundedScenario)
    await page.goto('/')

    // The three cards that used to read testnet "Trading power / Protected /
    // Exposure" now read the virtual account's own figures: cash 999.4748,
    // fees 0.4004, and exposure 0.0059 BTC @ 84468.89 = 498.37 over 999.8877.
    const cash = page.locator('article.metric-card').filter({ hasText: 'Virtual cash' })
    await expect(cash).toContainText('$999.47')

    const fees = page.locator('article.metric-card').filter({ hasText: 'Fees charged' })
    await expect(fees).toContainText('$0.40')

    const exposure = page.locator('article.metric-card').filter({ hasText: 'Open exposure' })
    await expect(exposure).toContainText('+49.84%')

    await expect(page.locator('article.balance-card')).toContainText('Virtual equity')
    // The headline rounds to whole dollars; the capital split carries the cents.
    await expect(page.locator('article.balance-card')).toContainText('$999')
    await expect(page.locator('.pp-split')).toContainText('$999.89')
    await expect(page.locator('article.balance-card')).toContainText('realized plus unrealized')

    // The open virtual position and its fills surface on the same page.
    await expect(page.getByText('BTCUSDT').first()).toBeVisible()
    await expect(page.getByText('Sold Bitcoin').first()).toBeVisible()
  })

  test('never prints a sandbox balance on an account card', async ({ page }) => {
    await mockPranavPayNetwork(page, fundedScenario)
    await page.goto('/')

    // The sandbox is on a $30,000 scale and the account on a $1,000 one. Its
    // equity must not surface as an account figure anywhere above the sandbox
    // panel, and its label must be present wherever it does.
    await expect(page.locator('article.balance-card')).not.toContainText('$29,999')
    await expect(page.locator('article.metric-card').filter({ hasText: 'Virtual cash' })).not.toContainText(
      '$99.99'
    )

    const sandbox = page.locator('article.pp-sandbox-card')
    await expect(sandbox).toContainText('Testnet sandbox (not your money)')
    await expect(sandbox).toContainText('$29,999.12')
  })

  test('an empty ledger reads as no virtual trades yet, not as a zero balance', async ({ page }) => {
    await mockPranavPayNetwork(page, emptyScenario)
    await page.goto('/')

    await expect(page.getByText('No virtual trades yet').first()).toBeVisible()
    await expect(page.getByText(/ledger is empty because the worker has not run/i).first()).toBeVisible()
    await expect(page.getByText(/start it with \.\/paper/i).first()).toBeVisible()

    // A ledger that reported nothing must not read as a real balance anywhere.
    await expect(page.locator('article.balance-card')).toContainText('not reported')
    await expect(page.locator('article.balance-card')).not.toContainText('$0.00')
    await expect(page.getByText('No open virtual positions').first()).toBeVisible()
    await expect(page.getByText('Nothing to allocate yet').first()).toBeVisible()
    await expect(page.getByText('No realized P&L recorded yet').first()).toBeVisible()
  })
})