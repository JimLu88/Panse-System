/* @ds-bundle: {"format":3,"namespace":"ERPDesignSystem_dc7e11","components":[{"name":"Button","sourcePath":"components/core/Button.jsx"},{"name":"Card","sourcePath":"components/core/Card.jsx"},{"name":"Input","sourcePath":"components/core/Input.jsx"},{"name":"Select","sourcePath":"components/core/Select.jsx"},{"name":"StatCard","sourcePath":"components/core/StatCard.jsx"},{"name":"Tag","sourcePath":"components/core/Tag.jsx"},{"name":"DataTable","sourcePath":"components/data/DataTable.jsx"},{"name":"PageHeader","sourcePath":"components/navigation/PageHeader.jsx"},{"name":"Segmented","sourcePath":"components/navigation/Segmented.jsx"},{"name":"Tabs","sourcePath":"components/navigation/Tabs.jsx"},{"name":"TopNav","sourcePath":"components/navigation/TopNav.jsx"}],"sourceHashes":{"components/core/Button.jsx":"31f65ac13b26","components/core/Card.jsx":"d8047b6f657e","components/core/Input.jsx":"61aff1dc7a7c","components/core/Select.jsx":"bc9f1a201d71","components/core/StatCard.jsx":"10789ee75fe2","components/core/Tag.jsx":"ec992d2487ce","components/data/DataTable.jsx":"d13019bfd126","components/navigation/PageHeader.jsx":"47ac399af241","components/navigation/Segmented.jsx":"2f87a9da6f1c","components/navigation/Tabs.jsx":"42210c1c50c4","components/navigation/TopNav.jsx":"e47b619a8a63","ui_kits/mobile/kit-m.jsx":"c3543b6b8c4b","ui_kits/mobile/screens-m.jsx":"5d24c594ab03","ui_kits/web/DashboardScreen.jsx":"8b426d2068cb","ui_kits/web/InventoryScreen.jsx":"7f7625cc581e","ui_kits/web/LoginScreen.jsx":"7c91f2a5c7d5","ui_kits/web/OrdersScreen.jsx":"0efd1dfaaed9","ui_kits/web/ReconScreen.jsx":"ef17cef68e00","ui_kits/web/kit-ui.jsx":"a8c9b5e268df"},"inlinedExternals":[],"unexposedExports":[]} */

