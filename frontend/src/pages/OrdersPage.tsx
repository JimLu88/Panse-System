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
  OrderCostBreakdown,
  changeOrderStatus,
  getOrderCostBreakdown,
  importOrdersCsv,
  listOrders,
  recomputeAllOrderCosts,
  recomputeOrderCost,
  generateOrderDetails,
} from '../api/client';
import OrderTimelineDrawer from '../components/OrderTimelineDrawer';
import { Drawer, Spin, Table as AntTable } from 'antd';

function fmtMoney(v: string | null | undefined): string {
  if (v === null || v === undefined || v === '') return '—';
  const n = Number(v);
  return Number.isFinite(n) ? `¥${n.toFixed(2)}` : String(v);
}

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
  const [costFor, setCostFor] = useState<{ id: number; order_no: string } | null>(null);
  const [costData, setCostData] = useState<OrderCostBreakdown | null>(null);
  const [costLoading, setCostLoading] = useState(false);

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

  const recomputeAllMut = useMutation({
    mutationFn: () => recomputeAllOrderCosts(true),
    onSuccess: (r) => {
      message.success(`理论成本反推完成：更新 ${r.updated} 单，${r.skipped_no_bom} 单无 BOM 跳过`);
      qc.invalidateQueries({ queryKey: ['orders'] });
    },
    onError: (e: any) => message.error(e?.response?.data?.detail ?? '反推失败'),
  });

  const genDetailsMut = useMutation({
    mutationFn: () => generateOrderDetails(),
    onSuccess: (r) => {
      message.success(
        `订单细节生成：扫描 ${r.orders_scanned} 单，新建 ${r.details_created} 行，` +
          `跳过 ${r.details_skipped} 行${r.orders_no_bom_count ? `，${r.orders_no_bom_count} 单无 BOM` : ''}`,
      );
    },
    onError: (e: any) => message.error(e?.response?.data?.detail ?? '生成失败'),
  });

  async function openCost(id: number, order_no: string) {
    setCostFor({ id, order_no });
    setCostData(null);
    setCostLoading(true);
    try {
      setCostData(await getOrderCostBreakdown(id));
    } catch (e: any) {
      message.error(e?.response?.data?.detail ?? '获取成本明细失败');
    } finally {
      setCostLoading(false);
    }
  }

  const recomputeOneMut = useMutation({
    mutationFn: (id: number) => recomputeOrderCost(id),
    onSuccess: (bd) => {
      setCostData(bd);
      message.success('已反推并保存理论成本');
      qc.invalidateQueries({ queryKey: ['orders'] });
    },
    onError: (e: any) => message.error(e?.response?.data?.detail ?? '反推失败'),
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
      title: '理论成本',
      dataIndex: 'theoretical_cost',
      width: 100,
      align: 'right' as const,
      render: (v: string | null) =>
        v === null || v === undefined ? <Typography.Text type="secondary">未反推</Typography.Text> : fmtMoney(v),
    },
    {
      title: '实际成本',
      dataIndex: 'actual_cost',
      width: 100,
      align: 'right' as const,
      render: (v: string | null) => fmtMoney(v),
    },
    {
      title: '差异',
      dataIndex: 'cost_diff',
      width: 90,
      align: 'right' as const,
      render: (v: string | null) => {
        if (v === null || v === undefined) return <Typography.Text type="secondary">—</Typography.Text>;
        const n = Number(v);
        // 实际 > 理论 = 超支(红); 实际 < 理论 = 结余(绿)
        const color = n > 0 ? '#cf1322' : n < 0 ? '#389e0d' : undefined;
        return <span style={{ color }}>{n > 0 ? '+' : ''}{n.toFixed(2)}</span>;
      },
    },
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
              onClick={() => openCost(r.id, r.order_no)}
            >
              成本明细
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
          <Button
            onClick={() => recomputeAllMut.mutate()}
            loading={recomputeAllMut.isPending}
          >
            反推理论成本
          </Button>
          <Button
            onClick={() => genDetailsMut.mutate()}
            loading={genDetailsMut.isPending}
          >
            生成订单细节
          </Button>
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

      <Drawer
        title={`理论成本反推 — ${costFor?.order_no ?? ''}`}
        width={560}
        open={costFor !== null}
        onClose={() => { setCostFor(null); setCostData(null); }}
        extra={
          costFor && (
            <Button
              type="primary"
              loading={recomputeOneMut.isPending}
              onClick={() => recomputeOneMut.mutate(costFor.id)}
              disabled={!costData?.resolved}
            >
              重新反推并保存
            </Button>
          )
        }
      >
        {costLoading ? (
          <Spin />
        ) : costData ? (
          <Space direction="vertical" style={{ width: '100%' }} size="middle">
            {costData.note && (
              <Alert type={costData.resolved ? 'warning' : 'info'} showIcon message={costData.note} />
            )}
            <Typography.Text type="secondary">
              SKU 编码：{costData.sku_code ?? '（未匹配）'}　·　按 BOM 每项物料「单耗 × 单价」累加
            </Typography.Text>
            <AntTable
              size="small"
              rowKey="material_code"
              pagination={false}
              dataSource={costData.lines}
              columns={[
                { title: '物料编码', dataIndex: 'material_code', width: 120 },
                { title: '物料名称', dataIndex: 'material_name', ellipsis: true,
                  render: (v: string | null) => v ?? '—' },
                { title: '单耗', dataIndex: 'qty_per_product', width: 70, align: 'right' as const,
                  render: (v: string) => Number(v).toString() },
                { title: '单价', dataIndex: 'unit_price', width: 90, align: 'right' as const,
                  render: (v: string | null, row: any) =>
                    row.missing_price ? <Tag color="red">缺价</Tag> : fmtMoney(v) },
                { title: '小计', dataIndex: 'line_cost', width: 100, align: 'right' as const,
                  render: (v: string | null) => fmtMoney(v) },
              ] as any}
              summary={() => (
                <AntTable.Summary>
                  <AntTable.Summary.Row>
                    <AntTable.Summary.Cell index={0} colSpan={4}>
                      <b>单件理论成本</b>
                    </AntTable.Summary.Cell>
                    <AntTable.Summary.Cell index={4} align="right">
                      <b>{fmtMoney(costData.unit_cost)}</b>
                    </AntTable.Summary.Cell>
                  </AntTable.Summary.Row>
                  <AntTable.Summary.Row>
                    <AntTable.Summary.Cell index={0} colSpan={4}>
                      × 数量 {costData.qty} = 订单总理论成本
                    </AntTable.Summary.Cell>
                    <AntTable.Summary.Cell index={4} align="right">
                      {fmtMoney(costData.total_cost)}
                    </AntTable.Summary.Cell>
                  </AntTable.Summary.Row>
                </AntTable.Summary>
              )}
            />
          </Space>
        ) : null}
      </Drawer>
    </Space>
  );
}
