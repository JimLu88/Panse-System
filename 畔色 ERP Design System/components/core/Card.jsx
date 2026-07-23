import React from 'react';

let injected = false;
function inject() {
  if (injected || typeof document === 'undefined') return;
  injected = true;
  const css = `
.pds-card{background:var(--surface-card);border:1px solid var(--border);border-radius:var(--radius-xl);box-shadow:var(--shadow-xs);transition:box-shadow var(--dur-base) var(--ease-out),border-color var(--dur-base) var(--ease-out),transform var(--dur-base) var(--ease-out)}
.pds-card--hoverable{cursor:pointer}
.pds-card--hoverable:hover{box-shadow:var(--shadow-md);border-color:var(--primary-border);transform:translateY(-1px)}
.pds-card__hd{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:14px 18px;border-bottom:1px solid var(--border-subtle)}
.pds-card__title{font-size:15px;font-weight:var(--weight-bold);color:var(--text-primary);letter-spacing:var(--tracking-snug)}
.pds-card__extra{font-size:13px;color:var(--text-secondary)}
.pds-card__body{padding:18px}
.pds-card__body--tight{padding:12px 14px}
`;
  const el = document.createElement('style');
  el.setAttribute('data-pds', 'card');
  el.textContent = css;
  document.head.appendChild(el);
}

/** 卡片容器。圆角 16，柔和阴影。可选 title / extra；hoverable 可点击。 */
export function Card({ title, extra, hoverable = false, tight = false, children, style, className = '', ...rest }) {
  inject();
  const cls = ['pds-card', hoverable ? 'pds-card--hoverable' : '', className].filter(Boolean).join(' ');
  return (
    <div className={cls} style={style} {...rest}>
      {(title || extra) && (
        <div className="pds-card__hd">
          <span className="pds-card__title">{title}</span>
          {extra && <span className="pds-card__extra">{extra}</span>}
        </div>
      )}
      <div className={`pds-card__body${tight ? ' pds-card__body--tight' : ''}`}>{children}</div>
    </div>
  );
}
