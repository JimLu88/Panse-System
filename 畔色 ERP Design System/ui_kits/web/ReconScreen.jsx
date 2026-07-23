// 对账中心 — Tab (结算/工厂/支付宝/代付) + 支付宝流水智能核销列表
function ReconScreen() {
  const [tab, setTab] = React.useState('alipay');
  const flows = [
    { id: 'AL-66821', date: '06-22 14:32', amount: 12480, match: ['PS-20260622-018'], st: 'matched' },
    { id: 'AL-66820', date: '06-22 11:08', amount: 31900, match: ['PS-20260621-094', 'PS-20260620-131'], st: 'matched' },
    { id: 'AL-66819', date: '06-22 09:51', amount: 3200, match: [], st: 'pending' },
    { id: 'AL-66818', date: '06-21 17:20', amount: 7680, match: ['PS-20260621-088'], st: 'matched' },
    { id: 'AL-66817', date: '06-21 15:44', amount: 1560, match: [], st: 'conflict' },
  ];
  const ST = { matched: ['已核销', 'success'], pending: ['待匹配', 'warning'], conflict: ['有差异', 'danger'] };

  return (
    <div>
      <div style={{ marginBottom: 16 }}>
        <div style={{ fontSize: 12, color: 'var(--text-tertiary)', marginBottom: 6 }}>财务 / 对账中心</div>
        <h1 style={{ fontSize: 24, fontWeight: 800, letterSpacing: '-.02em', margin: 0, color: 'var(--text-primary)' }}>对账中心</h1>
      </div>
      <div style={{ marginBottom: 16 }}>
        <KTabs value={tab} onChange={setTab} items={[
          { key: 'settle', label: '结算' }, { key: 'factory', label: '工厂对账' },
          { key: 'alipay', label: '支付宝核销', badge: 2 }, { key: 'prepay', label: '代付' }]} />
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(200px,1fr))', gap: 16, marginBottom: 18 }}>
        <KCard><KStat title="本月流水" prefix="¥" value="1,486,300" icon={<MIcon n="credit_card" />} /></KCard>
        <KCard><KStat title="已核销" value="412" icon={<MIcon n="check_circle" />} valueColor="var(--success)" footer="笔" /></KCard>
        <KCard><KStat title="待匹配" value="2" icon={<MIcon n="search" />} valueColor="var(--warning)" footer="笔" /></KCard>
        <KCard><KStat title="差异" value="1" icon={<MIcon n="warning" />} valueColor="var(--danger)" footer="笔需复核" /></KCard>
      </div>

      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 12 }}>
        <div style={{ fontSize: 15, fontWeight: 700, color: 'var(--text-primary)' }}>支付宝流水 · 自动核销</div>
        <KBtn variant="primary" size="sm" icon={<MIcon n="auto_awesome" />}>智能匹配</KBtn>
      </div>

      <div className="k-tbl-wrap">
        <div className="k-tbl-scroll">
          <table className="k-tbl">
            <thead><tr><th>流水号</th><th>时间</th><th className="num">金额</th><th>匹配单据</th><th className="ctr">状态</th><th className="ctr">操作</th></tr></thead>
            <tbody>
              {flows.map((f) => { const [t, tone] = ST[f.st]; return (
                <tr key={f.id} style={{ cursor: 'default' }}>
                  <td className="k-mono" style={{ color: 'var(--text-link)', fontWeight: 600 }}>{f.id}</td>
                  <td className="k-mono" style={{ color: 'var(--text-tertiary)' }}>{f.date}</td>
                  <td className="num k-mono" style={{ fontWeight: 600 }}>¥{f.amount.toLocaleString()}</td>
                  <td>{f.match.length ? <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>{f.match.map((m) => <KTag key={m} tone="brand">{m}</KTag>)}</div> : <span style={{ color: 'var(--text-tertiary)' }}>—</span>}</td>
                  <td className="ctr"><KTag tone={tone} dot>{t}</KTag></td>
                  <td className="ctr">{f.st === 'matched' ? <KBtn variant="text" size="sm">查看</KBtn> : <KBtn variant="ghost" size="sm">手动匹配</KBtn>}</td>
                </tr>); })}
            </tbody>
          </table>
        </div>
      </div>
      <div style={{ marginTop: 12, fontSize: 12, color: 'var(--text-tertiary)', display: 'flex', alignItems: 'center', gap: 6 }}><MIcon n="lightbulb" size={16} /> 子集和算法：一笔流水自动匹配 1~N 张待付单据（如 AL-66820 → 两单合计 ¥31,900）。</div>
    </div>
  );
}
window.ReconScreen = ReconScreen;
