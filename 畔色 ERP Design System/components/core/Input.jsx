import React from 'react';

let injected = false;
function inject() {
  if (injected || typeof document === 'undefined') return;
  injected = true;
  const css = `
.pds-field{display:flex;flex-direction:column;gap:6px;font-family:var(--font-sans)}
.pds-field__label{font-size:13px;font-weight:var(--weight-medium);color:var(--text-secondary)}
.pds-field__req{color:var(--danger);margin-left:2px}
.pds-input-wrap{display:flex;align-items:center;gap:8px;background:var(--surface-card);border:1px solid var(--border-strong);border-radius:var(--radius-md);padding:0 12px;height:36px;transition:border-color var(--dur-fast) var(--ease-out),box-shadow var(--dur-fast) var(--ease-out)}
.pds-input-wrap:hover{border-color:var(--primary)}
.pds-input-wrap:focus-within{border-color:var(--primary);box-shadow:var(--focus-ring)}
.pds-input-wrap--lg{height:44px}
.pds-input-wrap--sm{height:28px;padding:0 10px}
.pds-input-wrap--err{border-color:var(--danger)}
.pds-input-wrap--err:focus-within{box-shadow:0 0 0 3px var(--danger-soft)}
.pds-input-wrap--disabled{background:var(--surface-sunken);opacity:.7;pointer-events:none}
.pds-input{flex:1;border:none;outline:none;background:transparent;font-family:inherit;font-size:14px;color:var(--text-primary);min-width:0}
.pds-input::placeholder{color:var(--text-tertiary)}
.pds-input__affix{color:var(--text-tertiary);font-size:14px;display:inline-flex;flex:none}
.pds-field__err{font-size:12px;color:var(--danger)}
`;
  const el = document.createElement('style');
  el.setAttribute('data-pds', 'input');
  el.textContent = css;
  document.head.appendChild(el);
}

/** 文本输入。可选 label / 前后缀图标 / 错误态。 */
export function Input({ label, required = false, size = 'md', prefix, suffix, error, disabled = false, className = '', ...rest }) {
  inject();
  const wrapCls = ['pds-input-wrap', `pds-input-wrap--${size}`, error ? 'pds-input-wrap--err' : '', disabled ? 'pds-input-wrap--disabled' : ''].filter(Boolean).join(' ');
  const input = (
    <div className={wrapCls}>
      {prefix && <span className="pds-input__affix">{prefix}</span>}
      <input className="pds-input" disabled={disabled} {...rest} />
      {suffix && <span className="pds-input__affix">{suffix}</span>}
    </div>
  );
  if (!label && !error) return <span className={className}>{input}</span>;
  return (
    <label className={`pds-field ${className}`}>
      {label && <span className="pds-field__label">{label}{required && <span className="pds-field__req">*</span>}</span>}
      {input}
      {error && <span className="pds-field__err">{error}</span>}
    </label>
  );
}
