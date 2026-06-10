/**
 * apps/web_dashboard/static/app.js
 * 手机接待仪表盘前端逻辑
 * 纯原生 JS，零依赖；每 5 秒自动轮询后端 API 刷新数据。
 */

'use strict';

// ── 状态配置 ──────────────────────────────────────────────────────────────
const STATE_MAP = {
  running:      { dot: 'dot-running', badge: 'badge-running', label: '🟢 接待中'      },
  paused:       { dot: 'dot-paused',  badge: 'badge-paused',  label: '⏸ 已暂停'      },
  error:        { dot: 'dot-error',   badge: 'badge-error',   label: '❌ 发生错误'     },
  connected:    { dot: 'dot-other',   badge: 'badge-other',   label: '已连接（待启动）' },
  connecting:   { dot: 'dot-other',   badge: 'badge-other',   label: '正在连接…'       },
  disconnected: { dot: 'dot-other',   badge: 'badge-other',   label: '未连接'          },
};

let autoRefresh  = true;
let refreshTimer = null;

// ── 启动 ──────────────────────────────────────────────────────────────────
(function init() {
  fetchAll();
  startTimer();
})();

function startTimer() {
  if (refreshTimer) clearInterval(refreshTimer);
  refreshTimer = setInterval(fetchAll, 5000);
}

function toggleAutoRefresh() {
  autoRefresh = !autoRefresh;
  const lbl = document.getElementById('auto-label');
  if (autoRefresh) {
    startTimer();
    lbl.textContent = '暂停自动刷新';
    setRefInd('✅ 自动刷新（每 5 秒）');
  } else {
    clearInterval(refreshTimer);
    lbl.textContent = '恢复自动刷新';
    setRefInd('⏸ 已暂停自动刷新');
  }
}

// ── 数据获取 ──────────────────────────────────────────────────────────────
async function fetchAll() {
  setRefInd('🔄 刷新中…');
  try {
    const [ov, devs, msgs] = await Promise.all([
      fetchJSON('/api/overview'),
      fetchJSON('/api/devices'),
      fetchJSON('/api/recent_msgs?limit=30'),
    ]);
    renderOverview(ov);
    renderDevices(devs);
    renderMsgs(msgs);
    setRefInd('✅ ' + new Date().toLocaleTimeString('zh-CN'));
  } catch (err) {
    setRefInd('❌ 获取失败，将重试…');
    console.error('fetchAll:', err);
  }
}

async function fetchJSON(url) {
  const r = await fetch(url, { cache: 'no-store' });
  if (!r.ok) throw new Error('HTTP ' + r.status);
  return r.json();
}

// ── 渲染：概览 ────────────────────────────────────────────────────────────
function renderOverview(d) {
  setText('s-total',   d.total_today    ?? '—');
  setText('s-active',  d.active_devices ?? '—');
  setText('s-updated', d.updated_at     ?? '—');

  const errEl = document.getElementById('s-error');
  const n = d.error_devices ?? 0;
  errEl.textContent = n;
  errEl.className   = 'stat-val' + (n > 0 ? ' red' : '');
}

// ── 渲染：设备 ────────────────────────────────────────────────────────────
function renderDevices(devs) {
  const el = document.getElementById('devices-list');
  if (!devs || !devs.length) {
    el.innerHTML = '<p class="placeholder">暂无设备，请先在主程序「手机接待」Tab 中添加设备</p>';
    return;
  }
  el.innerHTML = devs.map(d => {
    const s   = STATE_MAP[d.state] || STATE_MAP.disconnected;
    const err = d.error_msg
      ? `<div style="color:#E74C3C;font-size:.75rem;margin-top:4px;">⚠️ ${esc(d.error_msg)}</div>`
      : '';
    return `
      <div class="device-card">
        <div class="device-dot ${s.dot}"></div>
        <div class="device-info">
          <div class="device-shop">${esc(d.shop || '（未绑定店铺）')}</div>
          <div class="device-id">${esc(d.device_id)} &nbsp;·&nbsp; ${typeLabel(d.type)}</div>
          <div class="device-stats">
            今日接待：${d.today_count ?? 0} 条
            &ensp;|&ensp;
            上次回复：${d.last_trigger || '—'}
          </div>
          ${err}
        </div>
        <div class="device-badge ${s.badge}">${s.label}</div>
      </div>`;
  }).join('');
}

// ── 渲染：最近消息 ────────────────────────────────────────────────────────
function renderMsgs(msgs) {
  const el = document.getElementById('msgs-list');
  if (!msgs || !msgs.length) {
    el.innerHTML = '<p class="placeholder">暂无接待记录</p>';
    return;
  }
  el.innerHTML = [...msgs].reverse().map(m => `
    <div class="msg-row">
      <span class="msg-time">${esc(m.time || '')}</span>
      <span class="msg-device">${esc(m.device || '')}</span>
      <span class="msg-text">${esc(m.text || '')}</span>
    </div>`).join('');
}

// ── 控制指令 ──────────────────────────────────────────────────────────────
async function doPauseAll() {
  const el = document.getElementById('ctrl-msg');
  el.textContent = '⏳ 正在发送暂停指令…';
  try {
    const data = await (await fetch('/api/pause_all', { method: 'POST' })).json();
    el.textContent = '✅ ' + (data.message || '已发送');
    setTimeout(() => { el.textContent = ''; }, 6000);
    setTimeout(fetchAll, 1500);
  } catch { el.textContent = '❌ 发送失败，请检查网络'; }
}

async function doResumeAll() {
  const el = document.getElementById('ctrl-msg');
  el.textContent = '⏳ 正在发送恢复指令…';
  try {
    const data = await (await fetch('/api/resume_all', { method: 'POST' })).json();
    el.textContent = '✅ ' + (data.message || '已发送');
    setTimeout(() => { el.textContent = ''; }, 6000);
    setTimeout(fetchAll, 1500);
  } catch { el.textContent = '❌ 发送失败，请检查网络'; }
}

// ── 工具 ──────────────────────────────────────────────────────────────────
function setText(id, v) { const e = document.getElementById(id); if (e) e.textContent = v; }
function setRefInd(t)   { setText('refresh-indicator', t); }

function esc(s) {
  return String(s)
    .replace(/&/g,'&amp;').replace(/</g,'&lt;')
    .replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

function typeLabel(t) {
  return ({emulator:'雷电模拟器', wifi:'WiFi 无线', usb:'USB 数据线'})[t] || t || '未知';
}
