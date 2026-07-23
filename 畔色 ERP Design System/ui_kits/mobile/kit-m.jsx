/* 畔色 ERP — 移动端 UI Kit 组件 + 手机外壳 (基于设计系统 token) */
(function () {
  if (document.getElementById('kit-m-style')) return;
  const css = `
.m-phone{width:390px;max-width:100%;height:780px;max-height:94vh;background:var(--surface-card);border-radius:42px;box-shadow:var(--shadow-xl),0 0 0 11px #0c1a1c,0 0 0 13px #1f3a3d;position:relative;overflow:hidden;display:flex;flex-direction:column;font-family:var(--font-sans)}
.m-notch{position:absolute;top:0;left:50%;transform:translateX(-50%);width:150px;height:30px;background:#0c1a1c;border-radius:0 0 18px 18px;z-index:30}
.m-status{height:46px;display:flex;align-items:flex-end;justify-content:space-between;padding:0 24px 6px;font-size:13px;font-weight:600;color:var(--text-primary);flex:none}
.m-status .r{display:flex;gap:6px;align-items:center}
.m-body{flex:1;overflow-y:auto;overflow-x:hidden;background:var(--bg-app);-webkit-overflow-scrolling:touch}
.m-pad{padding:16px}
.m-tabbar{flex:none;display:flex;background:var(--surface-card);border-top:1px solid var(--border);padding:8px 6px calc(8px + env(safe-area-inset-bottom,10px));position:relative;z-index:20}
.m-tab{flex:1;display:flex;flex-direction:column;align-items:center;gap:3px;padding:4px 0;cursor:pointer;color:var(--text-tertiary);font-size:11px;font-weight:500;min-height:44px;justify-content:center;transition:color var(--dur-fast) var(--ease-out)}
.m-tab .ic{font-size:20px;line-height:1}
.m-tab.on{color:var(--primary);font-weight:600}
.m-card{background:var(--surface-card);border:1px solid var(--border);border-radius:var(--radius-lg);box-shadow:var(--shadow-xs)}
.m-btn{display:inline-flex;align-items:center;justify-content:center;gap:7px;height:48px;padding:0 20px;border-radius:var(--radius-md);border:none;background:var(--primary);color:var(--on-primary);font-family:inherit;font-size:15px;font-weight:600;cursor:pointer;width:100%;transition:background var(--dur-fast) var(--ease-out)}
.m-btn:active{background:var(--primary-active)}
.m-btn--sec{background:var(--surface-card);color:var(--text-primary);border:1px solid var(--border-strong)}
.m-btn--ghost{background:var(--primary-soft);color:var(--primary)}
.m-tag{display:inline-flex;align-items:center;gap:4px;font-size:12px;font-weight:600;line-height:1;padding:3px 9px;border-radius:var(--radius-sm);border:1px solid transparent}
.m-tag .dot{width:6px;height:6px;border-radius:50%;background:currentColor}
.m-tag--success{background:var(--success-soft);color:var(--success);border-color:var(--success-border)}
.m-tag--warning{background:var(--warning-soft);color:#b45309;border-color:var(--warning-border)}
.m-tag--danger{background:var(--danger-soft);color:var(--danger);border-color:var(--danger-border)}
.m-tag--brand{background:var(--primary-soft);color:var(--primary);border-color:var(--primary-border)}
.m-tag--info{background:var(--info-soft);color:#0369a1;border-color:var(--info-border)}
.m-row{display:flex;align-items:center;gap:12px;padding:14px 16px;background:var(--surface-card);border-bottom:1px solid var(--border-subtle);min-height:44px}
.m-row:last-child{border-bottom:none}
.m-input{display:flex;align-items:center;gap:8px;background:var(--surface-card);border:1px solid var(--border-strong);border-radius:var(--radius-md);padding:0 14px;height:48px}
.m-input input{flex:1;border:none;outline:none;background:transparent;font-family:inherit;font-size:15px;color:var(--text-primary);min-width:0}
.m-input input::placeholder{color:var(--text-tertiary)}
.m-mono{font-family:var(--font-mono);font-feature-settings:"tnum" 1}
.m-hd{padding:16px 16px 4px}
.m-h1{font-size:22px;font-weight:800;letter-spacing:-.02em;color:var(--text-primary);margin:0}
`;
  const el = document.createElement('style');
  el.id = 'kit-m-style';
  el.textContent = css;
  document.head.appendChild(el);
})();

const MTag = ({ tone = 'brand', dot, children }) =>
  React.createElement('span', { className: `m-tag m-tag--${tone}` }, dot && React.createElement('span', { className: 'dot' }), children);

function Phone({ children, tab, onTab }) {
  const tabs = [
    { key: 'home', ic: 'home', label: '工作台' },
    { key: 'capture', ic: 'photo_camera', label: '录单' },
    { key: 'stock', ic: 'inventory_2', label: '库存' },
    { key: 'me', ic: 'person', label: '我的' },
  ];
  return (
    <div className="m-phone">
      <div className="m-notch" />
      <div className="m-status">
        <span>9:41</span>
        <span className="r"><span>5G</span><span>📶</span><span>🔋</span></span>
      </div>
      <div className="m-body">{children}</div>
      <div className="m-tabbar">
        {tabs.map((t) => (
          <div key={t.key} className={`m-tab${t.key === tab ? ' on' : ''}`} onClick={() => onTab(t.key)}>
            <span className="ic material-symbols-outlined">{t.ic}</span><span>{t.label}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
const MIcon = ({ n, size, style }) =>
  React.createElement('span', { className: 'material-symbols-outlined', style: { fontSize: size || '1.1em', lineHeight: 1, verticalAlign: 'middle', ...(style || {}) } }, n);
Object.assign(window, { MTag, Phone, MIcon });
