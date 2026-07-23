// 运营大盘 — KPI + 资金条 + 状态环图 + 趋势图 + 库存/对账健康
function DashboardScreen({ go }) {
  const pieRef = React.useRef(null), trendRef = React.useRef(null);
  const [period, setPeriod] = React.useState('30d');

  React.useEffect(() => {
    if (!window.echarts) return;
    const C = getComputedStyle(document.documentElement);
    const v = (n) => C.getPropertyValue(n).trim();
    const sub = v('--text-tertiary'), grid = '#eef2f7';
    // 防止重复 init 叠加空白 canvas
    [pieRef.current, trendRef.current].forEach((el) => { const ex = window.echarts.getInstanceByDom(el); if (ex) ex.dispose(); });
    const pie = window.echarts.init(pieRef.current, null, { renderer: 'svg' });
    pie.setOption({
      tooltip: { trigger: 'item', formatter: '{b}: {c} ({d}%)' },
      legend: { orient: 'vertical', right: 6, top: 'center', textStyle: { color: v('--text-secondary'), fontSize: 12 }, itemWidth: 10, itemHeight: 10 },
      series: [{ type: 'pie', radius: ['56%', '78%'], center: ['34%', '50%'], itemStyle: { borderColor: '#fff', borderWidth: 3, borderRadius: 6 },
        label: { show: true, formatter: '{d}%', color: sub, fontSize: 11 },
        data: [
          { name: '已发货', value: 38, itemStyle: { color: v('--teal-500') } },
          { name: '待付款', value: 14, itemStyle: { color: v('--amber-500') } },
          { name: '已签收', value: 28, itemStyle: { color: v('--indigo-500') } },
          { name: '已付款', value: 16, itemStyle: { color: v('--sky-500') } },
          { name: '售后', value: 4, itemStyle: { color: v('--red-500') } },
        ] }],
    });
    const days = Array.from({ length: 14 }, (_, i) => '06-' + String(i + 9).padStart(2, '0'));
    const trend = window.echarts.init(trendRef.current, null, { renderer: 'svg' });
    trend.setOption({
      tooltip: { trigger: 'axis' },
      legend: { data: ['订单数', '收入(¥)'], textStyle: { color: v('--text-secondary') }, top: 0, itemWidth: 12, itemHeight: 8 },
      grid: { top: 30, left: 6, right: 6, bottom: 2, containLabel: true },
      xAxis: { type: 'category', data: days, axisLine: { lineStyle: { color: '#e2e8f0' } }, axisTick: { show: false }, axisLabel: { color: sub, fontSize: 10 } },
      yAxis: [{ type: 'value', splitLine: { lineStyle: { color: grid } }, axisLabel: { color: sub } },
        { type: 'value', splitLine: { show: false }, axisLabel: { color: sub, formatter: (x) => '¥' + (x / 1000).toFixed(0) + 'k' } }],
      series: [
        { name: '订单数', type: 'bar', data: [22, 30, 18, 41, 36, 28, 33, 45, 39, 31, 48, 42, 37, 52], itemStyle: { color: v('--teal-300'), borderRadius: [4, 4, 0, 0] }, barWidth: '46%' },
        { name: '收入(¥)', type: 'line', yAxisIndex: 1, smooth: true, symbol: 'none', data: [42, 58, 33, 76, 64, 51, 60, 88, 71, 55, 92, 80, 68, 104].map((x) => x * 1000),
          lineStyle: { color: v('--teal-600'), width: 2.5 }, itemStyle: { color: v('--teal-600') },
          areaStyle: { color: { type: 'linear', x: 0, y: 0, x2: 0, y2: 1, colorStops: [{ offset: 0, color: 'rgba(13,148,136,.20)' }, { offset: 1, color: 'rgba(13,148,136,0)' }] } } },
      ],
    });
    const ro = () => { pie.resize(); trend.resize(); };
    window.addEventListener('resize', ro);
    requestAnimationFrame(ro);
    return () => { window.removeEventListener('resize', ro); pie.dispose(); trend.dispose(); };
  }, []);

  const reconRules = [['平台对账', 'ok'], ['工厂对账', 'ok'], ['支付宝核销', 'warning'], ['物流账单', 'ok'], ['退补单', 'danger'], ['保证金', 'ok']];
  const dotColor = { ok: 'var(--success)', warning: 'var(--warning)', danger: 'var(--danger)' };
  const dotIcon = { ok: '✓', warning: '!', danger: '✕' };

  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'flex-end', justifyContent: 'space-between', flexWrap: 'wrap', gap: 8, marginBottom: 18 }}>
        <div>
          <h1 style={{ fontSize: 24, fontWeight: 800, letterSpacing: '-.02em', margin: 0, color: 'var(--text-primary)' }}>运营大盘</h1>
          <div style={{ color: 'var(--text-secondary)', fontSize: 13, marginTop: 4 }}>实时经营概览 · 每分钟自动刷新</div>
        </div>
        <KSeg value={period} onChange={setPeriod} options={[{ label: '今日', value: 'today' }, { label: '昨日', value: 'yesterday' }, { label: '近7天', value: '7d' }, { label: '近30天', value: '30d' }]} />
      </div>

      {/* 资金条 */}
      <div className="k-card k-card--hover" onClick={() => go('finance')} style={{ marginBottom: 16, background: 'linear-gradient(135deg,#fff 0%,var(--teal-50) 100%)', borderColor: 'var(--teal-200)' }}>
        <div className="k-card__body" style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: 12 }}>
          <div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, color: 'var(--text-secondary)', fontSize: 13, fontWeight: 600 }}><MIcon n="account_balance_wallet" style={{ color: 'var(--primary)' }} /> 剩余流水 · 可用资金（实时）</div>
            <div style={{ color: 'var(--success)', fontWeight: 800, fontSize: 30, letterSpacing: '-.01em', marginTop: 4, fontFamily: 'var(--font-mono)' }}>¥ 2,486,300</div>
            <div style={{ marginTop: 4, fontSize: 12, color: 'var(--text-tertiary)' }}>
              <span style={{ color: 'var(--success)' }}>↑ 加项 ¥3,920,000</span> · <span style={{ color: 'var(--danger)' }}>↓ 减项 ¥1,433,700</span>
            </div>
          </div>
          <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', justifyContent: 'flex-end' }}>
            <KTag tone="success" dot>支付宝 · 今天</KTag>
            <KTag tone="success" dot>银行 · 1天前</KTag>
            <KTag tone="warning" dot>现金 · 5天前</KTag>
          </div>
        </div>
      </div>

      {/* KPI */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(220px,1fr))', gap: 16, marginBottom: 16 }}>
        <KCard hover onClick={() => go('orders')}><KStat title="近 7 天订单" value="248" icon={<MIcon n="shopping_cart" />} delta="8.2%" dir="up" footer="单" /></KCard>
        <KCard hover onClick={() => go('orders')}><KStat title="近 30 天收入" prefix="¥" value="1,284,560" icon={<MIcon n="payments" />} delta="12.4%" dir="up" footer="较上月" /></KCard>
        <KCard hover onClick={() => go('orders')}><KStat title="毛利率" value="18.4%" icon={<MIcon n="trending_up" />} delta="1.6%" dir="up" /></KCard>
        <KCard hover><KStat title="待处理异常" value="7" icon={<MIcon n="warning" />} valueColor="var(--warning)" footer="需复核" /></KCard>
      </div>

      {/* 图表 */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(320px,1fr))', gap: 16, marginBottom: 16 }}>
        <KCard title="订单状态分布" extra="近 30 天"><div ref={pieRef} style={{ height: 240 }} /></KCard>
        <KCard title="近 14 天订单趋势" extra="订单数 / 收入"><div ref={trendRef} style={{ height: 240 }} /></KCard>
      </div>

      {/* 对账健康 */}
      <div style={{ fontSize: 15, fontWeight: 700, color: 'var(--text-primary)', margin: '18px 2px 10px' }}>对账健康</div>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(140px,1fr))', gap: 12 }}>
        {reconRules.map(([label, st]) => (
          <div key={label} className="k-card k-card--hover" onClick={() => go('recon')} style={{ textAlign: 'center', borderColor: st === 'ok' ? 'var(--success-border)' : st === 'warning' ? 'var(--warning-border)' : 'var(--danger-border)' }}>
            <div className="k-card__body" style={{ padding: '12px 8px' }}>
              <div style={{ width: 26, height: 26, borderRadius: '50%', margin: '0 auto 6px', display: 'flex', alignItems: 'center', justifyContent: 'center', color: '#fff', fontWeight: 700, fontSize: 13, background: dotColor[st] }}>{dotIcon[st]}</div>
              <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--text-primary)' }}>{label}</div>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
window.DashboardScreen = DashboardScreen;
