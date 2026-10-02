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
  })

  test('renders Tradable, Protected and Exposure cards from mocked dashboard data', async ({
    page,
  }) => {
    await mockPranavPayNetwork(page, fundedScenario)
    await page.goto('/')

    // Trading power, protected savings and exposure all derive from funds.
    // equity 30000, tradable 10000, savings 20000, open 0.1 BTC @ 52000 = 5200
    // exposure -> 5200 / 30000 = 17.33%.
    const tradingPower = page.locator('article.metric-card').filter({ hasText: 'Trading power' })
    await expect(tradingPower).toContainText('$10,000.00')

    const protectedCard = page.locator('article.metric-card').filter({ hasText: 'Protected' })
    await expect(protectedCard).toContainText('$20,000.00')

    const exposure = page.locator('article.metric-card').filter({ hasText: 'Exposure' })
    await expect(exposure).toContainText('+17.33%')

    await expect(page.locator('article.balance-card')).toContainText('$30,000')

    // The mocked position and fills surface on the same page.
    await expect(page.getByText('BTCUSDT').first()).toBeVisible()
    await expect(page.getByText('Bought Bitcoin').first()).toBeVisible()
    await expect(page.getByText('Sold Ethereum').first()).toBeVisible()
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
})
