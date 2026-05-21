import {
  Alert,
  Card,
  Col,
  Empty,
  Row,
  Space,
  Spin,
  Statistic,
  Table,
  Tabs,
  Tag,
  Typography,
} from 'antd';
import { useQuery } from '@tanstack/react-query';
import { ReconciliationDiff, ReconciliationResult, runReconciliation } from '../api/client';

const RULE_LABELS: Record<string, { label: string; desc: string }> = {
  factory_payment: { label: '货款对账', desc: '工厂应付 ↔ 支付宝(工厂付款)' },
  install_fee: { label: '安装费收支', desc: '万师傅 CSV ↔ 售后表.安装费' },
  promotion: { label: '推广支出', desc: '推广记录.投入 ↔ 支付宝(推广)' },
  refill_compensation: { label: '补单赔实付', desc: '补单总成本 ↔ 主订单实付差额' },
  inventory_value: { label: '库存资产', desc: '配件库存 × 物料单价 = 账面价值' },
  logistics_fee: { label: '物流费销项', desc: '万师傅月结 ↔ 订单.运费汇总' },
};

const SEVERITY_COLOR: Record<string, string> = {
  ok: 'green',
  warning: 'orange',
  error: 'red',
  not_available: 'default',
};

export default function ReconciliationPage() {
  const { data, isLoading } = useQuery({
    queryKey: ['reconciliation'],
    queryFn: () => runReconciliation() as Promise<Record<string, ReconciliationResult>>,
  });

  if (isLoading) return <Spin />;
  if (!data) return <Empty />;

  return (
    <Space direction="vertical" style={{ width: '100%' }} size="middle">
      <Typography.Title level={4} style={{ margin: 0 }}>
        财务对账面板 — plan §8 六条规则
      </Typography.Title>

      <Row gutter={[12, 12]}>
        {Object.entries(data).map(([rule, res]) => (
          <Col span={8} key={rule}>
            <Card size="small" title={
              <Space>
                <span>{RULE_LABELS[rule]?.label ?? rule}</span>
                {res.error_count > 0 && <Tag color="red">{res.error_count} 严重</Tag>}
                {res.warning_count > 0 && <Tag color="orange">{res.warning_count} 提示</Tag>}
              </Space>
            }>
              <Space>
                <Statistic title="OK" value={res.ok_count} valueStyle={{ color: '#3f8600' }} />
                <Statistic title="差异" value={res.warning_count + res.error_count} valueStyle={{ color: res.error_count > 0 ? '#cf1322' : '#d4b106' }} />
                <Statistic title="总计" value={res.total_diffs} />
              </Space>
              <div style={{ marginTop: 8, color: '#999', fontSize: 12 }}>
                {RULE_LABELS[rule]?.desc}
              </div>
            </Card>
          </Col>
        ))}
      </Row>

      <Tabs
        items={Object.entries(data).map(([rule, res]) => ({
          key: rule,
          label: RULE_LABELS[rule]?.label ?? rule,
          children: <DiffTable rule={rule} result={res} />,
        }))}
      />
    </Space>
  );
}

function DiffTable({ rule, result }: { rule: string; result: ReconciliationResult }) {
  const notAvailable = result.diffs.length === 1 && result.diffs[0].severity === 'not_available';
  if (notAvailable) {
    return (
      <Alert
        type="info"
        showIcon
        message={result.diffs[0].message}
        description="可在后续 Phase 5/6 把 CSV 数据接入后启用"
      />
    );
  }

  const columns = [
    {
      title: '严重度',
      dataIndex: 'severity',
      width: 90,
      render: (v: string) => <Tag color={SEVERITY_COLOR[v] ?? 'default'}>{v}</Tag>,
    },
    { title: '业务键', dataIndex: 'key', width: 200, ellipsis: true,
      render: (v: string) => <code style={{ fontSize: 11 }}>{v}</code> },
    {
      title: '主表 (应/账面)',
      dataIndex: 'expected',
      width: 130,
      align: 'right' as const,
      render: (v: string | null) => v != null ? `¥${v}` : '-',
    },
    {
      title: '校验 (实/对照)',
      dataIndex: 'actual',
      width: 130,
      align: 'right' as const,
      render: (v: string | null) => v != null ? `¥${v}` : '-',
    },
    {
      title: '差额',
      dataIndex: 'diff',
      width: 110,
      align: 'right' as const,
      render: (v: string | null) =>
        v == null ? '-' : (
          <span style={{ color: Number(v) === 0 ? '#666' : Number(v) > 0 ? '#3f8600' : '#cf1322', fontWeight: 600 }}>
            ¥{v}
          </span>
        ),
    },
    { title: '说明', dataIndex: 'message', ellipsis: true },
  ];

  return (
    <Table<ReconciliationDiff>
      rowKey="key"
      dataSource={result.diffs}
      columns={columns as any}
      size="small"
      pagination={{ pageSize: 20 }}
    />
  );
}
