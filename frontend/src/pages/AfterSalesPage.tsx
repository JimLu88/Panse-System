/**
 * 退货 / 售后 (Phase 5, 业务需求 9).
 *
 * 列出所有 AfterSales 记录, 提供:
 *   - 创建退货
 *   - 标记签收 (mark received)
 *   - 二次确认入库 (按整产品入)
 *   - 标记损坏不入库
 *   - 拆 BOM (成品 → 物料)
 */
import { useEffect, useState } from 'react';
import ShipmentTracker from '../components/ShipmentTracker';
import {
  Alert,
  Button,
  Card,
  Checkbox,
  Drawer,
  Form,
  Input,
  InputNumber,
  Modal,
  Popconfirm,
  Segmented,
  Select,
  Space,
  Table,
  Tag,
  Tooltip,
  Typography,
  message,
} from 'antd';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import FullColumnView from '../components/FullColumnView';
import PresetTable from '../components/PresetTable';
import ResponsiveTable from '../components/ResponsiveTable';
import { StatusCard, type StatusTone } from '../components/MobileCards';
import {
  AfterSalesItem,
  AfterSalesPaymentLink,
  DisassemblyLogRow,
  confirmReturnInbound,
  confirmAfterSalesPaymentLink,
  createReturn,
  disassembleProduct,
  fetchAfterSales,
  fetchAfterSalesPaymentLinks,
  listDisassemblyLogs,
  markReturnDamaged,
  markReturnReceived,
  rejectAfterSalesPaymentLink,
  scanAfterSalesPaymentLinks,
  undoDisassembly,
  updateAfterSales,
} from '../api/client';