(() => {

const __ds_ns = (window.ERPDesignSystem_dc7e11 = window.ERPDesignSystem_dc7e11 || {});

const __ds_scope = {};

(__ds_ns.__errors = __ds_ns.__errors || []);

// components/core/Button.jsx
try { (() => {
function _extends() { return _extends = Object.assign ? Object.assign.bind() : function (n) { for (var e = 1; e < arguments.length; e++) { var t = arguments[e]; for (var r in t) ({}).hasOwnProperty.call(t, r) && (n[r] = t[r]); } return n; }, _extends.apply(null, arguments); }
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
function Button({
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
  const cls = ['pds-btn', `pds-btn--${variant}`, `pds-btn--${size}`, block ? 'pds-btn--block' : '', danger ? 'pds-btn--danger' : '', className].filter(Boolean).join(' ');
  return /*#__PURE__*/React.createElement("button", _extends({
    className: cls,
    disabled: disabled || loading
  }, rest), loading ? /*#__PURE__*/React.createElement("span", {
    className: "pds-btn__spin"
  }) : icon, children != null && /*#__PURE__*/React.createElement("span", null, children));
}
Object.assign(__ds_scope, { Button });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/core/Button.jsx", error: String((e && e.message) || e) }); }

// components/core/Card.jsx
try { (() => {
function _extends() { return _extends = Object.assign ? Object.assign.bind() : function (n) { for (var e = 1; e < arguments.length; e++) { var t = arguments[e]; for (var r in t) ({}).hasOwnProperty.call(t, r) && (n[r] = t[r]); } return n; }, _extends.apply(null, arguments); }
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
function Card({
  title,
  extra,
  hoverable = false,
  tight = false,
  children,
  style,
  className = '',
  ...rest
}) {
  inject();
  const cls = ['pds-card', hoverable ? 'pds-card--hoverable' : '', className].filter(Boolean).join(' ');
  return /*#__PURE__*/React.createElement("div", _extends({
    className: cls,
    style: style
  }, rest), (title || extra) && /*#__PURE__*/React.createElement("div", {
    className: "pds-card__hd"
  }, /*#__PURE__*/React.createElement("span", {
    className: "pds-card__title"
  }, title), extra && /*#__PURE__*/React.createElement("span", {
    className: "pds-card__extra"
  }, extra)), /*#__PURE__*/React.createElement("div", {
    className: `pds-card__body${tight ? ' pds-card__body--tight' : ''}`
  }, children));
}
Object.assign(__ds_scope, { Card });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/core/Card.jsx", error: String((e && e.message) || e) }); }

// components/core/Input.jsx
try { (() => {
function _extends() { return _extends = Object.assign ? Object.assign.bind() : function (n) { for (var e = 1; e < arguments.length; e++) { var t = arguments[e]; for (var r in t) ({}).hasOwnProperty.call(t, r) && (n[r] = t[r]); } return n; }, _extends.apply(null, arguments); }
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
function Input({
  label,
  required = false,
  size = 'md',
  prefix,
  suffix,
  error,
  disabled = false,
  className = '',
  ...rest
}) {
  inject();
  const wrapCls = ['pds-input-wrap', `pds-input-wrap--${size}`, error ? 'pds-input-wrap--err' : '', disabled ? 'pds-input-wrap--disabled' : ''].filter(Boolean).join(' ');
  const input = /*#__PURE__*/React.createElement("div", {
    className: wrapCls
  }, prefix && /*#__PURE__*/React.createElement("span", {
    className: "pds-input__affix"
  }, prefix), /*#__PURE__*/React.createElement("input", _extends({
    className: "pds-input",
    disabled: disabled
  }, rest)), suffix && /*#__PURE__*/React.createElement("span", {
    className: "pds-input__affix"
  }, suffix));
  if (!label && !error) return /*#__PURE__*/React.createElement("span", {
    className: className
  }, input);
  return /*#__PURE__*/React.createElement("label", {
    className: `pds-field ${className}`
  }, label && /*#__PURE__*/React.createElement("span", {
    className: "pds-field__label"
  }, label, required && /*#__PURE__*/React.createElement("span", {
    className: "pds-field__req"
  }, "*")), input, error && /*#__PURE__*/React.createElement("span", {
    className: "pds-field__err"
  }, error));
}
Object.assign(__ds_scope, { Input });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/core/Input.jsx", error: String((e && e.message) || e) }); }

// components/core/Select.jsx
try { (() => {
function _extends() { return _extends = Object.assign ? Object.assign.bind() : function (n) { for (var e = 1; e < arguments.length; e++) { var t = arguments[e]; for (var r in t) ({}).hasOwnProperty.call(t, r) && (n[r] = t[r]); } return n; }, _extends.apply(null, arguments); }
let injected = false;
function inject() {
  if (injected || typeof document === 'undefined') return;
  injected = true;
  const css = `
.pds-select{position:relative;font-family:var(--font-sans);display:inline-block}
.pds-select__ctrl{display:flex;align-items:center;justify-content:space-between;gap:8px;background:var(--surface-card);border:1px solid var(--border-strong);border-radius:var(--radius-md);padding:0 12px;height:36px;cursor:pointer;font-size:14px;color:var(--text-primary);transition:border-color var(--dur-fast) var(--ease-out),box-shadow var(--dur-fast) var(--ease-out);min-width:140px}
.pds-select__ctrl:hover{border-color:var(--primary)}
.pds-select--open .pds-select__ctrl{border-color:var(--primary);box-shadow:var(--focus-ring)}
.pds-select__ph{color:var(--text-tertiary)}
.pds-select__arrow{color:var(--text-tertiary);transition:transform var(--dur-base) var(--ease-out);flex:none}
.pds-select--open .pds-select__arrow{transform:rotate(180deg)}
.pds-select__menu{position:absolute;top:calc(100% + 6px);left:0;right:0;background:var(--surface-card);border:1px solid var(--border);border-radius:var(--radius-lg);box-shadow:var(--shadow-lg);padding:6px;z-index:50;max-height:260px;overflow:auto}
.pds-select__opt{display:flex;align-items:center;justify-content:space-between;gap:8px;padding:8px 10px;border-radius:var(--radius-sm);font-size:14px;color:var(--text-primary);cursor:pointer}
.pds-select__opt:hover{background:var(--surface-hover)}
.pds-select__opt--active{background:var(--primary-soft);color:var(--primary);font-weight:var(--weight-semibold)}
.pds-select__check{color:var(--primary)}
`;
  const el = document.createElement('style');
  el.setAttribute('data-pds', 'select');
  el.textContent = css;
  document.head.appendChild(el);
}

/** 下拉选择。options: [{label,value}]。受控传 value+onChange，非受控传 defaultValue。 */
function Select({
  options = [],
  value,
  defaultValue,
  onChange,
  placeholder = '请选择',
  style,
  className = '',
  ...rest
}) {
  inject();
  const [open, setOpen] = React.useState(false);
  const [inner, setInner] = React.useState(defaultValue);
  const ref = React.useRef(null);
  const cur = value !== undefined ? value : inner;
  const selected = options.find(o => o.value === cur);
  React.useEffect(() => {
    function onDoc(e) {
      if (ref.current && !ref.current.contains(e.target)) setOpen(false);
    }
    document.addEventListener('mousedown', onDoc);
    return () => document.removeEventListener('mousedown', onDoc);
  }, []);
  function pick(o) {
    if (value === undefined) setInner(o.value);
    onChange && onChange(o.value, o);
    setOpen(false);
  }
  return /*#__PURE__*/React.createElement("div", _extends({
    ref: ref,
    className: `pds-select${open ? ' pds-select--open' : ''} ${className}`,
    style: style
  }, rest), /*#__PURE__*/React.createElement("div", {
    className: "pds-select__ctrl",
    onClick: () => setOpen(v => !v)
  }, /*#__PURE__*/React.createElement("span", {
    className: selected ? '' : 'pds-select__ph'
  }, selected ? selected.label : placeholder), /*#__PURE__*/React.createElement("span", {
    className: "pds-select__arrow"
  }, "\u25BE")), open && /*#__PURE__*/React.createElement("div", {
    className: "pds-select__menu"
  }, options.map(o => /*#__PURE__*/React.createElement("div", {
    key: String(o.value),
    className: `pds-select__opt${o.value === cur ? ' pds-select__opt--active' : ''}`,
    onClick: () => pick(o)
  }, /*#__PURE__*/React.createElement("span", null, o.label), o.value === cur && /*#__PURE__*/React.createElement("span", {
    className: "pds-select__check"
  }, "\u2713")))));
}
Object.assign(__ds_scope, { Select });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/core/Select.jsx", error: String((e && e.message) || e) }); }

// components/core/StatCard.jsx
try { (() => {
function _extends() { return _extends = Object.assign ? Object.assign.bind() : function (n) { for (var e = 1; e < arguments.length; e++) { var t = arguments[e]; for (var r in t) ({}).hasOwnProperty.call(t, r) && (n[r] = t[r]); } return n; }, _extends.apply(null, arguments); }
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
function StatCard({
  title,
  value,
  prefix,
  icon,
  delta,
  deltaDir = 'up',
  footer,
  valueColor,
  ...rest
}) {
  inject();
  return /*#__PURE__*/React.createElement("div", _extends({
    className: "pds-stat"
  }, rest), /*#__PURE__*/React.createElement("div", {
    className: "pds-stat__top"
  }, icon && /*#__PURE__*/React.createElement("span", {
    className: "pds-stat__icon"
  }, icon), /*#__PURE__*/React.createElement("span", {
    className: "pds-stat__title"
  }, title)), /*#__PURE__*/React.createElement("div", {
    className: "pds-stat__value",
    style: valueColor ? {
      color: valueColor
    } : undefined
  }, prefix, value), (delta != null || footer) && /*#__PURE__*/React.createElement("div", {
    className: "pds-stat__foot"
  }, delta != null && /*#__PURE__*/React.createElement("span", {
    className: `pds-stat__delta pds-stat__delta--${deltaDir}`
  }, deltaDir === 'up' ? '↑' : '↓', " ", delta), footer));
}
Object.assign(__ds_scope, { StatCard });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/core/StatCard.jsx", error: String((e && e.message) || e) }); }

// components/core/Tag.jsx
try { (() => {
function _extends() { return _extends = Object.assign ? Object.assign.bind() : function (n) { for (var e = 1; e < arguments.length; e++) { var t = arguments[e]; for (var r in t) ({}).hasOwnProperty.call(t, r) && (n[r] = t[r]); } return n; }, _extends.apply(null, arguments); }
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
function Tag({
  tone = 'default',
  solid = false,
  dot = false,
  closable = false,
  onClose,
  children,
  className = '',
  ...rest
}) {
  inject();
  const cls = ['pds-tag', `pds-tag--${tone}`, solid ? 'pds-tag--solid' : '', className].filter(Boolean).join(' ');
  return /*#__PURE__*/React.createElement("span", _extends({
    className: cls
  }, rest), dot && /*#__PURE__*/React.createElement("span", {
    className: "pds-tag__dot"
  }), children, closable && /*#__PURE__*/React.createElement("span", {
    className: "pds-tag__close",
    onClick: onClose
  }, "\xD7"));
}
Object.assign(__ds_scope, { Tag });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/core/Tag.jsx", error: String((e && e.message) || e) }); }

// components/data/DataTable.jsx
try { (() => {
function _extends() { return _extends = Object.assign ? Object.assign.bind() : function (n) { for (var e = 1; e < arguments.length; e++) { var t = arguments[e]; for (var r in t) ({}).hasOwnProperty.call(t, r) && (n[r] = t[r]); } return n; }, _extends.apply(null, arguments); }
let injected = false;
function inject() {
  if (injected || typeof document === 'undefined') return;
  injected = true;
  const css = `
.pds-table-wrap{background:var(--surface-card);border:1px solid var(--border);border-radius:var(--radius-xl);overflow:hidden;box-shadow:var(--shadow-xs)}
.pds-table-scroll{overflow:auto}
.pds-table{width:100%;border-collapse:collapse;font-family:var(--font-sans);font-size:14px}
.pds-table thead th{position:sticky;top:0;background:var(--surface-sunken);color:var(--text-secondary);font-weight:var(--weight-semibold);font-size:12px;text-align:left;padding:0 16px;height:42px;white-space:nowrap;border-bottom:1px solid var(--border);z-index:2}
.pds-table th.pds-num,.pds-table td.pds-num{text-align:right}
.pds-table th.pds-center,.pds-table td.pds-center{text-align:center}
.pds-table th.pds-sortable{cursor:pointer;user-select:none}
.pds-table th.pds-sortable:hover{color:var(--text-primary)}
.pds-table__sort{margin-left:4px;font-size:10px;color:var(--text-tertiary)}
.pds-table__sort--on{color:var(--primary)}
.pds-table tbody td{padding:0 16px;color:var(--text-primary);border-bottom:1px solid var(--border-subtle);vertical-align:middle}
.pds-table tbody tr{transition:background var(--dur-fast) var(--ease-out)}
.pds-table tbody tr:hover{background:var(--surface-hover)}
.pds-table tbody tr:last-child td{border-bottom:none}
.pds-table--zebra tbody tr:nth-child(even){background:var(--slate-50)}
.pds-table--zebra tbody tr:nth-child(even):hover{background:var(--surface-hover)}
.pds-table--compact tbody td{height:var(--row-compact)}
.pds-table--default tbody td{height:var(--row-default)}
.pds-table--spacious tbody td{height:var(--row-spacious)}
.pds-table .pds-mono{font-family:var(--font-mono);font-feature-settings:"tnum" 1}
.pds-table__check{width:16px;height:16px;accent-color:var(--primary);cursor:pointer}
.pds-table__sel{background:var(--primary-soft)!important}
.pds-table__cell-checkbox{width:44px;text-align:center}
`;
  const el = document.createElement('style');
  el.setAttribute('data-pds', 'table');
  el.textContent = css;
  document.head.appendChild(el);
}

/**
 * 数据表格。columns: [{key,title,align,mono,sortable,render,width}]。
 * 内置排序指示、行 hover、可选斑马纹与多选。density 控制行高。
 */
function DataTable({
  columns = [],
  data = [],
  rowKey = 'id',
  density = 'default',
  zebra = false,
  selectable = false,
  maxHeight,
  onRowClick,
  ...rest
}) {
  inject();
  const [sort, setSort] = React.useState(null); // {key, dir}
  const [sel, setSel] = React.useState(() => new Set());
  const keyOf = (row, i) => typeof rowKey === 'function' ? rowKey(row) : row[rowKey] ?? i;
  const sorted = React.useMemo(() => {
    if (!sort) return data;
    const col = columns.find(c => c.key === sort.key);
    if (!col) return data;
    const arr = [...data];
    arr.sort((a, b) => {
      const av = a[sort.key],
        bv = b[sort.key];
      if (av == null) return 1;
      if (bv == null) return -1;
      const r = typeof av === 'number' && typeof bv === 'number' ? av - bv : String(av).localeCompare(String(bv), 'zh');
      return sort.dir === 'asc' ? r : -r;
    });
    return arr;
  }, [data, sort, columns]);
  function toggleSort(col) {
    if (!col.sortable) return;
    setSort(s => {
      if (!s || s.key !== col.key) return {
        key: col.key,
        dir: 'asc'
      };
      if (s.dir === 'asc') return {
        key: col.key,
        dir: 'desc'
      };
      return null;
    });
  }
  const allSel = sorted.length > 0 && sorted.every((r, i) => sel.has(keyOf(r, i)));
  function toggleAll() {
    setSel(() => allSel ? new Set() : new Set(sorted.map((r, i) => keyOf(r, i))));
  }
  function toggleRow(k) {
    setSel(s => {
      const n = new Set(s);
      n.has(k) ? n.delete(k) : n.add(k);
      return n;
    });
  }
  return /*#__PURE__*/React.createElement("div", _extends({
    className: "pds-table-wrap"
  }, rest), /*#__PURE__*/React.createElement("div", {
    className: "pds-table-scroll",
    style: maxHeight ? {
      maxHeight
    } : undefined
  }, /*#__PURE__*/React.createElement("table", {
    className: `pds-table pds-table--${density}${zebra ? ' pds-table--zebra' : ''}`
  }, /*#__PURE__*/React.createElement("thead", null, /*#__PURE__*/React.createElement("tr", null, selectable && /*#__PURE__*/React.createElement("th", {
    className: "pds-table__cell-checkbox"
  }, /*#__PURE__*/React.createElement("input", {
    className: "pds-table__check",
    type: "checkbox",
    checked: allSel,
    onChange: toggleAll
  })), columns.map(c => {
    const on = sort && sort.key === c.key;
    const cls = [c.align === 'right' || c.mono ? 'pds-num' : '', c.align === 'center' ? 'pds-center' : '', c.sortable ? 'pds-sortable' : ''].filter(Boolean).join(' ');
    return /*#__PURE__*/React.createElement("th", {
      key: c.key,
      className: cls,
      style: c.width ? {
        width: c.width
      } : undefined,
      onClick: () => toggleSort(c)
    }, c.title, c.sortable && /*#__PURE__*/React.createElement("span", {
      className: `pds-table__sort${on ? ' pds-table__sort--on' : ''}`
    }, on ? sort.dir === 'asc' ? '▲' : '▼' : '↕'));
  }))), /*#__PURE__*/React.createElement("tbody", null, sorted.map((row, i) => {
    const k = keyOf(row, i);
    const isSel = sel.has(k);
    return /*#__PURE__*/React.createElement("tr", {
      key: k,
      className: isSel ? 'pds-table__sel' : '',
      onClick: onRowClick ? () => onRowClick(row) : undefined,
      style: onRowClick ? {
        cursor: 'pointer'
      } : undefined
    }, selectable && /*#__PURE__*/React.createElement("td", {
      className: "pds-table__cell-checkbox",
      onClick: e => e.stopPropagation()
    }, /*#__PURE__*/React.createElement("input", {
      className: "pds-table__check",
      type: "checkbox",
      checked: isSel,
      onChange: () => toggleRow(k)
    })), columns.map(c => {
      const cls = [c.align === 'right' || c.mono ? 'pds-num' : '', c.align === 'center' ? 'pds-center' : '', c.mono ? 'pds-mono' : ''].filter(Boolean).join(' ');
      return /*#__PURE__*/React.createElement("td", {
        key: c.key,
        className: cls
      }, c.render ? c.render(row[c.key], row) : row[c.key]);
    }));
  })))));
}
Object.assign(__ds_scope, { DataTable });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/data/DataTable.jsx", error: String((e && e.message) || e) }); }

// components/navigation/PageHeader.jsx
try { (() => {
function _extends() { return _extends = Object.assign ? Object.assign.bind() : function (n) { for (var e = 1; e < arguments.length; e++) { var t = arguments[e]; for (var r in t) ({}).hasOwnProperty.call(t, r) && (n[r] = t[r]); } return n; }, _extends.apply(null, arguments); }
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
function PageHeader({
  title,
  subtitle,
  breadcrumb = [],
  extra,
  className = '',
  ...rest
}) {
  inject();
  return /*#__PURE__*/React.createElement("div", _extends({
    className: `pds-ph ${className}`
  }, rest), /*#__PURE__*/React.createElement("div", null, breadcrumb.length > 0 && /*#__PURE__*/React.createElement("div", {
    className: "pds-ph__crumb"
  }, breadcrumb.map((b, i) => /*#__PURE__*/React.createElement("span", {
    key: i
  }, b))), /*#__PURE__*/React.createElement("h1", {
    className: "pds-ph__title"
  }, title), subtitle && /*#__PURE__*/React.createElement("div", {
    className: "pds-ph__sub"
  }, subtitle)), extra && /*#__PURE__*/React.createElement("div", {
    className: "pds-ph__extra"
  }, extra));
}
Object.assign(__ds_scope, { PageHeader });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/navigation/PageHeader.jsx", error: String((e && e.message) || e) }); }

// components/navigation/Segmented.jsx
try { (() => {
function _extends() { return _extends = Object.assign ? Object.assign.bind() : function (n) { for (var e = 1; e < arguments.length; e++) { var t = arguments[e]; for (var r in t) ({}).hasOwnProperty.call(t, r) && (n[r] = t[r]); } return n; }, _extends.apply(null, arguments); }
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
function Segmented({
  options = [],
  value,
  defaultValue,
  onChange,
  size = 'md',
  className = '',
  ...rest
}) {
  inject();
  const norm = options.map(o => typeof o === 'object' ? o : {
    label: o,
    value: o
  });
  const [inner, setInner] = React.useState(defaultValue ?? (norm[0] && norm[0].value));
  const cur = value !== undefined ? value : inner;
  function pick(v) {
    if (value === undefined) setInner(v);
    onChange && onChange(v);
  }
  return /*#__PURE__*/React.createElement("div", _extends({
    className: `pds-seg${size === 'sm' ? ' pds-seg--sm' : ''} ${className}`
  }, rest), norm.map(o => /*#__PURE__*/React.createElement("span", {
    key: String(o.value),
    className: `pds-seg__opt${o.value === cur ? ' pds-seg__opt--active' : ''}`,
    onClick: () => pick(o.value)
  }, o.label)));
}
Object.assign(__ds_scope, { Segmented });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/navigation/Segmented.jsx", error: String((e && e.message) || e) }); }

// components/navigation/Tabs.jsx
try { (() => {
function _extends() { return _extends = Object.assign ? Object.assign.bind() : function (n) { for (var e = 1; e < arguments.length; e++) { var t = arguments[e]; for (var r in t) ({}).hasOwnProperty.call(t, r) && (n[r] = t[r]); } return n; }, _extends.apply(null, arguments); }
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
function Tabs({
  items = [],
  value,
  defaultValue,
  onChange,
  className = '',
  ...rest
}) {
  inject();
  const [inner, setInner] = React.useState(defaultValue ?? (items[0] && items[0].key));
  const cur = value !== undefined ? value : inner;
  function pick(k) {
    if (value === undefined) setInner(k);
    onChange && onChange(k);
  }
  return /*#__PURE__*/React.createElement("div", _extends({
    className: `pds-tabs ${className}`
  }, rest), items.map(it => /*#__PURE__*/React.createElement("div", {
    key: it.key,
    className: `pds-tab${it.key === cur ? ' pds-tab--active' : ''}`,
    onClick: () => pick(it.key)
  }, it.label, it.badge != null && /*#__PURE__*/React.createElement("span", {
    className: "pds-tab__badge"
  }, it.badge))));
}
Object.assign(__ds_scope, { Tabs });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/navigation/Tabs.jsx", error: String((e && e.message) || e) }); }

// components/navigation/TopNav.jsx
try { (() => {
function _extends() { return _extends = Object.assign ? Object.assign.bind() : function (n) { for (var e = 1; e < arguments.length; e++) { var t = arguments[e]; for (var r in t) ({}).hasOwnProperty.call(t, r) && (n[r] = t[r]); } return n; }, _extends.apply(null, arguments); }
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
function TopNav({
  brand = '畔色孚格 ERP',
  logo = '畔',
  items = [],
  activeKey,
  onSelect,
  right,
  className = '',
  ...rest
}) {
  inject();
  return /*#__PURE__*/React.createElement("div", _extends({
    className: `pds-nav ${className}`
  }, rest), /*#__PURE__*/React.createElement("div", {
    className: "pds-nav__brand"
  }, /*#__PURE__*/React.createElement("span", {
    className: "pds-nav__logo"
  }, logo), brand), /*#__PURE__*/React.createElement("div", {
    className: "pds-nav__items"
  }, items.map(it => /*#__PURE__*/React.createElement("span", {
    key: it.key,
    className: `pds-nav__item${it.key === activeKey ? ' pds-nav__item--active' : ''}`,
    onClick: () => onSelect && onSelect(it.key)
  }, it.label))), right && /*#__PURE__*/React.createElement("div", {
    className: "pds-nav__right"
  }, right));
}
Object.assign(__ds_scope, { TopNav });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/navigation/TopNav.jsx", error: String((e && e.message) || e) }); }

// ui_kits/mobile/kit-m.jsx
try { (() => {
/* 畔色 ERP — 移动端 UI Kit 组件 + 手机外壳 (基于设计系统 token) */
(function () {
  if (document.getElementById('kit-m-style')) return;
  const css = `
.m-phone{width:390px;max-width:100%;height:780px;max-height:94vh;background:var(--surface-card);border-radius:42px;box-shadow:var(--shadow-xl),0 0 0 11px #0c1a1c,0 0 0 13px #1f3a3d;position:relative;overflow:hidden;display:flex;flex-direction:column;font-family:var(--font-sans)}
.m-notch{position:absolute;top:0;left:50%;transform:translateX(-50%);width:150px;height:30px;background:#0c1a1c;border-radius:0 0 18px 18px;z-index:30}
.m-status{height:46px;display:flex;align-items:flex-end;justify-content:space-between;padding:0 24px 6px;font-size:13px;font-weight:600;color:var(--text-primary);flex:none}
.m-status .r{display:flex;gap:6px;align-items:center}
.m-body{flex:1;overflow-y:auto;overflow-x:hidden;background:var(--bg-app);-webkit-overflow-scrolling:touch}
.m-pad{padding:16px}
.m-tabbar{flex:none;display:flex;background:var(--surface-card);border-top:1px solid var(--border);padding:8px 6px calc(8px + env(safe-area-inset-bottom,10px));position:relative;z-index:20}
.m-tab{flex:1;display:flex;flex-direction:column;align-items:center;gap:3px;padding:4px 0;cursor:pointer;color:var(--text-tertiary);font-size:11px;font-weight:500;min-height:44px;justify-content:center;transition:color var(--dur-fast) var(--ease-out)}
.m-tab .ic{font-size:20px;line-height:1}
.m-tab.on{color:var(--primary);font-weight:600}
.m-card{background:var(--surface-card);border:1px solid var(--border);border-radius:var(--radius-lg);box-shadow:var(--shadow-xs)}
.m-btn{display:inline-flex;align-items:center;justify-content:center;gap:7px;height:48px;padding:0 20px;border-radius:var(--radius-md);border:none;background:var(--primary);color:var(--on-primary);font-family:inherit;font-size:15px;font-weight:600;cursor:pointer;width:100%;transition:background var(--dur-fast) var(--ease-out)}
.m-btn:active{background:var(--primary-active)}
.m-btn--sec{background:var(--surface-card);color:var(--text-primary);border:1px solid var(--border-strong)}
.m-btn--ghost{background:var(--primary-soft);color:var(--primary)}
.m-tag{display:inline-flex;align-items:center;gap:4px;font-size:12px;font-weight:600;line-height:1;padding:3px 9px;border-radius:var(--radius-sm);border:1px solid transparent}
.m-tag .dot{width:6px;height:6px;border-radius:50%;background:currentColor}
.m-tag--success{background:var(--success-soft);color:var(--success);border-color:var(--success-border)}
.m-tag--warning{background:var(--warning-soft);color:#b45309;border-color:var(--warning-border)}
.m-tag--danger{background:var(--danger-soft);color:var(--danger);border-color:var(--danger-border)}
.m-tag--brand{background:var(--primary-soft);color:var(--primary);border-color:var(--primary-border)}
.m-tag--info{background:var(--info-soft);color:#0369a1;border-color:var(--info-border)}
.m-row{display:flex;align-items:center;gap:12px;padding:14px 16px;background:var(--surface-card);border-bottom:1px solid var(--border-subtle);min-height:44px}
.m-row:last-child{border-bottom:none}
.m-input{display:flex;align-items:center;gap:8px;background:var(--surface-card);border:1px solid var(--border-strong);border-radius:var(--radius-md);padding:0 14px;height:48px}
.m-input input{flex:1;border:none;outline:none;background:transparent;font-family:inherit;font-size:15px;color:var(--text-primary);min-width:0}
.m-input input::placeholder{color:var(--text-tertiary)}
.m-mono{font-family:var(--font-mono);font-feature-settings:"tnum" 1}
.m-hd{padding:16px 16px 4px}
.m-h1{font-size:22px;font-weight:800;letter-spacing:-.02em;color:var(--text-primary);margin:0}
`;
  const el = document.createElement('style');
  el.id = 'kit-m-style';
  el.textContent = css;
  document.head.appendChild(el);
})();
const MTag = ({
  tone = 'brand',
  dot,
  children
}) => React.createElement('span', {
  className: `m-tag m-tag--${tone}`
}, dot && React.createElement('span', {
  className: 'dot'
}), children);
function Phone({
  children,
  tab,
  onTab
}) {
  const tabs = [{
    key: 'home',
    ic: 'home',
    label: '工作台'
  }, {
    key: 'capture',
    ic: 'photo_camera',
    label: '录单'
  }, {
    key: 'stock',
    ic: 'inventory_2',
    label: '库存'
  }, {
    key: 'me',
    ic: 'person',
    label: '我的'
  }];
  return /*#__PURE__*/React.createElement("div", {
    className: "m-phone"
  }, /*#__PURE__*/React.createElement("div", {
    className: "m-notch"
  }), /*#__PURE__*/React.createElement("div", {
    className: "m-status"
  }, /*#__PURE__*/React.createElement("span", null, "9:41"), /*#__PURE__*/React.createElement("span", {
    className: "r"
  }, /*#__PURE__*/React.createElement("span", null, "5G"), /*#__PURE__*/React.createElement("span", null, "\uD83D\uDCF6"), /*#__PURE__*/React.createElement("span", null, "\uD83D\uDD0B"))), /*#__PURE__*/React.createElement("div", {
    className: "m-body"
  }, children), /*#__PURE__*/React.createElement("div", {
    className: "m-tabbar"
  }, tabs.map(t => /*#__PURE__*/React.createElement("div", {
    key: t.key,
    className: `m-tab${t.key === tab ? ' on' : ''}`,
    onClick: () => onTab(t.key)
  }, /*#__PURE__*/React.createElement("span", {
    className: "ic material-symbols-outlined"
  }, t.ic), /*#__PURE__*/React.createElement("span", null, t.label)))));
}
const MIcon = ({
  n,
  size,
  style
}) => React.createElement('span', {
  className: 'material-symbols-outlined',
  style: {
    fontSize: size || '1.1em',
    lineHeight: 1,
    verticalAlign: 'middle',
    ...(style || {})
  }
}, n);
Object.assign(window, {
  MTag,
  Phone,
  MIcon
});
})(); } catch (e) { __ds_ns.__errors.push({ path: "ui_kits/mobile/kit-m.jsx", error: String((e && e.message) || e) }); }

// ui_kits/mobile/screens-m.jsx
try { (() => {
// 移动端屏幕集合

// 工作台
function HomeM({
  go
}) {
  const todos = [['待发货订单', '12 单', 'warning'], ['待对账流水', '3 笔', 'warning'], ['缺料预警', '5 种', 'danger'], ['今日已签收', '28 单', 'success']];
  const actions = [['photo_camera', '拍照录单', 'capture'], ['search', '库存查询', 'stock'], ['add_circle', '新建订单', null], ['credit_card', '对账核销', null]];
  return /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("div", {
    style: {
      background: 'linear-gradient(160deg,var(--teal-700),var(--teal-900))',
      padding: '18px 18px 26px',
      color: '#fff'
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      fontSize: 13,
      opacity: .8
    }
  }, "\u4E0A\u5348\u597D\uFF0C\u7BA1\u7406\u5458 \uD83D\uDC4B"), /*#__PURE__*/React.createElement("div", {
    style: {
      fontSize: 20,
      fontWeight: 800,
      letterSpacing: '-.02em',
      marginTop: 2
    }
  }, "\u7554\u8272\u5B5A\u683C ERP"), /*#__PURE__*/React.createElement("div", {
    className: "m-card",
    style: {
      marginTop: 16,
      background: 'rgba(255,255,255,.12)',
      border: '1px solid rgba(255,255,255,.18)',
      backdropFilter: 'blur(6px)'
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      padding: 14,
      display: 'flex',
      justifyContent: 'space-between'
    }
  }, /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("div", {
    style: {
      fontSize: 12,
      opacity: .8
    }
  }, "\u4ECA\u65E5\u8BA2\u5355"), /*#__PURE__*/React.createElement("div", {
    className: "m-mono",
    style: {
      fontSize: 26,
      fontWeight: 800
    }
  }, "36")), /*#__PURE__*/React.createElement("div", {
    style: {
      textAlign: 'right'
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      fontSize: 12,
      opacity: .8
    }
  }, "\u4ECA\u65E5\u6536\u5165"), /*#__PURE__*/React.createElement("div", {
    className: "m-mono",
    style: {
      fontSize: 26,
      fontWeight: 800
    }
  }, "\xA5184k"))))), /*#__PURE__*/React.createElement("div", {
    className: "m-pad",
    style: {
      marginTop: -12
    }
  }, /*#__PURE__*/React.createElement("div", {
    className: "m-card",
    style: {
      padding: 14
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'grid',
      gridTemplateColumns: 'repeat(4,1fr)',
      gap: 6
    }
  }, actions.map(([ic, label, dst]) => /*#__PURE__*/React.createElement("div", {
    key: label,
    onClick: () => dst && go(dst),
    style: {
      display: 'flex',
      flexDirection: 'column',
      alignItems: 'center',
      gap: 7,
      padding: '8px 0',
      cursor: 'pointer'
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      width: 46,
      height: 46,
      borderRadius: 14,
      background: 'var(--primary-soft)',
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'center'
    }
  }, /*#__PURE__*/React.createElement("span", {
    className: "material-symbols-outlined",
    style: {
      fontSize: 24,
      color: 'var(--primary)'
    }
  }, ic)), /*#__PURE__*/React.createElement("span", {
    style: {
      fontSize: 12,
      color: 'var(--text-secondary)',
      fontWeight: 500
    }
  }, label))))), /*#__PURE__*/React.createElement("div", {
    style: {
      fontSize: 15,
      fontWeight: 700,
      color: 'var(--text-primary)',
      margin: '20px 2px 10px'
    }
  }, "\u5F85\u529E\u4E8B\u9879"), /*#__PURE__*/React.createElement("div", {
    className: "m-card",
    style: {
      overflow: 'hidden'
    }
  }, todos.map(([label, val, tone]) => /*#__PURE__*/React.createElement("div", {
    key: label,
    className: "m-row"
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      flex: 1,
      fontSize: 15,
      color: 'var(--text-primary)'
    }
  }, label), /*#__PURE__*/React.createElement(MTag, {
    tone: tone,
    dot: true
  }, val), /*#__PURE__*/React.createElement("span", {
    style: {
      color: 'var(--text-tertiary)'
    }
  }, "\u203A"))))));
}

// 拍照录单
function CaptureM() {
  const [stage, setStage] = React.useState('shoot'); // shoot → recognizing → result
  React.useEffect(() => {
    if (stage === 'recognizing') {
      const t = setTimeout(() => setStage('result'), 1400);
      return () => clearTimeout(t);
    }
  }, [stage]);
  return /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("div", {
    className: "m-hd"
  }, /*#__PURE__*/React.createElement("h1", {
    className: "m-h1"
  }, "\u62CD\u7167\u5F55\u5355"), /*#__PURE__*/React.createElement("div", {
    style: {
      color: 'var(--text-secondary)',
      fontSize: 13,
      marginTop: 4
    }
  }, "\u62CD\u9001\u8D27\u5355 / \u8D26\u5355\uFF0CAI \u81EA\u52A8\u8BC6\u522B\u5165\u5E93")), /*#__PURE__*/React.createElement("div", {
    className: "m-pad"
  }, stage === 'shoot' && /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("div", {
    style: {
      border: '2px dashed var(--border-strong)',
      borderRadius: 'var(--radius-lg)',
      height: 280,
      display: 'flex',
      flexDirection: 'column',
      alignItems: 'center',
      justifyContent: 'center',
      gap: 12,
      background: 'var(--surface-sunken)',
      color: 'var(--text-tertiary)'
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      fontSize: 48,
      color: 'var(--text-tertiary)'
    }
  }, /*#__PURE__*/React.createElement("span", {
    className: "material-symbols-outlined",
    style: {
      fontSize: 52
    }
  }, "document_scanner")), /*#__PURE__*/React.createElement("div", {
    style: {
      fontSize: 14
    }
  }, "\u5C06\u9001\u8D27\u5355\u653E\u5165\u53D6\u666F\u6846")), /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      gap: 10,
      marginTop: 16
    }
  }, /*#__PURE__*/React.createElement("button", {
    className: "m-btn m-btn--sec",
    style: {
      flex: 1
    }
  }, "\u4ECE\u76F8\u518C\u9009"), /*#__PURE__*/React.createElement("button", {
    className: "m-btn",
    style: {
      flex: 1
    },
    onClick: () => setStage('recognizing')
  }, "\uD83D\uDCF7 \u62CD\u7167\u8BC6\u522B"))), stage === 'recognizing' && /*#__PURE__*/React.createElement("div", {
    style: {
      textAlign: 'center',
      padding: '80px 0',
      color: 'var(--text-secondary)'
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      width: 44,
      height: 44,
      border: '3px solid var(--primary-soft)',
      borderTopColor: 'var(--primary)',
      borderRadius: '50%',
      margin: '0 auto 18px',
      animation: 'mspin .7s linear infinite'
    }
  }), /*#__PURE__*/React.createElement("div", {
    style: {
      fontWeight: 600,
      color: 'var(--text-primary)'
    }
  }, "AI \u8BC6\u522B\u4E2D\u2026"), /*#__PURE__*/React.createElement("div", {
    style: {
      fontSize: 13,
      marginTop: 4
    }
  }, "\u6B63\u5728\u89E3\u6790\u5355\u636E\u5B57\u6BB5")), stage === 'result' && /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      alignItems: 'center',
      gap: 8,
      marginBottom: 12
    }
  }, /*#__PURE__*/React.createElement(MTag, {
    tone: "success",
    dot: true
  }, "\u8BC6\u522B\u5B8C\u6210"), /*#__PURE__*/React.createElement("span", {
    style: {
      fontSize: 13,
      color: 'var(--text-tertiary)'
    }
  }, "\u7F6E\u4FE1\u5EA6 96%\uFF0C\u8BF7\u6838\u5BF9")), /*#__PURE__*/React.createElement("div", {
    className: "m-card",
    style: {
      overflow: 'hidden'
    }
  }, [['供应商', '佳宝家居厂'], ['送货单号', 'SH-20260622-07'], ['日期', '2026-06-22'], ['金额', '¥31,900'], ['件数', '48 件']].map(([k, v]) => /*#__PURE__*/React.createElement("div", {
    key: k,
    className: "m-row"
  }, /*#__PURE__*/React.createElement("span", {
    style: {
      width: 80,
      color: 'var(--text-tertiary)',
      fontSize: 13
    }
  }, k), /*#__PURE__*/React.createElement("span", {
    className: `${k === '金额' || k.includes('号') ? 'm-mono' : ''}`,
    style: {
      flex: 1,
      fontWeight: 500,
      color: 'var(--text-primary)'
    }
  }, v), /*#__PURE__*/React.createElement("span", {
    style: {
      color: 'var(--primary)',
      fontSize: 13
    }
  }, "\u7F16\u8F91")))), /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      gap: 10,
      marginTop: 16
    }
  }, /*#__PURE__*/React.createElement("button", {
    className: "m-btn m-btn--sec",
    style: {
      flex: 1
    },
    onClick: () => setStage('shoot')
  }, "\u91CD\u62CD"), /*#__PURE__*/React.createElement("button", {
    className: "m-btn",
    style: {
      flex: 1.4
    },
    onClick: () => setStage('shoot')
  }, "\u786E\u8BA4\u5165\u5E93")))));
}

// 库存查询
function StockM() {
  const items = [['橡木板材 18mm', 'WD-OAK-001', '1,280 张', 'success', '充足'], ['阻尼铰链', 'HW-HINGE-22', '96 只', 'warning', '缺料'], ['松木方料 40×40', 'WD-PINE-014', '−24 根', 'danger', '负库存'], ['亚麻布料 米白', 'FB-LINEN-07', '540 米', 'success', '充足'], ['三节导轨 450mm', 'HW-RAIL-33', '220 套', 'warning', '缺料'], ['加固纸箱 XL', 'PK-BOX-XL', '1,840 个', 'success', '充足']];
  return /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("div", {
    className: "m-hd"
  }, /*#__PURE__*/React.createElement("h1", {
    className: "m-h1"
  }, "\u5E93\u5B58\u67E5\u8BE2")), /*#__PURE__*/React.createElement("div", {
    className: "m-pad"
  }, /*#__PURE__*/React.createElement("div", {
    className: "m-input",
    style: {
      marginBottom: 14
    }
  }, /*#__PURE__*/React.createElement("span", null, "\uD83D\uDD0D"), /*#__PURE__*/React.createElement("input", {
    placeholder: "\u641C\u7D22\u7269\u6599\u540D\u79F0 / \u7F16\u53F7"
  })), /*#__PURE__*/React.createElement("div", {
    className: "m-card",
    style: {
      overflow: 'hidden'
    }
  }, items.map(([name, code, qty, tone, st]) => /*#__PURE__*/React.createElement("div", {
    key: code,
    className: "m-row"
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      flex: 1,
      minWidth: 0
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      fontSize: 15,
      fontWeight: 500,
      color: 'var(--text-primary)'
    }
  }, name), /*#__PURE__*/React.createElement("div", {
    className: "m-mono",
    style: {
      fontSize: 12,
      color: 'var(--text-tertiary)',
      marginTop: 2
    }
  }, code)), /*#__PURE__*/React.createElement("div", {
    style: {
      textAlign: 'right'
    }
  }, /*#__PURE__*/React.createElement("div", {
    className: "m-mono",
    style: {
      fontSize: 15,
      fontWeight: 600,
      color: tone === 'danger' ? 'var(--danger)' : 'var(--text-primary)'
    }
  }, qty), /*#__PURE__*/React.createElement("div", {
    style: {
      marginTop: 4
    }
  }, /*#__PURE__*/React.createElement(MTag, {
    tone: tone,
    dot: true
  }, st))))))));
}

// 我的
function MeM() {
  const groups = [['账户', [['manage_accounts', '账户设置'], ['lock', '修改密码'], ['corporate_fare', '组织与角色']]], ['系统', [['smart_toy', 'AI / OCR 配置'], ['notifications', '消息通知'], ['monitoring', '系统监控']]]];
  return /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("div", {
    style: {
      background: 'linear-gradient(160deg,var(--teal-700),var(--teal-900))',
      padding: '24px 18px 30px',
      color: '#fff',
      display: 'flex',
      alignItems: 'center',
      gap: 14
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      width: 56,
      height: 56,
      borderRadius: '50%',
      background: 'rgba(255,255,255,.18)',
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'center',
      fontSize: 22,
      fontWeight: 700
    }
  }, "\u7BA1"), /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("div", {
    style: {
      fontSize: 18,
      fontWeight: 700
    }
  }, "\u7BA1\u7406\u5458"), /*#__PURE__*/React.createElement("div", {
    style: {
      fontSize: 13,
      opacity: .8
    }
  }, "admin \xB7 \u7CFB\u7EDF\u7BA1\u7406\u5458"))), /*#__PURE__*/React.createElement("div", {
    className: "m-pad",
    style: {
      marginTop: -10
    }
  }, groups.map(([title, rows]) => /*#__PURE__*/React.createElement("div", {
    key: title,
    style: {
      marginBottom: 16
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      fontSize: 12,
      color: 'var(--text-tertiary)',
      margin: '4px 4px 8px',
      fontWeight: 600
    }
  }, title), /*#__PURE__*/React.createElement("div", {
    className: "m-card",
    style: {
      overflow: 'hidden'
    }
  }, rows.map(([ic, label]) => /*#__PURE__*/React.createElement("div", {
    key: label,
    className: "m-row"
  }, /*#__PURE__*/React.createElement("span", {
    className: "material-symbols-outlined",
    style: {
      fontSize: 21,
      color: 'var(--text-secondary)'
    }
  }, ic), /*#__PURE__*/React.createElement("span", {
    style: {
      flex: 1,
      fontSize: 15,
      color: 'var(--text-primary)'
    }
  }, label), /*#__PURE__*/React.createElement("span", {
    style: {
      color: 'var(--text-tertiary)'
    }
  }, "\u203A")))))), /*#__PURE__*/React.createElement("button", {
    className: "m-btn m-btn--sec",
    style: {
      color: 'var(--danger)',
      borderColor: 'var(--danger-border)'
    }
  }, "\u9000\u51FA\u767B\u5F55")));
}
Object.assign(window, {
  HomeM,
  CaptureM,
  StockM,
  MeM
});
})(); } catch (e) { __ds_ns.__errors.push({ path: "ui_kits/mobile/screens-m.jsx", error: String((e && e.message) || e) }); }

// ui_kits/web/DashboardScreen.jsx
try { (() => {
// 运营大盘 — KPI + 资金条 + 状态环图 + 趋势图 + 库存/对账健康
function DashboardScreen({
  go
}) {
  const pieRef = React.useRef(null),
    trendRef = React.useRef(null);
  const [period, setPeriod] = React.useState('30d');
  React.useEffect(() => {
    if (!window.echarts) return;
    const C = getComputedStyle(document.documentElement);
    const v = n => C.getPropertyValue(n).trim();
    const sub = v('--text-tertiary'),
      grid = '#eef2f7';
    // 防止重复 init 叠加空白 canvas
    [pieRef.current, trendRef.current].forEach(el => {
      const ex = window.echarts.getInstanceByDom(el);
      if (ex) ex.dispose();
    });
    const pie = window.echarts.init(pieRef.current, null, {
      renderer: 'svg'
    });
    pie.setOption({
      tooltip: {
        trigger: 'item',
        formatter: '{b}: {c} ({d}%)'
      },
      legend: {
        orient: 'vertical',
        right: 6,
        top: 'center',
        textStyle: {
          color: v('--text-secondary'),
          fontSize: 12
        },
        itemWidth: 10,
        itemHeight: 10
      },
      series: [{
        type: 'pie',
        radius: ['56%', '78%'],
        center: ['34%', '50%'],
        itemStyle: {
          borderColor: '#fff',
          borderWidth: 3,
          borderRadius: 6
        },
        label: {
          show: true,
          formatter: '{d}%',
          color: sub,
          fontSize: 11
        },
        data: [{
          name: '已发货',
          value: 38,
          itemStyle: {
            color: v('--teal-500')
          }
        }, {
          name: '待付款',
          value: 14,
          itemStyle: {
            color: v('--amber-500')
          }
        }, {
          name: '已签收',
          value: 28,
          itemStyle: {
            color: v('--indigo-500')
          }
        }, {
          name: '已付款',
          value: 16,
          itemStyle: {
            color: v('--sky-500')
          }
        }, {
          name: '售后',
          value: 4,
          itemStyle: {
            color: v('--red-500')
          }
        }]
      }]
    });
    const days = Array.from({
      length: 14
    }, (_, i) => '06-' + String(i + 9).padStart(2, '0'));
    const trend = window.echarts.init(trendRef.current, null, {
      renderer: 'svg'
    });
    trend.setOption({
      tooltip: {
        trigger: 'axis'
      },
      legend: {
        data: ['订单数', '收入(¥)'],
        textStyle: {
          color: v('--text-secondary')
        },
        top: 0,
        itemWidth: 12,
        itemHeight: 8
      },
      grid: {
        top: 30,
        left: 6,
        right: 6,
        bottom: 2,
        containLabel: true
      },
      xAxis: {
        type: 'category',
        data: days,
        axisLine: {
          lineStyle: {
            color: '#e2e8f0'
          }
        },
        axisTick: {
          show: false
        },
        axisLabel: {
          color: sub,
          fontSize: 10
        }
      },
      yAxis: [{
        type: 'value',
        splitLine: {
          lineStyle: {
            color: grid
          }
        },
        axisLabel: {
          color: sub
        }
      }, {
        type: 'value',
        splitLine: {
          show: false
        },
        axisLabel: {
          color: sub,
          formatter: x => '¥' + (x / 1000).toFixed(0) + 'k'
        }
      }],
      series: [{
        name: '订单数',
        type: 'bar',
        data: [22, 30, 18, 41, 36, 28, 33, 45, 39, 31, 48, 42, 37, 52],
        itemStyle: {
          color: v('--teal-300'),
          borderRadius: [4, 4, 0, 0]
        },
        barWidth: '46%'
      }, {
        name: '收入(¥)',
        type: 'line',
        yAxisIndex: 1,
        smooth: true,
        symbol: 'none',
        data: [42, 58, 33, 76, 64, 51, 60, 88, 71, 55, 92, 80, 68, 104].map(x => x * 1000),
        lineStyle: {
          color: v('--teal-600'),
          width: 2.5
        },
        itemStyle: {
          color: v('--teal-600')
        },
        areaStyle: {
          color: {
            type: 'linear',
            x: 0,
            y: 0,
            x2: 0,
            y2: 1,
            colorStops: [{
              offset: 0,
              color: 'rgba(13,148,136,.20)'
            }, {
              offset: 1,
              color: 'rgba(13,148,136,0)'
            }]
          }
        }
      }]
    });
    const ro = () => {
      pie.resize();
      trend.resize();
    };
    window.addEventListener('resize', ro);
    requestAnimationFrame(ro);
    return () => {
      window.removeEventListener('resize', ro);
      pie.dispose();
      trend.dispose();
    };
  }, []);
  const reconRules = [['平台对账', 'ok'], ['工厂对账', 'ok'], ['支付宝核销', 'warning'], ['物流账单', 'ok'], ['退补单', 'danger'], ['保证金', 'ok']];
  const dotColor = {
    ok: 'var(--success)',
    warning: 'var(--warning)',
    danger: 'var(--danger)'
  };
  const dotIcon = {
    ok: '✓',
    warning: '!',
    danger: '✕'
  };
  return /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      alignItems: 'flex-end',
      justifyContent: 'space-between',
      flexWrap: 'wrap',
      gap: 8,
      marginBottom: 18
    }
  }, /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("h1", {
    style: {
      fontSize: 24,
      fontWeight: 800,
      letterSpacing: '-.02em',
      margin: 0,
      color: 'var(--text-primary)'
    }
  }, "\u8FD0\u8425\u5927\u76D8"), /*#__PURE__*/React.createElement("div", {
    style: {
      color: 'var(--text-secondary)',
      fontSize: 13,
      marginTop: 4
    }
  }, "\u5B9E\u65F6\u7ECF\u8425\u6982\u89C8 \xB7 \u6BCF\u5206\u949F\u81EA\u52A8\u5237\u65B0")), /*#__PURE__*/React.createElement(KSeg, {
    value: period,
    onChange: setPeriod,
    options: [{
      label: '今日',
      value: 'today'
    }, {
      label: '昨日',
      value: 'yesterday'
    }, {
      label: '近7天',
      value: '7d'
    }, {
      label: '近30天',
      value: '30d'
    }]
  })), /*#__PURE__*/React.createElement("div", {
    className: "k-card k-card--hover",
    onClick: () => go('finance'),
    style: {
      marginBottom: 16,
      background: 'linear-gradient(135deg,#fff 0%,var(--teal-50) 100%)',
      borderColor: 'var(--teal-200)'
    }
  }, /*#__PURE__*/React.createElement("div", {
    className: "k-card__body",
    style: {
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'space-between',
      flexWrap: 'wrap',
      gap: 12
    }
  }, /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      alignItems: 'center',
      gap: 8,
      color: 'var(--text-secondary)',
      fontSize: 13,
      fontWeight: 600
    }
  }, /*#__PURE__*/React.createElement(MIcon, {
    n: "account_balance_wallet",
    style: {
      color: 'var(--primary)'
    }
  }), " \u5269\u4F59\u6D41\u6C34 \xB7 \u53EF\u7528\u8D44\u91D1\uFF08\u5B9E\u65F6\uFF09"), /*#__PURE__*/React.createElement("div", {
    style: {
      color: 'var(--success)',
      fontWeight: 800,
      fontSize: 30,
      letterSpacing: '-.01em',
      marginTop: 4,
      fontFamily: 'var(--font-mono)'
    }
  }, "\xA5 2,486,300"), /*#__PURE__*/React.createElement("div", {
    style: {
      marginTop: 4,
      fontSize: 12,
      color: 'var(--text-tertiary)'
    }
  }, /*#__PURE__*/React.createElement("span", {
    style: {
      color: 'var(--success)'
    }
  }, "\u2191 \u52A0\u9879 \xA53,920,000"), " \xB7 ", /*#__PURE__*/React.createElement("span", {
    style: {
      color: 'var(--danger)'
    }
  }, "\u2193 \u51CF\u9879 \xA51,433,700"))), /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      gap: 6,
      flexWrap: 'wrap',
      justifyContent: 'flex-end'
    }
  }, /*#__PURE__*/React.createElement(KTag, {
    tone: "success",
    dot: true
  }, "\u652F\u4ED8\u5B9D \xB7 \u4ECA\u5929"), /*#__PURE__*/React.createElement(KTag, {
    tone: "success",
    dot: true
  }, "\u94F6\u884C \xB7 1\u5929\u524D"), /*#__PURE__*/React.createElement(KTag, {
    tone: "warning",
    dot: true
  }, "\u73B0\u91D1 \xB7 5\u5929\u524D")))), /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'grid',
      gridTemplateColumns: 'repeat(auto-fit,minmax(220px,1fr))',
      gap: 16,
      marginBottom: 16
    }
  }, /*#__PURE__*/React.createElement(KCard, {
    hover: true,
    onClick: () => go('orders')
  }, /*#__PURE__*/React.createElement(KStat, {
    title: "\u8FD1 7 \u5929\u8BA2\u5355",
    value: "248",
    icon: /*#__PURE__*/React.createElement(MIcon, {
      n: "shopping_cart"
    }),
    delta: "8.2%",
    dir: "up",
    footer: "\u5355"
  })), /*#__PURE__*/React.createElement(KCard, {
    hover: true,
    onClick: () => go('orders')
  }, /*#__PURE__*/React.createElement(KStat, {
    title: "\u8FD1 30 \u5929\u6536\u5165",
    prefix: "\xA5",
    value: "1,284,560",
    icon: /*#__PURE__*/React.createElement(MIcon, {
      n: "payments"
    }),
    delta: "12.4%",
    dir: "up",
    footer: "\u8F83\u4E0A\u6708"
  })), /*#__PURE__*/React.createElement(KCard, {
    hover: true,
    onClick: () => go('orders')
  }, /*#__PURE__*/React.createElement(KStat, {
    title: "\u6BDB\u5229\u7387",
    value: "18.4%",
    icon: /*#__PURE__*/React.createElement(MIcon, {
      n: "trending_up"
    }),
    delta: "1.6%",
    dir: "up"
  })), /*#__PURE__*/React.createElement(KCard, {
    hover: true
  }, /*#__PURE__*/React.createElement(KStat, {
    title: "\u5F85\u5904\u7406\u5F02\u5E38",
    value: "7",
    icon: /*#__PURE__*/React.createElement(MIcon, {
      n: "warning"
    }),
    valueColor: "var(--warning)",
    footer: "\u9700\u590D\u6838"
  }))), /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'grid',
      gridTemplateColumns: 'repeat(auto-fit,minmax(320px,1fr))',
      gap: 16,
      marginBottom: 16
    }
  }, /*#__PURE__*/React.createElement(KCard, {
    title: "\u8BA2\u5355\u72B6\u6001\u5206\u5E03",
    extra: "\u8FD1 30 \u5929"
  }, /*#__PURE__*/React.createElement("div", {
    ref: pieRef,
    style: {
      height: 240
    }
  })), /*#__PURE__*/React.createElement(KCard, {
    title: "\u8FD1 14 \u5929\u8BA2\u5355\u8D8B\u52BF",
    extra: "\u8BA2\u5355\u6570 / \u6536\u5165"
  }, /*#__PURE__*/React.createElement("div", {
    ref: trendRef,
    style: {
      height: 240
    }
  }))), /*#__PURE__*/React.createElement("div", {
    style: {
      fontSize: 15,
      fontWeight: 700,
      color: 'var(--text-primary)',
      margin: '18px 2px 10px'
    }
  }, "\u5BF9\u8D26\u5065\u5EB7"), /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'grid',
      gridTemplateColumns: 'repeat(auto-fit,minmax(140px,1fr))',
      gap: 12
    }
  }, reconRules.map(([label, st]) => /*#__PURE__*/React.createElement("div", {
    key: label,
    className: "k-card k-card--hover",
    onClick: () => go('recon'),
    style: {
      textAlign: 'center',
      borderColor: st === 'ok' ? 'var(--success-border)' : st === 'warning' ? 'var(--warning-border)' : 'var(--danger-border)'
    }
  }, /*#__PURE__*/React.createElement("div", {
    className: "k-card__body",
    style: {
      padding: '12px 8px'
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      width: 26,
      height: 26,
      borderRadius: '50%',
      margin: '0 auto 6px',
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'center',
      color: '#fff',
      fontWeight: 700,
      fontSize: 13,
      background: dotColor[st]
    }
  }, dotIcon[st]), /*#__PURE__*/React.createElement("div", {
    style: {
      fontSize: 13,
      fontWeight: 600,
      color: 'var(--text-primary)'
    }
  }, label))))));
}
window.DashboardScreen = DashboardScreen;
})(); } catch (e) { __ds_ns.__errors.push({ path: "ui_kits/web/DashboardScreen.jsx", error: String((e && e.message) || e) }); }

