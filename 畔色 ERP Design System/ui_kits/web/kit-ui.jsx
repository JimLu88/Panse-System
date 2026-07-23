/* 畔色 ERP — Web UI Kit 轻量组件 (基于设计系统 token 的视觉重建)
   说明: 生产代码请直接用 _ds_bundle.js 中的组件 (window.ERPDesignSystem_*)。
   此处为 UI Kit 自包含演示版本，外观与官方组件一致。 */
(function () {
  if (document.getElementById('kit-ui-style')) return;
  const css = `
.k-btn{display:inline-flex;align-items:center;justify-content:center;gap:7px;font-family:var(--font-sans);font-weight:600;border-radius:var(--radius-md);border:1px solid transparent;cursor:pointer;white-space:nowrap;line-height:1;height:36px;padding:0 16px;font-size:14px;transition:all var(--dur-fast) var(--ease-out)}
.k-btn--sm{height:28px;padding:0 12px;font-size:13px}.k-btn--lg{height:44px;padding:0 20px;font-size:15px}
.k-btn--primary{background:var(--primary);color:var(--on-primary)}.k-btn--primary:hover{background:var(--primary-hover)}
.k-btn--secondary{background:var(--surface-card);color:var(--text-primary);border-color:var(--border-strong)}.k-btn--secondary:hover{border-color:var(--primary);color:var(--primary)}
.k-btn--ghost{background:transparent;color:var(--primary);border-color:var(--primary-border)}.k-btn--ghost:hover{background:var(--primary-soft)}
.k-btn--text{background:transparent;color:var(--text-secondary)}.k-btn--text:hover{background:var(--surface-hover);color:var(--text-primary)}
.k-btn--block{width:100%}
.k-tag{display:inline-flex;align-items:center;gap:5px;font-size:12px;font-weight:600;line-height:1;padding:3px 9px;border-radius:var(--radius-sm);border:1px solid transparent;white-space:nowrap}
.k-tag .dot{width:6px;height:6px;border-radius:50%;background:currentColor}
.k-tag--default{background:var(--surface-sunken);color:var(--text-secondary);border-color:var(--border)}
.k-tag--brand{background:var(--primary-soft);color:var(--primary);border-color:var(--primary-border)}
.k-tag--success{background:var(--success-soft);color:var(--success);border-color:var(--success-border)}
.k-tag--warning{background:var(--warning-soft);color:#b45309;border-color:var(--warning-border)}
.k-tag--danger{background:var(--danger-soft);color:var(--danger);border-color:var(--danger-border)}
.k-tag--info{background:var(--info-soft);color:#0369a1;border-color:var(--info-border)}
.k-card{background:var(--surface-card);border:1px solid var(--border);border-radius:var(--radius-xl);box-shadow:var(--shadow-xs);transition:all var(--dur-base) var(--ease-out)}
.k-card--hover{cursor:pointer}.k-card--hover:hover{box-shadow:var(--shadow-md);border-color:var(--primary-border);transform:translateY(-1px)}
.k-card__hd{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:13px 18px;border-bottom:1px solid var(--border-subtle)}
.k-card__title{font-size:15px;font-weight:700;color:var(--text-primary)}
.k-card__extra{font-size:13px;color:var(--text-secondary)}
.k-card__body{padding:18px}
.k-stat__top{display:flex;align-items:center;gap:8px}
.k-stat__icon{display:inline-flex;align-items:center;justify-content:center;width:30px;height:30px;border-radius:var(--radius-md);background:var(--primary-soft);color:var(--primary);font-size:15px}
.k-stat__title{font-size:13px;color:var(--text-secondary);font-weight:500}
.k-stat__val{font-family:var(--font-mono);font-feature-settings:"tnum" 1;font-weight:800;font-size:28px;line-height:1.1;letter-spacing:-.01em;color:var(--text-primary);margin-top:6px}
.k-stat__foot{display:flex;align-items:center;gap:8px;font-size:12px;color:var(--text-tertiary);margin-top:6px}
.k-up{color:var(--success);font-weight:600}.k-down{color:var(--danger);font-weight:600}
.k-tbl-wrap{background:var(--surface-card);border:1px solid var(--border);border-radius:var(--radius-xl);overflow:hidden;box-shadow:var(--shadow-xs)}
.k-tbl-scroll{overflow:auto}
.k-tbl{width:100%;border-collapse:collapse;font-size:14px}
.k-tbl thead th{position:sticky;top:0;background:var(--surface-sunken);color:var(--text-secondary);font-weight:600;font-size:12px;text-align:left;padding:0 16px;height:42px;white-space:nowrap;border-bottom:1px solid var(--border);z-index:2}
.k-tbl th.num,.k-tbl td.num{text-align:right}.k-tbl th.ctr,.k-tbl td.ctr{text-align:center}
.k-tbl tbody td{padding:0 16px;height:48px;color:var(--text-primary);border-bottom:1px solid var(--border-subtle)}
.k-tbl tbody tr{transition:background var(--dur-fast) var(--ease-out);cursor:pointer}
.k-tbl tbody tr:hover{background:var(--surface-hover)}
.k-tbl tbody tr:last-child td{border-bottom:none}
.k-mono{font-family:var(--font-mono);font-feature-settings:"tnum" 1}
.k-field{display:flex;flex-direction:column;gap:6px}
.k-field__label{font-size:13px;font-weight:500;color:var(--text-secondary)}
.k-input{display:flex;align-items:center;gap:8px;background:var(--surface-card);border:1px solid var(--border-strong);border-radius:var(--radius-md);padding:0 12px;height:36px;transition:all var(--dur-fast) var(--ease-out)}
.k-input:focus-within{border-color:var(--primary);box-shadow:var(--focus-ring)}
.k-input input{flex:1;border:none;outline:none;background:transparent;font-family:inherit;font-size:14px;color:var(--text-primary);min-width:0}
.k-input input::placeholder{color:var(--text-tertiary)}
.k-input .affix{color:var(--text-tertiary);font-size:14px}
.k-seg{display:inline-flex;background:var(--surface-sunken);border:1px solid var(--border);border-radius:var(--radius-md);padding:3px;gap:2px}
.k-seg span{padding:5px 13px;border-radius:var(--radius-sm);font-size:13px;font-weight:500;color:var(--text-secondary);cursor:pointer;white-space:nowrap;transition:all var(--dur-fast) var(--ease-out)}
.k-seg span.on{background:var(--surface-card);color:var(--primary);font-weight:600;box-shadow:var(--shadow-xs)}
.k-tabs{display:flex;gap:4px;border-bottom:1px solid var(--border)}
.k-tab{position:relative;padding:10px 14px;font-size:14px;font-weight:500;color:var(--text-secondary);cursor:pointer;white-space:nowrap}
.k-tab:hover{color:var(--text-primary)}
.k-tab.on{color:var(--primary);font-weight:600}
.k-tab.on::after{content:"";position:absolute;left:12px;right:12px;bottom:-1px;height:2px;background:var(--primary);border-radius:2px}
.k-tab .bd{margin-left:6px;font-size:11px;font-weight:600;background:var(--surface-sunken);color:var(--text-secondary);padding:1px 6px;border-radius:999px}
.k-tab.on .bd{background:var(--primary-soft);color:var(--primary)}
.k-nav{display:flex;align-items:center;gap:4px;background:var(--nav-bg);height:56px;padding:0 16px}
.k-nav__brand{display:flex;align-items:center;gap:9px;color:var(--nav-text-strong);font-weight:700;font-size:16px;letter-spacing:-.02em;margin-right:16px;white-space:nowrap}
.k-nav__logo{width:26px;height:26px;border-radius:8px;background:linear-gradient(135deg,var(--teal-400),var(--teal-600));display:flex;align-items:center;justify-content:center;color:#fff;font-weight:800;font-size:14px}
.k-nav__items{display:flex;align-items:center;gap:2px;flex:1;min-width:0}
.k-nav__item{padding:7px 12px;border-radius:var(--radius-md);color:var(--nav-text);font-size:14px;font-weight:500;cursor:pointer;white-space:nowrap;transition:all var(--dur-fast) var(--ease-out)}
.k-nav__item:hover{color:var(--nav-text-strong);background:rgba(255,255,255,.08)}
.k-nav__item.on{color:var(--nav-active);background:var(--nav-active-bg)}
.k-nav__right{display:flex;align-items:center;gap:8px;margin-left:auto}
.k-avatar{width:30px;height:30px;border-radius:50%;background:var(--teal-600);color:#fff;display:flex;align-items:center;justify-content:center;font-size:13px;font-weight:600}
.k-icbtn{width:34px;height:34px;border-radius:var(--radius-md);border:1px solid rgba(255,255,255,.28);background:transparent;color:rgba(255,255,255,.85);display:inline-flex;align-items:center;justify-content:center;cursor:pointer;font-size:15px;transition:all var(--dur-fast) var(--ease-out)}
.k-icbtn:hover{background:rgba(255,255,255,.1);color:#fff}
`;
  const el = document.createElement('style');
  el.id = 'kit-ui-style';
  el.textContent = css;
  document.head.appendChild(el);
})();

