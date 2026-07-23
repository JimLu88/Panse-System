// 库存页 — 概览卡 + 配件库存表 (安全库存进度 / 缺料告警)
function InventoryScreen() {
  const parts = [
    { code: 'WD-OAK-001', name: '橡木板材 18mm', cur: 1280, safe: 800, unit: '张', st: 'ok' },
    { code: 'HW-HINGE-22', name: '阻尼铰链', cur: 96, safe: 400, unit: '只', st: 'low' },
    { code: 'WD-PINE-014', name: '松木方料 40×40', cur: -24, safe: 200, unit: '根', st: 'neg' },
    { code: 'FB-LINEN-07', name: '亚麻布料 米白', cur: 540, safe: 300, unit: '米', st: 'ok' },
    { code: 'HW-RAIL-33', name: '三节导轨 450mm', cur: 220, safe: 250, unit: '套', st: 'low' },
    { code: 'PK-BOX-XL', name: '加固纸箱 XL', cur: 1840, safe: 1000, unit: '个', st: 'ok' },
    { code: 'WD-MDF-009', name: '中纤板 15mm', cur: 64, safe: 500, unit: '张', st: 'low' },
  ];
  const ST = { ok: ['充足', 'success'], low: ['缺料', 'warning'], neg: ['负库存', 'danger'] };
  const [density, setDensity] = React.useState('default');
  const rowH = { compact: 40, default: 48, spacious: 56 }[density];

  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'flex-end', justifyContent: 'space-between', flexWrap: 'wrap', gap: 12, marginBottom: 16 }}>
        <div>
          <div style={{ fontSize: 12, color: 'var(--text-tertiary)', marginBottom: 6 }}>库存 / 配件库存</div>
          <h1 style={{ fontSize: 24, fontWeight: 800, letterSpacing: '-.02em', margin: 0, color: 'var(--text-primary)' }}>配件库存</h1>
        </div>
        <KSeg value={density} onChange={setDensity} options={[{ label: '紧凑', value: 'compact' }, { label: '默认', value: 'default' }, { label: '宽松', value: 'spacious' }]} />
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(200px,1fr))', gap: 16, marginBottom: 18 }}>
        <KCard><KStat title="配件品种" value="328" icon={<MIcon n="inventory_2" />} footer="种" /></KCard>
        <KCard><KStat title="缺料预警" value="14" icon={<MIcon n="warning" />} valueColor="var(--warning)" footer="种待补货" /></KCard>
        <KCard><KStat title="负库存" value="2" icon={<MIcon n="trending_down" />} valueColor="var(--danger)" footer="种超卖" /></KCard>
        <KCard><KStat title="库存总值" prefix="¥" value="864,200" icon={<MIcon n="savings" />} /></KCard>
      </div>

      <div className="k-tbl-wrap">
        <div className="k-tbl-scroll">
          <table className="k-tbl">
            <thead><tr><th>编号</th><th>物料名称</th><th className="ctr">状态</th><th className="num">当前库存</th><th className="num">安全库存</th><th style={{ width: 200 }}>水位</th></tr></thead>
            <tbody>
              {parts.map((p) => { const [t, tone] = ST[p.st]; const pct = Math.max(0, Math.min(100, (p.cur / (p.safe * 1.6)) * 100));
                const barColor = p.st === 'neg' ? 'var(--danger)' : p.st === 'low' ? 'var(--warning)' : 'var(--success)';
                return (
                <tr key={p.code} style={{ cursor: 'default' }}>
                  <td className="k-mono" style={{ height: rowH, color: 'var(--text-secondary)' }}>{p.code}</td>
                  <td style={{ fontWeight: 500 }}>{p.name}</td>
                  <td className="ctr"><KTag tone={tone} dot>{t}</KTag></td>
                  <td className="num k-mono" style={p.cur < 0 ? { color: 'var(--danger)', fontWeight: 600 } : null}>{p.cur.toLocaleString()} {p.unit}</td>
                  <td className="num k-mono" style={{ color: 'var(--text-tertiary)' }}>{p.safe.toLocaleString()}</td>
                  <td><div style={{ height: 8, borderRadius: 999, background: 'var(--surface-sunken)', overflow: 'hidden' }}><div style={{ width: pct + '%', height: '100%', background: barColor, borderRadius: 999 }} /></div></td>
                </tr>); })}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
window.InventoryScreen = InventoryScreen;