// ui_kits/web/InventoryScreen.jsx
try { (() => {
// 库存页 — 概览卡 + 配件库存表 (安全库存进度 / 缺料告警)
function InventoryScreen() {
  const parts = [{
    code: 'WD-OAK-001',
    name: '橡木板材 18mm',
    cur: 1280,
    safe: 800,
    unit: '张',
    st: 'ok'
  }, {
    code: 'HW-HINGE-22',
    name: '阻尼铰链',
    cur: 96,
    safe: 400,
    unit: '只',
    st: 'low'
  }, {
    code: 'WD-PINE-014',
    name: '松木方料 40×40',
    cur: -24,
    safe: 200,
    unit: '根',
    st: 'neg'
  }, {
    code: 'FB-LINEN-07',
    name: '亚麻布料 米白',
    cur: 540,
    safe: 300,
    unit: '米',
    st: 'ok'
  }, {
    code: 'HW-RAIL-33',
    name: '三节导轨 450mm',
    cur: 220,
    safe: 250,
    unit: '套',
    st: 'low'
  }, {
    code: 'PK-BOX-XL',
    name: '加固纸箱 XL',
    cur: 1840,
    safe: 1000,
    unit: '个',
    st: 'ok'
  }, {
    code: 'WD-MDF-009',
    name: '中纤板 15mm',
    cur: 64,
    safe: 500,
    unit: '张',
    st: 'low'
  }];
  const ST = {
    ok: ['充足', 'success'],
    low: ['缺料', 'warning'],
    neg: ['负库存', 'danger']
  };
  const [density, setDensity] = React.useState('default');
  const rowH = {
    compact: 40,
    default: 48,
    spacious: 56
  }[density];
  return /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      alignItems: 'flex-end',
      justifyContent: 'space-between',
      flexWrap: 'wrap',
      gap: 12,
      marginBottom: 16
    }
  }, /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("div", {
    style: {
      fontSize: 12,
      color: 'var(--text-tertiary)',
      marginBottom: 6
    }
  }, "\u5E93\u5B58 / \u914D\u4EF6\u5E93\u5B58"), /*#__PURE__*/React.createElement("h1", {
    style: {
      fontSize: 24,
      fontWeight: 800,
      letterSpacing: '-.02em',
      margin: 0,
      color: 'var(--text-primary)'
    }
  }, "\u914D\u4EF6\u5E93\u5B58")), /*#__PURE__*/React.createElement(KSeg, {
    value: density,
    onChange: setDensity,
    options: [{
      label: '紧凑',
      value: 'compact'
    }, {
      label: '默认',
      value: 'default'
    }, {
      label: '宽松',
      value: 'spacious'
    }]
  })), /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'grid',
      gridTemplateColumns: 'repeat(auto-fit,minmax(200px,1fr))',
      gap: 16,
      marginBottom: 18
    }
  }, /*#__PURE__*/React.createElement(KCard, null, /*#__PURE__*/React.createElement(KStat, {
    title: "\u914D\u4EF6\u54C1\u79CD",
    value: "328",
    icon: /*#__PURE__*/React.createElement(MIcon, {
      n: "inventory_2"
    }),
    footer: "\u79CD"
  })), /*#__PURE__*/React.createElement(KCard, null, /*#__PURE__*/React.createElement(KStat, {
    title: "\u7F3A\u6599\u9884\u8B66",
    value: "14",
    icon: /*#__PURE__*/React.createElement(MIcon, {
      n: "warning"
    }),
    valueColor: "var(--warning)",
    footer: "\u79CD\u5F85\u8865\u8D27"
  })), /*#__PURE__*/React.createElement(KCard, null, /*#__PURE__*/React.createElement(KStat, {
    title: "\u8D1F\u5E93\u5B58",
    value: "2",
    icon: /*#__PURE__*/React.createElement(MIcon, {
      n: "trending_down"
    }),
    valueColor: "var(--danger)",
    footer: "\u79CD\u8D85\u5356"
  })), /*#__PURE__*/React.createElement(KCard, null, /*#__PURE__*/React.createElement(KStat, {
    title: "\u5E93\u5B58\u603B\u503C",
    prefix: "\xA5",
    value: "864,200",
    icon: /*#__PURE__*/React.createElement(MIcon, {
      n: "savings"
    })
  }))), /*#__PURE__*/React.createElement("div", {
    className: "k-tbl-wrap"
  }, /*#__PURE__*/React.createElement("div", {
    className: "k-tbl-scroll"
  }, /*#__PURE__*/React.createElement("table", {
    className: "k-tbl"
  }, /*#__PURE__*/React.createElement("thead", null, /*#__PURE__*/React.createElement("tr", null, /*#__PURE__*/React.createElement("th", null, "\u7F16\u53F7"), /*#__PURE__*/React.createElement("th", null, "\u7269\u6599\u540D\u79F0"), /*#__PURE__*/React.createElement("th", {
    className: "ctr"
  }, "\u72B6\u6001"), /*#__PURE__*/React.createElement("th", {
    className: "num"
  }, "\u5F53\u524D\u5E93\u5B58"), /*#__PURE__*/React.createElement("th", {
    className: "num"
  }, "\u5B89\u5168\u5E93\u5B58"), /*#__PURE__*/React.createElement("th", {
    style: {
      width: 200
    }
  }, "\u6C34\u4F4D"))), /*#__PURE__*/React.createElement("tbody", null, parts.map(p => {
    const [t, tone] = ST[p.st];
    const pct = Math.max(0, Math.min(100, p.cur / (p.safe * 1.6) * 100));
    const barColor = p.st === 'neg' ? 'var(--danger)' : p.st === 'low' ? 'var(--warning)' : 'var(--success)';
    return /*#__PURE__*/React.createElement("tr", {
      key: p.code,
      style: {
        cursor: 'default'
      }
    }, /*#__PURE__*/React.createElement("td", {
      className: "k-mono",
      style: {
        height: rowH,
        color: 'var(--text-secondary)'
      }
    }, p.code), /*#__PURE__*/React.createElement("td", {
      style: {
        fontWeight: 500
      }
    }, p.name), /*#__PURE__*/React.createElement("td", {
      className: "ctr"
    }, /*#__PURE__*/React.createElement(KTag, {
      tone: tone,
      dot: true
    }, t)), /*#__PURE__*/React.createElement("td", {
      className: "num k-mono",
      style: p.cur < 0 ? {
        color: 'var(--danger)',
        fontWeight: 600
      } : null
    }, p.cur.toLocaleString(), " ", p.unit), /*#__PURE__*/React.createElement("td", {
      className: "num k-mono",
      style: {
        color: 'var(--text-tertiary)'
      }
    }, p.safe.toLocaleString()), /*#__PURE__*/React.createElement("td", null, /*#__PURE__*/React.createElement("div", {
      style: {
        height: 8,
        borderRadius: 999,
        background: 'var(--surface-sunken)',
        overflow: 'hidden'
      }
    }, /*#__PURE__*/React.createElement("div", {
      style: {
        width: pct + '%',
        height: '100%',
        background: barColor,
        borderRadius: 999
      }
    }))));
  }))))));
}
window.InventoryScreen = InventoryScreen;
})(); } catch (e) { __ds_ns.__errors.push({ path: "ui_kits/web/InventoryScreen.jsx", error: String((e && e.message) || e) }); }

