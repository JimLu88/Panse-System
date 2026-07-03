import { useEffect, useState } from 'react';
import {
  Alert, Button, Card, Input, message, Popconfirm, Space, Table, Tag, Typography,
} from 'antd';
import {
  type FsAlias, type FsDetailRow, type FsMissing, type FsMissingOrder, type FsMonth, type FsOverview, type FsPayment,
  downloadFsMissing, downloadFsDetail, fsAddAlias, fsDeleteAlias, fsReverse, fsScanAlipay, fsSettle, getFsMissing, getFsOverview,
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
  const [searchQ, setSearchQ] = useState(''); // 产品名/SKU/产品编码 模糊搜索 (2026-07-03)
  const [scanning, setScanning] = useState(false);
  const [missing, setMissing] = useState<FsMissing | null>(null);
  const [missLoading, setMissLoading] = useState(false);
  const [upToMonth, setUpToMonth] = useState('');

  const load = async (q: string = searchQ) => {
    setLoading(true);
    try {
      setData(await getFsOverview(undefined, q));
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

  const onScan = async () => {
    setScanning(true);
    try {
      const r = await fsScanAlipay();
      message.success(`扫描完成: 货款归类 ${r.flagged || 0} 笔, 关键词自动销账翻 ${r.flipped || 0} 单`);
      load();
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '扫描失败');
    } finally {
      setScanning(false);
    }
  };

  const loadMissing = async () => {
    setMissLoading(true);
    try {
      setMissing(await getFsMissing(upToMonth || undefined));
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '漏单查询失败');
    } finally {
      setMissLoading(false);
    }
  };

  const onDownloadMissing = async () => {
    try { await downloadFsMissing(upToMonth || undefined); }
    catch (e: any) { message.error(e?.response?.data?.detail || '导出失败'); }
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

  const missCols = [
    { title: '发货月', dataIndex: 'ship_month', key: 'sm', width: 90 },
    { title: '订单号', dataIndex: 'order_no', key: 'no', ellipsis: true },
    { title: '产品', dataIndex: 'product_name', key: 'pn', ellipsis: true },
    { title: '数量', dataIndex: 'qty', key: 'q', width: 60, align: 'right' as const },
    { title: '发货日', dataIndex: 'ship_date', key: 'sd', width: 110 },
    {
      title: '实付', dataIndex: 'paid_amount', key: 'pa', align: 'right' as const,
      render: (v: string) => `¥${v}`,
    },
    { title: '客户', dataIndex: 'customer_name', key: 'cn', width: 90, render: (v: string | null) => v || '—' },
  ];

  return (
    <div style={{ padding: 16 }}>
      <Space style={{ width: '100%', justifyContent: 'space-between' }} align="start">
        <Title level={3}>工厂月结销账 · {bd?.supplier || '博冠'}</Title>
        <Button onClick={onScan} loading={scanning}>扫支付宝自动销账</Button>
      </Space>
      <Alert
        type="info" style={{ marginBottom: 16 }}
        message="付了工厂月结货款后, 在这里把对应月份「已付清」, 现金流的「工厂结算(已开账单未付)」会随之下降。"
        description="销账按声明驱动(不卡金额, 工厂常有减免/加费)。结算月默认按下单月; 工厂账单另说月份时以账单为准。每笔销账可撤销。"
      />

      <Card size="small" style={{ marginBottom: 16 }}>
        <Space wrap>
          <Text strong>产品查询</Text>
          <Input.Search
            placeholder="搜产品名 / SKU / 产品编码 (模糊)"
            value={searchQ}
            allowClear
            enterButton="搜索"
            style={{ width: 340 }}
            onChange={(e) => { setSearchQ(e.target.value); if (!e.target.value) load(''); }}
            onSearch={(v) => load(v)}
          />
          <Text type="secondary">
            {searchQ && data?.detail
              ? `匹配 ${data.detail.length} 单; 下方台账已按此筛选`
              : '搜索后台账只汇总匹配单, 并列出逐单明细(工厂号/账单金额/付款状态)'}
          </Text>
        </Space>
      </Card>

      {searchQ && data?.detail && (
        <Card size="small" loading={loading} style={{ marginBottom: 16 }}
          title={`搜索结果 · 逐单明细 (${data.detail.length} 单)`}>
          <Table<FsDetailRow>
            rowKey={(r) => r.factory_order_no || String(r.platform_order_no)}
            size="small" pagination={{ pageSize: 20 }}
            columns={[
              { title: '结算月', dataIndex: 'settlement_month', key: 'sm', width: 80 },
              { title: '工厂单号', dataIndex: 'factory_order_no', key: 'fno', width: 90 },
              { title: '产品', dataIndex: 'product_name', key: 'pn', ellipsis: true },
              { title: 'SKU', dataIndex: 'sku', key: 'sku', width: 130, ellipsis: true },
              { title: '产品编码', dataIndex: 'product_code', key: 'pc', width: 130 },
              { title: '数量', dataIndex: 'qty', key: 'qty', width: 50 },
              { title: '账单金额', dataIndex: 'bill_amount', key: 'ba', width: 90, render: (v: string) => `¥${v}` },
              { title: '付款', dataIndex: 'payment_status', key: 'ps', width: 60,
                render: (v: string) => <Tag color={v === '已付' ? 'green' : 'red'}>{v}</Tag> },
              { title: '下单日', dataIndex: 'order_date', key: 'od', width: 100, render: (v: string | null) => v || '—' },
            ]}
            dataSource={data.detail}
          />
        </Card>
      )}

      <Card
        size="small" loading={loading} style={{ marginBottom: 16 }}
        title="月度欠款台账"
        extra={bd && (
          <Space size="large">
            <Text>应付合计 <Text strong>¥{bd.total_billed}</Text></Text>
            <Text>已付 <Text strong style={{ color: '#3f8600' }}>¥{bd.total_paid}</Text></Text>
            <Text>未付 <Text strong type="danger">¥{bd.total_unpaid}</Text></Text>
            <Button size="small" onClick={() => downloadFsDetail(bd?.supplier || undefined)}>
              导出明细Excel(账单+已付)
            </Button>
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

      <Card
        size="small" loading={missLoading} style={{ marginBottom: 16 }}
        title="漏单检测 (已发货但没被任何工厂账单覆盖)"
        extra={(
          <Space>
            <Text type="secondary">截至发货月</Text>
            <Input placeholder="YYYY-MM 如 2026-05" value={upToMonth}
              onChange={(e) => setUpToMonth(e.target.value)} style={{ width: 170 }} />
            <Button onClick={loadMissing}>查询</Button>
            <Button onClick={onDownloadMissing}>导出Excel</Button>
          </Space>
        )}
      >
        {missing ? (
          <>
            <Paragraph type="secondary" style={{ marginBottom: 8 }}>
              共 <Text strong type="danger">{missing.count}</Text> 单未被工厂账单覆盖, 实付合计 ¥{missing.total_paid}
              {missing.up_to_month ? `（截至 ${missing.up_to_month}, 按发货月累计）` : '（全部已发货）'}。
              这些是工厂账单可能漏开的单, 拿去和工厂核对。
            </Paragraph>
            <Table<FsMissingOrder> rowKey="order_no" size="small" pagination={{ pageSize: 10 }}
              columns={missCols} dataSource={missing.orders} />
          </>
        ) : (
          <Text type="secondary">填「截至发货月」后点查询(留空=全部已发货)。工厂出几月账单就查到几月的漏单。</Text>
        )}
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
