import React from 'react';

let injected = false;
function inject() {
  if (injected || typeof document === 'undefined') return;
  injected = true;
  const css = `
.pds-tag{display:inline-flex;align-items:center;gap:5px;font-family:var(--font-sans);font-size:12px;font-weight:var(--weight-semibold);line-height:1;padding:3px 9px;border-radius:var(--radius-sm);border:1px solid transparent;white-space:nowrap}
.pds-tag__dot{width:6px;height:6px;border-radius:50%;background:currentColor;flex:none}
.pds-tag--default{background:var(--surface-sunken);color:var(--text-secondary);border-color:var(--border)}
.pds-tag--brand{background:var(--primary-soft);color:var(--primary);border-color:var(--primary-border)}
.pds-tag--success{background:var(--success-soft);color:var(--success);border-color:var(--success-border)}
.pds-tag--warning{background:var(--warning-soft);color:#b45309;border-color:var(--warning-border)}
.pds-tag--danger{background:var(--danger-soft);color:var(--danger);border-color:var(--danger-border)}
.pds-tag--info{background:var(--info-soft);color:#0369a1;border-color:var(--info-border)}
.pds-tag--solid{border:none;color:#fff}
.pds-tag--solid.pds-tag--brand{background:var(--primary)}
.pds-tag--solid.pds-tag--success{background:var(--success)}
.pds-tag--solid.pds-tag--warning{background:var(--warning)}
.pds-tag--solid.pds-tag--danger{background:var(--danger)}
.pds-tag--solid.pds-tag--info{background:var(--info)}
.pds-tag__close{margin-left:2px;cursor:pointer;opacity:.6;font-size:13px;line-height:1}
.pds-tag__close:hover{opacity:1}
`;
  const el = document.createElement('style');
  el.setAttribute('data-pds', 'tag');
  el.textContent = css;
  document.head.appendChild(el);
}

/** 状态标签。tone 控制语义色；solid 实色；dot 前置圆点；closable 可关闭。 */
export function Tag({ tone = 'default', solid = false, dot = false, closable = false, onClose, children, className = '', ...rest }) {
  inject();
  const cls = ['pds-tag', `pds-tag--${tone}`, solid ? 'pds-tag--solid' : '', className].filter(Boolean).join(' ');
  return (
    <span className={cls} {...rest}>
      {dot && <span className="pds-tag__dot" />}
      {children}
      {closable && <span className="pds-tag__close" onClick={onClose}>×</span>}
    </span>
  );
}
