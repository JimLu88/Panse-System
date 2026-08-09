/**
 * Panse procurement supervised Chrome driver.
 *
 * This is intentionally review-only: it may search and open a platform page, but
 * it never clicks a platform send/order/payment control. The buyer copies the
 * approved ERP message, sends it manually, and confirms the result in a separate
 * review tab. Login/captcha/risk-control remain human-only.
 */
import crypto from 'node:crypto';
import os from 'node:os';
import path from 'node:path';
import process from 'node:process';

import { chromium } from '../../frontend/node_modules/playwright/index.mjs';

const PROFILE_DIR = process.env.PROCUREMENT_CHROME_PROFILE
  || path.join(os.homedir(), 'Desktop', 'AI', 'procurement-agent', 'chrome-profile');
const REVIEW_TIMEOUT_MS = Math.max(
  60_000,
  Math.min(Number(process.env.PROCUREMENT_REVIEW_TIMEOUT_MS || 600_000), 1_800_000),
);

const PLATFORM = {
  taobao_desktop: {
    home: 'https://www.taobao.com/',
    search: (query) => `https://s.taobao.com/search?q=${encodeURIComponent(query)}`,
    item: (href) => /(?:item\.taobao\.com\/item\.htm|detail\.tmall\.com\/item)/i.test(href),
  },
  taobao_chrome: {
    home: 'https://www.taobao.com/',
    search: (query) => `https://s.taobao.com/search?q=${encodeURIComponent(query)}`,
    item: (href) => /(?:item\.taobao\.com\/item\.htm|detail\.tmall\.com\/item)/i.test(href),
  },
  '1688_chrome': {
    home: 'https://www.1688.com/',
    search: (query) => `https://s.1688.com/selloffer/offer_search.htm?keywords=${encodeURIComponent(query)}`,
    item: (href) => /detail\.1688\.com\/offer\//i.test(href),
  },
  pinduoduo_chrome: {
    home: 'https://mobile.yangkeduo.com/',
    search: (query) => `https://mobile.yangkeduo.com/search_result.html?search_key=${encodeURIComponent(query)}`,
    item: (href) => /mobile\.yangkeduo\.com\/goods\.html|[?&]goods_id=/i.test(href),
  },
  xiaohongshu_chrome: {
    home: 'https://www.xiaohongshu.com/',
    search: (query) => `https://www.xiaohongshu.com/search_result?keyword=${encodeURIComponent(query)}&source=web_search_result_notes`,
    item: (href) => /xiaohongshu\.com\/explore\//i.test(href),
  },
};

function writeResult(value) {
  process.stdout.write(JSON.stringify(value));
}

function manual(reason) {
  return { outcome: 'manual', reason };
}

function failed(reason, retryable = true) {
  return { outcome: 'failed', reason, retryable };
}

async function readPayload() {
  let input = '';
  for await (const chunk of process.stdin) input += chunk;
  const payload = JSON.parse(input || '{}');
  if (!payload || typeof payload !== 'object' || Array.isArray(payload)) {
    throw new TypeError('driver payload must be an object');
  }
  return payload;
}

async function openContext() {
  return chromium.launchPersistentContext(PROFILE_DIR, {
    channel: 'chrome',
    headless: false,
    viewport: null,
    locale: 'zh-CN',
    args: ['--start-maximized'],
  });
}

function looksLikeRiskControl(url, text) {
  return /login|passport|captcha|security|verify/i.test(url)
    || /(滑块验证|安全验证|访问过于频繁|账号存在风险|请输入验证码)/.test(text || '');
}

function normalizeUrl(value) {
  try {
    const parsed = new URL(value);
    parsed.hash = '';
    for (const key of [...parsed.searchParams.keys()]) {
      if (/^(spm|scm|utm_|from|source|track)/i.test(key)) parsed.searchParams.delete(key);
    }
    return parsed.toString();
  } catch {
    return String(value || '').trim();
  }
}

