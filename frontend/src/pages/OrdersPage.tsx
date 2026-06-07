import { useMemo, useState, type Key } from 'react';
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
import { downloadCsv } from '../utils/csv';
import ShipmentTracker from '../components/ShipmentTracker';
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
import AccessoryChecklistDrawer from '../components/AccessoryChecklistDrawer';
import FullColumnView from '../components/FullColumnView';
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

// 批量推进可选目标 (逐单仍按 ALLOWED_NEXT 校验, 非法的自动跳过)
const BATCH_TARGETS: (keyof typeof STATUS_META)[] = [
  'paid', 'shipped', 'signed', 'aftersales', 'cancelled',
];

type StatusKey = keyof typeof STATUS_META | 'all';

export default function OrdersPage() {
  const qc = useQueryClient();
  const [statusFilter, setStatusFilter] = useState<StatusKey>('all');
  const [q, setQ] = useState('');
  const [importReport, setImportReport] = useState<CsvImportReport | null>(null);
  const [timelineFor, setTimelineFor] = useState<number | null>(null);
  const [accessoryFor, setAccessoryFor] = useState<{ id: number; order_no: string } | null>(null);
  const [costFor, setCostFor] = useState<{ id: number; order_no: string } | null>(null);
  const [costData, setCostData] = useState<OrderCostBreakdown | null>(null);
  const [costLoading, setCostLoading] = useState(false);
  const [viewMode, setViewMode] = useState<'curated' | 'full'>('curated');
  const [selectedKeys, setSelectedKeys] = useState<Key[]>([]);
  const [batchRunning, setBatchRunning] = useState(false);

  const { data, isLoading } = useQuery({
    queryKey: ['orders', statusFilter, q],
    queryFn: () =>
      listOrders({
        q: q || undefined,
        status: statusFilter === 'all' ? undefined : statusFilter,
        limit: 200,
      }),
  });

  // 仅对「当前列表里真实存在」的选中行操作 (过滤/搜索后失效的 key 自动忽略)
  const selectedOrders = useMemo(
    () => (data ?? []).filter((o) => selectedKeys.includes(o.id)),
    [data, selectedKeys],
  );

  function exportSelected() {
    const headers = [
      '平台', '订单号', '下单日期', '客户', '产品', 'SKU', '数量',
      '理论成本', '实际成本', '差异', '状态',
    ];
    const rows = selectedOrders.map((o) => [
      o.platform, o.order_no, o.order_date ?? '', o.customer_name ?? '',
      o.product_name ?? '', o.sku ?? '', o.qty,
      o.theoretical_cost ?? '', o.actual_cost ?? '', o.cost_diff ?? '',
      STATUS_META[o.status]?.label ?? o.status,
    ]);
    const today = new Date().toISOString().slice(0, 10);
    downloadCsv(`订单导出_${today}.csv`, headers, rows);
    message.success(`已导出 ${rows.length} 单`);
  }

  async function batchAdvance(target: string) {
    const eligible = selectedOrders.filter((o) =>
      (ALLOWED_NEXT[o.status] ?? []).includes(target));
    const skipped = selectedOrders.length - eligible.length;
    setBatchRunning(true);
    const results = await Promise.allSettled(
      eligible.map((o) => changeOrderStatus(o.id, target)),
    );
    setBatchRunning(false);
    const ok = results.filter((r) => r.status === 'fulfilled').length;
    const failed = results.length - ok;
    qc.invalidateQueries({ queryKey: ['orders'] });
    setSelectedKeys([]);
    const parts = [`成功 ${ok} 单`];
    if (skipped) parts.push(`跳过 ${skipped} 单(状态不允许)`);
    if (failed) parts.push(`失败 ${failed} 单`);
    (failed ? message.warning : message.success)(parts.join('，'));
  }

  function confirmBatchAdvance(target: keyof typeof STATUS_META) {
    const label = STATUS_META[target].label;
    const eligible = selectedOrders.filter((o) =>
      (ALLOWED_NEXT[o.status] ?? []).includes(target));
    if (eligible.length === 0) {
      message.warning(`所选订单都不能推进到「${label}」(状态不允许)`);
      return;
    }
    const skipped = selectedOrders.length - eligible.length;
    Modal.confirm({
      title: `批量推进到「${label}」`,
      content: `共选 ${selectedOrders.length} 单，其中 ${eligible.length} 单可推进` +
        (skipped ? `，${skipped} 单因状态不允许将跳过` : '') + '。确认继续？',
      okText: '确认推进',
      cancelText: '取消',
      onOk: () => batchAdvance(target),
    });
  }

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
    { title: '平台', dataIndex: 'platform', width: 80, fixed: 'left' as const },
    {
      title: '订单号',
      dataIndex: 'order_no',
      width: 180,
      fixed: 'left' as const,
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
    { title: '产品', dataIndex: 'product_name', width: 220, ellipsis: true },
    { title: 'SKU', dataIndex: 'sku', width: 200, ellipsis: true },
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
      width: 120,
      render: (v: string) => {
        // 导入的淘宝中文长状态 → 短标签, 避免撑爆列、挡住右侧「物流/查款」
        const RAW: Record<string, { label: string; color: string }> = {
          '买家已付款,等待卖家发货': { label: '待发货', color: 'cyan' },
          '卖家已发货，等待买家确认': { label: '待收货', color: 'blue' },
          '交易成功': { label: '交易成功', color: 'green' },
          '交易关闭': { label: '已关闭', color: 'default' },
          '等待买家付款': { label: '待付款', color: 'gold' },
        };
        const m = STATUS_META[v] ?? RAW[v] ?? { label: v, color: 'default' };
        return <Tag color={m.color}>{m.label}</Tag>;
      },
    },
    {
      title: '物流 / 查款',
      width: 220,
      render: (_: unknown, r: Order) => <ShipmentTracker entityType="order" entityId={r.id} />,
    },
    {
      title: '操作',
      width: 200,
      fixed: 'right' as const,
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
            <Button
              size="small"
              onClick={() => setAccessoryFor({ id: r.id, order_no: r.order_no })}
            >
              配件
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
            accept=".csv,.xlsx,.xls"
            showUploadList={false}
            beforeUpload={(file) => {
              importMut.mutate(file as File);
              return false;
            }}
          >
            <Button icon={<UploadOutlined />} loading={importMut.isPending}>
              导入订单 (CSV/Excel)
            </Button>
          </Upload>
        </Space>
      </Space>

      <Space wrap>
        <Segmented
          value={viewMode}
          onChange={(v) => setViewMode(v as 'curated' | 'full')}
          options={[
            { label: '精选视图（可编辑）', value: 'curated' },
            { label: '全部列', value: 'full' },
          ]}
        />
        {viewMode === 'curated' && (
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
        )}
      </Space>

      {viewMode === 'curated' && selectedOrders.length > 0 && (
        <div style={{
          background: '#f5f7fa', border: '1px solid #e6eaf0',
          borderRadius: 8, padding: '8px 12px',
        }}>
          <Space wrap>
            <span>已选 <b>{selectedOrders.length}</b> 单</span>
            <Button size="small" onClick={exportSelected}>批量导出 CSV</Button>
            <Dropdown
              menu={{
                items: BATCH_TARGETS.map((s) => ({
                  key: s,
                  label: STATUS_META[s].label,
                  onClick: () => confirmBatchAdvance(s),
                })),
              }}
            >
              <Button size="small" type="primary" loading={batchRunning}>
                批量推进状态
              </Button>
            </Dropdown>
            <Button size="small" type="text" onClick={() => setSelectedKeys([])}>
              取消选择
            </Button>
          </Space>
        </div>
      )}

      {viewMode === 'full' && <FullColumnView entity="order" defaultShowAll />}

      {viewMode === 'curated' && (
      <Table<Order>
        rowKey="id"
        loading={isLoading}
        dataSource={data}
        columns={columns as any}
        rowSelection={{
          selectedRowKeys: selectedKeys,
          onChange: setSelectedKeys,
          preserveSelectedRowKeys: true,
        }}
        scroll={{ x: 1870 }}
        pagination={{ pageSize: 30 }}
        size="middle"
      />
      )}

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
      <AccessoryChecklistDrawer
        orderId={accessoryFor?.id ?? null}
        orderNo={accessoryFor?.order_no}
        open={accessoryFor !== null}
        onClose={() => setAccessoryFor(null)}
      />
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
