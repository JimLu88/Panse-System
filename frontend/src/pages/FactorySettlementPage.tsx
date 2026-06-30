import { useEffect, useState } from 'react';
import {
  Alert, Button, Card, Input, message, Popconfirm, Space, Table, Tag, Typography,
} from 'antd';
import {
  type FsAlias, type FsMonth, type FsOverview, type FsPayment,
  fsAddAlias, fsDeleteAlias, fsReverse, fsSettle, getFsOverview,
} from '../api/factorySettlement';

const { Title, Text, Paragraph } = Typography;

const STATUS_TAG: Record<string, { color: string; label: string }> = {
  paid: { color: 'green', label: '已付清' },
  partial: { color: 'orange', label: '部分付清' },
  unpaid: { color: 'red', label: '未付清' },
};

export default function FactorySettlementPage() {
  const [data, setData] = useState<FsOverview | null>(null);
  const [loading, setLoading] = useState(false);
  const [aliasInput, setAliasInput] = useState('');

  const load = async () => {
    setLoading(true);
    try {
      setData(await getFsOverview());
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '加载失败');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); }, []);

  const onSettle = async (month: string) => {
    try {
      const r = await fsSettle({ month });
      if (r.flipped) message.success(`${month} 已付清: 翻 ${r.flipped} 单, 共 ¥${r.billed_total}`);
      else message.info(r.message || `${month} 已无未付的已开账单`);
      load();
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '销账失败');
    }
  };

  const onReverse = async (pid: number) => {
    try {
      const r = await fsReverse(pid);
      message.success(`已撤销, 恢复 ${r.reverted} 单为未付`);
      load();
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '撤销失败');
    }
  };

  const onAddAlias = async () => {
    const v = aliasInput.trim();
    if (!v) return;
    try {
      await fsAddAlias({ alias: v });
      setAliasInput('');
      message.success('已添加别名');
      load();
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '添加失败');
    }
  };

  const onDeleteAlias = async (id: number) => {
    try { await fsDeleteAlias(id); message.success('已删除'); load(); }
    catch (e: any) { message.error(e?.response?.data?.detail || '删除失败'); }
  };

  const bd = data?.breakdown;

  const monthCols = [
    { title: '结算月', dataIndex: 'month', key: 'month', width: 110 },
    {
      title: '应付(账单)', dataIndex: 'billed', key: 'billed', align: 'right' as const,
      render: (v: string) => `¥${v}`,
    },
    {
      title: '已付', dataIndex: 'paid', key: 'paid', align: 'right' as const,
      render: (v: string) => `¥${v}`,
    },
    {
      title: '未付', dataIndex: 'unpaid', key: 'unpaid', align: 'right' as const,
      render: (v: string) => <Text type={Number(v) > 0 ? 'danger' : undefined} strong>¥{v}</Text>,
    },
    { title: '单数', dataIndex: 'order_count', key: 'order_count', align: 'right' as const, width: 70 },
    {
      title: '状态', dataIndex: 'status', key: 'status', width: 100,
      render: (s: string) => {
        const t = STATUS_TAG[s] || { color: 'default', label: s };
        return <Tag color={t.color}>{t.label}</Tag>;
      },
    },
    {
      title: '操作', key: 'op', width: 130,
      render: (_: unknown, row: FsMonth) => (
        Number(row.unpaid) > 0 ? (
          <Popconfirm
            title={`确认「${row.month}」已付清?`}
            description={`将把该月 ${row.order_count} 单中未付的全部标为已付 (¥${row.unpaid})。可在下方撤销。`}
            okText="确认已付清" cancelText="取消"
            onConfirm={() => onSettle(row.month)}
          >
            <Button type="primary" size="small">已付清</Button>
          </Popconfirm>
        ) : <Text type="secondary">—</Text>
      ),
    },
  ];

  const payCols = [
    { title: '结算月', dataIndex: 'settlement_month', key: 'm', width: 100 },
    {
      title: '方式', dataIndex: 'trigger', key: 't', width: 90,
      render: (v: string) => <Tag>{v === 'keyword' ? '关键词' : '手动'}</Tag>,
    },
    { title: '翻单数', dataIndex: 'flipped_count', key: 'f', align: 'right' as const, width: 80 },
    {
      title: '实付(参考)', dataIndex: 'paid_amount', key: 'p', align: 'right' as const,
      render: (v: string | null) => (v ? `¥${v}` : '—'),
    },
    { title: '流水号', dataIndex: 'alipay_flow_no', key: 'fl', ellipsis: true, render: (v: string | null) => v || '—' },
    { title: '时间', dataIndex: 'created_at', key: 'c', width: 170, render: (v: string | null) => (v ? v.replace('T', ' ').slice(0, 19) : '—') },
    {
      title: '操作', key: 'op', width: 100,
      render: (_: unknown, row: FsPayment) => (
        row.reversed_at
          ? <Tag color="default">已撤销</Tag>
          : (
            <Popconfirm title="撤销这笔销账?" description="将把本批翻过的单恢复为未付。" okText="撤销" cancelText="取消"
              onConfirm={() => onReverse(row.id)}>
              <Button danger size="small">撤销</Button>
            </Popconfirm>
          )
      ),
    },
  ];

  const aliasCols = [
    { title: '对手方/账户别名', dataIndex: 'alias', key: 'a' },
    { title: '供应商', dataIndex: 'supplier', key: 's' },
    {
      title: '操作', key: 'op', width: 80,
      render: (_: unknown, row: FsAlias) => (
        <Popconfirm title="删除该别名?" okText="删除" cancelText="取消" onConfirm={() => onDeleteAlias(row.id)}>
          <Button danger size="small" type="link">删除</Button>
        </Popconfirm>
      ),
    },
  ];

  return (
    <div style={{ padding: 16 }}>
      <Title level={3}>工厂月结销账 · {bd?.supplier || '博冠'}</Title>
      <Alert
        type="info" style={{ marginBottom: 16 }}
        message="付了工厂月结货款后, 在这里把对应月份「已付清」, 现金流的「工厂结算(已开账单未付)」会随之下降。"
        description="销账按声明驱动(不卡金额, 工厂常有减免/加费)。结算月默认按下单月; 工厂账单另说月份时以账单为准。每笔销账可撤销。"
      />

      <Card
        size="small" loading={loading} style={{ marginBottom: 16 }}
        title="月度欠款台账"
        extra={bd && (
          <Space size="large">
            <Text>应付合计 <Text strong>¥{bd.total_billed}</Text></Text>
            <Text>已付 <Text strong style={{ color: '#3f8600' }}>¥{bd.total_paid}</Text></Text>
            <Text>未付 <Text strong type="danger">¥{bd.total_unpaid}</Text></Text>
          </Space>
        )}
      >
        <Table<FsMonth>
          rowKey="month" size="small" pagination={false}
          columns={monthCols} dataSource={bd?.months || []}
        />
      </Card>

      <Card size="small" loading={loading} style={{ marginBottom: 16 }} title="销账记录 (可撤销)">
        <Table<FsPayment>
          rowKey="id" size="small" pagination={{ pageSize: 10 }}
          columns={payCols} dataSource={data?.payments || []}
        />
      </Card>

      <Card size="small" loading={loading} title="供应商别名 (支付宝对手方 → 工厂, 用于关键词自动销账)">
        <Paragraph type="secondary" style={{ marginBottom: 8 }}>
          博冠货款常走个人账户(如 伟男/程卫燕), 流水里是打码名。把这些名字加进来, 系统才能认出"付给博冠"。
        </Paragraph>
        <Space style={{ marginBottom: 12 }}>
          <Input
            placeholder="新增别名 (对手方名/账户名)" value={aliasInput}
            onChange={(e) => setAliasInput(e.target.value)} onPressEnter={onAddAlias}
            style={{ width: 240 }}
          />
          <Button onClick={onAddAlias}>添加</Button>
        </Space>
        <Table<FsAlias>
          rowKey="id" size="small" pagination={false}
          columns={aliasCols} dataSource={data?.aliases || []}
        />
      </Card>
    </div>
  );
}