async function discover(payload) {
  if (payload.mode !== 'review') {
    return failed('真实浏览器搜索仅允许 review 模式；dry_run 不会调用驱动', false);
  }
  const action = payload.action || {};
  const adapter = PLATFORM[payload.capability];
  if (!adapter) return manual(`尚未配置平台能力 ${payload.capability}`);
  const query = String(action.search_query || '').trim();
  if (!query) return failed('ERP 未提供搜索词', false);

  const context = await openContext();
  try {
    const page = await context.newPage();
    await page.goto(adapter.search(query), { waitUntil: 'domcontentloaded', timeout: 60_000 });
    await page.waitForTimeout(4_000);
    const bodyText = await page.locator('body').innerText({ timeout: 10_000 }).catch(() => '');
    if (looksLikeRiskControl(page.url(), bodyText)) {
      return manual(`${payload.capability} 登录失效、出现验证码或风控，请人工处理后重试`);
    }

    const raw = await page.locator('a[href]').evaluateAll((anchors) => anchors.slice(0, 1200).map((anchor) => ({
      href: anchor.href || '',
      title: (anchor.getAttribute('title') || anchor.innerText || anchor.textContent || '').trim(),
      context: (anchor.parentElement?.innerText || '').trim().slice(0, 800),
    })));
    const excluded = new Set(
      (action.excluded_candidates || [])
        .flatMap((item) => [item.product_url, item.merchant_url])
        .filter(Boolean)
        .map(normalizeUrl),
    );
    const seen = new Set();
    const candidates = raw.filter((item) => {
      const href = normalizeUrl(item.href);
      if (!adapter.item(href) || excluded.has(href) || seen.has(href)) return false;
      seen.add(href);
      item.href = href;
      return true;
    });
    const selected = candidates[0];
    if (!selected) {
      return manual(`已打开“${query}”搜索页，但没有可靠识别到商品链接，请人工检查页面或补充搜索词`);
    }
    const title = (selected.title || selected.context || action.item_name || '候选商品')
      .replace(/\s+/g, ' ')
      .slice(0, 500);
    const priceMatch = selected.context.match(/[¥￥]\s*[\d,.]+(?:\s*[-~至]\s*[\d,.]+)?/);
    const queryTokens = query.toLowerCase().split(/\s+/).filter((item) => item.length > 1);
    const matched = queryTokens.filter((token) => title.toLowerCase().includes(token)).length;
    const score = Math.min(85, 45 + matched * 8);
    await page.bringToFront();
    return {
      outcome: 'found',
      merchant_name: null,
      merchant_url: null,
      product_url: selected.href,
      merchant_external_id: null,
      discovery_query: query,
      candidate_score: score,
      candidate_reason: '由独立采购 Chrome 的搜索结果自动发现，需在 ERP 中人工筛选后再审核询价内容',
      candidate_snapshot: {
        title,
        price_text: priceMatch?.[0] || null,
        captured_at: new Date().toISOString(),
      },
      source_rank: 1,
    };
  } finally {
    await context.close().catch(() => {});
  }
}