// 点击补填/修改快递单号 (退回/补发共用)
function EditableTrackingNo({ label, value, onSave }: {
  label: string; value: string | null; onSave: (v: string | null) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [val, setVal] = useState(value ?? '');
  if (!editing) {
    return (
      <span onClick={() => { setVal(value ?? ''); setEditing(true); }} style={{ cursor: 'pointer' }}>
        <Typography.Text type="secondary" style={{ fontSize: 11 }}>{label} </Typography.Text>
        {value || <Tag color="warning" style={{ marginInlineEnd: 0 }}>未填·点我填</Tag>}
      </span>
    );
  }
  const commit = () => {
    setEditing(false);
    const t = val.trim();
    if (t !== (value ?? '')) onSave(t || null);
  };
  return (
    <Input size="small" autoFocus value={val} style={{ width: 170 }}
           placeholder={`${label}快递单号`}
           onChange={(e) => setVal(e.target.value)} onBlur={commit} onPressEnter={commit} />
  );
}

const STATUS_COLOR: Record<string, string> = {
  pending_return: 'orange',
  received_pending_inspection: 'blue',
  returned_in_stock: 'green',
  damaged_not_inbound: 'red',
};

const STATUS_LABEL: Record<string, string> = {
  pending_return: '待收货',
  received_pending_inspection: '已签收 / 待检查',
  returned_in_stock: '已入库',
  damaged_not_inbound: '损坏未入库',
};

const STATUS_TONE: Record<string, StatusTone> = {
  pending_return: 'wait', received_pending_inspection: 'ship',
  returned_in_stock: 'done', damaged_not_inbound: 'close',
};


export default function AfterSalesPage() {
  const qc = useQueryClient();
  const [createOpen, setCreateOpen] = useState(false);
  const [inboundFor, setInboundFor] = useState<AfterSalesItem | null>(null);
  // 拆BOM 改为按单品行内操作: 记录目标行, 双重确认后才打开执行表单
  const [disFor, setDisFor] = useState<AfterSalesItem | null>(null);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [paymentConfirmFor, setPaymentConfirmFor] = useState<AfterSalesPaymentLink | null>(null);
  const [paymentOrderNo, setPaymentOrderNo] = useState('');
  const [paymentCategory, setPaymentCategory] = useState('');
  const [paymentTarget, setPaymentTarget] = useState<'aftersales' | 'order_install'>('aftersales');
  const [paymentClearWanshifu, setPaymentClearWanshifu] = useState(false);

  const confirmDisassemble = (r: AfterSalesItem) => {
    Modal.confirm({
      title: '拆 BOM — 这是干什么的？',
      width: 520,
      content: (
        <div>
          <p>把 <b>{r.product_name || r.product_code || '该产品'}</b> 的 N 件<b>成品库存</b>拆回
            BOM 物料库存：成品 −N，BOM 里每种物料按单耗 +N。</p>
          <p>用途：退回来的成品不再整件卖、决定拆了当配件用时才操作。</p>
          <p style={{ color: '#cf1322' }}>影响：成品库存 与 配件库存 两张表都会变动（有台账可查，但不会自动撤销）。</p>
        </div>
      ),
      okText: '我已了解，继续',
      cancelText: '取消',
      onOk: () => {
        Modal.confirm({
          title: '再次确认',
          content: `确定要拆解「${r.product_name || r.product_code || '该产品'}」的成品库存吗？下一步选择数量后执行。`,
          okText: '确认拆解',
          okButtonProps: { danger: true },
          cancelText: '取消',
          onOk: () => setDisFor(r),
        });
      },
    });
  };
  const [viewMode, setViewMode] = useState<'curated' | 'full'>('curated');

  const { data: rows = [] } = useQuery({
    queryKey: ['aftersales'],
    queryFn: () => fetchAfterSales(),
    refetchInterval: 30000,
  });

  const { data: paymentLinkData } = useQuery({
    queryKey: ['aftersales-payment-links', 'proposed'],
    queryFn: () => fetchAfterSalesPaymentLinks('proposed'),
    refetchInterval: 30000,
  });
  const paymentLinks = paymentLinkData?.rows ?? [];

  const refreshPaymentViews = () => {
    qc.invalidateQueries({ queryKey: ['aftersales-payment-links'] });
    qc.invalidateQueries({ queryKey: ['aftersales'] });
    qc.invalidateQueries({ queryKey: ['dashboard'] });
    qc.invalidateQueries({ queryKey: ['per-order-reconcile'] });
  };
  const scanPaymentMut = useMutation({
    mutationFn: () => scanAfterSalesPaymentLinks('2026-07-01', new Date().toISOString().slice(0, 10), false),
    onSuccess: (r: any) => {
      message.success(`已建 ${r.created ?? 0} 条候选，未改任何财务金额`);
      refreshPaymentViews();
    },
    onError: (e: any) => message.error(e?.response?.data?.detail ?? '扫描失败'),
  });
  const confirmPaymentMut = useMutation({
    mutationFn: (v: {
      row: AfterSalesPaymentLink; orderNo: string; category: string;
      target: 'aftersales' | 'order_install'; clearWanshifu: boolean;
    }) => confirmAfterSalesPaymentLink(v.row.id, {
      expected_version: v.row.version,
      order_no: v.orderNo.trim(),
      category: v.category,
      accounting_target: v.target,
      clear_wanshifu: v.clearWanshifu,
      note: '在售后页核对原流水、订单和用途后确认',
    }),
    onSuccess: (r) => {
      message.success(r.accounting_target === 'order_install'
        ? '已联动写入订单安装费、财务和逐单核对'
        : '已联动写入售后、财务和逐单核对');
      setPaymentConfirmFor(null);
      refreshPaymentViews();
    },
    onError: (e: any) => message.error(e?.response?.data?.detail ?? '确认失败'),
  });
  const rejectPaymentMut = useMutation({
    mutationFn: (r: AfterSalesPaymentLink) => rejectAfterSalesPaymentLink(
      r.id, r.version, '已核对：不应计入该售后',
    ),
    onSuccess: () => { message.success('已排除，原流水保留'); refreshPaymentViews(); },
    onError: (e: any) => message.error(e?.response?.data?.detail ?? '排除失败'),
  });

  const createMut = useMutation({
    mutationFn: createReturn,
    onSuccess: () => {
      message.success('退货已创建');
      setCreateOpen(false);
      qc.invalidateQueries({ queryKey: ['aftersales'] });
    },
    onError: (e: any) => message.error(e?.response?.data?.detail ?? '创建失败'),
  });

  const recvMut = useMutation({
    mutationFn: (id: number) => markReturnReceived(id),
    onSuccess: () => {
      message.success('已标记签收');
      qc.invalidateQueries({ queryKey: ['aftersales'] });
    },
  });

  const damagedMut = useMutation({
    mutationFn: (args: { id: number; reason: string }) => markReturnDamaged(args.id, args.reason),
    onSuccess: () => {
      message.success('已标记损坏');
      qc.invalidateQueries({ queryKey: ['aftersales'] });
    },
  });

  // 补填快递单号 (退回/补发); 填了自动纳入物流追踪
  const patchMut = useMutation({
    mutationFn: ({ id, patch }: { id: number; patch: Parameters<typeof updateAfterSales>[1] }) =>
      updateAfterSales(id, patch),
    onSuccess: () => {
      message.success('已保存, 单号将自动追踪');
      qc.invalidateQueries({ queryKey: ['aftersales'] });
    },
    onError: (e: any) => message.error(e?.response?.data?.detail ?? '保存失败'),
  });

  return (
    <Space direction="vertical" style={{ width: '100%' }} size="middle">
      <Alert
        type="info" showIcon
        message="退货 / 售后（含售后金额统计 · 原「营销与经营 → 售后」已并入此处，统一在这里看，不再分开）"
        description="① 创建退货 + 填快递单号 → ② 系统追踪快递, 签收后弹窗待确认 → ③ 检查完好 → 整产品入库 (不拆 BOM); 损坏 → 不入库, 留警告"
      />
      <Card
        size="small"
        title={`个人支付宝售后待匹配（${paymentLinks.length}）`}
        extra={
          <Popconfirm
            title="只扫描并建候选，不改财务金额"
            onConfirm={() => scanPaymentMut.mutate()}
          >
            <Button loading={scanPaymentMut.isPending}>扫描 7 月至今流水</Button>
          </Popconfirm>
        }
      >
        <Alert
          type="warning" showIcon style={{ marginBottom: 10 }}
          message="只有订单唯一才可确认；送装/直达可能是正常安装费，不会自动当退款"
        />
        <Table<AfterSalesPaymentLink>
          size="small" rowKey="id" dataSource={paymentLinks}
          pagination={{ pageSize: 20, showSizeChanger: true }}
          scroll={{ x: 1050 }}
          columns={[
            { title: '时间', width: 105, render: (_, r) => r.flow?.time?.slice(0, 10) ?? '—' },
            { title: '金额', dataIndex: 'allocated_amount', width: 85, align: 'right',
              render: (v: number) => <b>¥{Number(v).toFixed(2)}</b> },
            { title: '流水备注', width: 250, ellipsis: true,
              render: (_, r) => <Tooltip title={r.flow?.remark}>{r.flow?.remark || '—'}</Tooltip> },
            { title: '建议类型', dataIndex: 'category_label', width: 120,
              render: (v: string) => <Tag color="blue">{v}</Tag> },
            { title: '候选订单', width: 245,
              render: (_, r) => r.order ? (
                <div><b>{r.order.customer_name || '未填客户'}</b> · {r.order.order_no}<br />
                  <Typography.Text type="secondary" ellipsis>{r.order.product_name}</Typography.Text></div>
              ) : <Tag color="orange">无唯一订单</Tag> },
            { title: '判定', dataIndex: 'decision_note', width: 240, ellipsis: true,
              render: (v: string) => <Tooltip title={v}>{v || '—'}</Tooltip> },
            { title: '操作', fixed: 'right', width: 145,
              render: (_, r) => <Space>
                <Button
                  size="small" type="primary"
                  onClick={() => {
                    setPaymentConfirmFor(r);
                    setPaymentOrderNo(r.order?.order_no ?? '');
                    setPaymentCategory(r.category);
                    setPaymentTarget(r.accounting_target ?? 'aftersales');
                    setPaymentClearWanshifu(false);
                  }}
                >
                  核对
                </Button>
                <Popconfirm title="确认这笔不应计入该售后？" onConfirm={() => rejectPaymentMut.mutate(r)}>
                  <Button size="small">排除</Button>
                </Popconfirm>
              </Space> },
          ]}
        />
      </Card>
      <Modal
        open={paymentConfirmFor != null}
        title="确认个人支付宝售后归属"
        okText="确认并联动入账"
        cancelText="取消"
        okButtonProps={{
          disabled: !paymentOrderNo.trim() || !paymentCategory,
          loading: confirmPaymentMut.isPending,
        }}
        onCancel={() => setPaymentConfirmFor(null)}
        onOk={() => paymentConfirmFor && confirmPaymentMut.mutate({
          row: paymentConfirmFor,
          orderNo: paymentOrderNo,
          category: paymentCategory,
          target: paymentTarget,
          clearWanshifu: paymentClearWanshifu,
        })}
      >
        <Alert
          type="warning" showIcon style={{ marginBottom: 12 }}
          message="请核对真实 ERP 订单号和用途；送装、维修、退回可能与正常履约或万师傅重复。"
        />
        <Space direction="vertical" style={{ width: '100%' }}>
          <Typography.Text>原备注：{paymentConfirmFor?.flow?.remark || '—'}</Typography.Text>
          <Typography.Text>支出金额：¥{Number(paymentConfirmFor?.allocated_amount ?? 0).toFixed(2)}</Typography.Text>
          <Input
            value={paymentOrderNo}
            onChange={(e) => setPaymentOrderNo(e.target.value)}
            placeholder="输入 ERP 中真实存在的订单号"
          />
          <Select
            value={paymentCategory}
            style={{ width: '100%' }}
            onChange={setPaymentCategory}
            options={[
              { value: 'price_difference', label: '差价退补' },
              { value: 'review_refund', label: '晒图/好评返现' },
              { value: 'customer_compensation', label: '客户赔付' },
              { value: 'repair_service', label: '售后维修' },
              { value: 'onsite_service', label: '上门/送装服务' },
              { value: 'return_service', label: '退回/返厂服务' },
              { value: 'misc_after_sales', label: '其他售后' },
            ]}
          />
          <Select
            value={paymentTarget}
            style={{ width: '100%' }}
            onChange={setPaymentTarget}
            options={[
              { value: 'aftersales', label: '售后成本（差价、返现、赔付、维修、返厂）' },
              { value: 'order_install', label: '订单安装费（正常送装、直达）' },
            ]}
          />
          {paymentConfirmFor?.wanshifu_order && (
            <Space direction="vertical">
              <Alert
                type="info" showIcon
                message={`发现万师傅单 ${paymentConfirmFor.wanshifu_order.order_no}，确认前请排除重复记账`}
              />
              <Checkbox
                checked={paymentClearWanshifu}
                onChange={(e) => setPaymentClearWanshifu(e.target.checked)}
              >
                已核对：这笔个人支付宝支出与该万师傅单不是同一笔费用
              </Checkbox>
            </Space>
          )}
        </Space>
      </Modal>
      <Segmented
        value={viewMode}
        onChange={(v) => setViewMode(v as 'curated' | 'full')}
        options={[
          { label: '精选视图（可编辑）', value: 'curated' },
          { label: '全部列', value: 'full' },
        ]}
      />

      {viewMode === 'full' && <FullColumnView entity="aftersales" defaultShowAll />}

      {viewMode === 'curated' && (
      <Card title="退货/售后记录" size="small"
            extra={
              <Space>
                <Button onClick={() => setHistoryOpen(true)}>拆BOM 历史</Button>
                <Button type="primary" onClick={() => setCreateOpen(true)}>新建退货</Button>
              </Space>
            }>
        <ResponsiveTable<AfterSalesItem>
          data={rows}
          rowKey={(r) => r.id}
          emptyText="暂无售后"
          renderCard={(r) => (
            <StatusCard
              title={r.product_name || '(未关联产品)'}
              status={STATUS_LABEL[r.status ?? ''] ?? r.status ?? '—'}
              tone={STATUS_TONE[r.status ?? ''] ?? 'info'}
              fields={[
                { label: '订单', value: r.platform_order_no || '—' },
                { label: '客户', value: r.customer_name || '未关联' },
                { label: '原因', value: r.reason || '—' },
              ]}
              amount={r.total_cost != null && Number(r.total_cost) ? `¥${Number(r.total_cost).toLocaleString()}` : undefined}
              actions={[{ label: '拆BOM', onClick: () => confirmDisassemble(r) }]}
            />
          )}
          desktop={
        <PresetTable
          tableKey="aftersales"
          size="small"
          rowKey="id"
          dataSource={rows}
          pagination={{ defaultPageSize: 100, showSizeChanger: true, pageSizeOptions: [20, 50, 100, 200] }}
          columns={[
            { title: 'ID', dataIndex: 'id', width: 60 },
            { title: '订单号', dataIndex: 'platform_order_no', width: 170 },
            { title: '客户', dataIndex: 'customer_name', width: 100,
              render: (v: string | null) => v || <Tag color="warning">未关联</Tag> },
            { title: '产品', dataIndex: 'product_name', width: 130, ellipsis: true,
              render: (v: string | null) => v ? <Tooltip title={v}><span>{v}</span></Tooltip> : '-' },
            { title: '状态', dataIndex: 'status', width: 160,
              render: (v: string) => (
                <Tag color={STATUS_COLOR[v ?? ''] ?? 'default'}>
                  {STATUS_LABEL[v ?? ''] ?? v ?? '-'}
                </Tag>
              ),
            },
            { title: '原因', dataIndex: 'reason', width: 130, ellipsis: true,
              render: (v: string | null) => v ? <Tooltip title={v}><span>{v}</span></Tooltip> : '-' },
            { title: '售后金额', dataIndex: 'total_cost', width: 100, align: 'right' as const,
              render: (v: string | null) => v != null && Number(v) ? <b style={{ color: '#cf1322' }}>¥{Number(v).toLocaleString()}</b> : '-' },
            { title: '平台内', dataIndex: 'in_platform_total', width: 90, align: 'right' as const,
              render: (v: string | null) => v != null && Number(v) ? `¥${Number(v).toLocaleString()}` : '-' },
            { title: '平台外', dataIndex: 'out_platform_total', width: 90, align: 'right' as const,
              render: (v: string | null) => v != null && Number(v) ? `¥${Number(v).toLocaleString()}` : '-' },
            { title: '快递单号 (点击可填)', width: 200,
              render: (_: any, r: AfterSalesItem) => (
                <Space direction="vertical" size={2}>
                  <EditableTrackingNo label="退回" value={r.return_tracking_no}
                    onSave={(v) => patchMut.mutate({ id: r.id, patch: { return_tracking_no: v } })} />
                  <EditableTrackingNo label="补发" value={r.refill_tracking_no}
                    onSave={(v) => patchMut.mutate({ id: r.id, patch: { refill_tracking_no: v } })} />
                </Space>
              ),
            },
            { title: '二次确认', dataIndex: 'second_inbound_confirmed', width: 100,
              render: (v: string | null) => v === '是' ? '✓' : v === '否' ? '✗' : '-',
            },
            { title: '物流', width: 240,
              render: (_: any, r: AfterSalesItem) => (
                <Space direction="vertical" size={2}>
                  <Space size={4}><Tag color="blue">客户退回</Tag><ShipmentTracker entityType="after_sales_return" entityId={r.id} /></Space>
                  <Space size={4}><Tag color="green">我方补发</Tag><ShipmentTracker entityType="after_sales_refill" entityId={r.id} /></Space>
                </Space>
              ),
            },
            { title: '操作', fixed: 'right', width: 280,
              render: (_: any, r: AfterSalesItem) => (
                <Space>
                  <Button size="small" danger onClick={() => confirmDisassemble(r)}>拆BOM</Button>
                  {r.status === 'pending_return' && (
                    <Button size="small" onClick={() => recvMut.mutate(r.id)}>标记签收</Button>
                  )}
                  {(r.status === 'pending_return' || r.status === 'received_pending_inspection') && (
                    <>
                      <Button size="small" type="primary" onClick={() => setInboundFor(r)}>
                        确认入库
                      </Button>
                      <Button size="small" danger
                              onClick={() => Modal.confirm({
                                title: '确认产品损坏不入库?',
                                content: '会留 alert 提醒, 但库存不变',
                                onOk: () => damagedMut.mutate({ id: r.id, reason: '产品损坏' }),
                              })}>
                        损坏
                      </Button>
                    </>
                  )}
                </Space>
              ),
            },
          ]}
        />
        }
        />
      </Card>
      )}

      <CreateReturnModal open={createOpen} onClose={() => setCreateOpen(false)}
                         onSubmit={(v) => createMut.mutate(v)}
                         loading={createMut.isPending} />
      <ConfirmInboundModal item={inboundFor} onClose={() => setInboundFor(null)}
                           onOk={() => qc.invalidateQueries({ queryKey: ['aftersales'] })} />
      <DisassembleModal target={disFor} onClose={() => setDisFor(null)} />
      <DisassemblyHistoryDrawer open={historyOpen} onClose={() => setHistoryOpen(false)} />
    </Space>
  );
}


function CreateReturnModal({ open, onClose, onSubmit, loading }: {
  open: boolean; onClose: () => void; loading: boolean;
  onSubmit: (v: { order_no: string; reason: string; tracking_no?: string }) => void;
}) {
  const [form] = Form.useForm();
  return (
    <Modal open={open} onCancel={onClose} title="新建退货"
           okText="提交" confirmLoading={loading}
           onOk={() => form.submit()} destroyOnClose>
      <Form form={form} layout="vertical" onFinish={onSubmit}>
        <Form.Item name="order_no" label="订单号" rules={[{ required: true }]}>
          <Input placeholder="原订单号 (Order.order_no)" />
        </Form.Item>
        <Form.Item name="reason" label="退货原因" rules={[{ required: true }]}>
          <Input.TextArea placeholder="如客户不喜欢颜色 / 配件破损" />
        </Form.Item>
        <Form.Item name="tracking_no" label="快递单号 (可后补)">
          <Input placeholder="留空会持续弹窗" />
        </Form.Item>
      </Form>
    </Modal>
  );
}

function ConfirmInboundModal({ item, onClose, onOk }: {
  item: AfterSalesItem | null;
  onClose: () => void; onOk: () => void;
}) {
  const [form] = Form.useForm();
  const mut = useMutation({
    mutationFn: (v: { product_code: string; sku_code?: string; qty: number }) =>
      confirmReturnInbound(item!.id, v),
    onSuccess: () => {
      message.success('已入库 (整产品)');
      onOk();
      onClose();
    },
  });
  if (!item) return null;
  return (
    <Modal open={true} title={`确认入库: 订单 ${item.platform_order_no}`}
           onCancel={onClose}
           okText="确认入库" confirmLoading={mut.isPending}
           onOk={() => form.submit()} destroyOnClose>
      <Alert type="warning" showIcon style={{ marginBottom: 12 }}
             message="按整产品入库, 不会拆成 BOM 物料"
             description="如需拆分, 在退货页点 [拆 BOM] 按钮单独处理" />
      <Form form={form} layout="vertical" onFinish={(v) => mut.mutate(v)}>
        <Form.Item name="product_code" label="产品编码" rules={[{ required: true }]}>
          <Input />
        </Form.Item>
        <Form.Item name="sku_code" label="SKU 编码 (可选)">
          <Input />
        </Form.Item>
        <Form.Item name="qty" label="数量" rules={[{ required: true }]}
                   initialValue={1}>
          <InputNumber min={1} />
        </Form.Item>
      </Form>
    </Modal>
  );
}

// 拆 BOM 历史抽屉 (用户需求 2026-06-11: 留痕 + 可回撤, 误操作可补救)
function DisassemblyHistoryDrawer({ open, onClose }: { open: boolean; onClose: () => void }) {
  const qc = useQueryClient();
  const { data: logs, isLoading } = useQuery({
    queryKey: ['disassembly-logs'], queryFn: listDisassemblyLogs, enabled: open,
  });
  const undoMut = useMutation({
    mutationFn: (id: number) => undoDisassembly(id),
    onSuccess: () => {
      message.success('已回撤: 成品加回, 物料扣回');
      qc.invalidateQueries({ queryKey: ['disassembly-logs'] });
      qc.invalidateQueries({ queryKey: ['aftersales'] });
    },
    onError: (e: any) => message.error(e?.response?.data?.detail ?? '回撤失败'),
  });
  return (
    <Drawer open={open} onClose={onClose} title="拆 BOM 历史 (可回撤)" width={760}>
      <Table<DisassemblyLogRow>
        size="small" rowKey="id" loading={isLoading}
        dataSource={logs ?? []}
        pagination={{ defaultPageSize: 20 }}
        columns={[
          { title: '时间', dataIndex: 'created_at', width: 150,
            render: (v: string | null) => v ? v.slice(0, 16).replace('T', ' ') : '-' },
          { title: '产品', dataIndex: 'product_code', width: 150,
            render: (v: string, r) => <span>{v}{r.sku_code ? ` / ${r.sku_code}` : ''}</span> },
          { title: '数量', dataIndex: 'qty', width: 70,
            render: (v: number) => String(Number(v)) },
          { title: '拆出物料', render: (_: any, r) => (
              <Space size={4} wrap>
                {(r.parts ?? []).slice(0, 4).map((p) => (
                  <Tag key={p.material_code} style={{ fontSize: 11 }}>{p.material_code} +{Number(p.qty)}</Tag>
                ))}
                {(r.parts ?? []).length > 4 && <Tag style={{ fontSize: 11 }}>+{r.parts.length - 4}</Tag>}
              </Space>
            ) },
          { title: '操作人', dataIndex: 'actor', width: 90 },
          { title: '状态', width: 160, render: (_: any, r) => r.undone_at
              ? <Tag>已回撤 ({r.undone_by})</Tag>
              : (
                <Popconfirm
                  title="回撤这次拆解？"
                  description="成品数量加回、拆出的物料全部扣回。物料若已被领用不够扣, 会拒绝回撤。"
                  okText="确认回撤" okButtonProps={{ danger: true }}
                  onConfirm={() => undoMut.mutate(r.id)}
                >
                  <Button size="small" danger loading={undoMut.isPending}>回撤</Button>
                </Popconfirm>
              ) },
        ]}
      />
    </Drawer>
  );
}

function DisassembleModal({ target, onClose }: { target: AfterSalesItem | null; onClose: () => void }) {
  const [form] = Form.useForm();
  const [result, setResult] = useState<any>(null);
  const mut = useMutation({
    mutationFn: (v: { product_code: string; sku_code?: string; qty: number }) =>
      disassembleProduct(v),
    onSuccess: (r) => {
      setResult(r);
      message.success(`拆分完成, ${r.parts_added.length} 种物料增加`);
    },
    onError: (e: any) => message.error(e?.response?.data?.detail ?? '拆分失败'),
  });
  // 行内打开时预填该单品的产品/SKU 编码
  useEffect(() => {
    if (target) {
      setResult(null);
      form.setFieldsValue({
        product_code: target.product_code || undefined,
        sku_code: target.sku_code || undefined,
        qty: 1,
      });
    }
  }, [target, form]);
  return (
    <Modal open={!!target} title={`拆 BOM (成品 → 物料)${target?.product_name ? ` — ${target.product_name}` : ''}`}
           onCancel={() => { setResult(null); onClose(); }}
           onOk={() => form.submit()}
           okText="执行拆解" okButtonProps={{ danger: true }}
           confirmLoading={mut.isPending} destroyOnClose>
      <Form form={form} layout="vertical" onFinish={(v) => mut.mutate(v)}>
        <Form.Item name="product_code" label="产品编码" rules={[{ required: true }]}>
          <Input />
        </Form.Item>
        <Form.Item name="sku_code" label="SKU (可选)">
          <Input />
        </Form.Item>
        <Form.Item name="qty" label="数量" rules={[{ required: true }]} initialValue={1}>
          <InputNumber min={1} />
        </Form.Item>
      </Form>
      {result && (
        <Alert type="success" message={`成品剩余 ${result.product_remaining} 件`}
               description={
                 <ul>
                   {result.parts_added.map((p: any, i: number) =>
                     <li key={i}>{p.material_code}: +{p.qty}</li>)}
                 </ul>
               } />
      )}
    </Modal>
  );
}
