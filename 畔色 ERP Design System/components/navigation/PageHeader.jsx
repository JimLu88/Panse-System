import React from 'react';

let injected = false;
function inject() {
  if (injected || typeof document === 'undefined') return;
  injected = true;
  const css = `
.pds-ph{display:flex;align-items:flex-end;justify-content:space-between;gap:16px;flex-wrap:wrap;margin-bottom:18px}
.pds-ph__crumb{font-size:12px;color:var(--text-tertiary);margin-bottom:6px;display:flex;gap:6px;align-items:center}
.pds-ph__crumb span+span::before{content:"/";margin-right:6px;color:var(--border-strong)}
.pds-ph__title{font-size:24px;font-weight:var(--weight-black);color:var(--text-primary);letter-spacing:var(--tracking-tight);margin:0;line-height:1.2}
.pds-ph__sub{font-size:13px;color:var(--text-secondary);margin-top:4px}
.pds-ph__extra{display:flex;align-items:center;gap:8px;flex:none}
`;
  const el = document.createElement('style');
  el.setAttribute('data-pds', 'pageheader');
  el.textContent = css;
  document.head.appendChild(el);
}

/** 页面标题区。breadcrumb + 标题 + 副标题 + 右侧操作。 */
export function PageHeader({ title, subtitle, breadcrumb = [], extra, className = '', ...rest }) {
  inject();
  return (
    <div className={`pds-ph ${className}`} {...rest}>
      <div>
        {breadcrumb.length > 0 && (
          <div className="pds-ph__crumb">{breadcrumb.map((b, i) => <span key={i}>{b}</span>)}</div>
        )}
        <h1 className="pds-ph__title">{title}</h1>
        {subtitle && <div className="pds-ph__sub">{subtitle}</div>}
      </div>
      {extra && <div className="pds-ph__extra">{extra}</div>}
    </div>
  );
}
