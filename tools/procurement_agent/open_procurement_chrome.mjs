/** Open the isolated procurement Chrome profile for human login. */
import os from 'node:os';
import path from 'node:path';
import process from 'node:process';

import { chromium } from '../../frontend/node_modules/playwright/index.mjs';

const HOMES = {
  taobao: 'https://www.taobao.com/',
  '1688': 'https://www.1688.com/',
  pinduoduo: 'https://mobile.yangkeduo.com/',
  xiaohongshu: 'https://www.xiaohongshu.com/',
};
const channel = process.argv[2] || 'taobao';
const channels = channel === 'all' ? ['taobao', '1688', 'pinduoduo'] : [channel];
if (channels.some((item) => !HOMES[item])) throw new Error(`未知渠道 ${channel}`);
const profile = process.env.PROCUREMENT_CHROME_PROFILE
  || path.join(os.homedir(), 'Desktop', 'AI', 'procurement-agent', 'chrome-profile');
const context = await chromium.launchPersistentContext(profile, {
  channel: 'chrome',
  headless: false,
  viewport: null,
  locale: 'zh-CN',
  args: ['--start-maximized'],
});
const firstPage = context.pages()[0] || await context.newPage();
for (const [index, item] of channels.entries()) {
  const page = index === 0 ? firstPage : await context.newPage();
  await page.goto(HOMES[item], { waitUntil: 'domcontentloaded', timeout: 60_000 });
}
console.log(`已打开独立采购 Chrome：${channels.join('、')}。请人工登录，完成后直接关闭整个浏览器窗口。`);
await new Promise((resolve) => context.on('close', resolve));
