import { useState } from 'react';
import {
  Button,
  Form,
  Input,
  InputNumber,
  Modal,
  Popover,
  Select,
  Segmented,
  Space,
  Table,
  Tag,
  Typography,
  message,
} from 'antd';
import { PlusOutlined } from '@ant-design/icons';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import FullColumnView from '../components/FullColumnView';
import FieldPresetBar, { fieldsFromColumns, applyPreset } from '../components/FieldPresetBar';
import { Material, createMaterial, getMaterialUsedInProducts, getNextMaterialCode, listMaterials, updateMaterial } from '../api/client';

// #5: 物料反推产品 — 点「查看」弹出 BOM 里用到此物料的产品(懒加载)
function UsedInCell({ code }: { code: string }) {
  const [open, setOpen] = useState(false);
  const { data, isFetching } = useQuery({
    queryKey: ['material-used-in', code],
    queryFn: () => getMaterialUsedInProducts(code),
    enabled: open,
  });
  const content = (
    <div style={{ maxWidth: 400, maxHeight: 300, overflow: 'auto' }}>
      {isFetching ? (
        <Typography.Text type="secondary">加载中…</Typography.Text>
      ) : data && data.length > 0 ? (
        <Table
          size="small" rowKey="product_code" pagination={false} dataSource={data}
          columns={[
            { title: '产品', dataIndex: 'product_name', ellipsis: true, render: (v: string | null, r: any) => v || r.product_code },
            { title: '编码', dataIndex: 'product_code', width: 130 },
            { title: '用量', dataIndex: 'qty_per_product', width: 56, align: 'right' as const },
            { title: 'SKU', dataIndex: 'sku_count', width: 48, align: 'right' as const },
          ]}
        />
      ) : (
        <Typography.Text type="secondary">没有产品用到此物料</Typography.Text>
      )}
    </div>
  );
  return (
    <Popover trigger="click" open={open} onOpenChange={setOpen} content={content} title="用到此物料的产品 (BOM 反查)">
      <a>查看</a>
    </Popover>
  );
}

type FilterKey = 'all' | 'standard' | 'custom';
const PREFIX_OPTIONS = [
  { value: 'AC', label: 'AC — 配件/五金' },
  { value: 'MP', label: 'MP — 人力费' },
  { value: 'MW', label: 'MW — 木材' },
  { value: 'SP', label: 'SP — 特殊件' },
];