// ui_kits/web/LoginScreen.jsx
try { (() => {
// 登录页 — 居中卡片，呼应「畔色」水岸主色渐变
function LoginScreen({
  onLogin
}) {
  const [u, setU] = React.useState('admin');
  const [p, setP] = React.useState('admin');
  return /*#__PURE__*/React.createElement("div", {
    style: {
      minHeight: '100%',
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'center',
      background: 'radial-gradient(1200px 600px at 70% -10%, var(--teal-50), transparent), var(--bg-app)',
      padding: 24
    }
  }, /*#__PURE__*/React.createElement("div", {
    className: "k-card",
    style: {
      width: 380,
      boxShadow: 'var(--shadow-lg)',
      borderRadius: 'var(--radius-2xl)'
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      padding: '34px 32px 28px'
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      alignItems: 'center',
      gap: 11,
      justifyContent: 'center',
      marginBottom: 6
    }
  }, /*#__PURE__*/React.createElement("span", {
    className: "k-nav__logo",
    style: {
      width: 38,
      height: 38,
      borderRadius: 11,
      fontSize: 19
    }
  }, "\u7554"), /*#__PURE__*/React.createElement("div", {
    style: {
      fontSize: 22,
      fontWeight: 800,
      letterSpacing: '-.02em',
      color: 'var(--text-primary)'
    }
  }, "\u7554\u8272\u5B5A\u683C ERP")), /*#__PURE__*/React.createElement("div", {
    style: {
      textAlign: 'center',
      color: 'var(--text-tertiary)',
      fontSize: 13,
      marginBottom: 26
    }
  }, "\u5BB6\u5177\u7535\u5546\u5185\u90E8\u7BA1\u7406\u7CFB\u7EDF"), /*#__PURE__*/React.createElement("div", {
    className: "k-field",
    style: {
      marginBottom: 14
    }
  }, /*#__PURE__*/React.createElement("span", {
    className: "k-field__label"
  }, "\u7528\u6237\u540D"), /*#__PURE__*/React.createElement("div", {
    className: "k-input"
  }, /*#__PURE__*/React.createElement("span", {
    className: "affix"
  }, "\uD83D\uDC64"), /*#__PURE__*/React.createElement("input", {
    value: u,
    onChange: e => setU(e.target.value),
    placeholder: "\u7528\u6237\u540D"
  }))), /*#__PURE__*/React.createElement("div", {
    className: "k-field",
    style: {
      marginBottom: 22
    }
  }, /*#__PURE__*/React.createElement("span", {
    className: "k-field__label"
  }, "\u5BC6\u7801"), /*#__PURE__*/React.createElement("div", {
    className: "k-input"
  }, /*#__PURE__*/React.createElement("span", {
    className: "affix"
  }, "\uD83D\uDD12"), /*#__PURE__*/React.createElement("input", {
    type: "password",
    value: p,
    onChange: e => setP(e.target.value),
    placeholder: "\u5BC6\u7801"
  }))), /*#__PURE__*/React.createElement(KBtn, {
    variant: "primary",
    size: "lg",
    block: true,
    onClick: onLogin
  }, "\u767B\u5F55"), /*#__PURE__*/React.createElement("div", {
    style: {
      marginTop: 16,
      fontSize: 12,
      color: 'var(--text-tertiary)',
      textAlign: 'center',
      lineHeight: 1.6
    }
  }, "\u9ED8\u8BA4\u7BA1\u7406\u5458 ", /*#__PURE__*/React.createElement("code", {
    style: {
      fontFamily: 'var(--font-mono)',
      color: 'var(--text-secondary)'
    }
  }, "admin / admin"), /*#__PURE__*/React.createElement("br", null), "\u767B\u5F55\u540E\u8BF7\u7ACB\u5373\u4FEE\u6539\u5BC6\u7801"))));
}
window.LoginScreen = LoginScreen;
})(); } catch (e) { __ds_ns.__errors.push({ path: "ui_kits/web/LoginScreen.jsx", error: String((e && e.message) || e) }); }

