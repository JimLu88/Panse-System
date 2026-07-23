import React from 'react';

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
export function Select({ options = [], value, defaultValue, onChange, placeholder = '请选择', style, className = '', ...rest }) {
  inject();
  const [open, setOpen] = React.useState(false);
  const [inner, setInner] = React.useState(defaultValue);
  const ref = React.useRef(null);
  const cur = value !== undefined ? value : inner;
  const selected = options.find((o) => o.value === cur);

  React.useEffect(() => {
    function onDoc(e) { if (ref.current && !ref.current.contains(e.target)) setOpen(false); }
    document.addEventListener('mousedown', onDoc);
    return () => document.removeEventListener('mousedown', onDoc);
  }, []);

  function pick(o) {
    if (value === undefined) setInner(o.value);
    onChange && onChange(o.value, o);
    setOpen(false);
  }

  return (
    <div ref={ref} className={`pds-select${open ? ' pds-select--open' : ''} ${className}`} style={style} {...rest}>
      <div className="pds-select__ctrl" onClick={() => setOpen((v) => !v)}>
        <span className={selected ? '' : 'pds-select__ph'}>{selected ? selected.label : placeholder}</span>
        <span className="pds-select__arrow">▾</span>
      </div>
      {open && (
        <div className="pds-select__menu">
          {options.map((o) => (
            <div key={String(o.value)} className={`pds-select__opt${o.value === cur ? ' pds-select__opt--active' : ''}`} onClick={() => pick(o)}>
              <span>{o.label}</span>
              {o.value === cur && <span className="pds-select__check">✓</span>}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
