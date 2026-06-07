import { useState } from 'react';
import {
  Alert, Button, Card, Col, Empty, InputNumber, Modal, Row, Space, Spin, Statistic,
  Table, Tag, Tooltip, Typography, message,
} from 'antd';
import {
  EditOutlined, ReloadOutlined, ArrowUpOutlined, ArrowDownOutlined,
} from '@ant-design/icons';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  CashFlowFreshness, CashFlowLine, CashFlowSummary, getCashFlow, updateCashFlowSettings,
} from '../api/finance';

const { Title, Text } = Typography;

function money(v: string | number) {
  const n = typeof v === 'string' ? Number(v) : v;
  return `¥${n.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

const FRESH_META: Record<string, { color: string; dot: string; text: string }> = {
  fresh: { color: 'success', dot: '🟢', text: '较新' },
  aging: { color: 'warning', dot: '🟡', text: '偏旧' },
  stale: { color: 'error', dot: '🔴', text: '已过期' },
  unknown: { color: 'default', dot: '⚪', text: '无数据' },
};

function FreshnessBadge({ f }: { f: CashFlowFreshness }) {
  const meta = FRESH_META[f.status] || FRESH_META.unknown;
  const ago = f.days_ago == null ? '—' : f.days_ago === 0 ? '今天' : `${f.days_ago} 天前`;
  const asOf = f.as_of ? new Date(f.as_of).toLocaleDateString('zh-CN') : '无记录';
  return (
    <Tooltip title={`数据截至 ${asOf}`}>
      <Tag color={meta.color} style={{ fontSize: 13, padding: '2px 10px', marginBottom: 6 }}>
        {meta.dot} {f.source} · {ago}
      </Tag>
    </Tooltip>
  );
}

function LineTable({ lines, kind }: { lines: CashFlowLine[]; kind: 'add' | 'sub' }) {
  const color = kind === 'add' ? '#389e0d' : '#cf1322';
  const sign = kind === 'add' ? '+' : '−';
  return (
    <Table<CashFlowLine>
      rowKey="key"
      dataSource={lines}
      pagination={false}
      size="small"
      columns={[
        {
          title: '项目', dataIndex: 'label', render: (v, r) => (
            <Space size={4}>
              <Text>{v}</Text>
              {r.manual && <Tag color="blue" style={{ marginLeft: 2 }}>手动</Tag>}
            </Space>
          ),
        },
        { title: '来源', dataIndex: 'source', render: (v) => <Text type="secondary" style={{ fontSize: 12 }}>{v}</Text> },
        {
          title: '金额', dataIndex: 'amount', align: 'right' as const,
          render: (v) => <Text strong style={{ color }}>{sign} {money(v)}</Text>,
        },
      ]}
    />
  );
}

export default function CashFlowPage() {
  const qc = useQueryClient();
  const [editOpen, setEditOpen] = useState(false);
  const [deposit, setDeposit] = useState<number | null>(null);
  const [investment, setInvestment] = useState<number | null>(null);

  const { data, isLoading } = useQuery<CashFlowSummary>({
    queryKey: ['cash-flow'],
    queryFn: getCashFlow,
    refetchInterval: 60_000,
  });

  const saveMut = useMutation({
    mutationFn: () => updateCashFlowSettings({
      shop_deposit: deposit == null ? undefined : String(deposit),
      total_investment: investment == null ? undefined : String(investment),
    }),
    onSuccess: (fresh) => {
      qc.setQueryData(['cash-flow'], fresh);
      setEditOpen(false);
      message.success('已更新并重新测算');
    },
    onError: () => message.error('更新失败'),
  });

  if (isLoading || !data) {
    return <div style={{ display: 'flex', justifyContent: 'center', padding: 64 }}><Spin size="large" /></div>;
  }

  const hasStale = data.freshness.some((f) => f.status === 'stale');
  const totalNum = Number(data.total);

  const openEdit = () => {
    const dep = data.additions.find((a) => a.key === 'shop_deposit');
    setDeposit(dep ? Number(dep.amount) : 0);
    // 总投资已移出减项, 单列"投资回收"块
    setInvestment(data.investment ? Number(data.investment.total_investment) : 0);
    setEditOpen(true);
  };

  return (
    <div style={{ width: '100%' }}>
      <Row justify="space-between" align="middle" style={{ marginBottom: 16 }}>
        <Title level={4} style={{ margin: 0 }}>剩余流水 · 可用资金</Title>
        <Space>
          <Button icon={<EditOutlined />} onClick={openEdit}>编辑手动项</Button>
          <Button icon={<ReloadOutlined />} onClick={() => qc.invalidateQueries({ queryKey: ['cash-flow'] })}>
            刷新
          </Button>
        </Space>
      </Row>

      {/* 主数字 */}
      <Card style={{ marginBottom: 16, background: 'linear-gradient(135deg,#0f2d4a,#15466e)', border: 'none' }}>
        <Row align="middle" gutter={24}>
          <Col flex="auto">
            <Text style={{ color: 'rgba(255,255,255,0.75)', fontSize: 14 }}>当前剩余流水（实时测算）</Text>
            <div style={{
              color: totalNum >= 0 ? '#5cdb95' : '#ff7875',
              fontSize: 46, fontWeight: 700, lineHeight: 1.1, marginTop: 4,
            }}>
              {money(data.total)}
            </div>
            <Space size="large" style={{ marginTop: 8 }}>
              <span style={{ color: 'rgba(255,255,255,0.85)' }}>
                <ArrowUpOutlined /> 加项合计 {money(data.total_additions)}
              </span>
              <span style={{ color: 'rgba(255,255,255,0.85)' }}>
                <ArrowDownOutlined /> 减项合计 {money(data.total_subtractions)}
              </span>
            </Space>
          </Col>
        </Row>
        {/* 数据新鲜度红绿灯 */}
        <div style={{ marginTop: 16, paddingTop: 12, borderTop: '1px solid rgba(255,255,255,0.15)' }}>
          <Text style={{ color: 'rgba(255,255,255,0.7)', fontSize: 12, marginRight: 8 }}>数据截至：</Text>
          {data.freshness.map((f) => <FreshnessBadge key={f.source} f={f} />)}
        </div>
      </Card>

      {hasStale && (
        <Alert
          type="warning" showIcon style={{ marginBottom: 16 }}
          message="部分数据已过期（超过 31 天未更新），上方数字可能不准确"
          description="建议按红色标记把对应数据导入 / 更新一遍，数字会自动刷新。"
        />
      )}

      <Row gutter={16}>
        <Col xs={24} lg={12}>
          <Card title="加项" size="small" headStyle={{ color: '#389e0d' }} style={{ marginBottom: 16 }}>
            <LineTable lines={data.additions} kind="add" />
            <Statistic
              style={{ marginTop: 12, textAlign: 'right' }}
              valueStyle={{ color: '#389e0d', fontSize: 18 }}
              prefix="合计 +" value={money(data.total_additions)}
            />
          </Card>
        </Col>
        <Col xs={24} lg={12}>
          <Card title="减项" size="small" headStyle={{ color: '#cf1322' }} style={{ marginBottom: 16 }}>
            <LineTable lines={data.subtractions} kind="sub" />
            <Statistic
              style={{ marginTop: 12, textAlign: 'right' }}
              valueStyle={{ color: '#cf1322', fontSize: 18 }}
              prefix="合计 −" value={money(data.total_subtractions)}
            />
          </Card>
        </Col>
      </Row>

      {/* 投资回收: 总投资 ↔ 累计总利润 (总投资是沉没本金, 不进可用资金, 单列对比) */}
      {data.investment && (
        <Card title="投资回收（总投资 ↔ 累计总利润）" size="small" style={{ marginBottom: 16 }}>
          <Row gutter={24}>
            <Col xs={12} md={6}>
              <Statistic title="总投资费用" value={money(data.investment.total_investment)} valueStyle={{ fontSize: 20 }} />
            </Col>
            <Col xs={12} md={6}>
              <Statistic title="累计总利润" value={money(data.investment.total_profit)}
                valueStyle={{ fontSize: 20, color: '#389e0d' }} />
            </Col>
            <Col xs={12} md={6}>
              <Statistic title="回收率"
                value={data.investment.recovery_rate == null ? '—'
                  : `${(data.investment.recovery_rate * 100).toFixed(1)}%`}
                valueStyle={{ fontSize: 20, color: data.investment.recovered ? '#389e0d' : '#d46b08' }} />
            </Col>
            <Col xs={12} md={6}>
              <Statistic
                title={data.investment.recovered ? '已回本 · 超出' : '距回本还差'}
                value={money(Math.abs(Number(data.investment.remaining)))}
                valueStyle={{ fontSize: 20, color: data.investment.recovered ? '#389e0d' : '#d46b08' }} />
            </Col>
          </Row>
          {data.investment.profit_detail.orders_missing_cost > 0 && (
            <Alert
              type="warning" showIcon style={{ marginTop: 12 }}
              message={`有 ${data.investment.profit_detail.orders_missing_cost} 单缺成本(未反推理论成本)，按 0 计入 → 累计总利润偏高`}
              description="可在 订单/成本 反推理论成本后更准。"
            />
          )}
          <Text type="secondary" style={{ fontSize: 12, display: 'block', marginTop: 8 }}>
            说明：总投资是沉没本金，不计入上方「可用资金」；这里单独与累计利润对比看是否回本。
            累计利润口径 = 真实销售(非补单/非取消) 营收 − 成本 − 售后费用。
          </Text>
        </Card>
      )}

      <Modal
        title="编辑手动项" open={editOpen} onCancel={() => setEditOpen(false)}
        onOk={() => saveMut.mutate()} confirmLoading={saveMut.isPending} okText="保存并测算"
      >
        <Space direction="vertical" style={{ width: '100%' }} size="large">
          <div>
            <Text>店铺保证金</Text>
            <InputNumber
              style={{ width: '100%' }} value={deposit} onChange={setDeposit}
              min={0} precision={2} addonBefore="¥"
              formatter={(v) => `${v}`.replace(/\B(?=(\d{3})+(?!\d))/g, ',')}
              parser={(v) => Number(`${v}`.replace(/,/g, '')) as any}
            />
          </div>
          <div>
            <Text>总投资费用</Text>
            <InputNumber
              style={{ width: '100%' }} value={investment} onChange={setInvestment}
              min={0} precision={2} addonBefore="¥"
              formatter={(v) => `${v}`.replace(/\B(?=(\d{3})+(?!\d))/g, ',')}
              parser={(v) => Number(`${v}`.replace(/,/g, '')) as any}
            />
            <Text type="secondary" style={{ fontSize: 12 }}>建议每月更新一次；超过 31 天未改会在上方变红提醒。</Text>
          </div>
        </Space>
      </Modal>

      {data.additions.length === 0 && <Empty />}
    </div>
  );
}
