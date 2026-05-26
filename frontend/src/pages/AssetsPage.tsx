/**
 * 资产汇总 + 饼图 + 公式核对 + 未核销异常池 (Phase 4, 业务需求 14/19).
 *
 * - 顶部 4 个 KPI: 总资产 / 公式 A / 公式 B / 差额
 * - 内联 SVG 饼图 (按类别)
 * - 差额 > 100 元时, 展开"未核销异常池"列表
 */
import {
  Alert,
  Card,
  Col,
  Row,
  Space,
  Statistic,
  Table,
  Tag,
  Typography,
} from 'antd';
import { useQuery } from '@tanstack/react-query';
import { fetchAssets, fetchUnmatchedFlows } from '../api/client';


export default function AssetsPage() {
  const { data, isLoading } = useQuery({
    queryKey: ['assets'], queryFn: fetchAssets, refetchInterval: 60000,
  });
  const { data: unmatched } = useQuery({
    queryKey: ['unmatched-flows'], queryFn: () => fetchUnmatchedFlows(7),
  });

  if (isLoading || !data) {
    return <Card loading />;
  }

  const diffAbs = Math.abs(data.diff);
  const hasGap = diffAbs > 100;

  return (
    <Space direction="vertical" style={{ width: '100%' }} size="middle">
      <Row gutter={12}>
        <Col span={6}>
          <Card size="small">
            <Statistic title="资产总额" value={data.total.toFixed(2)} suffix="元" />
          </Card>
        </Col>
        <Col span={6}>
          <Card size="small">
            <Statistic title="公式 A (账面)" value={data.formula_a.toFixed(2)} suffix="元" />
          </Card>
        </Col>
        <Col span={6}>
          <Card size="small">
            <Statistic title="公式 B (订单+余额)" value={data.formula_b.toFixed(2)} suffix="元" />
          </Card>
        </Col>
        <Col span={6}>
          <Card size="small">
            <Statistic
              title="差额 A - B"
              value={data.diff.toFixed(2)} suffix="元"
              valueStyle={{ color: hasGap ? '#cf1322' : '#3f8600' }}
            />
          </Card>
        </Col>
      </Row>

      {hasGap && (
        <Alert
          type="warning" showIcon
          message={`检测到账面差额 ${data.diff.toFixed(2)} 元`}
          description={(unmatched?.rows.length ?? 0) > 0
            ? `这部分可能藏在最近 7 天 ${unmatched?.rows.length} 条未核销流水里, 看下面表格.`
            : '账上没有未核销流水, 差额可能源自历史脏数据 (建议调整 期初余额 重置)'}
        />
      )}

      <Card size="small" title="资产分类">
        <Row>
          <Col span={12}>
            <Table
              size="small" rowKey="name" pagination={false}
              dataSource={data.categories}
              columns={[
                { title: '类别', dataIndex: 'name', width: 160 },
                { title: '金额 (元)', dataIndex: 'amount',
                  render: (v: number) => v.toFixed(2) },
                { title: '占比', render: (_: any, r: any) =>
                  data.total > 0 ?
                    <Tag>{((r.amount / data.total) * 100).toFixed(1)}%</Tag> : '-',
                },
              ]}
            />
          </Col>
          <Col span={12}>
            <PieChart categories={data.categories} total={data.total} />
          </Col>
        </Row>
      </Card>

      {(unmatched?.rows.length ?? 0) > 0 && (
        <Card size="small" title={`未核销异常池 (最近 ${unmatched?.days ?? 7} 天)`}>
          <Table
            size="small" rowKey="id"
            dataSource={unmatched?.rows ?? []}
            pagination={{ pageSize: 15 }}
            columns={[
              { title: '时间', dataIndex: 'transaction_time', width: 160 },
              { title: '交易号', dataIndex: 'transaction_no', width: 200 },
              { title: '对方', dataIndex: 'counterparty' },
              { title: '类型', dataIndex: 'transaction_type', width: 100 },
              { title: '金额', dataIndex: 'amount', width: 100,
                render: (v: number) => (
                  <span style={{ color: v < 0 ? '#cf1322' : '#3f8600' }}>
                    {v.toFixed(2)}
                  </span>
                ),
              },
              { title: '备注', dataIndex: 'remark', ellipsis: true },
            ]}
          />
        </Card>
      )}
    </Space>
  );
}

// 内联 SVG 饼图 (避免引第三方 chart 库)
function PieChart({ categories, total }: {
  categories: { name: string; amount: number }[]; total: number;
}) {
  const cx = 130, cy = 130, r = 100;
  const colors = ['#1677ff', '#52c41a', '#fa8c16', '#722ed1', '#13c2c2', '#f5222d'];
  let angle = 0;
  const slices = categories.filter((c) => c.amount > 0).map((c, i) => {
    const portion = total > 0 ? c.amount / total : 0;
    const startAngle = angle;
    const endAngle = angle + portion * 2 * Math.PI;
    angle = endAngle;
    const x1 = cx + r * Math.cos(startAngle);
    const y1 = cy + r * Math.sin(startAngle);
    const x2 = cx + r * Math.cos(endAngle);
    const y2 = cy + r * Math.sin(endAngle);
    const largeArc = portion > 0.5 ? 1 : 0;
    const path = `M${cx},${cy} L${x1},${y1} A${r},${r} 0 ${largeArc} 1 ${x2},${y2} Z`;
    // label 位置在弧中心
    const midAngle = (startAngle + endAngle) / 2;
    const lx = cx + (r * 0.6) * Math.cos(midAngle);
    const ly = cy + (r * 0.6) * Math.sin(midAngle);
    return { path, fill: colors[i % colors.length], name: c.name,
             pct: portion, lx, ly };
  });
  return (
    <svg width={320} height={260} viewBox="0 0 320 260">
      {slices.map((s, i) => (
        <g key={i}>
          <path d={s.path} fill={s.fill} stroke="#fff" strokeWidth={2} />
          {s.pct > 0.05 && (
            <text x={s.lx} y={s.ly} fontSize={11} fill="#fff"
                  textAnchor="middle">
              {(s.pct * 100).toFixed(0)}%
            </text>
          )}
        </g>
      ))}
      {slices.map((s, i) => (
        <g key={`legend-${i}`} transform={`translate(280, ${20 + i * 22})`}>
          <rect x={-110} y={-8} width={12} height={12} fill={s.fill} />
          <text x={-92} y={2} fontSize={11}>{s.name}</text>
        </g>
      ))}
    </svg>
  );
}
