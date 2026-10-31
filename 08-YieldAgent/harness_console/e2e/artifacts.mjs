// Browser integration against the production build, without starting any server.
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { chromium } from 'playwright';

// HTTPS preserves secure-context APIs such as crypto.randomUUID used at app startup.
const baseURL = 'https://harness.test';
const browser = await chromium.launch({
  headless: true,
  ...(process.env.PLAYWRIGHT_EXECUTABLE_PATH
    ? { executablePath: process.env.PLAYWRIGHT_EXECUTABLE_PATH }
    : process.env.PW_CHANNEL ? { channel: process.env.PW_CHANNEL } : {}),
});
try {
  const page = await browser.newPage({viewport:{width:1500,height:1000}});
  const errors = [];
  page.on('pageerror',error => errors.push(error.message));
  const session = 'session-artifacts';
  const html = '<!doctype html><html><body><h1>4S4 수율표</h1><table><tr><td>95.2%</td></tr></table><script>document.body.dataset.ready="yes"</script></body></html>';
  const files = {
    html:{mime:'text/html; charset=utf-8',data:html},
    fallback:{mime:'application/octet-stream',data:html},
    code:{mime:'text/plain',data:'print("<script>literal</script>")'},
    markdown:{mime:'text/markdown',data:'# 분석 결과\n\n**완료**'},
    image:{mime:'image/svg+xml',data:'<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20"><rect width="20" height="20" fill="red"/></svg>'},
    pptx:{mime:'application/vnd.openxmlformats-officedocument.presentationml.presentation',data:'fixture pptx'},
    retry:{mime:'text/html',data:html},
  };
  const refs = Object.keys(files).map(id => ({artifact_id:id,title:id === 'html' ? '4S4 수율표' : id,...(id === 'fallback' ? {mime:'text/html'} : {})}));
  const columns = Array.from({length:30},(_,i) => `measurement_${i}`);
  const table = {table_id:'wide',title:'Wide result',columns,total_rows:100,complete:true,units:{},offset:0,
    rows:Array.from({length:100},(_,i) => Object.fromEntries(columns.map((column,j) => [column,j === 0 ? 1.123449 - i * 0.00000001 : i + j / 7])))};
  const runs = [
    {run_id:'older',session_id:session,query:'이전 수율표',status:'completed',goal_revision:1,usage:{tokens:10},observations:[{invocation_id:'table',tool_name:'query_yield',result_id:'result',status:'success',tables:[table]}]},
    {run_id:'latest',session_id:session,query:'다른 아티팩트',status:'completed',goal_revision:1,usage:{tokens:999},observations:[]},
  ];
  let retryCount = 0;
  await page.route(`${baseURL}/**`,async route => {
    const url = new URL(route.request().url());
    const path = url.pathname;
    const json = body => route.fulfill({json:body});
    if (path === '/sessions') return json([{session_id:session,last_query:'저장된 분석',turn_count:2,updated_at:'2026-10-24T00:00:00Z'}]);
    if (path === `/harness/sessions/${session}`) return json({runs:[...runs].reverse(),next_cursor:null});
    if (path.startsWith('/runs/')) {
      const run = runs.find(item => path.split('/')[2] === item.run_id);
      if (path.endsWith('/events')) {
        const artifacts = run.run_id === 'older' ? refs.slice(0,1) : refs.slice(1);
        const events = artifacts.map((payload,i) => ({event_id:`${run.run_id}-${i}`,sequence:i+1,type:'artifact',payload}));
        return route.fulfill({contentType:'text/event-stream',body:events.map(event => `data: ${JSON.stringify(event)}\n\n`).join('')});
      }
      return json(run);
    }
    if (path === '/harness/results/result') return json(table);
    if (path.startsWith('/harness/artifacts/')) {
      assert.equal(url.searchParams.get('session_id'),session);
      const id = path.split('/').at(-1);
      if (id === 'retry' && retryCount++ === 0) return route.fulfill({status:503,body:'unavailable'});
      const file = files[id];
      return route.fulfill({contentType:file.mime,body:file.data});
    }
    if (path === '/' || path.startsWith('/assets/')) {
      return route.fulfill({body:await readFile(new URL(`../dist${path === '/' ? '/index.html' : path}`,import.meta.url)),contentType:path.endsWith('.js') ? 'application/javascript' : path.endsWith('.css') ? 'text/css' : 'text/html'});
    }
    throw new Error(`Unexpected request ${url}`);
  });
  await page.goto(baseURL);
  assert.equal(await page.evaluate(() => isSecureContext && typeof crypto.randomUUID === 'function'),true);
  assert.deepEqual(errors,[], 'App failed during startup');
  await page.getByRole('button',{name:/저장된 분석/}).waitFor();
  await page.locator(`.sidebar [data-session-id="${session}"]`).click();
  const chat = page.locator('.conversation');
  const grid = chat.locator('.result-table');
  await grid.locator('tbody tr').nth(99).waitFor();
  assert.equal(await grid.locator('tbody td').first().innerText(),'1.1234');
  const scroll = grid.locator('.table-scroll');
  assert.equal(await scroll.evaluate(el => el.scrollWidth > el.clientWidth && el.scrollHeight > el.clientHeight),true);
  assert.equal(await grid.locator('td').first().evaluate(el => getComputedStyle(el).whiteSpace),'nowrap');
  assert.ok(await grid.locator('td').first().evaluate(el => el.getBoundingClientRect().width) >= 112);
  assert.ok(await grid.locator('tbody tr').first().evaluate(el => el.getBoundingClientRect().height) < 60);
  await grid.getByRole('button',{name:'measurement_0',exact:true}).click();
  // First-column values round identically: the second column proves sorting uses raw numbers.
  assert.equal(await grid.locator('tbody tr').first().locator('td').nth(1).innerText(),'99.1429');
  await grid.getByRole('button',{name:'measurement_0',exact:true}).click();
  assert.equal(await grid.locator('tbody tr').first().locator('td').nth(1).innerText(),'0.1429');
  await scroll.evaluate(el => {el.scrollTop = 200;el.scrollLeft = 200;});
  const headerTop = await grid.locator('th').first().evaluate(el => el.getBoundingClientRect().top);
  const scrollTop = await scroll.evaluate(el => el.getBoundingClientRect().top);
  assert.ok(Math.abs(headerTop - scrollTop) < 2);
  await chat.getByRole('button',{name:'재시도',exact:true}).click();
  await chat.locator('[data-testid="html-artifact"]').nth(2).waitFor();
  assert.equal(await chat.locator('[data-testid="html-artifact"]').count(),3);
  const frame = chat.locator('[data-testid="html-artifact"]').first();
  assert.equal(await frame.getAttribute('sandbox'),'allow-scripts');
  await frame.contentFrame().locator('body[data-ready="yes"]').waitFor();
  assert.equal(await frame.evaluate(el => getComputedStyle(el).backgroundColor),'rgb(255, 255, 255)');
  assert.match(await chat.locator('.artifact-code').innerText(),/<script>literal/);
  await chat.getByRole('heading',{name:'분석 결과'}).waitFor();
  await chat.locator('.artifact img').waitFor();
  assert.equal(await chat.locator('.artifact img').evaluate(img => img.complete && img.naturalWidth > 0),true);
  const popupPromise = page.waitForEvent('popup');
  await chat.getByRole('link',{name:'새 창',exact:true}).first().click();
  const popup = await popupPromise;
  assert.ok(popup.url().startsWith('blob:'));
  await popup.close();
  const downloadPromise = page.waitForEvent('download');
  await chat.locator('.artifact').filter({hasText:'pptx'}).getByRole('link',{name:'파일 다운로드',exact:true}).last().click();
  assert.equal((await downloadPromise).suggestedFilename(),'pptx');
  await chat.getByRole('button',{name:'4S4 수율표',exact:true}).click();
  const canvas = page.getByRole('region',{name:'캔버스',exact:true});
  await canvas.locator('[data-testid="html-artifact"]').nth(2).waitFor();
  assert.equal(await canvas.locator('.artifact').count(),7);
  assert.equal(await canvas.locator('.selected').innerText().then(s => s.includes('4S4 수율표')),true);
  assert.ok(await canvas.locator('[data-testid="html-artifact"]').first().evaluate(el => el.clientHeight) > await frame.evaluate(el => el.clientHeight));
  // Selecting the same artifact again must return to it after manual scrolling.
  await canvas.evaluate(el => {el.scrollTop = el.scrollHeight;});
  assert.ok(await canvas.evaluate(el => el.scrollTop) > 0);
  await chat.getByRole('button',{name:'4S4 수율표',exact:true}).click();
  await page.waitForFunction(() => document.querySelector('.canvas-panel').scrollTop < 20);
  await page.getByRole('button',{name:'실행 정보',exact:true}).click();
  assert.match(await page.locator('.usage-panel').innerText(),/999/);
  await page.getByRole('button',{name:/새 대화/}).click();
  await page.getByRole('button',{name:'캔버스 (0)',exact:true}).click();
  assert.equal(await page.locator('[data-testid="html-artifact"]').count(),0);
  await page.locator(`.sidebar [data-session-id="${session}"]`).click();
  await page.getByRole('button',{name:'캔버스 (7)',exact:true}).waitFor();
  await canvas.locator('[data-testid="html-artifact"]').nth(2).waitFor();
  await page.setViewportSize({width:700,height:900});
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth),true);
  assert.deepEqual(errors,[]);
  console.log('PASS: replay, response/event MIME, sandbox script, text/markdown/image, popup/download, retry, all-run canvas selection, usage, session reset/reopen, mobile width');
} finally {
  await browser.close();
}