// ui_kits/web/OrdersScreen.jsx
try { (() => {
// 订单页 — 标题区 + 标签筛选 + 工具栏 + 数据表格 (排序/多选/行抽屉)
function OrdersScreen() {
  const STATUS = {
    paid: ['已付款', 'info'],
    shipped: ['已发货', 'brand'],
    signed: ['已签收', 'success'],
    pending: ['待付款', 'warning'],
    aftersales: ['售后', 'danger']
  };
  const ALL = [{
    id: 'PS-20260622-018',
    customer: '佳宝家居旗舰店',
    sku: '北欧实木餐桌 1.4m',
    status: 'shipped',
    qty: 12,
    amount: 12480,
    date: '06-22'
  }, {
    id: 'PS-20260622-017',
    customer: '宜美优品专营店',
    sku: '原木电视柜 2.0m',
    status: 'pending',
    qty: 3,
    amount: 3200,
    date: '06-22'
  }, {
    id: 'PS-20260621-094',
    customer: '木言木语家具',
    sku: '橡木书架组合',
    status: 'signed',
    qty: 48,
    amount: 128900,
    date: '06-21'
  }, {
    id: 'PS-20260621-088',
    customer: '北欧时光',
    sku: '布艺三人沙发',
    status: 'paid',
    qty: 6,
    amount: 7680,
    date: '06-21'
  }, {
    id: 'PS-20260621-072',
    customer: '原木良品',
    sku: '岩板茶几',
    status: 'aftersales',
    qty: 2,
    amount: -1240,
    date: '06-21'
  }, {
    id: 'PS-20260620-145',
    customer: '栖居生活馆',
    sku: '实木床架 1.8m',
    status: 'shipped',
    qty: 21,
    amount: 43200,
    date: '06-20'
  }, {
    id: 'PS-20260620-131',
    customer: '简屋家居',
    sku: '餐边柜 1.2m',
    status: 'signed',
    qty: 9,
    amount: 16740,
    date: '06-20'
  }, {
    id: 'PS-20260620-110',
    customer: '青木工坊',
    sku: '儿童学习桌椅套装',
    status: 'paid',
    qty: 15,
    amount: 28500,
    date: '06-20'
  }];
  const [tab, setTab] = React.useState('all');
  const [sort, setSort] = React.useState(null);
  const [sel, setSel] = React.useState(() => new Set());
  const [active, setActive] = React.useState(null);
  let rows = tab === 'all' ? ALL : ALL.filter(r => tab === 'pending' ? r.status === 'pending' : tab === 'shipped' ? r.status === 'shipped' : r.status === 'aftersales');
  if (sort) {
    rows = [...rows].sort((a, b) => {
      const r = a[sort.k] > b[sort.k] ? 1 : a[sort.k] < b[sort.k] ? -1 : 0;
      return sort.d === 'asc' ? r : -r;
    });
  }
  const allSel = rows.length > 0 && rows.every(r => sel.has(r.id));
  const head = (k, label, cls) => {
    const on = sort && sort.k === k;
    return /*#__PURE__*/React.createElement("th", {
      className: cls,
      onClick: () => setSort(s => !s || s.k !== k ? {
        k,
        d: 'asc'
      } : s.d === 'asc' ? {
        k,
        d: 'desc'
      } : null),
      style: {
        cursor: 'pointer'
      }
    }, label, " ", /*#__PURE__*/React.createElement("span", {
      style: {
        color: on ? 'var(--primary)' : 'var(--text-tertiary)',
        fontSize: 10
      }
    }, on ? sort.d === 'asc' ? '▲' : '▼' : '↕'));
  };
  return /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      alignItems: 'flex-end',
      justifyContent: 'space-between',
      flexWrap: 'wrap',
      gap: 12,
      marginBottom: 16
    }
  }, /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("div", {
    style: {
      fontSize: 12,
      color: 'var(--text-tertiary)',
      marginBottom: 6
    }
  }, "\u8BA2\u5355 / \u5168\u90E8\u8BA2\u5355"), /*#__PURE__*/React.createElement("h1", {
    style: {
      fontSize: 24,
      fontWeight: 800,
      letterSpacing: '-.02em',
      margin: 0,
      color: 'var(--text-primary)'
    }
  }, "\u8BA2\u5355"), /*#__PURE__*/React.createElement("div", {
    style: {
      color: 'var(--text-secondary)',
      fontSize: 13,
      marginTop: 4
    }
  }, "\u5171 1,284 \u5355 \xB7 \u4ECA\u65E5\u65B0\u589E 36")), /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      gap: 8
    }
  }, /*#__PURE__*/React.createElement(KBtn, {
    variant: "secondary",
    size: "md",
    icon: /*#__PURE__*/React.createElement(MIcon, {
      n: "download"
    })
  }, "\u5BFC\u51FA"), /*#__PURE__*/React.createElement(KBtn, {
    variant: "primary",
    size: "md",
    icon: /*#__PURE__*/React.createElement(MIcon, {
      n: "add"
    })
  }, "\u65B0\u5EFA\u8BA2\u5355"))), /*#__PURE__*/React.createElement("div", {
    style: {
      marginBottom: 14
    }
  }, /*#__PURE__*/React.createElement(KTabs, {
    value: tab,
    onChange: k => {
      setTab(k);
      setSel(new Set());
    },
    items: [{
      key: 'all',
      label: '全部',
      badge: 1284
    }, {
      key: 'pending',
      label: '待处理',
      badge: 36
    }, {
      key: 'shipped',
      label: '已发货',
      badge: 412
    }, {
      key: 'aftersales',
      label: '售后',
      badge: 7
    }]
  })), /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      alignItems: 'center',
      gap: 10,
      marginBottom: 12,
      flexWrap: 'wrap'
    }
  }, /*#__PURE__*/React.createElement("div", {
    className: "k-input",
    style: {
      width: 260
    }
  }, /*#__PURE__*/React.createElement("span", {
    className: "affix"
  }, /*#__PURE__*/React.createElement(MIcon, {
    n: "search"
  })), /*#__PURE__*/React.createElement("input", {
    placeholder: "\u641C\u7D22\u8BA2\u5355\u53F7 / \u5BA2\u6237 / \u4EA7\u54C1"
  })), /*#__PURE__*/React.createElement(KSeg, {
    value: "all",
    onChange: () => {},
    options: [{
      label: '全部店铺',
      value: 'all'
    }, {
      label: '天猫',
      value: 'tm'
    }, {
      label: '淘宝',
      value: 'tb'
    }]
  }), /*#__PURE__*/React.createElement("div", {
    style: {
      flex: 1
    }
  }), sel.size > 0 ? /*#__PURE__*/React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement("span", {
    style: {
      fontSize: 13,
      color: 'var(--text-secondary)'
    }
  }, "\u5DF2\u9009 ", sel.size, " \u9879"), /*#__PURE__*/React.createElement(KBtn, {
    variant: "ghost",
    size: "sm"
  }, "\u6279\u91CF\u53D1\u8D27"), /*#__PURE__*/React.createElement(KBtn, {
    variant: "text",
    size: "sm"
  }, "\u5BFC\u51FA\u6240\u9009")) : /*#__PURE__*/React.createElement("span", {
    style: {
      fontSize: 12,
      color: 'var(--text-tertiary)'
    }
  }, "\u70B9\u51FB\u8868\u5934\u6392\u5E8F \xB7 \u52FE\u9009\u6279\u91CF\u64CD\u4F5C")), /*#__PURE__*/React.createElement("div", {
    className: "k-tbl-wrap"
  }, /*#__PURE__*/React.createElement("div", {
    className: "k-tbl-scroll"
  }, /*#__PURE__*/React.createElement("table", {
    className: "k-tbl"
  }, /*#__PURE__*/React.createElement("thead", null, /*#__PURE__*/React.createElement("tr", null, /*#__PURE__*/React.createElement("th", {
    className: "ctr",
    style: {
      width: 44
    }
  }, /*#__PURE__*/React.createElement("input", {
    type: "checkbox",
    style: {
      accentColor: 'var(--primary)'
    },
    checked: allSel,
    onChange: () => setSel(allSel ? new Set() : new Set(rows.map(r => r.id)))
  })), head('id', '订单号'), /*#__PURE__*/React.createElement("th", null, "\u5BA2\u6237"), /*#__PURE__*/React.createElement("th", null, "\u4EA7\u54C1"), head('status', '状态', 'ctr'), head('qty', '数量', 'num'), head('amount', '金额', 'num'), /*#__PURE__*/React.createElement("th", null, "\u4E0B\u5355\u65E5"))), /*#__PURE__*/React.createElement("tbody", null, rows.map(r => {
    const [t, tone] = STATUS[r.status];
    return /*#__PURE__*/React.createElement("tr", {
      key: r.id,
      onClick: () => setActive(r),
      className: sel.has(r.id) ? '' : '',
      style: sel.has(r.id) ? {
        background: 'var(--primary-soft)'
      } : null
    }, /*#__PURE__*/React.createElement("td", {
      className: "ctr",
      onClick: e => e.stopPropagation()
    }, /*#__PURE__*/React.createElement("input", {
      type: "checkbox",
      style: {
        accentColor: 'var(--primary)'
      },
      checked: sel.has(r.id),
      onChange: () => setSel(s => {
        const n = new Set(s);
        n.has(r.id) ? n.delete(r.id) : n.add(r.id);
        return n;
      })
    })), /*#__PURE__*/React.createElement("td", {
      className: "k-mono",
      style: {
        color: 'var(--text-link)',
        fontWeight: 600
      }
    }, r.id), /*#__PURE__*/React.createElement("td", null, r.customer), /*#__PURE__*/React.createElement("td", {
      style: {
        color: 'var(--text-secondary)'
      }
    }, r.sku), /*#__PURE__*/React.createElement("td", {
      className: "ctr"
    }, /*#__PURE__*/React.createElement(KTag, {
      tone: tone,
      dot: true
    }, t)), /*#__PURE__*/React.createElement("td", {
      className: "num k-mono"
    }, r.qty), /*#__PURE__*/React.createElement("td", {
      className: "num k-mono",
      style: r.amount < 0 ? {
        color: 'var(--danger)'
      } : null
    }, r.amount < 0 ? '−¥' + Math.abs(r.amount).toLocaleString() : '¥' + r.amount.toLocaleString()), /*#__PURE__*/React.createElement("td", {
      style: {
        color: 'var(--text-tertiary)'
      },
      className: "k-mono"
    }, r.date));
  }))))), active && /*#__PURE__*/React.createElement("div", {
    onClick: () => setActive(null),
    style: {
      position: 'fixed',
      inset: 0,
      background: 'var(--surface-overlay)',
      zIndex: 100,
      display: 'flex',
      justifyContent: 'flex-end'
    }
  }, /*#__PURE__*/React.createElement("div", {
    onClick: e => e.stopPropagation(),
    style: {
      width: 420,
      maxWidth: '92vw',
      background: 'var(--surface-card)',
      height: '100%',
      boxShadow: 'var(--shadow-xl)',
      padding: 24,
      overflow: 'auto',
      animation: 'slideIn .28s var(--ease-out)'
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      justifyContent: 'space-between',
      alignItems: 'flex-start',
      marginBottom: 18
    }
  }, /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("div", {
    style: {
      fontSize: 12,
      color: 'var(--text-tertiary)'
    }
  }, "\u8BA2\u5355\u8BE6\u60C5"), /*#__PURE__*/React.createElement("div", {
    className: "k-mono",
    style: {
      fontSize: 18,
      fontWeight: 700,
      color: 'var(--text-primary)'
    }
  }, active.id)), /*#__PURE__*/React.createElement(KBtn, {
    variant: "text",
    size: "sm",
    onClick: () => setActive(null)
  }, "\u2715")), (() => {
    const [t, tone] = STATUS[active.status];
    return /*#__PURE__*/React.createElement(KTag, {
      tone: tone,
      dot: true
    }, t);
  })(), /*#__PURE__*/React.createElement("div", {
    style: {
      marginTop: 18,
      display: 'grid',
      gridTemplateColumns: '1fr 1fr',
      gap: 14
    }
  }, [['客户', active.customer], ['产品', active.sku], ['数量', active.qty + ' 件'], ['下单日', '2026-' + active.date], ['金额', active.amount < 0 ? '−¥' + Math.abs(active.amount).toLocaleString() : '¥' + active.amount.toLocaleString()], ['店铺', '天猫旗舰店']].map(([k, val]) => /*#__PURE__*/React.createElement("div", {
    key: k
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      fontSize: 12,
      color: 'var(--text-tertiary)',
      marginBottom: 3
    }
  }, k), /*#__PURE__*/React.createElement("div", {
    style: {
      fontSize: 14,
      color: 'var(--text-primary)',
      fontWeight: 500
    }
  }, val)))), /*#__PURE__*/React.createElement("div", {
    style: {
      marginTop: 24,
      display: 'flex',
      gap: 8
    }
  }, /*#__PURE__*/React.createElement(KBtn, {
    variant: "primary",
    block: true
  }, "\u751F\u6210\u5DE5\u5382\u4E0B\u5355"), /*#__PURE__*/React.createElement(KBtn, {
    variant: "secondary"
  }, "\u6253\u5370")))));
}
window.OrdersScreen = OrdersScreen;
})(); } catch (e) { __ds_ns.__errors.push({ path: "ui_kits/web/OrdersScreen.jsx", error: String((e && e.message) || e) }); }

