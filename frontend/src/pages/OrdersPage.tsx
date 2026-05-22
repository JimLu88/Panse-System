import { useState } from 'react';
import {
  Alert,
  Button,
  Dropdown,
  Input,
  Modal,
  Segmented,
  Space,
  Table,
  Tag,
  Typography,
  Upload,
  message,
} from 'antd';
import { UploadOutlined } from '@ant-design/icons';
import { FirstVisitTip } from '../components/FirstVisitTip';
import type { UploadFile } from 'antd/es/upload/interface';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  CsvImportReport,
  Order,
  changeOrderStatus,
  importOrdersCsv,
  listOrders,
} from '../api/client';
import OrderTimelineDrawer from '../components/OrderTimelineDrawer';

const STATUS_META: Record<string, { label: string; color: string }> = {
  pending_payment: { label: '待付款', color: 'default' },
  paid: { label: '已付款', color: 'blue' },
  shipped: { label: '已发货', color: 'cyan' },
  signed: { label: '已签收', color: 'green' },
  aftersales: { label: '售后中', color: 'orange' },
  cancelled: { label: '已取消', color: 'red' },
};

// 合法迁移图（前端镜像后端）
const ALLOWED_NEXT: Record<string, string[]> = {
  pending_payment: ['paid', 'cancelled'],
  paid: ['shipped', 'aftersales', 'cancelled'],
  shipped: ['signed', 'aftersales'],
  signed: ['aftersales'],
  aftersales: ['signed'],
  cancelled: [],
};

type StatusKey = keyof typeof STATUS_META | 'all';

export default function OrdersPage() {
  const qc = useQueryClient();
  const [statusFilter, setStatusFilter] = useState<StatusKey>('all');
  const [q, setQ] = useState('');
  const [importReport, setImportReport] = useState<CsvImportReport | null>(null);
  const [timelineFor, setTimelineFor] = useState<number | null>(null);

  const { data, isLoading } = useQuery({
    queryKey: ['orders', statusFilter, q],
    queryFn: () =>
      listOrders({
        q: q || undefined,
        status: statusFilter === 'all' ? undefined : statusFilter,
        limit: 200,
      }),
  });

  const statusMut = useMutation({
    mutationFn: ({ id, status }: { id: number; status: string }) => changeOrderStatus(id, status),
    onSuccess: () => {
      message.success('状态已更新');
      qc.invalidateQueries({ queryKey: ['orders'] });
    },
    onError: (e: any) => message.error(e?.response?.data?.detail ?? '更新失败'),
  });

  const importMut = useMutation({
    mutationFn: (file: File) => importOrdersCsv(file),
    onSuccess: (r) => {
      setImportReport(r);
      qc.invalidateQueries({ queryKey: ['orders'] });
    },
    onError: (e: any) => message.error(e?.response?.data?.detail ?? '导入失败'),
  });

  const columns = [
    { title: '平台', dataIndex: 'platform', width: 80 },
    {
      title: '订单号',
      dataIndex: 'order_no',
      width: 180,
      render: (v: string, r: Order) => (
        <Space size={4}>
          <code>{v}</code>
          {r.is_refill && <Tag color="purple">补单</Tag>}
          {r.is_custom && <Tag color="orange">定制</Tag>}
        </Space>
      ),
    },
    { title: '下单日期', dataIndex: 'order_date', width: 110 },
    { title: '客户', dataIndex: 'customer_name', width: 90 },
    { title: '产品', dataIndex: 'product_name', ellipsis: true },
    { title: 'SKU', dataIndex: 'sku', ellipsis: true },
    { title: '数量', dataIndex: 'qty', width: 60 },
    {
      title: '状态',
      dataIndex: 'status',
      width: 100,
      render: (v: string) => {
        const m = STATUS_META[v] ?? { label: v, color: 'default' };
        return <Tag color={m.color}>{m.label}</Tag>;
      },
    },
    {
      title: '操作',
      width: 200,
      render: (_: unknown, r: Order) => {
        const next = ALLOWED_NEXT[r.status] ?? [];
        return (
          <Space size="small">
            <Button
              size="small"
              onClick={() => setTimelineFor(r.id)}
            >
              时间线
            </Button>
            <Button
              size="small"
              onClick={() => window.open(`/orders/${r.id}/factory-sheet`, '_blank')}
            >
              制单图
            </Button>
            {next.length > 0 && (
              <Dropdown
                menu={{
                  items: next.map((s) => ({
                    key: s,
                    label: STATUS_META[s]?.label ?? s,
                    onClick: () => statusMut.mutate({ id: r.id, status: s }),
                  })),
                }}
              >
                <Button size="small">推进</Button>
              </Dropdown>
            )}
          </Space>
        );
      },
    },
  ];

  return (
    <Space direction="vertical" style={{ width: '100%' }} size="middle">
      <FirstVisitTip
        storageKey="orders"
        title="订单状态机"
        description={
          <span>
            待付款 → 已付款 → 已发货 → 已签收 (任何时候可转售后)。
            非法跳转会被拒绝；用「推进」下拉只显示合法目标。
            CSV 导入支持淘宝标准列名 (订单编号 / 下单日期 / 客户姓名 / 数量 / 实付金额 等)。
          </span>
        }
      />
      <Space style={{ justifyContent: 'space-between', width: '100%' }}>
        <Typography.Title level={4} style={{ margin: 0 }}>
          订单总表 (5)
        </Typography.Title>
        <Space>
          <Input.Search
            placeholder="搜订单号 / 客户名"
            allowClear
            style={{ width: 260 }}
            onSearch={setQ}
          />
          <Upload
            accept=".csv"
            showUploadList={false}
            beforeUpload={(file) => {
              importMut.mutate(file as File);
              return false;
            }}
          >
            <Button icon={<UploadOutlined />} loading={importMut.isPending}>
              CSV 导入
            </Button>
          </Upload>
        </Space>
      </Space>

      <Segmented<StatusKey>
        value={statusFilter}
        onChange={(v) => setStatusFilter(v as StatusKey)}
        options={[
          { label: '全部', value: 'all' },
          { label: '待付款', value: 'pending_payment' },
          { label: '已付款', value: 'paid' },
          { label: '已发货', value: 'shipped' },
          { label: '已签收', value: 'signed' },
          { label: '售后中', value: 'aftersales' },
        ]}
      />

      <Table<Order>
        rowKey="id"
        loading={isLoading}
        dataSource={data}
        columns={columns as any}
        pagination={{ pageSize: 30 }}
        size="middle"
      />

      <Modal
        title="CSV 导入结果"
        open={!!importReport}
        onCancel={() => setImportReport(null)}
        onOk={() => setImportReport(null)}
        footer={[
          <Button key="ok" type="primary" onClick={() => setImportReport(null)}>
            知道了
          </Button>,
        ]}
      >
        {importReport && (
          <Space direction="vertical">
            <div>
              新增：<Tag color="green">{importReport.inserted}</Tag>
            </div>
            <div>
              重复跳过：<Tag>{importReport.skipped_duplicate}</Tag>
            </div>
            <div>
              无效跳过：<Tag color="red">{importReport.skipped_invalid}</Tag>
            </div>
            {importReport.errors.length > 0 && (
              <Alert
                type="error"
                showIcon
                message="错误"
                description={importReport.errors.join('\n')}
              />
            )}
          </Space>
        )}
      </Modal>
      <OrderTimelineDrawer
        orderId={timelineFor}
        open={timelineFor !== null}
        onClose={() => setTimelineFor(null)}
      />
    </Space>
  );
}
