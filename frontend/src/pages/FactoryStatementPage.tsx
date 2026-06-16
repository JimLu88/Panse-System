import { useEffect, useState } from 'react';
import { Button, Select, Space, Statistic, Table, Tag, Typography, message } from 'antd';
import { FileExcelOutlined } from '@ant-design/icons';
import type { ColumnsType } from 'antd/es/table';

const { Title, Text, Paragraph } = Typography;

interface StatementRow {
  order_no: string;
  order_date: string | null;
  product_name: string | null;
  sku: string | null;
  qty: number;
  is_custom: boolean;
  revenue: number | null;
  factory_predicted: number | null;
  break_even_factory: number | null;
  break_even_buffer: number | null;
  note: string | null;
}
interface Statement {
  period: string | null;
  count: number;
  missing: number;
  totals: {
    revenue: number;
    factory_predicted: number;
    break_even_factory: number;
    break_even_buffer: number;
  };
  rows: StatementRow[];
}

function authHeaders(): Record<string, string> {
  const t = localStorage.getItem('panse_token');
  return t ? { Authorization: `Bearer ${t}` } : {};
}

const yuan = (v: number | null) => (v == null ? '—' : `¥${Math.round(v).toLocaleString()}`);

const cols: ColumnsType<StatementRow> = [
  { title: '订单号', dataIndex: 'order_no', key: 'order_no', width: 150, fixed: 'left' },
  { title: '日期', dataIndex: 'order_date', key: 'order_date', width: 100 },
  {
    title: '产品',
    dataIndex: 'product_name',
    key: 'product_name',
    ellipsis: true,
    render: (v: string | null, r: StatementRow) => (
      <span>
        {v ?? '—'}
        {r.is_custom ? <Tag color="purple" style={{ marginLeft: 4 }}>定制</Tag> : null}
      </span>
    ),
  },
  { title: 'SKU', dataIndex: 'sku', key: 'sku', ellipsis: true, width: 160 },
  { title: '数量', dataIndex: 'qty', key: 'qty', width: 56, align: 'right' },
  { title: '售价(实收)', dataIndex: 'revenue', key: 'revenue', width: 100, align: 'right', render: yuan },
  { title: '预测工厂价', dataIndex: 'factory_predicted', key: 'fp', width: 100, align: 'right', render: yuan },
  {
    title: '盈亏平衡价(红线)',
    dataIndex: 'break_even_factory',
    key: 'be',
    width: 130,
    align: 'right',
    render: (v: number | null) => <Text strong style={{ color: '#cf1322' }}>{yuan(v)}</Text>,
  },
  {
    title: '安全垫',
    dataIndex: 'break_even_buffer',
    key: 'buf',
    width: 90,
    align: 'right',
    render: (v: number | null) => (
      <Text style={{ color: v != null && v >= 0 ? '#389e0d' : '#cf1322' }}>{yuan(v)}</Text>
    ),
  },
  {
    title: '备注',
    dataIndex: 'note',
    key: 'note',
    ellipsis: true,
    render: (v: string | null) => (v ? <Text type="warning" style={{ fontSize: 12 }}>{v}</Text> : null),
  },
];

export default function FactoryStatementPage() {
  const [periods, setPeriods] = useState<string[]>([]);
  const [period, setPeriod] = useState<string | undefined>();
  const [data, setData] = useState<Statement | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    fetch('/api/factory-statement/periods', { headers: authHeaders() })
      .then((r) => r.json())
      .then((ps: string[]) => {
        setPeriods(ps);
        if (ps.length) setPeriod(ps[0]);
      })
      .catch(() => {});
  }, []);

  const gen = async () => {
    setLoading(true);
    try {
      const url = '/api/factory-statement/data' + (period ? `?period=${period}` : '');
      const r = await fetch(url, { headers: authHeaders() });
      if (!r.ok) throw new Error('生成失败');
      setData((await r.json()) as Statement);
    } catch (e) {
      message.error((e as Error).message);
    } finally {
      setLoading(false);
    }
  };

  const exportXlsx = () => {
    window.open('/api/factory-statement/export' + (period ? `?period=${period}` : ''), '_blank');
  };

  return (
    <div style={{ padding: 16 }}>
      <Title level={4}>工厂对账单生成</Title>
      <Paragraph type="secondary">
        按月生成给工厂的对账单。每单含 预测工厂价 / 盈亏平衡价(红线) / 安全垫。
        工厂实际报价 ≤ 红线才不亏;红线 − 预测 = 安全垫(≈本单利润)。净不亏口径(已扣运费/安装/税/平台扣点)。
      </Paragraph>
      <Space wrap style={{ marginBottom: 12 }}>
        <Select
          style={{ width: 180 }}
          placeholder="选择月份(空=全部)"
          allowClear
          value={period}
          onChange={(v) => setPeriod(v)}
          options={periods.map((p) => ({ value: p, label: p }))}
        />
        <Button type="primary" loading={loading} onClick={gen}>
          生成对账单
        </Button>
        <Button icon={<FileExcelOutlined />} onClick={exportXlsx} disabled={!data}>
          导出 Excel
        </Button>
      </Space>

      {data && (
        <>
          <Space size="large" wrap style={{ marginBottom: 12 }}>
            <Statistic title="订单数" value={data.count} />
            <Statistic title="售价合计" value={data.totals.revenue} precision={0} prefix="¥" />
            <Statistic title="预测工厂价合计" value={data.totals.factory_predicted} precision={0} prefix="¥" />
            <Statistic
              title="盈亏平衡合计(红线)"
              value={data.totals.break_even_factory}
              precision={0}
              prefix="¥"
              valueStyle={{ color: '#cf1322' }}
            />
            <Statistic
              title="安全垫合计"
              value={data.totals.break_even_buffer}
              precision={0}
              prefix="¥"
              valueStyle={{ color: '#389e0d' }}
            />
            {data.missing > 0 && (
              <Statistic title="缺数据单" value={data.missing} valueStyle={{ color: '#d46b08' }} />
            )}
          </Space>
          <Table<StatementRow>
            size="small"
            rowKey="order_no"
            columns={cols}
            dataSource={data.rows}
            scroll={{ x: 1150 }}
            pagination={{ pageSize: 50, showSizeChanger: true }}
          />
        </>
      )}
    </div>
  );
}
