// 订单页 — 标题区 + 标签筛选 + 工具栏 + 数据表格 (排序/多选/行抽屉)
function OrdersScreen() {
  const STATUS = { paid: ['已付款', 'info'], shipped: ['已发货', 'brand'], signed: ['已签收', 'success'], pending: ['待付款', 'warning'], aftersales: ['售后', 'danger'] };
  const ALL = [
    { id: 'PS-20260622-018', customer: '佳宝家居旗舰店', sku: '北欧实木餐桌 1.4m', status: 'shipped', qty: 12, amount: 12480, date: '06-22' },
    { id: 'PS-20260622-017', customer: '宜美优品专营店', sku: '原木电视柜 2.0m', status: 'pending', qty: 3, amount: 3200, date: '06-22' },
    { id: 'PS-20260621-094', customer: '木言木语家具', sku: '橡木书架组合', status: 'signed', qty: 48, amount: 128900, date: '06-21' },
    { id: 'PS-20260621-088', customer: '北欧时光', sku: '布艺三人沙发', status: 'paid', qty: 6, amount: 7680, date: '06-21' },
    { id: 'PS-20260621-072', customer: '原木良品', sku: '岩板茶几', status: 'aftersales', qty: 2, amount: -1240, date: '06-21' },
    { id: 'PS-20260620-145', customer: '栖居生活馆', sku: '实木床架 1.8m', status: 'shipped', qty: 21, amount: 43200, date: '06-20' },
    { id: 'PS-20260620-131', customer: '简屋家居', sku: '餐边柜 1.2m', status: 'signed', qty: 9, amount: 16740, date: '06-20' },
    { id: 'PS-20260620-110', customer: '青木工坊', sku: '儿童学习桌椅套装', status: 'paid', qty: 15, amount: 28500, date: '06-20' },
  ];
  const [tab, setTab] = React.useState('all');
  const [sort, setSort] = React.useState(null);
  const [sel, setSel] = React.useState(() => new Set());
  const [active, setActive] = React.useState(null);

  let rows = tab === 'all' ? ALL : ALL.filter((r) => (tab === 'pending' ? r.status === 'pending' : tab === 'shipped' ? r.status === 'shipped' : r.status === 'aftersales'));
  if (sort) { rows = [...rows].sort((a, b) => { const r = a[sort.k] > b[sort.k] ? 1 : a[sort.k] < b[sort.k] ? -1 : 0; return sort.d === 'asc' ? r : -r; }); }
  const allSel = rows.length > 0 && rows.every((r) => sel.has(r.id));
  const head = (k, label, cls) => {
    const on = sort && sort.k === k;
    return <th className={cls} onClick={() => setSort((s) => (!s || s.k !== k ? { k, d: 'asc' } : s.d === 'asc' ? { k, d: 'desc' } : null))} style={{ cursor: 'pointer' }}>
      {label} <span style={{ color: on ? 'var(--primary)' : 'var(--text-tertiary)', fontSize: 10 }}>{on ? (sort.d === 'asc' ? '▲' : '▼') : '↕'}</span></th>;
  };

  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'flex-end', justifyContent: 'space-between', flexWrap: 'wrap', gap: 12, marginBottom: 16 }}>
        <div>
          <div style={{ fontSize: 12, color: 'var(--text-tertiary)', marginBottom: 6 }}>订单 / 全部订单</div>
          <h1 style={{ fontSize: 24, fontWeight: 800, letterSpacing: '-.02em', margin: 0, color: 'var(--text-primary)' }}>订单</h1>
          <div style={{ color: 'var(--text-secondary)', fontSize: 13, marginTop: 4 }}>共 1,284 单 · 今日新增 36</div>
        </div>
        <div style={{ display: 'flex', gap: 8 }}>
          <KBtn variant="secondary" size="md" icon={<MIcon n="download" />}>导出</KBtn>
          <KBtn variant="primary" size="md" icon={<MIcon n="add" />}>新建订单</KBtn>
        </div>
      </div>

      <div style={{ marginBottom: 14 }}>
        <KTabs value={tab} onChange={(k) => { setTab(k); setSel(new Set()); }} items={[
          { key: 'all', label: '全部', badge: 1284 }, { key: 'pending', label: '待处理', badge: 36 },
          { key: 'shipped', label: '已发货', badge: 412 }, { key: 'aftersales', label: '售后', badge: 7 }]} />
      </div>

      {/* 工具栏 */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 12, flexWrap: 'wrap' }}>
        <div className="k-input" style={{ width: 260 }}><span className="affix"><MIcon n="search" /></span><input placeholder="搜索订单号 / 客户 / 产品" /></div>
        <KSeg value="all" onChange={() => {}} options={[{ label: '全部店铺', value: 'all' }, { label: '天猫', value: 'tm' }, { label: '淘宝', value: 'tb' }]} />
        <div style={{ flex: 1 }} />
        {sel.size > 0
          ? <><span style={{ fontSize: 13, color: 'var(--text-secondary)' }}>已选 {sel.size} 项</span><KBtn variant="ghost" size="sm">批量发货</KBtn><KBtn variant="text" size="sm">导出所选</KBtn></>
          : <span style={{ fontSize: 12, color: 'var(--text-tertiary)' }}>点击表头排序 · 勾选批量操作</span>}
      </div>

      <div className="k-tbl-wrap">
        <div className="k-tbl-scroll">
          <table className="k-tbl">
            <thead><tr>
              <th className="ctr" style={{ width: 44 }}><input type="checkbox" style={{ accentColor: 'var(--primary)' }} checked={allSel} onChange={() => setSel(allSel ? new Set() : new Set(rows.map((r) => r.id)))} /></th>
              {head('id', '订单号')}<th>客户</th><th>产品</th>{head('status', '状态', 'ctr')}{head('qty', '数量', 'num')}{head('amount', '金额', 'num')}<th>下单日</th>
            </tr></thead>
            <tbody>
              {rows.map((r) => { const [t, tone] = STATUS[r.status]; return (
                <tr key={r.id} onClick={() => setActive(r)} className={sel.has(r.id) ? '' : ''} style={sel.has(r.id) ? { background: 'var(--primary-soft)' } : null}>
                  <td className="ctr" onClick={(e) => e.stopPropagation()}><input type="checkbox" style={{ accentColor: 'var(--primary)' }} checked={sel.has(r.id)} onChange={() => setSel((s) => { const n = new Set(s); n.has(r.id) ? n.delete(r.id) : n.add(r.id); return n; })} /></td>
                  <td className="k-mono" style={{ color: 'var(--text-link)', fontWeight: 600 }}>{r.id}</td>
                  <td>{r.customer}</td>
                  <td style={{ color: 'var(--text-secondary)' }}>{r.sku}</td>
                  <td className="ctr"><KTag tone={tone} dot>{t}</KTag></td>
                  <td className="num k-mono">{r.qty}</td>
                  <td className="num k-mono" style={r.amount < 0 ? { color: 'var(--danger)' } : null}>{r.amount < 0 ? '−¥' + Math.abs(r.amount).toLocaleString() : '¥' + r.amount.toLocaleString()}</td>
                  <td style={{ color: 'var(--text-tertiary)' }} className="k-mono">{r.date}</td>
                </tr>); })}
            </tbody>
          </table>
        </div>
      </div>

      {/* 行抽屉 */}
      {active && (
        <div onClick={() => setActive(null)} style={{ position: 'fixed', inset: 0, background: 'var(--surface-overlay)', zIndex: 100, display: 'flex', justifyContent: 'flex-end' }}>
          <div onClick={(e) => e.stopPropagation()} style={{ width: 420, maxWidth: '92vw', background: 'var(--surface-card)', height: '100%', boxShadow: 'var(--shadow-xl)', padding: 24, overflow: 'auto', animation: 'slideIn .28s var(--ease-out)' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 18 }}>
              <div><div style={{ fontSize: 12, color: 'var(--text-tertiary)' }}>订单详情</div><div className="k-mono" style={{ fontSize: 18, fontWeight: 700, color: 'var(--text-primary)' }}>{active.id}</div></div>
              <KBtn variant="text" size="sm" onClick={() => setActive(null)}>✕</KBtn>
            </div>
            {(() => { const [t, tone] = STATUS[active.status]; return <KTag tone={tone} dot>{t}</KTag>; })()}
            <div style={{ marginTop: 18, display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 14 }}>
              {[['客户', active.customer], ['产品', active.sku], ['数量', active.qty + ' 件'], ['下单日', '2026-' + active.date], ['金额', (active.amount < 0 ? '−¥' + Math.abs(active.amount).toLocaleString() : '¥' + active.amount.toLocaleString())], ['店铺', '天猫旗舰店']].map(([k, val]) => (
                <div key={k}><div style={{ fontSize: 12, color: 'var(--text-tertiary)', marginBottom: 3 }}>{k}</div><div style={{ fontSize: 14, color: 'var(--text-primary)', fontWeight: 500 }}>{val}</div></div>
              ))}
            </div>
            <div style={{ marginTop: 24, display: 'flex', gap: 8 }}>
              <KBtn variant="primary" block>生成工厂下单</KBtn><KBtn variant="secondary">打印</KBtn>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
window.OrdersScreen = OrdersScreen;
