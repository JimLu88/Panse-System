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
import { useState } from 'react';
import ShipmentTracker from '../components/ShipmentTracker';
import {
  Alert,
  Button,
  Card,
  Form,
  Input,
  InputNumber,
  Modal,
  Segmented,
  Space,
  Table,
  Tag,
  Typography,
  message,
} from 'antd';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import FullColumnView from '../components/FullColumnView';
import {
  AfterSalesItem,
  confirmReturnInbound,
  createReturn,
  disassembleProduct,
  fetchAfterSales,
  markReturnDamaged,
  markReturnReceived,
} from '../api/client';

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


export default function AfterSalesPage() {
  const qc = useQueryClient();
  const [createOpen, setCreateOpen] = useState(false);
  const [inboundFor, setInboundFor] = useState<AfterSalesItem | null>(null);
  const [disOpen, setDisOpen] = useState(false);
  const [viewMode, setViewMode] = useState<'curated' | 'full'>('curated');

  const { data: rows = [] } = useQuery({
    queryKey: ['aftersales'],
    queryFn: () => fetchAfterSales(),
    refetchInterval: 30000,
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

  return (
    <Space direction="vertical" style={{ width: '100%' }} size="middle">
      <Alert
        type="info" showIcon
        message="退货闭环流程"
        description="① 创建退货 + 填快递单号 → ② 系统追踪快递, 签收后弹窗待确认 → ③ 检查完好 → 整产品入库 (不拆 BOM); 损坏 → 不入库, 留警告"
      />
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
                <Button onClick={() => setDisOpen(true)}>拆 BOM (成品 → 物料)</Button>
                <Button type="primary" onClick={() => setCreateOpen(true)}>新建退货</Button>
              </Space>
            }>
        <Table
          size="small"
          rowKey="id"
          dataSource={rows}
          pagination={{ pageSize: 20 }}
          columns={[
            { title: 'ID', dataIndex: 'id', width: 60 },
            { title: '订单号', dataIndex: 'platform_order_no', width: 180 },
            { title: '状态', dataIndex: 'status', width: 160,
              render: (v: string) => (
                <Tag color={STATUS_COLOR[v ?? ''] ?? 'default'}>
                  {STATUS_LABEL[v ?? ''] ?? v ?? '-'}
                </Tag>
              ),
            },
            { title: '原因', dataIndex: 'reason', ellipsis: true },
            { title: '快递单号', dataIndex: 'refill_tracking_no', width: 140,
              render: (v: string | null) =>
                v ? v : <Tag color="warning">未填</Tag>,
            },
            { title: '二次确认', dataIndex: 'second_inbound_confirmed', width: 100,
              render: (v: string | null) => v === '是' ? '✓' : v === '否' ? '✗' : '-',
            },
            { title: '物流(补发/返厂)', width: 210,
              render: (_: any, r: AfterSalesItem) => (
                <Space direction="vertical" size={2}>
                  <ShipmentTracker entityType="after_sales_refill" entityId={r.id} />
                  <ShipmentTracker entityType="after_sales_return" entityId={r.id} />
                </Space>
              ),
            },
            { title: '操作', fixed: 'right', width: 280,
              render: (_: any, r: AfterSalesItem) => (
                <Space>
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
      </Card>
      )}

      <CreateReturnModal open={createOpen} onClose={() => setCreateOpen(false)}
                         onSubmit={(v) => createMut.mutate(v)}
                         loading={createMut.isPending} />
      <ConfirmInboundModal item={inboundFor} onClose={() => setInboundFor(null)}
                           onOk={() => qc.invalidateQueries({ queryKey: ['aftersales'] })} />
      <DisassembleModal open={disOpen} onClose={() => setDisOpen(false)} />
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

function DisassembleModal({ open, onClose }: { open: boolean; onClose: () => void }) {
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
  return (
    <Modal open={open} title="拆 BOM (成品 → 物料)"
           onCancel={() => { setResult(null); onClose(); }}
           onOk={() => form.submit()}
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
