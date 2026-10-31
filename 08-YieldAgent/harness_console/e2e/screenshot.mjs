import { chromium } from 'playwright';
import { mkdir } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';

// Run manually with the backend and Vite server already running.
const output = fileURLToPath(new URL('../screenshots/run.png', import.meta.url));
const browser = await chromium.launch({
  headless: true,
  ...(process.env.PLAYWRIGHT_EXECUTABLE_PATH
    ? { executablePath: process.env.PLAYWRIGHT_EXECUTABLE_PATH }
    : process.env.PW_CHANNEL ? { channel: process.env.PW_CHANNEL } : {}),
});
try {
  const page = await browser.newPage({ viewport: { width: 1500, height: 1050 }, deviceScaleFactor: 1 });
  await page.goto('http://127.0.0.1:5175', { waitUntil: 'domcontentloaded' });
  await page.getByTestId('chat-input').fill('최근 일주일 동안 수율 낮은 거 알려줘');
  await page.getByTestId('send').click();
  await page.getByTestId('run-status').first().waitFor({ timeout: 30_000 });
  await page.waitForFunction(() => {
    const statuses = [...document.querySelectorAll('[data-testid="run-status"]')];
    const status = statuses.at(-1)?.getAttribute('data-status');
    const terminal = status && !['created', 'running', 'cancelling', 'waiting_user'].includes(status);
    const visible = element => !!element && element.getBoundingClientRect().height > 0;
    const timeline = document.querySelector('[data-testid="tool-timeline"]');
    const answer = [...document.querySelectorAll('[data-testid="final-answer"]')].at(-1);
    return terminal || (visible(timeline) && visible(answer) && answer.textContent.trim().length > 0);
  }, undefined, { timeout: 15 * 60 * 1000, polling: 1000 });
  // Allow the final observation refresh and artifact blobs to settle.
  await page.waitForTimeout(3500);
  await page.evaluate(async () => {
    await Promise.all([...document.images].map(image => image.decode().catch(() => {})));
  });
  for (const artifact of await page.getByTestId('html-artifact').all()) {
    await artifact.scrollIntoViewIfNeeded();
    await artifact.contentFrame().locator('table tr').first().waitFor({state:'visible'});
    // Wait for the sandboxed srcdoc frame to paint after it enters the viewport.
    await page.waitForTimeout(2500);
  }
  await mkdir(new URL('../screenshots/', import.meta.url), { recursive: true });
  await page.screenshot({ path: output, fullPage: true });
  console.log(`Saved ${output}`);
} finally {
  await browser.close();
}
