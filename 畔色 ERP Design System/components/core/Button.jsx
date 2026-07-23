import React from 'react';

let injected = false;
function inject() {
  if (injected || typeof document === 'undefined') return;
  injected = true;
  const css = `
.pds-btn{display:inline-flex;align-items:center;justify-content:center;gap:8px;font-family:var(--font-sans);font-weight:var(--weight-semibold);border-radius:var(--radius-md);border:1px solid transparent;cursor:pointer;white-space:nowrap;user-select:none;line-height:1;transition:background var(--dur-fast) var(--ease-out),border-color var(--dur-fast) var(--ease-out),color var(--dur-fast) var(--ease-out),box-shadow var(--dur-fast) var(--ease-out),transform var(--dur-fast) var(--ease-out)}
.pds-btn:focus-visible{outline:none;box-shadow:var(--focus-ring)}
.pds-btn:active{transform:translateY(.5px)}
.pds-btn--sm{height:28px;padding:0 12px;font-size:13px}
.pds-btn--md{height:36px;padding:0 16px;font-size:14px}
.pds-btn--lg{height:44px;padding:0 20px;font-size:15px}
.pds-btn--block{width:100%}
.pds-btn[disabled]{opacity:.45;cursor:not-allowed;pointer-events:none}
.pds-btn--primary{background:var(--primary);color:var(--on-primary)}
.pds-btn--primary:hover{background:var(--primary-hover)}
.pds-btn--primary:active{background:var(--primary-active)}
.pds-btn--secondary{background:var(--surface-card);color:var(--text-primary);border-color:var(--border-strong)}
.pds-btn--secondary:hover{border-color:var(--primary);color:var(--primary)}
.pds-btn--ghost{background:transparent;color:var(--primary);border-color:var(--primary-border)}
.pds-btn--ghost:hover{background:var(--primary-soft)}
.pds-btn--text{background:transparent;color:var(--text-secondary)}
.pds-btn--text:hover{background:var(--surface-hover);color:var(--text-primary)}
.pds-btn--primary.pds-btn--danger{background:var(--danger)}
.pds-btn--primary.pds-btn--danger:hover{filter:brightness(.94)}
.pds-btn--secondary.pds-btn--danger{color:var(--danger);border-color:var(--danger-border)}
.pds-btn--secondary.pds-btn--danger:hover{border-color:var(--danger);background:var(--danger-soft)}
.pds-btn--text.pds-btn--danger{color:var(--danger)}
.pds-btn--text.pds-btn--danger:hover{background:var(--danger-soft)}
.pds-btn__spin{width:14px;height:14px;border:2px solid currentColor;border-top-color:transparent;border-radius:50%;animation:pds-spin .6s linear infinite}
@keyframes pds-spin{to{transform:rotate(360deg)}}
`;
  const el = document.createElement('style');
  el.setAttribute('data-pds', 'button');
  el.textContent = css;
  document.head.appendChild(el);
}

/**
 * 主操作按钮。变体: primary / secondary / ghost / text；尺寸 sm/md/lg。
 */
export function Button({
  variant = 'primary',
  size = 'md',
  icon = null,
  loading = false,
  disabled = false,
  block = false,
  danger = false,
  children,
  className = '',
  ...rest
}) {
  inject();
  const cls = [
    'pds-btn',
    `pds-btn--${variant}`,
    `pds-btn--${size}`,
    block ? 'pds-btn--block' : '',
    danger ? 'pds-btn--danger' : '',
    className,
  ].filter(Boolean).join(' ');
  return (
    <button className={cls} disabled={disabled || loading} {...rest}>
      {loading ? <span className="pds-btn__spin" /> : icon}
      {children != null && <span>{children}</span>}
    </button>
  );
}