export default function MaterialsPage() {
  const qc = useQueryClient();
  const [filter, setFilter] = useState<FilterKey>('all');
  const [q, setQ] = useState('');
  const [editing, setEditing] = useState<Material | null>(null);
  const [form] = Form.useForm();
  const [creating, setCreating] = useState(false);
  const [createForm] = Form.useForm();
  const [previewCode, setPreviewCode] = useState<string>('');
  const [previewLoading, setPreviewLoading] = useState(false);
  const [viewMode, setViewMode] = useState<'curated' | 'full'>('curated');
  const [visibleKeys, setVisibleKeys] = useState<string[] | null>(null);
  // #图4: 物料筛选 — 编码前缀 / 床铺板·非床铺板 / 尺寸类型 / 单位
  const [prefix, setPrefix] = useState('all');
  const [bedboard, setBedboard] = useState('all');
  const [sizeType, setSizeType] = useState<string | undefined>(undefined);
  const [unitF, setUnitF] = useState<string | undefined>(undefined);

  const fetchPreview = async (prefix: string) => {
    setPreviewLoading(true);
    try {
      const r = await getNextMaterialCode(prefix);
      setPreviewCode(r.code);
    } catch {
      setPreviewCode('—');
    } finally {
      setPreviewLoading(false);
    }
  };

  const openCreate = () => {
    createForm.resetFields();
    createForm.setFieldsValue({ prefix: 'AC' });
    setCreating(true);
    fetchPreview('AC');
  };

  const isCustom = filter === 'custom' ? true : filter === 'standard' ? false : undefined;

  const { data, isLoading } = useQuery({
    queryKey: ['materials', q, isCustom],
    queryFn: () => listMaterials(q || undefined, isCustom),
  });

  // 客户端筛选(物料表已全量加载) — 前缀/床铺板/尺寸/单位
  const sizeOpts = Array.from(new Set((data ?? []).map((m) => m.size_type).filter(Boolean))) as string[];
  const unitOpts = Array.from(new Set((data ?? []).map((m) => m.unit).filter(Boolean))) as string[];
  const filtered = (data ?? []).filter((m) => {
    if (prefix !== 'all' && !String(m.code || '').toUpperCase().startsWith(prefix)) return false;
    if (bedboard === 'bed' && !/铺板/.test(m.name || '')) return false;
    if (bedboard === 'nonbed' && /铺板/.test(m.name || '')) return false;
    if (sizeType && m.size_type !== sizeType) return false;
    if (unitF && m.unit !== unitF) return false;
    return true;
  });

  const updateMut = useMutation({
    mutationFn: ({ id, patch }: { id: number; patch: Partial<Material> }) =>
      updateMaterial(id, patch),
    onSuccess: () => {
      message.success('已保存');
      setEditing(null);
      qc.invalidateQueries({ queryKey: ['materials'] });
    },
  });

  const createMut = useMutation({
    mutationFn: createMaterial,
    onSuccess: (mat) => {
      message.success(`配件已创建，编码 ${mat.code}`);
      setCreating(false);
      qc.invalidateQueries({ queryKey: ['materials'] });
    },
    onError: (e: any) => message.error(e?.response?.data?.detail ?? '创建失败'),
  });

  const columns = [
    {
      title: '编码',
      dataIndex: 'code',
      width: 110,
      render: (v: string, row: Material) =>
        row.is_custom ? <Tag color="orange">{v}</Tag> : v,
    },
    { title: '名称', dataIndex: 'name', ellipsis: true },
    { title: '尺寸类型', dataIndex: 'size_type', width: 100 },
    { title: '单位', dataIndex: 'unit', width: 80 },
    {
      title: '价格',
      dataIndex: 'price',
      width: 110,
      render: (v: string | null) =>
        v == null ? <Tag color="red">待补</Tag> : <span>{v}</span>,
    },
    { title: '备注', dataIndex: 'remark', ellipsis: true },
    {
      title: '用于产品',
      width: 80,
      render: (_: unknown, row: Material) => <UsedInCell code={row.code} />,
    },
    {
      title: '操作',
      width: 80,
      render: (_: unknown, row: Material) => (
        <a
          onClick={() => {
            setEditing(row);
            form.setFieldsValue(row);
          }}
        >
          编辑
        </a>
      ),
    },
  ];

  return (
    <Space direction="vertical" style={{ width: '100%' }} size="middle">
      <Space style={{ justifyContent: 'space-between', width: '100%' }}>
        <Typography.Title level={4} style={{ margin: 0 }}>
          物料单价库 (3b)
        </Typography.Title>
        <Space>
          <Input.Search
            placeholder="按编码或名称搜索"
            allowClear
            style={{ width: 280 }}
            onSearch={setQ}
          />
          <Segmented<FilterKey>
            value={filter}
            onChange={(v) => setFilter(v as FilterKey)}
            options={[
              { label: '全部', value: 'all' },
              { label: '标准', value: 'standard' },
              { label: '定制 (AC≥1000)', value: 'custom' },
            ]}
          />
          <Button type="primary" icon={<PlusOutlined />} onClick={openCreate}>
            新建配件
          </Button>
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
          <FieldPresetBar
            tableKey="material"
            allFields={fieldsFromColumns(columns)}
            defaults={[{ name: '常用', fields: ['material_code', 'material_name', 'unit', 'calc_price', 'spec'] }]}
            onChange={setVisibleKeys}
          />
        )}
      </Space>

      {viewMode === 'curated' && (
        <Space wrap>
          <Typography.Text type="secondary">筛选:</Typography.Text>
          <Select size="middle" style={{ width: 130 }} value={prefix} onChange={setPrefix}
            options={[{ value: 'all', label: '全部类别' }, { value: 'AC', label: 'AC 配件' }, { value: 'MW', label: 'MW 木料' }, { value: 'MP', label: 'MP 人力' }, { value: 'SP', label: 'SP 特殊' }]} />
          <Select size="middle" style={{ width: 130 }} value={bedboard} onChange={setBedboard}
            options={[{ value: 'all', label: '全部' }, { value: 'bed', label: '仅床铺板' }, { value: 'nonbed', label: '非床铺板' }]} />
          <Select size="middle" allowClear placeholder="尺寸类型" style={{ width: 130 }} value={sizeType} onChange={setSizeType}
            options={sizeOpts.map((s) => ({ value: s, label: s }))} />
          <Select size="middle" allowClear placeholder="单位" style={{ width: 110 }} value={unitF} onChange={setUnitF}
            options={unitOpts.map((s) => ({ value: s, label: s }))} />
          <Typography.Text type="secondary">共 {filtered.length} 项</Typography.Text>
        </Space>
      )}

      {viewMode === 'full' && <FullColumnView entity="material" defaultShowAll />}

      {viewMode === 'curated' && (
      <Table<Material>
        rowKey="id"
        loading={isLoading}
        dataSource={filtered}
        columns={applyPreset(columns, visibleKeys) as any}
        pagination={{ defaultPageSize: 100, showSizeChanger: true, pageSizeOptions: [20, 50, 100, 200] }}
        size="middle"
      />
      )}

      {/* 新建配件 Modal */}
      <Modal
        title="新建配件"
        open={creating}
        onCancel={() => setCreating(false)}
        onOk={() => createForm.submit()}
        confirmLoading={createMut.isPending}
        destroyOnClose
      >
        <Form
          form={createForm}
          layout="vertical"
          onFinish={(v) => createMut.mutate({ ...v, prefix: v.prefix })}
        >
          <Form.Item name="prefix" label="配件类型" rules={[{ required: true }]}>
            <Select
              options={PREFIX_OPTIONS}
              onChange={(v) => fetchPreview(v)}
            />
          </Form.Item>
          <Form.Item label="自动分配编码">
            <Typography.Text code style={{ fontSize: 16 }}>
              {previewLoading ? '计算中…' : (previewCode || '—')}
            </Typography.Text>
            <Typography.Text type="secondary" style={{ marginLeft: 8, fontSize: 12 }}>
              (提交时自动写入，无需手填)
            </Typography.Text>
          </Form.Item>
          <Form.Item name="name" label="配件名称" rules={[{ required: true }]}>
            <Input placeholder="如：餐桌-人工费-中型" />
          </Form.Item>
          <Form.Item name="unit" label="单位">
            <Input placeholder="如：条/个/套/m²" />
          </Form.Item>
          <Form.Item name="price" label="单价">
            <InputNumber min={0} step={0.01} style={{ width: '100%' }} placeholder="元" />
          </Form.Item>
          <Form.Item name="size_type" label="尺寸类型">
            <Input placeholder="如 组合 / 个数 / 长度" />
          </Form.Item>
          <Form.Item name="remark" label="备注">
            <Input />
          </Form.Item>
        </Form>
      </Modal>

      {/* 编辑物料 Modal */}
      <Modal
        title={editing ? `编辑物料 ${editing.code}` : ''}
        open={!!editing}
        onCancel={() => setEditing(null)}
        onOk={() => form.submit()}
        confirmLoading={updateMut.isPending}
        destroyOnClose
      >
        <Form
          form={form}
          layout="vertical"
          onFinish={(v) => editing && updateMut.mutate({ id: editing.id, patch: v })}
        >
          <Form.Item name="name" label="名称" rules={[{ required: true }]}>
            <Input />
          </Form.Item>
          <Form.Item name="size_type" label="尺寸类型">
            <Input placeholder="如 组合 / 个数 / 长度" />
          </Form.Item>
          <Form.Item name="unit" label="单位">
            <Input placeholder="如 条/个/套" />
          </Form.Item>
          <Form.Item name="price" label="价格">
            <InputNumber min={0} step={0.01} style={{ width: '100%' }} />
          </Form.Item>
          <Form.Item name="remark" label="备注">
            <Input />
          </Form.Item>
        </Form>
      </Modal>
    </Space>
  );
}
