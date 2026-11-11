import { chromium } from 'playwright';
import { mkdir } from 'node:fs/promises';
const out='/tmp/pr9b_shots';
await mkdir(out,{recursive:true});
const browser=await chromium.launch({
  headless:true,
  ...(process.env.PLAYWRIGHT_EXECUTABLE_PATH
    ? { executablePath: process.env.PLAYWRIGHT_EXECUTABLE_PATH }
    : process.env.PW_CHANNEL ? { channel: process.env.PW_CHANNEL } : {}),
});
try {
 const page=await browser.newPage({viewport:{width:1440,height:1000},reducedMotion:'reduce'});
 const errors=[];page.on('pageerror',e => errors.push(e.message));
 await page.goto(`${process.env.PR9_WEB_URL || 'http://127.0.0.1:5175'}/?user=alice`);
 await page.getByRole('button',{name:'일반 대화',exact:true}).waitFor();
 await page.screenshot({path:`${out}/01-sessions.png`,fullPage:true});
 await page.getByRole('button',{name:'표와 검증 결과',exact:true}).click();
 await page.locator('[data-verification="pass"]').waitFor();
 await page.getByRole('button',{name:'미리보기',exact:true}).first().click();
 await page.getByTitle('결과 미리보기').waitFor();
 await page.screenshot({path:`${out}/02-results.png`,fullPage:true});
 await page.getByRole('button',{name:'검증과 질문 대기',exact:true}).click();
 await page.getByText('조회 기간을 선택해주세요.',{exact:true}).first().waitFor();
 await page.screenshot({path:`${out}/03-clarify.png`,fullPage:true});
 if(errors.length)throw new Error(errors.join('\n'));
 console.log(`Screenshots saved to ${out}`);
} finally {await browser.close();}