const KBtn = ({ variant = 'primary', size = 'md', block, icon, children, ...p }) =>
  React.createElement('button', { className: `k-btn k-btn--${variant}${size !== 'md' ? ' k-btn--' + size : ''}${block ? ' k-btn--block' : ''}`, ...p }, icon, children && React.createElement('span', null, children));
const KTag = ({ tone = 'default', dot, children, ...p }) =>
  React.createElement('span', { className: `k-tag k-tag--${tone}`, ...p }, dot && React.createElement('span', { className: 'dot' }), children);
const KCard = ({ title, extra, hover, children, style, ...p }) =>
  React.createElement('div', { className: `k-card${hover ? ' k-card--hover' : ''}`, style, ...p },
    (title || extra) && React.createElement('div', { className: 'k-card__hd' },
      React.createElement('span', { className: 'k-card__title' }, title),
      extra && React.createElement('span', { className: 'k-card__extra' }, extra)),
    React.createElement('div', { className: 'k-card__body' }, children));
const KStat = ({ title, value, prefix, icon, delta, dir = 'up', footer, valueColor }) =>
  React.createElement('div', null,
    React.createElement('div', { className: 'k-stat__top' }, icon && React.createElement('span', { className: 'k-stat__icon' }, icon), React.createElement('span', { className: 'k-stat__title' }, title)),
    React.createElement('div', { className: 'k-stat__val', style: valueColor ? { color: valueColor } : null }, prefix, value),
    (delta != null || footer) && React.createElement('div', { className: 'k-stat__foot' },
      delta != null && React.createElement('span', { className: dir === 'up' ? 'k-up' : 'k-down' }, (dir === 'up' ? '↑ ' : '↓ ') + delta), footer));
const KSeg = ({ options, value, onChange }) =>
  React.createElement('div', { className: 'k-seg' }, options.map((o) => {
    const opt = typeof o === 'object' ? o : { label: o, value: o };
    return React.createElement('span', { key: String(opt.value), className: opt.value === value ? 'on' : '', onClick: () => onChange && onChange(opt.value) }, opt.label);
  }));
const KTabs = ({ items, value, onChange }) =>
  React.createElement('div', { className: 'k-tabs' }, items.map((it) =>
    React.createElement('div', { key: it.key, className: `k-tab${it.key === value ? ' on' : ''}`, onClick: () => onChange && onChange(it.key) },
      it.label, it.badge != null && React.createElement('span', { className: 'bd' }, it.badge))));

const MIcon = ({ n, size, style }) =>
  React.createElement('span', { className: 'material-symbols-outlined', style: { fontSize: size || '1.15em', lineHeight: 1, verticalAlign: 'middle', ...(style || {}) } }, n);

Object.assign(window, { KBtn, KTag, KCard, KStat, KSeg, KTabs, MIcon });
