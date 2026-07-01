/**
 * 销售排行榜 — 按月/按年, 分产品的 销量/销售额/利润率 排行 + 每期冠军时间线。
 * 口径: 正式销售 (不含补单/补差价/邮费专链)。销售额 = 买家实付−退款。
 * 利润率 (2026-06-25): 净利 = 实付−退款−会计成本(商品/物流/安装/平台扣点/税/售后), 与逐单核对同口径;
 *   按净利率排序, 每行同时给出利润额(¥), 便于看「哪个产品贡献利润」。不含推广/人员/固定(见月度经营)。
 */
import { useState } from 'react';
import {
  Alert, Card, Col, Grid, Row, Segmented, Select, Space, Statistic, Table, Tag, Typography,
} from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { useQuery } from '@tanstack/react-query';
import { RankMetric, RankPeriod, RankRow, fetchSalesRanking } from '../api/reports';
import PresetTable from '../components/PresetTable';
import RefillCallout from '../components/RefillCallout';

const yuan = (v: number) => `¥${Number(v || 0).toLocaleString('zh-CN', { maximumFractionDigits: 0 })}`;
const pct = (v: number | undefined) => `${(Number(v || 0) * 100).toFixed(1)}%`;
const medal = (r: number) => (r === 1 ? '🥇' : r === 2 ? '🥈' : r === 3 ? '🥉' : `#${r}`);
const profitColor = (v: number | undefined) => ((Number(v || 0)) >= 0 ? '#389e0d' : '#cf1322');

// 手机端: 排行榜用「列表」而非挤压的表格 (研究结论: ranked content 用 list)。产品名为主, 名次徽章 + 主指标右侧高亮。
function MobileRankList({ rows, metric }: { rows: RankRow[]; metric: RankMetric }) {
  if (!rows.length) return <div style={{ padding: 24, textAlign: 'center', color: '#94a3b8' }}>暂无数据</div>;
  return (
    <div>
      {rows.map((row) => {
        const sub = metric === 'revenue'
          ? `销量 ${row.qty} 件 · 订单 ${row.order_count} 单`
          : metric === 'qty'
            ? `销售额 ${yuan(row.revenue)} · 订单 ${row.order_count} 单`
            : `利润 ${yuan(row.net_profit ?? 0)} · 销售额 ${yuan(row.revenue)}`;   // 利润率榜: 旁边带利润额
        const mainVal = metric === 'revenue' ? yuan(row.revenue)
          : metric === 'qty' ? `${row.qty} 件`
            : pct(row.profit_rate);   // 利润率为主指标
        const mainColor = metric === 'profit' ? profitColor(row.net_profit) : '#1677ff';
        return (
          <div key={row.rank} style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '10px 2px', borderBottom: '1px solid #f1f5f9' }}>
            <div style={{ width: 32, textAlign: 'center', flexShrink: 0, fontSize: row.rank <= 3 ? 22 : 15, fontWeight: 600, color: row.rank <= 3 ? undefined : '#94a3b8' }}>{medal(row.rank)}</div>
            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{ fontWeight: 600, color: '#1e293b', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                {row.product_name || '(未命名)'}
                {row.product_code ? <Tag style={{ marginLeft: 6 }}>{row.product_code}</Tag> : null}
              </div>
              <div style={{ marginTop: 2, fontSize: 12, color: '#94a3b8' }}>{sub}</div>
            </div>
            <div style={{ flexShrink: 0, fontWeight: 700, color: mainColor, fontSize: 15 }}>
              {mainVal}
            </div>
          </div>
        );
      })}
    </div>
  );
}

// 手机端: 冠军时间线列表 (点周期切换该期排行)
function MobileChampionList({ periods, sel, metric, onPick }: { periods: RankPeriod[]; sel: string | null; metric: RankMetric; onPick: (p: string) => void }) {
  if (!periods.length) return <div style={{ padding: 24, textAlign: 'center', color: '#94a3b8' }}>暂无数据</div>;
  const champVal = (p: RankPeriod) => metric === 'qty' ? `${p.champion_qty} 件`
    : metric === 'revenue' ? yuan(p.champion_revenue)
      : pct(p.champion_profit_rate);
  const totalVal = (p: RankPeriod) => metric === 'qty' ? `${p.total_qty} 件`
    : metric === 'revenue' ? yuan(p.total_revenue)
      : `利润 ${yuan(p.total_profit ?? 0)}`;
  return (
    <div>
      {periods.map((p) => (
        <div key={p.period} onClick={() => onPick(p.period)}
          style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 10, padding: '10px 8px', borderBottom: '1px solid #f1f5f9', cursor: 'pointer', borderRadius: 8, background: p.period === sel ? '#e6f4ff' : undefined }}>
          <div style={{ minWidth: 0 }}>
            <div style={{ fontWeight: p.period === sel ? 700 : 600 }}>{p.period}</div>
            <div style={{ fontSize: 12, color: '#94a3b8', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>🏆 {p.champion_name || '-'}</div>
          </div>
          <div style={{ textAlign: 'right', flexShrink: 0 }}>
            <div style={{ fontWeight: 600, fontSize: 13 }}>{champVal(p)}</div>
            <div style={{ fontSize: 12, color: '#94a3b8' }}>合计 {totalVal(p)}</div>
          </div>
        </div>
      ))}
    </div>
  );
}

