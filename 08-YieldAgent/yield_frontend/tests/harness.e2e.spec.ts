import { test, expect, type Page } from '@playwright/test';

const start = { type: 'stream_start', run_id: 'r101', goal_revision: 1, after_sequence: 0 };
const end = { type: 'stream_end', status: 'completed', run_id: 'r101', event_id: 'end:1', sequence: 8 };
const sse = (events: object[]) => events.map(event => `data: ${JSON.stringify(event)}\n\n`).join('');
async function restore(page: Page, history: object) {
  await page.addInitScript(() => sessionStorage.setItem('yield-session', 'session'));
  await page.route('**/session/session/history', route => route.fulfill({ json: history }));
}
const activeHistory = { turns: [{ role: 'user', content: '조회 요청' }], latest_run: { run_id: 'r101', status: 'running', query: '조회 요청' }, through_sequence: 0 };

test('waiting question retains cancel and cancellation reaches API', async ({ page }) => {
  await restore(page, activeHistory);
  await page.route('**/chat/stream', route => route.fulfill({ contentType: 'text/event-stream', body: sse([start,
    { type: 'interrupt', run_id: 'r101', event_id: 'q:0', sequence: 2, interrupt_id: 'q', interrupt_type: 'missing_param', message: '기간을 선택해 주세요', fields: [{ slot: 'period', label: '조회 기간' }], options: [], param: '', route: 'harness' }]) }));
  let cancelled = false;
  await page.route('**/runs/r101/cancel', route => { cancelled = true; return route.fulfill({ json: { status: 'cancelled' } }); });
  await page.route('**/runs/r101', route => route.fulfill({ json: { status: 'cancelled', answer: '작업을 중지했습니다.' } }));
  await page.goto('/');
  await expect(page.getByText('기간을 선택해 주세요')).toBeVisible();
  await expect(page.getByPlaceholder('period', { exact: true })).toBeEnabled();
  await page.reload();
  await expect(page.getByPlaceholder('period', { exact: true })).toBeEnabled();
  await page.getByPlaceholder('period', { exact: true }).fill('최근 4주');
  await page.getByRole('button', { name: '중지', exact: true }).click();
  await expect(page.getByText('작업을 중지했습니다.')).toHaveCount(1);
  expect(cancelled).toBe(true);
  await expect(page.getByRole('button', { name: '중지', exact: true })).toHaveCount(0);
  await expect(page.getByText('기간을 선택해 주세요')).toBeVisible();
  await expect(page.getByRole('button', { name: '제출', exact: true })).toHaveCount(0);
  await expect(page.getByPlaceholder('period', { exact: true })).toHaveCount(0);
});

test('invocations keep running, error, partial and empty states distinct', async ({ page }) => {
  await restore(page, activeHistory);
  const status = (id: string, state: string) => ({ type: 'status', node: 'same_tool', invocation_id: id, state, message: `${id}:${state}`, elapsed: 1.25, run_id: 'r101', event_id: `${id}:${state}` });
  await page.route('**/chat/stream', route => route.fulfill({ contentType: 'text/event-stream', body: sse([start, status('a', 'running'), status('a', 'error'), status('b', 'partial'), status('c', 'empty'), status('d', 'running'), { type: 'interrupt', message: '대기', fields: [], options: [] }]) }));
  await page.goto('/');
  await expect(page.locator('li[data-state="error"]')).toHaveCount(1);
  await expect(page.locator('li[data-state="partial"]')).toHaveCount(1);
  await expect(page.locator('li[data-state="empty"]')).toHaveCount(1);
  await expect(page.locator('li[data-state="running"]')).toHaveCount(1);
  await expect(page.locator('li[data-state="success"]')).toHaveCount(0);
});

test('completion after snapshot restores latest run once and keeps artifact order', async ({ page }) => {
  await restore(page, activeHistory);
  let calls = 0;
  await page.route('**/chat/stream', async route => {
    expect(route.request().postDataJSON().run_id).toBe('r101');
    expect(route.request().postDataJSON().after_sequence).toBe(0);
    calls++;
    const events = [start,
      { type: 'message', role: 'assistant', content: '어느 기간인가요?', event_id: 'q:0', run_id: 'r101', sequence: 2 },
      { type: 'user_input', content: '최근 4주', event_id: 'i:0', run_id: 'r101', sequence: 3 },
      ...['표 B', '표 A'].map((title, i) => ({ type: 'artifact', artifact_id: `a${i}`, title, artifact_type: 'markdown', data: `자료 ${i}`, event_id: `a${i}:0`, run_id: 'r101', sequence: 4 + i })),
      { type: 'message', role: 'assistant', content: '완료된 답변', event_id: 'end:0', run_id: 'r101', sequence: 8 }];
    return route.fulfill({ contentType: 'text/event-stream', body: sse(calls === 1 ? events : [...events, end]) });
  });
  await page.goto('/');
  await expect(page.getByText('완료된 답변', { exact: true })).toHaveCount(1);
  await expect(page.getByText('최근 4주', { exact: true })).toHaveCount(1);
  await expect(page.getByText('완료', { exact: true })).toBeVisible();
  expect(calls).toBe(2);
  expect(await page.getByText(/^표 [AB]$/).allTextContents()).toEqual(['표 B', '표 A']);
});

test('completed snapshot preserves Q&A and distinct table pagination', async ({ page }) => {
  const table = (id: string, title: string, column: string) => ({ artifact_id: id, artifact_type: 'table', title, mime: 'application/json', agent: 'tool', data: JSON.stringify({ result_id: 'result', session_id: 'session', table_id: id, columns: [column], units: {}, preview_rows: [{ [column]: 'first' }], total_rows: 2, complete: true }) });
  await restore(page, { through_sequence: 8, latest_run: { run_id: 'r101', status: 'completed' }, turns: [
    { role: 'user', content: '요청' }, { role: 'assistant', content: '기간 질문' }, { role: 'user', content: '기간 답변' },
    { role: 'assistant', content: '최종 답변', artifacts: [table('one', '첫 번째 표', '수율'), table('two', '두 번째 표', '온도')] }] });
  await page.route('**/harness/results/result?**', route => {
    const url = new URL(route.request().url());
    expect(url.searchParams.get('table_id')).toBe('two');
    expect(url.searchParams.get('offset')).toBe('1');
    return route.fulfill({ json: { rows: [{ 온도: 'second' }] } });
  });
  await page.goto('/');
  await expect(page.getByText('기간 질문', { exact: true })).toHaveCount(1);
  await expect(page.getByText('기간 답변', { exact: true })).toHaveCount(1);
  await expect(page.getByRole('columnheader', { name: '수율', exact: true })).toBeVisible();
  await expect(page.getByRole('columnheader', { name: '온도', exact: true })).toBeVisible();
  await page.getByRole('button', { name: '더 보기' }).nth(1).click();
  await expect(page.getByRole('cell', { name: 'second', exact: true })).toHaveCount(1);
});
