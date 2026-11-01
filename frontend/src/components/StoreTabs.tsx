/// <reference types="vite/client" />
/** Cross-origin navigation only. Never change a global tenant or carry credentials. */
export default function StoreTabs() {
  const raw = import.meta.env.VITE_FUGE_ORIGIN;
  let url: URL | undefined;
  try {
    const candidate = new URL(raw || '');
    if (['http:', 'https:'].includes(candidate.protocol) && !candidate.username && !candidate.password && candidate.origin !== window.location.origin) {
      candidate.pathname = '/'; candidate.search = ''; candidate.hash = ''; url = candidate;
    }
  } catch { /* Unconfigured store stays disabled, never falls back to Panse. */ }
  return <nav aria-label="店铺入口" style={{ display: 'flex', gap: 16, flexWrap: 'wrap' }}>
    <span aria-current="page">当前：畔色 ERP</span>
    {url ? <a href={url.href} target="_blank" rel="noopener noreferrer" referrerPolicy="no-referrer">打开孚格 ERP</a>
      : <span title="孚格独立环境尚未配置">孚格（未启用）</span>}
  </nav>;
}
