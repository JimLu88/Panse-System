import { useState } from 'react';
import {
  Alert,
  AutoComplete,
  Badge,
  Button,
  Collapse,
  Form,
  Input,
  InputNumber,
  Modal,
  Segmented,
  Space,
  Switch,
  Table,
  Tag,
  Tooltip,
  Typography,
  message,
} from 'antd';
import { PlusOutlined, ReloadOutlined, SyncOutlined } from '@ant-design/icons';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  ProductInventoryRow,
  addProductInventoryRow,
  listProductInventory,
  listProducts,
  refreshProductInventoryStats,
  updateProductInventory,
} from '../api/client';
import FullColumnView from '../components/FullColumnView';

const STATUS_CONFIG: Record<string, { color: string; label: string }> = {
  ok:       { color: 'success', label: '正常' },
  warning:  { color: 'warning', label: '即将不足' },
  danger:   { color: 'error',   label: '低于预警线' },
  critical: { color: 'error',   label: '库存告急' },
  excess:   { color: 'default', label: '滞销/超量' },
};

export default function ProductInventoryPage() {
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  const [form] = Form.useForm();
  const [productSearch, setProductSearch] = useState('');
  const [editId, setEditId] = useState<number | null>(null);
  const [editForm] = Form.useForm();
  const [warningOnly, setWarningOnly] = useState(false);
  const [viewMode, setViewMode] = useState<'curated' | 'full'>('curated');

  const { data, isLoading } = useQuery({
    queryKey: ['product-inventory', warningOnly],
    // 全部视图带出所有产品(含还没建库存行的, 前端折叠); 仅预警视图后端会忽略 include_all
    queryFn: () => listProductInventory(warningOnly, !warningOnly),
  });

  const { data: products } = useQuery({
    queryKey: ['products', productSearch],
    queryFn: () => listProducts(productSearch || undefined),
  });

  const refreshMut = useMutation({
    mutationFn: refreshProductInventoryStats,
    onSuccess: (res) => {
      message.success(res.message);
      qc.invalidateQueries({ queryKey: ['product-inventory'] });
    },
  });

  const addMut = useMutation({
    mutationFn: (v: Parameters<typeof addProductInventoryRow>[0]) => addProductInventoryRow(v),
    onSuccess: () => {
      message.success('已添加');
      qc.invalidateQueries({ queryKey: ['product-inventory'] });
      setOpen(false);
      form.resetFields();
    },
  });

  const editMut = useMutation({
    mutationFn: ({ id, patch }: { id: number; patch: Parameters<typeof updateProductInventory>[1] }) =>
      updateProductInventory(id, patch),
    onSuccess: () => {
      message.success('已保存');
      qc.invalidateQueries({ queryKey: ['product-inventory'] });
      setEditId(null);
    },
  });

  const columns = [
    {
      title: '产品', dataIndex: 'sku', width: 230,
      render: (_: any, r: ProductInventoryRow) => (
        <Space direction="vertical" size={0}>
          <Typography.Text strong style={{ fontSize: 14 }}>{r.sku || r.product_name || r.product_code}</Typography.Text>
          <Space size={4}>
            <Typography.Text type="secondary" style={{ fontSize: 11 }}>{r.product_code}</Typography.Text>
            {r.has_inventory === false && <Tag color="default" style={{ fontSize: 10, lineHeight: '16px' }}>无库存行</Tag>}
          </Space>
        </Space>
      ),
    },
    { title: '仓库', dataIndex: 'warehouse', width: 80 },
    {
      title: '库存状态',
      dataIndex: 'warning_status',
      width: 100,
      render: (s: string) => {
        const cfg = STATUS_CONFIG[s] || { color: 'default', label: s };
        return <Badge status={cfg.color as any} text={cfg.label} />;
      },
    },
    {
      title: '现货 / 可用',
      width: 110,
      render: (_: any, r: ProductInventoryRow) => (
        <Space direction="vertical" size={0}>
          <span>现货 {Number(r.physical_qty).toFixed(0)}</span>
          <Typography.Text type={r.available_qty < 0 ? 'danger' : 'secondary'} style={{ fontSize: 12 }}>
            可用 {r.available_qty.toFixed(0)}
          </Typography.Text>
        </Space>
      ),
    },
    {
      title: (
        <Tooltip title="该产品(所有尺寸合计)近30天真实订单日均发货量（不含补单）；同一产品各尺寸行共享此值">日均销量</Tooltip>
      ),
      dataIndex: 'daily_sales_30d',
      width: 90,
      render: (v: number) => v > 0 ? v.toFixed(2) : <Typography.Text type="secondary">暂无</Typography.Text>,
    },
    {
      title: (
        <Tooltip title="按日均销量折算的库存可用天数">可用天数</Tooltip>
      ),
      dataIndex: 'days_of_stock',
      width: 90,
      render: (v: number | null) => {
        if (v === null) return <Typography.Text type="secondary">—</Typography.Text>;
        const color = v < 14 ? '#ff4d4f' : v < 30 ? '#fa8c16' : '#52c41a';
        return <span style={{ color }}>{v.toFixed(0)} 天</span>;
      },
    },
    {
      title: (
        <Tooltip title="当可用库存 ≤ 预警线时触发警告，建议补货">预警线</Tooltip>
      ),
      width: 90,
      render: (_: any, r: ProductInventoryRow) => (
        <span>{(r.reorder_point ?? r.reorder_point_computed).toFixed(0)}</span>
      ),
    },
    {
      title: (
        <Tooltip title="安全库存：最低不能低于的库存量">安全库存</Tooltip>
      ),
      width: 90,
      render: (_: any, r: ProductInventoryRow) => (
        <span>{(r.safety_stock ?? r.safety_stock_computed).toFixed(0)}</span>
      ),
    },
    {
      title: (
        <Tooltip title="工厂平均交货天数（手填 > 工厂历史推算 > 一般家具默认 30 天）">提前期(天)</Tooltip>
      ),
      width: 90,
      render: (_: any, r: ProductInventoryRow) => {
        const v = r.lead_time_days ?? r.lead_time_days_computed;
        if (v !== null && v !== undefined) return `${v}天`;
        return (
          <Tooltip title="未手填、也无工厂历史，按一般家具默认 30 天估算（可在编辑里改）">
            <Typography.Text type="secondary">30天<span style={{ fontSize: 10 }}> 默认</span></Typography.Text>
          </Tooltip>
        );
      },
    },
    {
      title: (
        <Tooltip title="建议补货量 = 预警线×2 − 当前可用量">推荐备货</Tooltip>
      ),
      dataIndex: 'auto_reorder_qty',
      width: 90,
      render: (v: number) => v > 0
        ? <Tag color="blue">{v.toFixed(0)} {}</Tag>
        : <Typography.Text type="secondary">充足</Typography.Text>,
    },
    {
      title: '滞销阈值',
      dataIndex: 'slow_moving_days',
      width: 80,
      render: (v: number | null) => v ? `${v}天` : '60天',
    },
    {
      title: '操作',
      width: 80,
      render: (_: any, r: ProductInventoryRow) => (
        r.has_inventory === false || r.id == null ? (
          <Button size="small" type="link" onClick={() => {
            form.setFieldsValue({ product_code: r.product_code });
            setOpen(true);
          }}>建库存</Button>
        ) : (
        <Button size="small" onClick={() => {
          setEditId(r.id);
          editForm.setFieldsValue({
            qty: Number(r.physical_qty),
            locked_qty: Number(r.locked_qty),
            safety_stock: r.safety_stock !== null ? Number(r.safety_stock) : undefined,
            lead_time_days: r.lead_time_days,
            slow_moving_days: r.slow_moving_days ?? 60,
            reorder_point: r.reorder_point !== null ? Number(r.reorder_point) : undefined,
            remark: r.remark,
          });
        }}>编辑</Button>
        )
      ),
    },
  ];

  const warningCount = data?.filter(r => r.warning_status !== 'ok').length ?? 0;

  // 三类: ① 需预警(全显示) ② 已建库存但不预警(折叠) ③ 还没建库存行的产品(折叠)
  const rows = data ?? [];
  const alertRows = rows.filter((r) => r.warning_status !== 'ok');
  const normalRows = rows.filter((r) => r.has_inventory !== false && r.warning_status === 'ok');
  const noInvRows = rows.filter((r) => r.has_inventory === false);

  const renderInvTable = (list: ProductInventoryRow[], paginate: boolean) => (
    <Table
      rowKey={(r) => (r.id != null ? String(r.id) : 'p:' + r.product_code)}
      columns={columns}
      dataSource={list}
      loading={isLoading}
      pagination={paginate ? { pageSize: 50 } : false}
      scroll={{ x: 1280 }}
      rowClassName={(r) =>
        r.warning_status === 'critical' ? 'ant-table-row-danger' :
        r.warning_status === 'danger' ? 'ant-table-row-warning' : ''
      }
      size="small"
    />
  );

  return (
    <Space direction="vertical" style={{ width: '100%' }} size="middle">
      <Space style={{ justifyContent: 'space-between', width: '100%' }}>
        <Space>
          <Typography.Title level={4} style={{ margin: 0 }}>成品库存</Typography.Title>
          {warningCount > 0 && (
            <Tag color="red">{warningCount} 项需关注</Tag>
          )}
        </Space>
        <Space>
          <Switch
            checked={warningOnly}
            onChange={setWarningOnly}
            checkedChildren="仅预警"
            unCheckedChildren="全部"
          />
          <Button
            icon={<SyncOutlined />}
            loading={refreshMut.isPending}
            onClick={() => refreshMut.mutate()}
          >
            重算推算字段
          </Button>
          <Button icon={<ReloadOutlined />} onClick={() => qc.invalidateQueries({ queryKey: ['product-inventory'] })}>
            刷新
          </Button>
          <Button type="primary" icon={<PlusOutlined />} onClick={() => setOpen(true)}>
            添加库存
          </Button>
        </Space>
      </Space>

      {warningCount > 0 && (
        <Alert
          type="warning"
          showIcon
          message={`${warningCount} 个 SKU 库存状态需关注（低于预警线、告急或滞销）`}
        />
      )}

      <Segmented
        value={viewMode}
        onChange={(v) => setViewMode(v as 'curated' | 'full')}
        options={[
          { label: '精选视图（可编辑）', value: 'curated' },
          { label: '全部列', value: 'full' },
        ]}
      />

      {viewMode === 'full' && <FullColumnView entity="product_inventory" defaultShowAll />}

      {viewMode === 'curated' && (warningOnly ? (
        renderInvTable(data ?? [], true)
      ) : (
        <Space direction="vertical" style={{ width: '100%' }} size="small">
          {/* ① 需预警的产品: 全部显示 */}
          <Typography.Text strong style={{ color: alertRows.length ? '#cf1322' : undefined }}>
            ⚠️ 需关注 · 预警（{alertRows.length}）{alertRows.length === 0 ? ' — 暂无' : ''}
          </Typography.Text>
          {alertRows.length > 0 && renderInvTable(alertRows, true)}

          {/* ② 已建库存但不预警: 折叠 */}
          <Collapse
            items={[{
              key: 'normal',
              label: `有货 · 库存正常（${normalRows.length}）— 点击展开`,
              children: renderInvTable(normalRows, true),
            }]}
          />

          {/* ③ 还没建库存行的产品: 折叠 */}
          {noInvRows.length > 0 && (
            <Collapse
              items={[{
                key: 'noinv',
                label: `还没建库存行的产品（${noInvRows.length}）— 点击展开 · 可「建库存」`,
                children: renderInvTable(noInvRows, true),
              }]}
            />
          )}
        </Space>
      ))}

      {/* 添加库存弹窗 */}
      <Modal
        title="添加成品库存"
        open={open}
        onOk={() => form.submit()}
        onCancel={() => { setOpen(false); form.resetFields(); }}
        confirmLoading={addMut.isPending}
      >
        <Form form={form} layout="vertical" onFinish={(v) => addMut.mutate(v)}>
          <Form.Item name="warehouse" label="仓库" rules={[{ required: true }]}>
            <Input />
          </Form.Item>
          <Form.Item name="product_code" label="产品编码" rules={[{ required: true }]}>
            <AutoComplete
              options={(products || []).map(p => ({ value: p.code, label: `${p.code} ${p.name || ''}` }))}
              onSearch={setProductSearch}
              filterOption={(input, opt) => (opt?.label as string || '').toLowerCase().includes(input.toLowerCase())}
            />
          </Form.Item>
          <Form.Item name="sku" label="SKU"><Input /></Form.Item>
          <Form.Item name="physical_qty" label="现货数量" initialValue={0}>
            <InputNumber min={0} style={{ width: '100%' }} />
          </Form.Item>
          <Form.Item name="slow_moving_days" label="滞销预警天数" initialValue={60}>
            <InputNumber min={1} style={{ width: '100%' }} />
          </Form.Item>
          <Form.Item name="remark" label="备注"><Input /></Form.Item>
        </Form>
      </Modal>

      {/* 编辑弹窗 */}
      <Modal
        title="编辑库存参数"
        open={editId !== null}
        onOk={() => editForm.submit()}
        onCancel={() => setEditId(null)}
        confirmLoading={editMut.isPending}
      >
        <Alert
          type="info"
          showIcon
          style={{ marginBottom: 12 }}
          message="安全库存、提前期、预警线若不填，系统会根据订单历史自动推算。"
        />
        <Form
          form={editForm}
          layout="vertical"
          onFinish={(v) => editId && editMut.mutate({
            id: editId,
            patch: {
              qty: v.qty,
              locked_qty: v.locked_qty,
              safety_stock: v.safety_stock,
              lead_time_days: v.lead_time_days,
              slow_moving_days: v.slow_moving_days,
              reorder_point: v.reorder_point,
              remark: v.remark,
            },
          })}
        >
          <Form.Item name="qty" label="现货数量">
            <InputNumber min={0} style={{ width: '100%' }} />
          </Form.Item>
          <Form.Item name="locked_qty" label="锁定数量">
            <InputNumber min={0} style={{ width: '100%' }} />
          </Form.Item>
          <Form.Item name="safety_stock" label="安全库存（留空=系统推算）">
            <InputNumber min={0} style={{ width: '100%' }} />
          </Form.Item>
          <Form.Item name="lead_time_days" label="提前期天数（留空=从工厂历史推算）">
            <InputNumber min={1} style={{ width: '100%' }} />
          </Form.Item>
          <Form.Item name="reorder_point" label="预警线（留空=自动计算）">
            <InputNumber min={0} style={{ width: '100%' }} />
          </Form.Item>
          <Form.Item name="slow_moving_days" label="滞销预警天数">
            <InputNumber min={1} style={{ width: '100%' }} />
          </Form.Item>
          <Form.Item name="remark" label="备注"><Input /></Form.Item>
        </Form>
      </Modal>
    </Space>
  );
}
