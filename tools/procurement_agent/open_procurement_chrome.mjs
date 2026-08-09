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
const target = HOMES[channel];
if (!target) throw new Error(`未知渠道 ${channel}`);
const profile = process.env.PROCUREMENT_CHROME_PROFILE
  || path.join(os.homedir(), 'Desktop', 'AI', 'procurement-agent', 'chrome-profile');
const context = await chromium.launchPersistentContext(profile, {
  channel: 'chrome',
  headless: false,
  viewport: null,
  locale: 'zh-CN',
  args: ['--start-maximized'],
});
const page = context.pages()[0] || await context.newPage();
await page.goto(target, { waitUntil: 'domcontentloaded', timeout: 60_000 });
console.log(`已打开独立采购 Chrome：${channel}。请人工登录，完成后直接关闭整个浏览器窗口。`);
await new Promise((resolve) => context.on('close', resolve));
