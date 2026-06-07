/**
 * 销售排行榜 — 按月/按年, 分产品的 销量/销售额 排行 + 每期冠军时间线。
 * 口径: 正式销售 (不含补单/补差价/邮费专链)。销售额 = 买家实付。
 */
import { useState } from 'react';
import {
  Alert, Card, Col, Row, Segmented, Select, Space, Statistic, Table, Tag, Typography,
} from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { useQuery } from '@tanstack/react-query';
import { RankPeriod, RankRow, fetchSalesRanking } from '../api/reports';

const yuan = (v: number) => `¥${Number(v || 0).toLocaleString('zh-CN', { maximumFractionDigits: 0 })}`;
const medal = (r: number) => (r === 1 ? '🥇' : r === 2 ? '🥈' : r === 3 ? '🥉' : `#${r}`);

export default function SalesRankingPage() {
  const [granularity, setGranularity] = useState<'month' | 'year'>('month');
  const [metric, setMetric] = useState<'revenue' | 'qty'>('revenue');
  const [period, setPeriod] = useState<string | undefined>(undefined);

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
          <Col span={10}>
            <Card size="small" style={{ background: 'linear-gradient(135deg,#fff7e6,#fffbe6)' }}>
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
          <Col span={7}><Card size="small"><Statistic title={`${sel} 总销售额`} value={champ.total_revenue} precision={0} prefix="¥" /></Card></Col>
          <Col span={7}><Card size="small"><Statistic title={`${sel} 总销量`} value={champ.total_qty} suffix="件" /></Card></Col>
        </Row>
      )}

      {data && data.excluded_non_product > 0 && (
        <Alert
          type="info" showIcon
          message={`已排除 ${data.excluded_non_product} 笔 补差价/邮费/专拍 等非产品订单 (不计入排行)`}
        />
      )}

      <Row gutter={12}>
        <Col span={14}>
          <Card size="small" title={`${sel ?? ''} 产品排行 (Top 30)`}>
            <Table<RankRow>
              rowKey="rank" size="small" loading={isLoading}
              dataSource={data?.ranking ?? []} pagination={false}
              scroll={{ y: 520 }}
              columns={rankCols}
            />
          </Card>
        </Col>
        <Col span={10}>
          <Card size="small" title="冠军时间线 (点周期查看该期排行)">
            <Table<RankPeriod>
              rowKey="period" size="small" loading={isLoading}
              dataSource={periods} pagination={false}
              scroll={{ y: 520 }}
              columns={periodCols}
              rowClassName={(r) => (r.period === sel ? 'ant-table-row-selected' : '')}
            />
          </Card>
        </Col>
      </Row>
    </Space>
  );
}
