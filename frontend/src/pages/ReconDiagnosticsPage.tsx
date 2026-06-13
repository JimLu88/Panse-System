/**
 * 对账诊断 — 揭示"对账缺口在哪": 账户余额钩稽 / 孤儿流水(没人认领的钱) / 各账户流水覆盖。
 * 只读体检, 帮老板定位"该补哪批流水、哪些钱没归类、哪本余额表对不平"。
 */
import {
  Alert, Card, Col, Row, Space, Statistic, Table, Tag, Typography,
} from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { useQuery } from '@tanstack/react-query';
import { fetchReconDiagnostics } from '../api/settlements';
import ReconConfigCard from '../components/ReconConfigCard';

const yuan = (v: number | null | undefined) =>
  v == null ? '-' : `¥${Number(v).toLocaleString('zh-CN', { maximumFractionDigits: 0 })}`;

export default function ReconDiagnosticsPage() {
  const { data, isLoading } = useQuery({ queryKey: ['recon-diagnostics'], queryFn: fetchReconDiagnostics });

  const bc = data?.balance_check;
  const of = data?.orphan_flows;
  const cov = data?.coverage;

  const balCols: ColumnsType<NonNullable<typeof bc>['rows'][number]> = [
    { title: '账户', dataIndex: 'account_name', width: 140 },
    { title: '周期', dataIndex: 'period', width: 90 },
    { title: '期初', dataIndex: 'opening', width: 110, align: 'right', render: yuan },
    { title: '收入', dataIndex: 'income', width: 110, align: 'right', render: yuan },
    { title: '支出', dataIndex: 'expense', width: 110, align: 'right', render: yuan },
    { title: '期末(填报)', dataIndex: 'closing', width: 120, align: 'right', render: yuan },
    { title: '期末(应为)', dataIndex: 'expected_closing', width: 120, align: 'right', render: yuan },
    { title: '差额', dataIndex: 'diff', width: 110, align: 'right', render: (v: number) => <b style={{ color: '#cf1322' }}>{yuan(v)}</b> },
  ];

  const covCols: ColumnsType<NonNullable<typeof cov>['accounts'][number]> = [
    { title: '账户', dataIndex: 'account', width: 120 },
    { title: '流水笔数', dataIndex: 'total', width: 90, align: 'right' },
    { title: '有订单号', dataIndex: 'with_order', width: 90, align: 'right' },
    { title: '已核销', dataIndex: 'matched', width: 110, align: 'right',
      render: (v: number, r) => <span>{v} <Tag color={r.matched_pct >= 50 ? 'success' : 'warning'}>{r.matched_pct}%</Tag></span> },
    { title: '未归类', dataIndex: 'unclassified', width: 90, align: 'right',
      render: (v: number) => (v > 0 ? <span style={{ color: '#d46b08' }}>{v}</span> : v) },
    { title: '缺日期', dataIndex: 'no_date', width: 90, align: 'right',
      render: (v: number) => (v > 0 ? <span style={{ color: '#cf1322' }}>{v}</span> : v) },
  ];

  return (
    <Space direction="vertical" style={{ width: '100%' }} size="middle">
      <Typography.Title level={4} style={{ margin: 0 }}>对账诊断</Typography.Title>
      <Alert
        type="info" showIcon
        message="对账缺口体检 (只读)"
        description="①账户余额钩稽: 期初+收入−支出 应等于 期末, 对不平=填报有误/漏记。②孤儿流水: 既无订单又无归类的钱, 最该先认领。③各账户流水覆盖: 未归类/缺日期占比高=该账户没对全, 对账自然一片红。"
      />

      <ReconConfigCard />

      <Card size="small" title={`① 账户余额钩稽 (${bc?.unbalanced ?? 0}/${bc?.checked ?? 0} 对不平)`} loading={isLoading}>
        {bc && bc.unbalanced === 0 ? (
          <Alert type="success" showIcon message="全部账户余额表期初+收−支=期末, 钩稽通过。" />
        ) : (
          <Table rowKey={(r) => `${r.account_name}-${r.period}`} size="small" pagination={false}
            dataSource={bc?.rows ?? []} columns={balCols} scroll={{ x: 900 }} />
        )}
      </Card>

      <Card size="small" title="② 孤儿流水 (没人认领的钱)" loading={isLoading}>
        {of && (
          <Row gutter={12} style={{ marginBottom: 12 }}>
            <Col span={6}><Statistic title="孤儿笔数" value={of.orphan_count} suffix={`/ ${of.total_flows}`} valueStyle={{ color: of.orphan_count ? '#d46b08' : undefined }} /></Col>
            <Col span={6}><Statistic title="未认领收入" value={of.orphan_income} precision={0} prefix="¥" valueStyle={{ color: '#389e0d' }} /></Col>
            <Col span={6}><Statistic title="未认领支出" value={of.orphan_expense} precision={0} prefix="¥" valueStyle={{ color: '#cf1322' }} /></Col>
            <Col span={6}><div style={{ color: '#888', fontSize: 12 }}>按账户: {Object.entries(of.by_account).map(([k, v]) => `${k}:${v}`).join('  ') || '—'}</div></Col>
          </Row>
        )}
        <Table rowKey="transaction_no" size="small" pagination={{ defaultPageSize: 100, showSizeChanger: true, pageSizeOptions: [20, 50, 100, 200] }}
          dataSource={of?.samples ?? []}
          columns={[
            { title: '账户', dataIndex: 'account', width: 90 },
            { title: '交易时间', dataIndex: 'transaction_time', width: 160, render: (v: string | null) => (v ? new Date(v).toLocaleString('zh-CN') : <Tag color="warning">无日期</Tag>) },
            { title: '类型', dataIndex: 'transaction_type', width: 100 },
            { title: '金额', dataIndex: 'amount', width: 110, align: 'right' as const, render: (v: number) => <span style={{ color: v >= 0 ? '#389e0d' : '#cf1322' }}>{yuan(v)}</span> },
            { title: '对手方', dataIndex: 'counterparty', ellipsis: true },
            { title: '备注', dataIndex: 'remark', ellipsis: true },
          ]}
          scroll={{ x: 800 }} />
      </Card>

      <Card size="small" title="③ 各账户流水覆盖" loading={isLoading}>
        <Table rowKey="account" size="small" pagination={false}
          dataSource={cov?.accounts ?? []} columns={covCols} scroll={{ x: 620 }} />
      </Card>
    </Space>
  );
}
