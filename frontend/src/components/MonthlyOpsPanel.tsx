/**
 * 数据大盘 · 月度经营面板 — 工厂口径月度利润/ROI(可切月) + 销售占比饼图。
 * 未对清月份(recon_status=reference_only)整块标「仅供参考」。
 */
import { lazy, Suspense, useMemo, useState } from 'react';
import { Alert, Card, Col, Row, Segmented, Select, Spin, Statistic, Tag, Typography } from 'antd';
import { useQuery } from '@tanstack/react-query';
import { fetchMonthlyPnl, fetchSalesMix } from '../api/reports';
import RefillCallout from './RefillCallout';

const ReactECharts = lazy(() => import('echarts-for-react'));
const SalesRankingPage = lazy(() => import('../pages/SalesRankingPage'));   // 销售排行榜 嵌进大盘
const ForecastPage = lazy(() => import('../pages/ForecastPage'));           // 销售预测 嵌进大盘

const money = (v: number | null | undefined) =>
  v == null ? '-' : `¥${Number(v).toLocaleString('zh-CN', { maximumFractionDigits: 0 })}`;

const PIE_COLORS = ['#6366f1', '#38bdf8', '#10b981', '#f59e0b', '#f43f5e', '#8b5cf6', '#14b8a6', '#fb923c', '#a3a3a3', '#0ea5e9', '#cbd5e1'];

