import React from 'react';

let injected = false;
function inject() {
  if (injected || typeof document === 'undefined') return;
  injected = true;
  const css = `
.pds-stat{display:flex;flex-direction:column;gap:6px}
.pds-stat__top{display:flex;align-items:center;gap:8px}
.pds-stat__icon{display:inline-flex;align-items:center;justify-content:center;width:30px;height:30px;border-radius:var(--radius-md);background:var(--primary-soft);color:var(--primary);font-size:15px;flex:none}
.pds-stat__title{font-size:13px;color:var(--text-secondary);font-weight:var(--weight-medium)}
.pds-stat__value{font-family:var(--font-mono);font-feature-settings:"tnum" 1;font-weight:var(--weight-black);font-size:28px;line-height:1.1;letter-spacing:var(--tracking-snug);color:var(--text-primary)}
.pds-stat__foot{display:flex;align-items:center;gap:8px;font-size:12px;color:var(--text-tertiary)}
.pds-stat__delta{font-weight:var(--weight-semibold)}
.pds-stat__delta--up{color:var(--success)}
.pds-stat__delta--down{color:var(--danger)}
`;
  const el = document.createElement('style');
  el.setAttribute('data-pds', 'statcard');
  el.textContent = css;
  document.head.appendChild(el);
}

/** KPI 数字卡。title + 大数字 (等宽) + 可选图标 / 涨跌 / 脚注。 */
export function StatCard({ title, value, prefix, icon, delta, deltaDir = 'up', footer, valueColor, ...rest }) {
  inject();
  return (
    <div className="pds-stat" {...rest}>
      <div className="pds-stat__top">
        {icon && <span className="pds-stat__icon">{icon}</span>}
        <span className="pds-stat__title">{title}</span>
      </div>
      <div className="pds-stat__value" style={valueColor ? { color: valueColor } : undefined}>
        {prefix}{value}
      </div>
      {(delta != null || footer) && (
        <div className="pds-stat__foot">
          {delta != null && (
            <span className={`pds-stat__delta pds-stat__delta--${deltaDir}`}>
              {deltaDir === 'up' ? '↑' : '↓'} {delta}
            </span>
          )}
          {footer}
        </div>
      )}
    </div>
  );
}