function reviewHtml(action) {
  const message = String(action.suggested_message || '');
  const merchant = String(action.merchant_name || `候选 #${action.inquiry_id || ''}`);
  const messageJson = JSON.stringify(message).replaceAll('<', '\\u003c');
  return `<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>畔色 ERP 发送审核</title>
  <style>
  body{font-family:system-ui,"Microsoft YaHei",sans-serif;background:#f5f7fb;margin:0;padding:40px;color:#17223b}
  main{max-width:840px;margin:auto;background:white;border-radius:18px;padding:28px;box-shadow:0 12px 40px #1d3d6d22}
  h1{font-size:24px;margin:0 0 8px}.safe{color:#b42318;background:#fff1f0;padding:12px;border-radius:10px}
  textarea{width:100%;height:220px;box-sizing:border-box;margin:14px 0;padding:14px;font-size:17px;line-height:1.65}
  button{padding:12px 20px;border:0;border-radius:9px;margin-right:10px;font-size:16px;cursor:pointer}
  .copy{background:#e6f4ff;color:#0958d9}.ok{background:#1677ff;color:white}.manual{background:#fff1f0;color:#cf1322}
  </style><main><h1>发送前人工审核 · ${merchant.replaceAll('<', '&lt;')}</h1>
  <p class="safe">驱动不会替你点击平台“发送”。请切换到已经打开的平台标签页，确认商家和内容，手动粘贴并发送后再回来确认。</p>
  <textarea id="msg" readonly></textarea>
  <button class="copy" id="copy">复制已审核话术</button>
  <button class="ok" id="sent">我已在平台实际发送</button>
  <button class="manual" id="manual">未发送，转人工</button>
  <p id="state"></p></main><script>
  const message=${messageJson}; const box=document.querySelector('#msg'); box.value=message;
  document.querySelector('#copy').onclick=async()=>{box.select();try{await navigator.clipboard.writeText(message)}catch{document.execCommand('copy')};document.querySelector('#state').textContent='已复制，请到平台粘贴并人工发送。'};
  document.querySelector('#sent').onclick=()=>window.panseDecision('sent');
  document.querySelector('#manual').onclick=()=>window.panseDecision('manual');
  </script></html>`;
}

async function send(payload) {
  if (payload.mode !== 'review') {
    return manual('本驱动只实现人工复核发送；未实现也未授权 live 自动发送');
  }
  const action = payload.action || {};
  const target = action.product_url || action.merchant_url;
  if (!target) return manual('缺少商品或店铺链接，不能打开平台页面');
  const context = await openContext();
  try {
    const platformPage = await context.newPage();
    await platformPage.goto(target, { waitUntil: 'domcontentloaded', timeout: 60_000 });
    await platformPage.waitForTimeout(2_000);
    const bodyText = await platformPage.locator('body').innerText({ timeout: 10_000 }).catch(() => '');
    if (looksLikeRiskControl(platformPage.url(), bodyText)) {
      return manual(`${payload.capability} 登录失效、出现验证码或风控，请人工处理；本次没有发送`);
    }
    const reviewPage = await context.newPage();
    let resolveDecision;
    const decision = new Promise((resolve) => { resolveDecision = resolve; });
    await reviewPage.exposeFunction('panseDecision', (value) => resolveDecision(value));
    await reviewPage.setContent(reviewHtml(action), { waitUntil: 'domcontentloaded' });
    await platformPage.bringToFront();
    const outcome = await Promise.race([
      decision,
      new Promise((resolve) => setTimeout(() => resolve('timeout'), REVIEW_TIMEOUT_MS)),
    ]);
    if (outcome !== 'sent') {
      return manual(outcome === 'timeout' ? '人工发送确认超时；本次按未发送处理' : '采购人员选择未发送并转人工');
    }
    const digest = crypto.createHash('sha256')
      .update(`${action.inquiry_id}:${action.action_key}:${action.suggested_message}`)
      .digest('hex')
      .slice(0, 16);
    return {
      outcome: 'sent',
      external_message_id: `human-review-${Date.now()}-${digest}`,
      external_thread_id: action.external_thread_id || null,
      sent_content: action.suggested_message,
      meta: {
        confirmation: 'human_confirmed_after_platform_send',
        platform_url: platformPage.url(),
      },
    };
  } finally {
    await context.close().catch(() => {});
  }
}

async function main() {
  const payload = await readPayload();
  if (payload.operation === 'discover') return discover(payload);
  if (payload.operation === 'send') return send(payload);
  if (payload.operation === 'poll_replies') {
    // Platform inbox selectors must be verified against a logged-in account before enabling.
    return { replies: [] };
  }
  return failed(`unsupported operation: ${payload.operation || '(empty)'}`, false);
}

try {
  writeResult(await main());
} catch (error) {
  console.error(error?.stack || String(error));
  writeResult(manual(`浏览器驱动异常：${error?.message || String(error)}`));
}