export default function SalesRankingPage(
  { period: extPeriod, onPeriodChange }:
  { period?: string; onPeriodChange?: (p: string | undefined) => void } = {},
) {
  // 受控模式 (extPeriod 传入): 周期由外部(运营大盘月份)驱动, 隐藏本页「统计粒度/周期」选择器, 只留「排行依据」。
  const controlled = extPeriod !== undefined;
  const [granularityState, setGranularityState] = useState<'month' | 'year'>('month');
  const [metric, setMetric] = useState<RankMetric>('revenue');
  const [periodState, setPeriodState] = useState<string | undefined>(undefined);
  const granularity = controlled ? 'month' : granularityState;
  const period = controlled ? extPeriod : periodState;
  const setPeriod = (p: string | undefined) => { if (controlled) onPeriodChange?.(p); else setPeriodState(p); };
  const setGranularity = setGranularityState;
  const screens = Grid.useBreakpoint();
  const isMobile = screens.md === false;
  const isProfit = metric === 'profit';

  const { data, isLoading } = useQuery({
    queryKey: ['sales-ranking', granularity, metric, period],
    queryFn: () => fetchSalesRanking({ granularity, metric, period, limit: 30 }),
  });

  const periods = data?.periods ?? [];
  const sel = data?.selected_period ?? null;
  const champ = periods.find((p) => p.period === sel);
  const gLabel = granularity === 'year' ? '年度' : '月度';

  const onGranularity = (v: string) => { setGranularity(v as 'month' | 'year'); setPeriod(undefined); };

  // 共用列
  const colRank: ColumnsType<RankRow>[number] = {
    title: '名次', dataIndex: 'rank', width: 70, align: 'center',
    render: (r: number) => <span style={{ fontSize: r <= 3 ? 20 : 14 }}>{medal(r)}</span>,
  };
  const colProduct: ColumnsType<RankRow>[number] = {
    title: '产品', dataIndex: 'product_name', ellipsis: true,
    render: (v: string, row) => (
      <span>{v}{row.product_code ? <Tag style={{ marginLeft: 6 }}>{row.product_code}</Tag> : null}</span>
    ),
  };
  const colOrders: ColumnsType<RankRow>[number] = { title: '订单数', dataIndex: 'order_count', width: 80, align: 'right' };

  // 利润率榜: 利润率(主, 高亮) + 利润额(¥) + 销售额(参照); 其余: 销量 + 销售额
  const rankCols: ColumnsType<RankRow> = isProfit
    ? [
        colRank, colProduct,
        { title: '利润率', dataIndex: 'profit_rate', width: 100, align: 'right',
          render: (v: number) => <b style={{ color: '#1677ff' }}>{pct(v)}</b> },
        { title: '利润额', dataIndex: 'net_profit', width: 120, align: 'right',
          render: (v: number) => <b style={{ color: profitColor(v) }}>{yuan(v)}</b> },
        { title: '销售额', dataIndex: 'revenue', width: 120, align: 'right',
          render: (v: number) => yuan(v) },
        colOrders,
      ]
    : [
        colRank, colProduct,
        { title: '销量', dataIndex: 'qty', width: 100, align: 'right',
          render: (v: number) => metric === 'qty'
            ? <b style={{ color: '#1677ff' }}>{v} 件</b> : `${v} 件` },
        { title: '销售额', dataIndex: 'revenue', width: 130, align: 'right',
          render: (v: number) => metric === 'revenue'
            ? <b style={{ color: '#1677ff' }}>{yuan(v)}</b> : yuan(v) },
        colOrders,
      ];

  const periodCols: ColumnsType<RankPeriod> = [
    { title: '周期', dataIndex: 'period', width: 90,
      render: (v: string) => <a onClick={() => setPeriod(v)} style={{ fontWeight: v === sel ? 700 : 400 }}>{v}</a> },
    { title: '冠军产品', dataIndex: 'champion_name', ellipsis: true, render: (v: string | null) => v || '-' },
    { title: isProfit ? '冠军利润率' : metric === 'qty' ? '冠军销量' : '冠军销售额', key: 'champ', width: 110, align: 'right',
      render: (_: unknown, r) => isProfit
        ? <b style={{ color: profitColor(r.champion_profit) }}>{pct(r.champion_profit_rate)}</b>
        : metric === 'qty'
          ? <b>{r.champion_qty} 件</b> : <b>{yuan(r.champion_revenue)}</b> },
    { title: '本期合计', key: 'total', width: 120, align: 'right',
      render: (_: unknown, r) => isProfit
        ? <span style={{ color: profitColor(r.total_profit) }}>{yuan(r.total_profit ?? 0)}</span>
        : metric === 'qty' ? `${r.total_qty} 件` : yuan(r.total_revenue) },
    { title: '产品数', dataIndex: 'product_kinds', width: 70, align: 'right' },
  ];

  return (
    <Space direction="vertical" style={{ width: '100%' }} size="middle">
      <Typography.Title level={4} style={{ margin: 0 }}>销售排行榜</Typography.Title>

      <RefillCallout />

      <Card size="small">
        <Space wrap size="large">
          {!controlled && (
          <Space>
            <span>统计粒度:</span>
            <Segmented
              value={granularity} onChange={onGranularity}
              options={[{ label: '按月', value: 'month' }, { label: '按年', value: 'year' }]}
            />
          </Space>
          )}
          <Space>
            <span>排行依据:</span>
            <Segmented
              value={metric} onChange={(v) => setMetric(v as RankMetric)}
              options={[
                { label: '销售额', value: 'revenue' },
                { label: '销量', value: 'qty' },
                { label: '利润率', value: 'profit' },
              ]}
            />
          </Space>
          {!controlled && (
          <Space>
            <span>周期:</span>
            <Select
              style={{ width: 130 }} value={sel ?? undefined} loading={isLoading}
              onChange={(v) => setPeriod(v)}
              options={periods.map((p) => ({ label: p.period, value: p.period }))}
            />
          </Space>
          )}
        </Space>
      </Card>

      {champ && (
        <Row gutter={12}>
          <Col xs={24} sm={12} md={10}>
            <Card size="small" style={{ borderColor: '#fde68a' }}>
              <Statistic
                title={`🏆 ${gLabel}${isProfit ? '利润率' : ''}冠军 · ${sel}`}
                value={champ.champion_name ?? '-'}
                valueStyle={{ fontSize: 16, color: '#d46b08' }}
              />
              <div style={{ marginTop: 6, color: '#888' }}>
                {metric === 'qty'
                  ? `销量 ${champ.champion_qty} 件`
                  : metric === 'revenue'
                    ? `销售额 ${yuan(champ.champion_revenue)}`
                    : `利润率 ${pct(champ.champion_profit_rate)} · 利润 ${yuan(champ.champion_profit ?? 0)}`}
              </div>
            </Card>
          </Col>
          {isProfit ? (
            <>
              <Col xs={12} sm={8} md={7}><Card size="small"><Statistic title={`${sel} 总利润`} value={champ.total_profit ?? 0} precision={0} prefix="¥" valueStyle={{ color: profitColor(champ.total_profit) }} /></Card></Col>
              <Col xs={12} sm={8} md={7}><Card size="small"><Statistic title={`${sel} 总利润率`} value={(champ.total_profit_rate ?? 0) * 100} precision={1} suffix="%" /></Card></Col>
            </>
          ) : (
            <>
              <Col xs={12} sm={8} md={7}><Card size="small"><Statistic title={`${sel} 总销售额`} value={champ.total_revenue} precision={0} prefix="¥" /></Card></Col>
              <Col xs={12} sm={8} md={7}><Card size="small"><Statistic title={`${sel} 总销量`} value={champ.total_qty} suffix="件" /></Card></Col>
            </>
          )}
        </Row>
      )}

      {isProfit && (
        <Alert
          type="info" showIcon
          message="利润率 = 净利 ÷ 销售额; 净利 = 实付−退款−成本(商品/物流/安装/平台扣点/税/售后), 与逐单核对同口径。此处为各产品逐单净利之和, 不含推广/人员/固定成本 (那些是整体费用, 见月度经营)。"
        />
      )}

      {data && (data.excluded_non_product > 0 || data.refund_excluded) && (
        <Alert
          type="info" showIcon
          message={[
            data.excluded_non_product > 0
              ? `已排除 ${data.excluded_non_product} 笔 补差价/邮费/专拍 等非产品订单 (不计入排行)`
              : null,
            data.refund_excluded ? '销售额 = 买家实付 − 退款 (已去除退款)' : null,
          ].filter(Boolean).join('　·　')}
        />
      )}

      <Row gutter={12}>
        <Col xs={24} sm={14}>
          <Card size="small" title={`${sel ?? ''} 产品排行 (Top 30)`}>
            {isMobile ? (
              <MobileRankList rows={data?.ranking ?? []} metric={metric} />
            ) : (
              <PresetTable<RankRow>
                tableKey="sales_ranking"
                rowKey="rank" size="small" loading={isLoading}
                dataSource={data?.ranking ?? []} pagination={false}
                scroll={{ y: 520 }}
                columns={rankCols}
              />
            )}
          </Card>
        </Col>
        <Col xs={24} sm={12} md={10}>
          <Card size="small" title="冠军时间线 (点周期查看该期排行)">
            {isMobile ? (
              <MobileChampionList periods={periods} sel={sel} metric={metric} onPick={setPeriod} />
            ) : (
              <Table<RankPeriod>
                rowKey="period" size="small" loading={isLoading}
                dataSource={periods} pagination={false}
                scroll={{ y: 520 }}
                columns={periodCols}
                rowClassName={(r) => (r.period === sel ? 'ant-table-row-selected' : '')}
              />
            )}
          </Card>
        </Col>
      </Row>
    </Space>
  );
}