// ui_kits/web/ReconScreen.jsx
try { (() => {
// 对账中心 — Tab (结算/工厂/支付宝/代付) + 支付宝流水智能核销列表
function ReconScreen() {
  const [tab, setTab] = React.useState('alipay');
  const flows = [{
    id: 'AL-66821',
    date: '06-22 14:32',
    amount: 12480,
    match: ['PS-20260622-018'],
    st: 'matched'
  }, {
    id: 'AL-66820',
    date: '06-22 11:08',
    amount: 31900,
    match: ['PS-20260621-094', 'PS-20260620-131'],
    st: 'matched'
  }, {
    id: 'AL-66819',
    date: '06-22 09:51',
    amount: 3200,
    match: [],
    st: 'pending'
  }, {
    id: 'AL-66818',
    date: '06-21 17:20',
    amount: 7680,
    match: ['PS-20260621-088'],
    st: 'matched'
  }, {
    id: 'AL-66817',
    date: '06-21 15:44',
    amount: 1560,
    match: [],
    st: 'conflict'
  }];
  const ST = {
    matched: ['已核销', 'success'],
    pending: ['待匹配', 'warning'],
    conflict: ['有差异', 'danger']
  };
  return /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("div", {
    style: {
      marginBottom: 16
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      fontSize: 12,
      color: 'var(--text-tertiary)',
      marginBottom: 6
    }
  }, "\u8D22\u52A1 / \u5BF9\u8D26\u4E2D\u5FC3"), /*#__PURE__*/React.createElement("h1", {
    style: {
      fontSize: 24,
      fontWeight: 800,
      letterSpacing: '-.02em',
      margin: 0,
      color: 'var(--text-primary)'
    }
  }, "\u5BF9\u8D26\u4E2D\u5FC3")), /*#__PURE__*/React.createElement("div", {
    style: {
      marginBottom: 16
    }
  }, /*#__PURE__*/React.createElement(KTabs, {
    value: tab,
    onChange: setTab,
    items: [{
      key: 'settle',
      label: '结算'
    }, {
      key: 'factory',
      label: '工厂对账'
    }, {
      key: 'alipay',
      label: '支付宝核销',
      badge: 2
    }, {
      key: 'prepay',
      label: '代付'
    }]
  })), /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'grid',
      gridTemplateColumns: 'repeat(auto-fit,minmax(200px,1fr))',
      gap: 16,
      marginBottom: 18
    }
  }, /*#__PURE__*/React.createElement(KCard, null, /*#__PURE__*/React.createElement(KStat, {
    title: "\u672C\u6708\u6D41\u6C34",
    prefix: "\xA5",
    value: "1,486,300",
    icon: /*#__PURE__*/React.createElement(MIcon, {
      n: "credit_card"
    })
  })), /*#__PURE__*/React.createElement(KCard, null, /*#__PURE__*/React.createElement(KStat, {
    title: "\u5DF2\u6838\u9500",
    value: "412",
    icon: /*#__PURE__*/React.createElement(MIcon, {
      n: "check_circle"
    }),
    valueColor: "var(--success)",
    footer: "\u7B14"
  })), /*#__PURE__*/React.createElement(KCard, null, /*#__PURE__*/React.createElement(KStat, {
    title: "\u5F85\u5339\u914D",
    value: "2",
    icon: /*#__PURE__*/React.createElement(MIcon, {
      n: "search"
    }),
    valueColor: "var(--warning)",
    footer: "\u7B14"
  })), /*#__PURE__*/React.createElement(KCard, null, /*#__PURE__*/React.createElement(KStat, {
    title: "\u5DEE\u5F02",
    value: "1",
    icon: /*#__PURE__*/React.createElement(MIcon, {
      n: "warning"
    }),
    valueColor: "var(--danger)",
    footer: "\u7B14\u9700\u590D\u6838"
  }))), /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'space-between',
      marginBottom: 12
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      fontSize: 15,
      fontWeight: 700,
      color: 'var(--text-primary)'
    }
  }, "\u652F\u4ED8\u5B9D\u6D41\u6C34 \xB7 \u81EA\u52A8\u6838\u9500"), /*#__PURE__*/React.createElement(KBtn, {
    variant: "primary",
    size: "sm",
    icon: /*#__PURE__*/React.createElement(MIcon, {
      n: "auto_awesome"
    })
  }, "\u667A\u80FD\u5339\u914D")), /*#__PURE__*/React.createElement("div", {
    className: "k-tbl-wrap"
  }, /*#__PURE__*/React.createElement("div", {
    className: "k-tbl-scroll"
  }, /*#__PURE__*/React.createElement("table", {
    className: "k-tbl"
  }, /*#__PURE__*/React.createElement("thead", null, /*#__PURE__*/React.createElement("tr", null, /*#__PURE__*/React.createElement("th", null, "\u6D41\u6C34\u53F7"), /*#__PURE__*/React.createElement("th", null, "\u65F6\u95F4"), /*#__PURE__*/React.createElement("th", {
    className: "num"
  }, "\u91D1\u989D"), /*#__PURE__*/React.createElement("th", null, "\u5339\u914D\u5355\u636E"), /*#__PURE__*/React.createElement("th", {
    className: "ctr"
  }, "\u72B6\u6001"), /*#__PURE__*/React.createElement("th", {
    className: "ctr"
  }, "\u64CD\u4F5C"))), /*#__PURE__*/React.createElement("tbody", null, flows.map(f => {
    const [t, tone] = ST[f.st];
    return /*#__PURE__*/React.createElement("tr", {
      key: f.id,
      style: {
        cursor: 'default'
      }
    }, /*#__PURE__*/React.createElement("td", {
      className: "k-mono",
      style: {
        color: 'var(--text-link)',
        fontWeight: 600
      }
    }, f.id), /*#__PURE__*/React.createElement("td", {
      className: "k-mono",
      style: {
        color: 'var(--text-tertiary)'
      }
    }, f.date), /*#__PURE__*/React.createElement("td", {
      className: "num k-mono",
      style: {
        fontWeight: 600
      }
    }, "\xA5", f.amount.toLocaleString()), /*#__PURE__*/React.createElement("td", null, f.match.length ? /*#__PURE__*/React.createElement("div", {
      style: {
        display: 'flex',
        gap: 6,
        flexWrap: 'wrap'
      }
    }, f.match.map(m => /*#__PURE__*/React.createElement(KTag, {
      key: m,
      tone: "brand"
    }, m))) : /*#__PURE__*/React.createElement("span", {
      style: {
        color: 'var(--text-tertiary)'
      }
    }, "\u2014")), /*#__PURE__*/React.createElement("td", {
      className: "ctr"
    }, /*#__PURE__*/React.createElement(KTag, {
      tone: tone,
      dot: true
    }, t)), /*#__PURE__*/React.createElement("td", {
      className: "ctr"
    }, f.st === 'matched' ? /*#__PURE__*/React.createElement(KBtn, {
      variant: "text",
      size: "sm"
    }, "\u67E5\u770B") : /*#__PURE__*/React.createElement(KBtn, {
      variant: "ghost",
      size: "sm"
    }, "\u624B\u52A8\u5339\u914D")));
  }))))), /*#__PURE__*/React.createElement("div", {
    style: {
      marginTop: 12,
      fontSize: 12,
      color: 'var(--text-tertiary)',
      display: 'flex',
      alignItems: 'center',
      gap: 6
    }
  }, /*#__PURE__*/React.createElement(MIcon, {
    n: "lightbulb",
    size: 16
  }), " \u5B50\u96C6\u548C\u7B97\u6CD5\uFF1A\u4E00\u7B14\u6D41\u6C34\u81EA\u52A8\u5339\u914D 1~N \u5F20\u5F85\u4ED8\u5355\u636E\uFF08\u5982 AL-66820 \u2192 \u4E24\u5355\u5408\u8BA1 \xA531,900\uFF09\u3002"));
}
window.ReconScreen = ReconScreen;
})(); } catch (e) { __ds_ns.__errors.push({ path: "ui_kits/web/ReconScreen.jsx", error: String((e && e.message) || e) }); }

