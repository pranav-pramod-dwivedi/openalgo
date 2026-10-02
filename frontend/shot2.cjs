const { chromium } = require('playwright')
const EXE = process.env.HOME + '/Library/Caches/ms-playwright/chromium_headless_shell-1243/chrome-headless-shell-mac-arm64/chrome-headless-shell'
;(async () => {
  const b = await chromium.launch({ executablePath: EXE })
  const p = await (await b.newContext({ viewport: { width: 1512, height: 950 } })).newPage()
  const errs = []
  p.on('pageerror', (e) => errs.push(e.message))
  p.on('console', (m) => { if (m.type() === 'error') errs.push(m.text()) })
  await p.goto('http://127.0.0.1:5001/', { waitUntil: 'networkidle' })
  await p.waitForTimeout(3000)
  await p.locator('.chart-card').screenshot({ path: '/tmp/ppshots/pchart.png' })
  await p.locator('.calendar-card').screenshot({ path: '/tmp/ppshots/pcal.png' })
  await p.locator('.signals-card').screenshot({ path: '/tmp/ppshots/pfacts.png' })
  await p.screenshot({ path: '/tmp/ppshots/overview.png', fullPage: true })
  const cal = await p.evaluate(() => {
    const cells = [...document.querySelectorAll('.pp-day')]
    const state = {}
    for (const c of cells) { const k = [...c.classList].find((x) => x.startsWith('is-')); state[k] = (state[k] || 0) + 1 }
    return { total: cells.length, state }
  })
  console.log('calendar:', JSON.stringify(cal))
  console.log('errors:', errs.length, errs.slice(0, 3).join(' | '))
  await b.close()
})()
