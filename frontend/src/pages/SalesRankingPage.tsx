/**
 * 销售排行榜 — 按月/按年, 分产品的 销量/销售额 排行 + 每期冠军时间线。
 * 口径: 正式销售 (不含补单/补差价/邮费专链)。销售额 = 买家实付。
 */
import { useState } from 'react';
import {
  Alert, Card, Col, Grid, Row, Segmented, Select, Space, Statistic, Table, Tag, Typography,
} from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { useQuery } from '@tanstack/react-query';
import { RankPeriod, RankRow, fetchSalesRanking } from '../api/reports';
import PresetTable from '../components/PresetTable';
import RefillCallout from '../components/RefillCallout';

const yuan = (v: number) => `¥${Number(v || 0).toLocaleString('zh-CN', { maximumFractionDigits: 0 })}`;
const medal = (r: number) => (r === 1 ? '🥇' : r === 2 ? '🥈' : r === 3 ? '🥉' : `#${r}`);

// 手机端: 排行榜用「列表」而非挤压的表格 (研究结论: ranked content 用 list)。产品名为主, 名次徽章 + 主指标右侧高亮。
function MobileRankList({ rows, metric }: { rows: RankRow[]; metric: 'revenue' | 'qty' }) {
  if (!rows.length) return <div style={{ padding: 24, textAlign: 'center', color: '#94a3b8' }}>暂无数据</div>;
  return (
    <div>
      {rows.map((row) => {
        const sub = metric === 'revenue'
          ? `销量 ${row.qty} 件 · 订单 ${row.order_count} 单`
          : `销售额 ${yuan(row.revenue)} · 订单 ${row.order_count} 单`;
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
            <div style={{ flexShrink: 0, fontWeight: 700, color: '#1677ff', fontSize: 15 }}>
              {metric === 'revenue' ? yuan(row.revenue) : `${row.qty} 件`}
            </div>
          </div>
        );
      })}
    </div>
  );
}

// 手机端: 冠军时间线列表 (点周期切换该期排行)
function MobileChampionList({ periods, sel, metric, onPick }: { periods: RankPeriod[]; sel: string | null; metric: 'revenue' | 'qty'; onPick: (p: string) => void }) {
  if (!periods.length) return <div style={{ padding: 24, textAlign: 'center', color: '#94a3b8' }}>暂无数据</div>;
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
            <div style={{ fontWeight: 600, fontSize: 13 }}>{metric === 'qty' ? `${p.champion_qty} 件` : yuan(p.champion_revenue)}</div>
            <div style={{ fontSize: 12, color: '#94a3b8' }}>合计 {metric === 'qty' ? `${p.total_qty} 件` : yuan(p.total_revenue)}</div>
          </div>
        </div>
      ))}
    </div>
  );
}