// ui_kits/web/kit-ui.jsx
try { (() => {
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
const KBtn = ({
  variant = 'primary',
  size = 'md',
  block,
  icon,
  children,
  ...p
}) => React.createElement('button', {
  className: `k-btn k-btn--${variant}${size !== 'md' ? ' k-btn--' + size : ''}${block ? ' k-btn--block' : ''}`,
  ...p
}, icon, children && React.createElement('span', null, children));
const KTag = ({
  tone = 'default',
  dot,
  children,
  ...p
}) => React.createElement('span', {
  className: `k-tag k-tag--${tone}`,
  ...p
}, dot && React.createElement('span', {
  className: 'dot'
}), children);
const KCard = ({
  title,
  extra,
  hover,
  children,
  style,
  ...p
}) => React.createElement('div', {
  className: `k-card${hover ? ' k-card--hover' : ''}`,
  style,
  ...p
}, (title || extra) && React.createElement('div', {
  className: 'k-card__hd'
}, React.createElement('span', {
  className: 'k-card__title'
}, title), extra && React.createElement('span', {
  className: 'k-card__extra'
}, extra)), React.createElement('div', {
  className: 'k-card__body'
}, children));
const KStat = ({
  title,
  value,
  prefix,
  icon,
  delta,
  dir = 'up',
  footer,
  valueColor
}) => React.createElement('div', null, React.createElement('div', {
  className: 'k-stat__top'
}, icon && React.createElement('span', {
  className: 'k-stat__icon'
}, icon), React.createElement('span', {
  className: 'k-stat__title'
}, title)), React.createElement('div', {
  className: 'k-stat__val',
  style: valueColor ? {
    color: valueColor
  } : null
}, prefix, value), (delta != null || footer) && React.createElement('div', {
  className: 'k-stat__foot'
}, delta != null && React.createElement('span', {
  className: dir === 'up' ? 'k-up' : 'k-down'
}, (dir === 'up' ? '↑ ' : '↓ ') + delta), footer));
const KSeg = ({
  options,
  value,
  onChange
}) => React.createElement('div', {
  className: 'k-seg'
}, options.map(o => {
  const opt = typeof o === 'object' ? o : {
    label: o,
    value: o
  };
  return React.createElement('span', {
    key: String(opt.value),
    className: opt.value === value ? 'on' : '',
    onClick: () => onChange && onChange(opt.value)
  }, opt.label);
}));
const KTabs = ({
  items,
  value,
  onChange
}) => React.createElement('div', {
  className: 'k-tabs'
}, items.map(it => React.createElement('div', {
  key: it.key,
  className: `k-tab${it.key === value ? ' on' : ''}`,
  onClick: () => onChange && onChange(it.key)
}, it.label, it.badge != null && React.createElement('span', {
  className: 'bd'
}, it.badge))));
const MIcon = ({
  n,
  size,
  style
}) => React.createElement('span', {
  className: 'material-symbols-outlined',
  style: {
    fontSize: size || '1.15em',
    lineHeight: 1,
    verticalAlign: 'middle',
    ...(style || {})
  }
}, n);
Object.assign(window, {
  KBtn,
  KTag,
  KCard,
  KStat,
  KSeg,
  KTabs,
  MIcon
});
})(); } catch (e) { __ds_ns.__errors.push({ path: "ui_kits/web/kit-ui.jsx", error: String((e && e.message) || e) }); }

__ds_ns.Button = __ds_scope.Button;

__ds_ns.Card = __ds_scope.Card;

__ds_ns.Input = __ds_scope.Input;

__ds_ns.Select = __ds_scope.Select;

__ds_ns.StatCard = __ds_scope.StatCard;

__ds_ns.Tag = __ds_scope.Tag;

__ds_ns.DataTable = __ds_scope.DataTable;

__ds_ns.PageHeader = __ds_scope.PageHeader;

__ds_ns.Segmented = __ds_scope.Segmented;

__ds_ns.Tabs = __ds_scope.Tabs;

__ds_ns.TopNav = __ds_scope.TopNav;

})();
