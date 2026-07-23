import React from 'react';

let injected = false;
function inject() {
  if (injected || typeof document === 'undefined') return;
  injected = true;
  const css = `
.pds-tabs{display:flex;align-items:center;gap:4px;border-bottom:1px solid var(--border);font-family:var(--font-sans)}
.pds-tab{position:relative;padding:10px 14px;font-size:14px;font-weight:var(--weight-medium);color:var(--text-secondary);cursor:pointer;white-space:nowrap;transition:color var(--dur-fast) var(--ease-out)}
.pds-tab:hover{color:var(--text-primary)}
.pds-tab--active{color:var(--primary);font-weight:var(--weight-semibold)}
.pds-tab--active::after{content:"";position:absolute;left:12px;right:12px;bottom:-1px;height:2px;background:var(--primary);border-radius:2px}
.pds-tab__badge{margin-left:6px;font-size:11px;font-weight:600;background:var(--surface-sunken);color:var(--text-secondary);padding:1px 6px;border-radius:var(--radius-pill)}
.pds-tab--active .pds-tab__badge{background:var(--primary-soft);color:var(--primary)}
`;
  const el = document.createElement('style');
  el.setAttribute('data-pds', 'tabs');
  el.textContent = css;
  document.head.appendChild(el);
}

/** 下划线标签页。items: [{key,label,badge}]。受控 value+onChange。 */
export function Tabs({ items = [], value, defaultValue, onChange, className = '', ...rest }) {
  inject();
  const [inner, setInner] = React.useState(defaultValue ?? (items[0] && items[0].key));
  const cur = value !== undefined ? value : inner;
  function pick(k) { if (value === undefined) setInner(k); onChange && onChange(k); }
  return (
    <div className={`pds-tabs ${className}`} {...rest}>
      {items.map((it) => (
        <div key={it.key} className={`pds-tab${it.key === cur ? ' pds-tab--active' : ''}`} onClick={() => pick(it.key)}>
          {it.label}
          {it.badge != null && <span className="pds-tab__badge">{it.badge}</span>}
        </div>
      ))}
    </div>
  );
}
