/**
 * 财务概览面板 — 原在运营大盘, 2026-07-01 移到「报表」页顶部。
 * 自带时间段 (今日/昨日/近7天/近30天/选月份), 查 /api/dashboard/finance-overview。
 */
import { useState } from 'react';
import { Card, Col, DatePicker, Grid, Row, Segmented, Space, Spin, Statistic, Tag, Typography } from 'antd';
import dayjs from 'dayjs';
import { useNavigate } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { api } from '../api/client';

const M = { emerald: '#10b981', amber: '#f59e0b', rose: '#f43f5e', ink: '#1e293b' };
const cardStyle = { borderRadius: 16, border: '1px solid #eef0f4', boxShadow: '0 1px 2px rgba(15,23,42,.04)' };
const midNum = { color: M.ink, fontWeight: 700, fontSize: 20 };
const sectionTitle = { color: M.ink, fontWeight: 700, margin: '4px 2px 10px' };
function MCard({ children, style, ...rest }: any) {
  return <Card size="small" bordered={false} style={{ ...cardStyle, ...style }} {...rest}>{children}</Card>;
}
function money(v: number) {
  return `¥${Number(v).toLocaleString('zh-CN', { maximumFractionDigits: 0 })}`;
}

export default function FinanceOverviewPanel() {
  const nav = useNavigate();
  const screens = Grid.useBreakpoint();
  const isMobile = screens.md === false;
  const navProps = (path: string) => (isMobile ? {} : { onClick: () => nav(path), style: { cursor: 'pointer' as const } });
  const [finPeriod, setFinPeriod] = useState<string>('30d');
  const { data: fin, isLoading } = useQuery({
    queryKey: ['finance-overview', finPeriod],
    queryFn: () => api.get('/api/dashboard/finance-overview', { params: { period: finPeriod } }).then((r) => r.data),
    refetchInterval: 600_000,
  });

  return (
    <div style={{ marginBottom: 16 }}>
      <Space wrap style={{ marginBottom: 8 }}>
        <Typography.Title level={5} style={{ ...sectionTitle, margin: 0 }}>财务概览</Typography.Title>
        <Segmented size="small"
          value={['today', 'yesterday', '7d', '30d'].includes(finPeriod) ? finPeriod : ''}
          onChange={(v) => setFinPeriod(v as string)}
          options={[{ label: '今日', value: 'today' }, { label: '昨日', value: 'yesterday' }, { label: '近7天', value: '7d' }, { label: '近30天', value: '30d' }]} />
        <DatePicker picker="month" size="small" placeholder="选月份" allowClear
          value={/^\d{4}-\d{2}$/.test(finPeriod) ? dayjs(finPeriod + '-01') : null}
          onChange={(d) => setFinPeriod(d ? d.format('YYYY-MM') : '30d')} />
      </Space>
      {isLoading || !fin ? (
        <MCard><div style={{ height: 120, display: 'flex', alignItems: 'center', justifyContent: 'center' }}><Spin /></div></MCard>
      ) : (
        <>
          <Row gutter={[16, 16]} style={{ marginBottom: 16 }}>
            <Col xs={12} lg={6}>
              <MCard {...navProps('/orders')}><Statistic title="订单收入" value={fin.order_revenue} formatter={(v) => money(Number(v))} valueStyle={midNum} /></MCard>
            </Col>
            <Col xs={12} lg={6}>
              <MCard {...navProps('/pricing')}><Statistic title="理论成本" value={fin.theoretical_cost} formatter={(v) => money(Number(v))} valueStyle={midNum} /></MCard>
            </Col>
            <Col xs={12} lg={6}>
              <MCard {...navProps('/recon-center')}><Statistic title="实际成本" value={fin.actual_cost} formatter={(v) => money(Number(v))} valueStyle={midNum} /></MCard>
            </Col>
            <Col xs={12} lg={6}>
              <MCard {...navProps('/assets-cashflow')}>
                <Statistic title="毛利" value={fin.gross_profit} formatter={(v) => money(Number(v))}
                  valueStyle={{ ...midNum, color: fin.gross_profit >= 0 ? M.emerald : M.rose }} />
                <Tag style={{ marginTop: 8, borderRadius: 8 }}
                  color={fin.gross_margin_rate >= 0.15 ? 'success' : fin.gross_margin_rate >= 0 ? 'warning' : 'error'}>
                  毛利率 {(fin.gross_margin_rate * 100).toFixed(1)}%
                </Tag>
              </MCard>
            </Col>
          </Row>
          <Row gutter={[16, 16]}>
            <Col xs={12} lg={6}>
              <MCard {...navProps('/alipay')}><Statistic title="支付宝收入" value={fin.alipay_income} formatter={(v) => money(Number(v))} valueStyle={midNum} /></MCard>
            </Col>
            <Col xs={12} lg={6}>
              <MCard {...navProps('/recon-center')}><Statistic title="对账未清" value={fin.reconciliation_unresolved} suffix="条"
                valueStyle={{ ...midNum, color: fin.reconciliation_unresolved > 0 ? M.amber : M.emerald }} /></MCard>
            </Col>
            <Col xs={12} lg={6}>
              <MCard {...navProps('/aftersales')}><Statistic title="售后笔数" value={fin.aftersales_count} suffix="单" valueStyle={midNum} /></MCard>
            </Col>
            <Col xs={12} lg={6}>
              <MCard {...navProps('/aftersales')}><Statistic title="售后成本" value={fin.aftersales_cost} formatter={(v) => money(Number(v))} valueStyle={{ ...midNum, color: M.rose }} /></MCard>
            </Col>
          </Row>
        </>
      )}
    </div>
  );
}
