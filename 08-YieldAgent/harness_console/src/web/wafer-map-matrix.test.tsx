import { readFileSync } from 'node:fs'
import { afterEach, expect, it, vi } from 'vitest'

afterEach(() => {
  document.body.replaceChildren()
  vi.unstubAllGlobals()
})

it('keeps missing combinations distinct from zero failure after a local transpose', () => {
  const template = readFileSync(process.cwd() + '/../harness/tools/map_matrix.html', 'utf8')
  const points = { wafer_count: 1, observation_count: 3, fail_count: 0, measurement_count: 1 }
  const payload = {
    matrix: { rows: { source: 'legend', field: 'FLOW_ID' }, columns: { source: 'map', field: 'WF_ID' } },
    axis_values: [['F1', 'F2'], ['2', '10']],
    groups: [
      { ...points, row_value: 'F1', column_value: '2' },
      { ...points, row_value: 'F1', column_value: '10', fail_count: 3 },
      { ...points, row_value: 'F2', column_value: '10' },
    ],
    bounds: [0, 0, 0, 0],
    coverage: { measurement_count: 4, included_measurement_count: 3, missing_history_measurements: 1 },
  }
  const page = new DOMParser().parseFromString(template.replace('<!-- MATRIX_DATA -->', JSON.stringify(payload)), 'text/html')
  document.body.replaceChildren(...page.body.childNodes)
  vi.stubGlobal('IntersectionObserver', class { observe() {} disconnect() {} })
  document.querySelector<HTMLElement>('#viewport')!.scrollTo = vi.fn()
  const fetch = vi.fn()
  vi.stubGlobal('fetch', fetch)
  new Function(document.querySelector('script:not([type])')!.textContent!)()

  const rows = () => [...document.querySelectorAll('tbody tr')].map(row =>
    [...row.children].map(cell => cell.textContent))
  expect(rows()).toEqual([['F1', '1장 · 0%', '1장 · 100%'], ['F2', '측정 없음', '1장 · 0%']])
  expect(document.querySelector('#coverage')!.textContent).toContain('공정 이력 없음 1건')
  document.querySelector<HTMLButtonElement>('#transpose')!.click()
  expect(rows()).toEqual([['2', '1장 · 0%', '측정 없음'], ['10', '1장 · 100%', '1장 · 0%']])
  expect(fetch).not.toHaveBeenCalled()
})
