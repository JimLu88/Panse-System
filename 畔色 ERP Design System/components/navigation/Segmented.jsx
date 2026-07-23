import React from 'react';

let injected = false;
function inject() {
  if (injected || typeof document === 'undefined') return;
  injected = true;
  const css = `
.pds-seg{display:inline-flex;background:var(--surface-sunken);border:1px solid var(--border);border-radius:var(--radius-md);padding:3px;gap:2px;font-family:var(--font-sans)}
.pds-seg__opt{padding:5px 14px;border-radius:var(--radius-sm);font-size:13px;font-weight:var(--weight-medium);color:var(--text-secondary);cursor:pointer;white-space:nowrap;transition:color var(--dur-fast) var(--ease-out),background var(--dur-fast) var(--ease-out),box-shadow var(--dur-fast) var(--ease-out)}
.pds-seg__opt:hover{color:var(--text-primary)}
.pds-seg__opt--active{background:var(--surface-card);color:var(--primary);font-weight:var(--weight-semibold);box-shadow:var(--shadow-xs)}
.pds-seg--sm .pds-seg__opt{padding:3px 10px;font-size:12px}
`;
  const el = document.createElement('style');
  el.setAttribute('data-pds', 'segmented');
  el.textContent = css;
  document.head.appendChild(el);
}

/** 分段控制器 (2-N 个短选项)。受控 value+onChange。 */
export function Segmented({ options = [], value, defaultValue, onChange, size = 'md', className = '', ...rest }) {
  inject();
  const norm = options.map((o) => (typeof o === 'object' ? o : { label: o, value: o }));
  const [inner, setInner] = React.useState(defaultValue ?? (norm[0] && norm[0].value));
  const cur = value !== undefined ? value : inner;
  function pick(v) { if (value === undefined) setInner(v); onChange && onChange(v); }
  return (
    <div className={`pds-seg${size === 'sm' ? ' pds-seg--sm' : ''} ${className}`} {...rest}>
      {norm.map((o) => (
        <span key={String(o.value)} className={`pds-seg__opt${o.value === cur ? ' pds-seg__opt--active' : ''}`} onClick={() => pick(o.value)}>
          {o.label}
        </span>
      ))}
    </div>
  );
}
