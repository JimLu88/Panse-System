/// <reference types="vite/client" />
/** Cross-origin navigation only. Never change a global tenant or carry credentials. */
export default function StoreTabs() {
  const raw = import.meta.env.VITE_FUGE_ORIGIN;
  let url: URL | undefined;
  try {
    const candidate = new URL(raw || '');
    if (['http:', 'https:'].includes(candidate.protocol) && !candidate.username && !candidate.password && candidate.origin !== window.location.origin) {
      candidate.pathname = '/stores/fuge'; candidate.search = ''; candidate.hash = ''; url = candidate;
    }
  } catch { /* Unconfigured store stays disabled, never falls back to Panse. */ }
  return <nav aria-label="店铺切换" style={{ display: 'flex', gap: 8, marginRight: 12, whiteSpace: 'nowrap' }}>
    <span aria-current="page" style={{ color: '#fff', fontWeight: 700 }}>畔色 ERP</span>
    {url ? <a href={url.href} referrerPolicy="no-referrer" style={{ color: '#b6d8c6' }}>孚格 ERP</a>
      : <span title="孚格独立环境尚未配置" style={{ color: '#bbb' }}>孚格（未启用）</span>}
  </nav>;
}

