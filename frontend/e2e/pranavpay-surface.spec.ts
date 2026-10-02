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

  test('overview renders the virtual cash, fees and exposure from the paper ledger', async ({
    page,
  }) => {
    await mockPranavPayNetwork(page, fundedScenario)
    await page.goto('/')

    // paper: cash 999.4748, fees 0.4004; open 0.0059 BTC @ 84468.89 = 498.37,
    // so exposure reads 498.37 / 999.8877 = 49.84%.
    const cash = page.locator('article.metric-card').filter({ hasText: 'Virtual cash' })
    await expect(cash).toContainText('$999.47')

    const fees = page.locator('article.metric-card').filter({ hasText: 'Fees charged' })
    await expect(fees).toContainText('$0.40')

    const exposure = page.locator('article.metric-card').filter({ hasText: 'Open exposure' })
    await expect(exposure).toContainText('+49.84%')
  })

  test('the sandbox is labelled wherever its figures appear', async ({ page }) => {
    await mockPranavPayNetwork(page, fundedScenario)
    await page.goto('/')

    const sandbox = page.locator('article.pp-sandbox-card')
    await expect(sandbox).toContainText('Testnet sandbox (not your money)')
    await expect(sandbox).toContainText('$29,999.12')
    await expect(sandbox).toContainText('$99.99')
    await expect(sandbox.getByRole('link', { name: /open the testnet sandbox/i })).toHaveAttribute(
      'href',
      '/dashboard'
    )
  })

  test('empty account shows explicit empty states, never a zero masquerading as data', async ({
    page,
  }) => {
    await mockPranavPayNetwork(page, emptyScenario)
    await page.goto('/')

    for (const copy of ['No virtual trades yet', 'No open virtual positions']) {
      await expect(page.getByText(copy).first()).toBeVisible()
    }

    // A share of an unreported equity is unknown, not 0%.
    await expect(page.locator('.signal-row').filter({ hasText: 'Cash share' })).toContainText(
      'not reported'
    )

    // No position table is rendered when there is nothing to tabulate.
    await expect(page.locator('table')).toHaveCount(0)
  })

  test('transactions page lists virtual fills with the ledger figures', async ({ page }) => {
    await mockPranavPayNetwork(page, fundedScenario)
    await page.goto('/transactions')

    // Realized is the ledger's account total, not a sum over fills: the paper
    // engine stores no realized result on an individual fill.
    const realized = page
      .locator('article.metric-card')
      .filter({ has: page.getByText('Realized P&L', { exact: true }) })
    await expect(realized).toContainText('+$0.28')
    await expect(realized).toContainText('across 2 fills')

    const fees = page.locator('article.metric-card').filter({ hasText: 'Fees charged' })
    await expect(fees).toContainText('$0.40')

    await expect(page.getByText('Sold Bitcoin').first()).toBeVisible()
    await expect(page.getByText('Bought Bitcoin').first()).toBeVisible()
  })

  test('manage page reports only the ledger figures and says the rest is unreported', async ({
    page,
  }) => {
    await mockPranavPayNetwork(page, fundedScenario)
    await page.goto('/manage')

    const hero = page.locator('article.manage-hero')
    await expect(hero).toContainText('Virtual account')
    await expect(hero).toContainText('999.89')

    // The engine enforces these but does not publish its configuration, so the
    // page says so instead of quoting a number it never read.
    const cap = page.locator('article.manage-panel').filter({ hasText: 'Spending cap' })
    await expect(cap).toContainText('not reported')
    await expect(cap).not.toContainText('$20,000.00')

    // The sandbox's own figures live in their own labelled panel.
    const sandbox = page.locator('article.pp-sandbox-card')
    await expect(sandbox).toContainText('Testnet sandbox (not your money)')
  })

  test('account page shows the Advanced link as a testnet sandbox', async ({ page }) => {
    await mockPranavPayNetwork(page, fundedScenario)
    await page.goto('/account')

    const advanced = page.getByRole('link', { name: /open the testnet sandbox/i })
    await expect(advanced).toBeVisible()
    await expect(advanced).toHaveAttribute('href', '/dashboard')
    await expect(page.getByText('Testnet sandbox (not your money)').first()).toBeVisible()
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