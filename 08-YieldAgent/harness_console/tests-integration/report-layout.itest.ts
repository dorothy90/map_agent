import { execFileSync } from 'node:child_process'
import { fileURLToPath } from 'node:url'
import { chromium } from 'playwright'
import { expect, test } from 'vitest'

test('the report frame reports its measured height on load and after its rows grow', async () => {
  const python = process.env.PR8_PYTHON ?? fileURLToPath(new URL('../../../.venv/bin/python', import.meta.url))
  const html = execFileSync(python, ['-c', [
    'from harness.tests.report_table_fakes import report_html',
    'from harness.tools.table_artifact import _ReportInput',
    'print(report_html(_ReportInput(title="small", rows=[dict(period="2026-W45", process="pt1h", param="VTH", value=1)])))',
  ].join('\n')], { cwd: new URL('../../', import.meta.url), encoding: 'utf8' })
  const browser = await chromium.launch({ channel: process.env.PW_CHANNEL })
  try {
    const page = await browser.newPage()
    await page.setContent('<iframe width="240"></iframe>')
    const frame = page.locator('iframe')
    await frame.evaluate((el, src) => {
      if (!(el instanceof HTMLIFrameElement)) throw new Error('Expected report frame')
      window.addEventListener('message', event => {
        if (event.source === el.contentWindow && event.data?.type === 'yield-table-height') {
          el.dataset.reported = String(event.data.height)
        }
      })
      el.srcdoc = src
    }, html)
    const reportedHeight = () => frame.getAttribute('data-reported')
    const measuredHeight = () => frame.contentFrame().locator('body').evaluate(
      body => Math.ceil(body.getBoundingClientRect().height))
    const table = frame.contentFrame().locator('table')
    await table.waitFor()
    const initial = await measuredHeight()
    await expect.poll(reportedHeight).toBe(String(initial))
    const container = frame.contentFrame().locator('.table-container')
    expect(await container.evaluate(el => el.scrollWidth > el.clientWidth)).toBe(true)
    await table.locator('tbody').evaluate(body => {
      if (!(body instanceof HTMLTableSectionElement)) throw new Error('Expected report rows')
      const row = body.rows[0]
      for (let i = 0; i < 8; i++) body.appendChild(row.cloneNode(true))
    })
    const grown = await measuredHeight()
    expect(grown).toBeGreaterThan(initial)
    await expect.poll(reportedHeight).toBe(String(grown))
  } finally {
    await browser.close()
  }
})