export default function MonthlyOpsPanel() {
  const [period, setPeriod] = useState<string | undefined>(undefined);
  const [by, setBy] = useState<'product' | 'shop'>('product');
  const [salesView, setSalesView] = useState<'ranking' | 'forecast'>('ranking');  // 销售: 排行榜 ⇄ 预测

  const { data: pnl, isLoading } = useQuery({ queryKey: ['monthly-pnl'], queryFn: fetchMonthlyPnl });
  const rows = pnl?.rows ?? [];
  const sel = period ?? rows[0]?.period;
  const selRow = rows.find((r) => r.period === sel);

  const [y, m] = (sel ?? '0-0').split('-').map(Number);
  const { data: mix } = useQuery({
    queryKey: ['sales-mix', sel, by],
    queryFn: () => fetchSalesMix(y, m, by),
    enabled: !!sel,
  });

  const pieOption = useMemo(() => ({
    tooltip: { trigger: 'item', formatter: '{b}<br/>¥{c} ({d}%)' },
    // 图例(带圆点图标, 可滚动; 长名截断) + 扇形上直接标名称与百分比 (用户拍板 2026-06-17)
    legend: {
      type: 'scroll', orient: 'vertical', right: 6, top: 'middle', icon: 'circle',
      textStyle: { fontSize: 11 }, itemWidth: 10, itemHeight: 10,
      formatter: (name: string) => (name && name.length > 11 ? name.slice(0, 11) + '…' : name),
    },
    color: PIE_COLORS,
    series: [{
      type: 'pie', radius: ['40%', '66%'], center: ['34%', '50%'],
      minShowLabelAngle: 6,   // 太小的扇形不挤标签
      data: (mix?.slices ?? []).map((s) => ({ name: s.name, value: s.revenue })),
      label: {
        show: true, formatter: '{b}\n{d}%', fontSize: 11, color: '#475569', lineHeight: 14,
      },
      labelLine: { show: true, length: 8, length2: 8 },
    }],
  }), [mix]);

  const isRef = selRow?.recon_status === 'reference_only';

  // 选中月份首尾日(YYYY-MM-DD), 传给刷单单列提示; sel 未就绪时不传(无参=本年至今)
  const validMonth = !!sel && y > 0 && m >= 1 && m <= 12;
  const monthStart = validMonth ? `${y}-${String(m).padStart(2, '0')}-01` : undefined;
  const monthEnd = validMonth
    ? `${y}-${String(m).padStart(2, '0')}-${String(new Date(y, m, 0).getDate()).padStart(2, '0')}`
    : undefined;

  return (
    <div style={{ marginTop: 18 }}>
      {/* 刷单(补单)单列提示 — 跟随所选月份; 已从下方月度经营数据剔除, 单独亮出 */}
      <RefillCallout periodStart={monthStart} periodEnd={monthEnd} />

      <Row justify="space-between" align="middle" style={{ margin: '0 2px 10px' }}>
        <Typography.Text style={{ fontWeight: 700, fontSize: 15 }}>月度经营(工厂口径)</Typography.Text>
        <Select
          size="small" style={{ width: 120 }} value={sel} loading={isLoading}
          onChange={setPeriod}
          options={rows.map((r) => ({
            label: r.recon_status === 'accurate' ? `${r.period} ✓` : `${r.period} ⚠`,
            value: r.period,
          }))}
        />
      </Row>

      {isRef && (
        <Alert
          type="warning" showIcon style={{ marginBottom: 10 }}
          message={`${sel} 未完全核对完成,数据仅供参考`}
          description={
            selRow && selRow.factory_recon_count === 0
              ? '该月暂无工厂对账记录;利润为预估值。完成工厂对账后自动转为「准确」。'
              : `该月有 ${selRow?.unbalanced_factories ?? 0} 个工厂未对清。完成工厂对账后自动转为「准确」。`
          }
        />
      )}

      {selRow && (
        <Row gutter={[12, 12]}>
          <Col span={4}>
            <Card size="small" style={{ background: isRef ? '#fffbe6' : undefined }}>
              <Statistic title="当月利润" value={selRow.net_profit ?? 0} precision={0} prefix="¥"
                valueStyle={{ color: (selRow.net_profit ?? 0) >= 0 ? '#389e0d' : '#cf1322', fontSize: 18 }} />
              <Tag color={isRef ? 'warning' : 'success'} style={{ marginTop: 4 }}>
                {isRef ? '仅供参考' : '已核对'}
              </Tag>
            </Card>
          </Col>
          <Col span={4}><Card size="small" style={{ background: isRef ? '#fffbe6' : undefined }}>
            <Statistic title="利润率" value={selRow.net_profit_rate ?? 0} suffix="%" precision={1} />
          </Card></Col>
          <Col span={4}><Card size="small">
            {/* 销售额口径 = 剔除补单 (用户拍板 2026-06-12), 补单金额单独注释 */}
            <Statistic title="当月营收 (不含补单)" value={selRow.real_revenue ?? 0} precision={0} prefix="¥" valueStyle={{ fontSize: 18 }} />
            {(selRow.refill_revenue ?? 0) > 0 && (
              <div style={{ color: '#999', fontSize: 12 }}>
                有补单 ¥{Math.round(selRow.refill_revenue ?? 0).toLocaleString()} 未计入
              </div>
            )}
          </Card></Col>
          <Col span={4}><Card size="small">
            <Statistic title="推广ROI" value={selRow.promo_roi ?? 0} precision={2} suffix="×" />
            <div style={{ color: '#999', fontSize: 12 }}>推广占比 {selRow.promo_spend_ratio != null ? `${(selRow.promo_spend_ratio * 100).toFixed(1)}%` : '-'}</div>
          </Card></Col>
          <Col span={4}><Card size="small">
            <Statistic title="累计利润" value={selRow.cumulative_profit} precision={0} prefix="¥" valueStyle={{ fontSize: 18 }} />
          </Card></Col>
          <Col span={4}><Card size="small">
            <Statistic title="投资回收率" value={selRow.recovery_rate != null ? selRow.recovery_rate * 100 : 0} suffix="%" precision={1}
              valueStyle={{ color: '#1677ff' }} />
            <div style={{ color: '#999', fontSize: 12 }}>总投资 {money(pnl?.total_investment)}</div>
          </Card></Col>
        </Row>
      )}

      {/* 销售 (排行榜 ⇄ 预测) — 嵌进大盘, 月度经营下方 (用户需求 2026-06-22) */}
      <Card
        size="small" title="销售" style={{ marginTop: 12 }}
        extra={<Segmented size="small" value={salesView} onChange={(v) => setSalesView(v as 'ranking' | 'forecast')}
          options={[{ label: '销售排行榜', value: 'ranking' }, { label: '销售预测', value: 'forecast' }]} />}
      >
        <Suspense fallback={<div style={{ height: 200, display: 'flex', alignItems: 'center', justifyContent: 'center' }}><Spin /></div>}>
          {salesView === 'ranking' ? <SalesRankingPage /> : <ForecastPage />}
        </Suspense>
      </Card>

      <Card
        size="small" title={`${sel ?? ''} 销售占比`} style={{ marginTop: 12 }}
        extra={<Segmented size="small" value={by} onChange={(v) => setBy(v as 'product' | 'shop')}
          options={[{ label: '按产品', value: 'product' }, { label: '按店铺', value: 'shop' }]} />}
      >
        {(mix?.slices?.length ?? 0) === 0 ? (
          <div style={{ height: 260, display: 'flex', alignItems: 'center', justifyContent: 'center', color: '#999' }}>该月暂无销售数据</div>
        ) : (
          <Suspense fallback={<div style={{ height: 260, display: 'flex', alignItems: 'center', justifyContent: 'center' }}><Spin /></div>}>
            <ReactECharts option={pieOption} style={{ height: 260 }} />
          </Suspense>
        )}
      </Card>
    </div>
  );
}