export default function SalesRankingPage() {
  const [granularity, setGranularity] = useState<'month' | 'year'>('month');
  const [metric, setMetric] = useState<'revenue' | 'qty'>('revenue');
  const [period, setPeriod] = useState<string | undefined>(undefined);
  const screens = Grid.useBreakpoint();
  const isMobile = screens.md === false;

  const { data, isLoading } = useQuery({
    queryKey: ['sales-ranking', granularity, metric, period],
    queryFn: () => fetchSalesRanking({ granularity, metric, period, limit: 30 }),
  });

  const periods = data?.periods ?? [];
  const sel = data?.selected_period ?? null;
  const champ = periods.find((p) => p.period === sel);
  const gLabel = granularity === 'year' ? '年度' : '月度';

  const onGranularity = (v: string) => { setGranularity(v as 'month' | 'year'); setPeriod(undefined); };

  const rankCols: ColumnsType<RankRow> = [
    { title: '名次', dataIndex: 'rank', width: 70, align: 'center',
      render: (r: number) => <span style={{ fontSize: r <= 3 ? 20 : 14 }}>{medal(r)}</span> },
    { title: '产品', dataIndex: 'product_name', ellipsis: true,
      render: (v: string, row) => (
        <span>{v}{row.product_code ? <Tag style={{ marginLeft: 6 }}>{row.product_code}</Tag> : null}</span>
      ) },
    { title: '销量', dataIndex: 'qty', width: 100, align: 'right',
      render: (v: number) => metric === 'qty'
        ? <b style={{ color: '#1677ff' }}>{v} 件</b> : `${v} 件` },
    { title: '销售额', dataIndex: 'revenue', width: 130, align: 'right',
      render: (v: number) => metric === 'revenue'
        ? <b style={{ color: '#1677ff' }}>{yuan(v)}</b> : yuan(v) },
    { title: '订单数', dataIndex: 'order_count', width: 80, align: 'right' },
  ];

  const periodCols: ColumnsType<RankPeriod> = [
    { title: '周期', dataIndex: 'period', width: 90,
      render: (v: string) => <a onClick={() => setPeriod(v)} style={{ fontWeight: v === sel ? 700 : 400 }}>{v}</a> },
    { title: '冠军产品', dataIndex: 'champion_name', ellipsis: true, render: (v: string | null) => v || '-' },
    { title: metric === 'qty' ? '冠军销量' : '冠军销售额', key: 'champ', width: 110, align: 'right',
      render: (_: unknown, r) => metric === 'qty'
        ? <b>{r.champion_qty} 件</b> : <b>{yuan(r.champion_revenue)}</b> },
    { title: '本期合计', key: 'total', width: 120, align: 'right',
      render: (_: unknown, r) => metric === 'qty' ? `${r.total_qty} 件` : yuan(r.total_revenue) },
    { title: '产品数', dataIndex: 'product_kinds', width: 70, align: 'right' },
  ];

  return (
    <Space direction="vertical" style={{ width: '100%' }} size="middle">
      <Typography.Title level={4} style={{ margin: 0 }}>销售排行榜</Typography.Title>

      <RefillCallout />

      <Card size="small">
        <Space wrap size="large">
          <Space>
            <span>统计粒度:</span>
            <Segmented
              value={granularity} onChange={onGranularity}
              options={[{ label: '按月', value: 'month' }, { label: '按年', value: 'year' }]}
            />
          </Space>
          <Space>
            <span>排行依据:</span>
            <Segmented
              value={metric} onChange={(v) => setMetric(v as 'revenue' | 'qty')}
              options={[{ label: '销售额', value: 'revenue' }, { label: '销量', value: 'qty' }]}
            />
          </Space>
          <Space>
            <span>周期:</span>
            <Select
              style={{ width: 130 }} value={sel ?? undefined} loading={isLoading}
              onChange={(v) => setPeriod(v)}
              options={periods.map((p) => ({ label: p.period, value: p.period }))}
            />
          </Space>
        </Space>
      </Card>

      {champ && (
        <Row gutter={12}>
          <Col xs={24} sm={12} md={10}>
            <Card size="small" style={{ borderColor: '#fde68a' }}>
              <Statistic
                title={`🏆 ${gLabel}冠军 · ${sel}`}
                value={champ.champion_name ?? '-'}
                valueStyle={{ fontSize: 16, color: '#d46b08' }}
              />
              <div style={{ marginTop: 6, color: '#888' }}>
                {metric === 'qty'
                  ? `销量 ${champ.champion_qty} 件`
                  : `销售额 ${yuan(champ.champion_revenue)}`}
              </div>
            </Card>
          </Col>
          <Col xs={12} sm={8} md={7}><Card size="small"><Statistic title={`${sel} 总销售额`} value={champ.total_revenue} precision={0} prefix="¥" /></Card></Col>
          <Col xs={12} sm={8} md={7}><Card size="small"><Statistic title={`${sel} 总销量`} value={champ.total_qty} suffix="件" /></Card></Col>
        </Row>
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
