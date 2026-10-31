import assert from 'node:assert/strict';
import { mkdir, stat } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright';

// Uses existing Vite (5175) -> backend (8001) servers. No mocks or server startup.
const baseURL = 'http://127.0.0.1:5175';
let browser;
try {
  const sessionId = process.env.SESSION_ID;
  assert.ok(sessionId, 'Set SESSION_ID to an existing session containing an HTML artifact');
  browser = await chromium.launch({
    headless: true,
    ...(process.env.PLAYWRIGHT_EXECUTABLE_PATH
      ? { executablePath: process.env.PLAYWRIGHT_EXECUTABLE_PATH }
      : process.env.PW_CHANNEL ? { channel: process.env.PW_CHANNEL } : {}),
  });
  const page = await browser.newPage({viewport:{width:1500,height:1050},deviceScaleFactor:1});
  page.setDefaultTimeout(30_000);
  async function getJSON(path) {
    const response = await page.request.get(`${baseURL}${path}`);
    assert.ok(response.ok(), `GET ${path}: HTTP ${response.status()}`);
    return response.json();
  }
  const sessions = await getJSON('/sessions');
  assert.ok(sessions.some(session => session.session_id === sessionId), `Session ${sessionId} missing from GET /sessions`);
  // Read every history page so the assertion checks the oldest query, not just the sidebar label.
  const runs = [];
  let cursor = null;
  do {
    const params = new URLSearchParams({limit:'100'});
    if (cursor) params.set('cursor',cursor);
    const history = await getJSON(`/harness/sessions/${encodeURIComponent(sessionId)}?${params}`);
    runs.push(...history.runs);
    cursor = history.next_cursor;
  } while (cursor);
  const oldQuery = runs.at(-1)?.query;
  assert.ok(oldQuery?.trim(), 'Session has no stored harness query');
  await page.goto(baseURL,{waitUntil:'domcontentloaded'});
  const selector = await page.evaluate(id => `[data-session-id="${CSS.escape(id)}"]`,sessionId);
  await page.locator(`.sidebar ${selector}`).click();
  const chat = page.locator('.conversation');
  await chat.locator('.user-message').getByText(oldQuery,{exact:true}).first().waitFor({state:'visible'});
  const artifact = chat.getByTestId('html-artifact').first();
  await artifact.waitFor({state:'visible'});
  await artifact.scrollIntoViewIfNeeded();
  await artifact.contentFrame().locator('table tr').first().waitFor({state:'visible'});
  // srcdoc DOM readiness precedes Chrome's sandboxed iframe paint.
  await page.waitForTimeout(2500);
  await mkdir(new URL('../screenshots/',import.meta.url),{recursive:true});
  const viewportPath = fileURLToPath(new URL('../screenshots/reopen_viewport.png',import.meta.url));
  const artifactPath = fileURLToPath(new URL('../screenshots/reopen_html.png',import.meta.url));
  await page.screenshot({path:viewportPath,fullPage:false});
  const clip = await artifact.boundingBox();
  assert.ok(clip && clip.width > 0 && clip.height > 0, 'HTML iframe has no screenshot bounds');
  await page.screenshot({path:artifactPath,clip});
  // Check the iframe separately: surrounding UI can hide a blank frame in the viewport.
  for (const path of [artifactPath,viewportPath]) {
    const {size} = await stat(path);
    assert.ok(size > 20 * 1024, `Possibly blank screenshot: ${path} (${size} bytes; expected > 20KB)`);
  }
  console.log(`PASS: reopened ${sessionId}; old query and populated HTML iframe verified\n${viewportPath}\n${artifactPath}`);
} catch (error) {
  console.error(`FAIL: ${error instanceof Error ? error.message : String(error)}`);
  process.exitCode = 1;
} finally {
  await browser?.close();
}
