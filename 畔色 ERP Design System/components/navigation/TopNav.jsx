import React from 'react';

let injected = false;
function inject() {
  if (injected || typeof document === 'undefined') return;
  injected = true;
  const css = `
.pds-nav{display:flex;align-items:center;gap:4px;background:var(--nav-bg);height:56px;padding:0 16px;font-family:var(--font-sans)}
.pds-nav__brand{display:flex;align-items:center;gap:9px;color:var(--nav-text-strong);font-weight:var(--weight-bold);font-size:16px;letter-spacing:var(--tracking-tight);margin-right:18px;white-space:nowrap}
.pds-nav__logo{width:26px;height:26px;border-radius:8px;background:linear-gradient(135deg,var(--teal-400),var(--teal-600));display:flex;align-items:center;justify-content:center;color:#fff;font-weight:800;font-size:14px;flex:none}
.pds-nav__items{display:flex;align-items:center;gap:2px;flex:1;min-width:0;overflow:hidden}
.pds-nav__item{padding:7px 13px;border-radius:var(--radius-md);color:var(--nav-text);font-size:14px;font-weight:var(--weight-medium);cursor:pointer;white-space:nowrap;transition:background var(--dur-fast) var(--ease-out),color var(--dur-fast) var(--ease-out)}
.pds-nav__item:hover{color:var(--nav-text-strong);background:rgba(255,255,255,.08)}
.pds-nav__item--active{color:var(--nav-active);background:var(--nav-active-bg)}
.pds-nav__right{display:flex;align-items:center;gap:8px;margin-left:auto;flex:none}
`;
  const el = document.createElement('style');
  el.setAttribute('data-pds', 'nav');
  el.textContent = css;
  document.head.appendChild(el);
}

/** 顶部导航栏 (深青底)。brand + 横向菜单 + 右侧操作区。 */
export function TopNav({ brand = '畔色孚格 ERP', logo = '畔', items = [], activeKey, onSelect, right, className = '', ...rest }) {
  inject();
  return (
    <div className={`pds-nav ${className}`} {...rest}>
      <div className="pds-nav__brand">
        <span className="pds-nav__logo">{logo}</span>
        {brand}
      </div>
      <div className="pds-nav__items">
        {items.map((it) => (
          <span
            key={it.key}
            className={`pds-nav__item${it.key === activeKey ? ' pds-nav__item--active' : ''}`}
            onClick={() => onSelect && onSelect(it.key)}
          >
            {it.label}
          </span>
        ))}
      </div>
      {right && <div className="pds-nav__right">{right}</div>}
    </div>
  );
}
