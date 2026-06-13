/**
 * BOM 清单 (扁平/可管理) — 反馈整改。
 *
 * 按产品编码 / 物料编码筛 BOM 行; 显示产品名+图; 可编辑(改 SKU 归属/料号/单耗/单位)或删除。
 * 与 /bom/:productCode 的"按产品看 BOM"不同 —— 这里是全局可搜、可改、可删的管理列表。
 */
import { useState } from 'react';
import {
  Alert, Button, Card, Form, Image, Input, InputNumber, Modal, Popconfirm,
  Select, Space, Table, Tooltip, Typography, message,
} from 'antd';
import { DeleteOutlined, EditOutlined, PlusOutlined } from '@ant-design/icons';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { createBomLine, deleteBomLine, getNextMaterialCode, listBomLines, listProductCategories,
  updateBomLine } from '../api/client';
import type { BomLineRow } from '../api/client';
import ProductThumb from '../components/ProductThumb';
import FieldPresetBar, { type PresetField } from '../components/FieldPresetBar';

const BOM_FIELDS: PresetField[] = [
  { key: 'sku_code', label: 'SKU 编码', group: '字段' },
  { key: 'material_code', label: '物料编码', group: '字段' },
  { key: 'material_name', label: '物料名称', group: '字段' },
  { key: 'unit', label: '单位', group: '字段' },
  { key: 'qty_per_product', label: '单耗', group: '字段' },
];
const BOM_PRESETS = [
  { name: '常用', fields: ['sku_code', 'material_code', 'material_name', 'qty_per_product'] },
];

export default function BomListPage() {
  const qc = useQueryClient();
  const [productCode, setProductCode] = useState('');
  const [productName, setProductName] = useState('');
  const [materialCode, setMaterialCode] = useState('');
  const [category, setCategory] = useState<string | undefined>(undefined);
  const [editRow, setEditRow] = useState<BomLineRow | null>(null);
  const [form] = Form.useForm();
  const [addOpen, setAddOpen] = useState(false);   // 图2: 行内新增 BOM
  const [addForm] = Form.useForm();
  const [addNewMat, setAddNewMat] = useState(false);   // 新建物料 vs 选已有
  const [nextCode, setNextCode] = useState<string>('');
  const [visibleKeys, setVisibleKeys] = useState<string[] | null>(null);
  const applyView = (cols: any[]) =>
    visibleKeys === null ? cols : cols.filter((c: any) => !c.dataIndex || visibleKeys.includes(c.dataIndex));

  const { data = [], isLoading } = useQuery({
    queryKey: ['bom-list', productCode, productName, materialCode, category],
    queryFn: () => listBomLines({
      product_code: productCode || undefined,
      product: productName || undefined,
      material_code: materialCode || undefined,
      category,
    }),
  });
  const { data: categories = [] } = useQuery({
    queryKey: ['product-categories'], queryFn: listProductCategories, staleTime: 5 * 60 * 1000,
  });

  const invalidate = () => qc.invalidateQueries({ queryKey: ['bom-list'] });
  const delMut = useMutation({
    mutationFn: (id: number) => deleteBomLine(id),
    onSuccess: () => { message.success('已删除该 BOM 行'); invalidate(); },
    onError: () => message.error('删除失败'),
  });
  const editMut = useMutation({
    mutationFn: ({ id, patch }: { id: number; patch: Record<string, unknown> }) => updateBomLine(id, patch),
    onSuccess: () => { message.success('已保存'); setEditRow(null); form.resetFields(); invalidate(); },
    onError: (e: any) => message.error(e?.response?.data?.detail ?? '保存失败'),
  });

  const addMut = useMutation({
    mutationFn: createBomLine,
    onSuccess: () => { message.success('已新增 BOM 行'); setAddOpen(false); addForm.resetFields(); setNextCode(''); setAddNewMat(false); invalidate(); },
    onError: (e: any) => message.error(e?.response?.data?.detail ?? '新增失败'),
  });
  const fetchNextCode = async () => {
    const prefix = addForm.getFieldValue('material_prefix') || 'AC';
    try {
      const r = await getNextMaterialCode(prefix);
      setNextCode(r.code);
      message.success(`下一个可用编码: ${r.code}`);
    } catch { message.error('取编码失败'); }
  };
  const openAdd = () => {
    addForm.resetFields();
    addForm.setFieldsValue({ product_code: productCode || undefined, material_prefix: 'AC', unit: '套', qty_per_product: 1 });
    setAddNewMat(false); setNextCode(''); setAddOpen(true);
  };

  const openEdit = (r: BomLineRow) => {
    setEditRow(r);
    form.setFieldsValue({
      sku_code: r.sku_code, sku: r.sku, material_code: r.material_code,
      material_name: r.material_name, unit: r.unit, qty_per_product: Number(r.qty_per_product),
    });
  };

  const columns = [
    {
      title: '产品', width: 230,
      render: (_: unknown, r: BomLineRow) => (
        <Space>
          <ProductThumb src={r.product_image_url ?? null} size={36} />
          <div style={{ lineHeight: 1.2 }}>
            <div style={{ fontSize: 12 }}>{r.product_name ?? '-'}</div>
            <code style={{ fontSize: 11, color: '#999' }}>{r.product_code}</code>
          </div>
        </Space>
      ),
    },
    { title: 'SKU 编码', dataIndex: 'sku_code', width: 150, render: (v: string | null) => v ? <code style={{ fontSize: 12 }}>{v}</code> : <span style={{ color: '#ccc' }}>—</span> },
    { title: '物料编码', dataIndex: 'material_code', width: 110, render: (v: string) => <code style={{ fontSize: 12 }}>{v}</code> },
    {
      title: '物料名称', dataIndex: 'material_name', ellipsis: true,
      render: (v: string | null, r: BomLineRow) => v ? <Tooltip title={v}><span>{v}</span></Tooltip> : <span style={{ color: '#ccc' }}>{r.material_code}</span>,
    },
    { title: '单位', dataIndex: 'unit', width: 60 },
    { title: '单耗', dataIndex: 'qty_per_product', width: 70, align: 'right' as const },
    {
      title: '操作', width: 140,
      render: (_: unknown, r: BomLineRow) => (
        <Space>
          <Button size="small" icon={<EditOutlined />} onClick={() => openEdit(r)}>编辑</Button>
          <Popconfirm title="删这条 BOM 行？" description="只删这一条料，不影响产品。" okText="删除" okButtonProps={{ danger: true }} cancelText="取消" onConfirm={() => delMut.mutate(r.id)}>
            <Button size="small" danger icon={<DeleteOutlined />} />
          </Popconfirm>
        </Space>
      ),
    },
  ];

  return (
    <Space direction="vertical" style={{ width: '100%' }} size="middle">
      <Typography.Title level={4} style={{ margin: 0 }}>BOM 清单</Typography.Title>
      <Alert
        type="info" showIcon
        message="按产品编码 / 物料编码筛 BOM 行。数据错了可直接「编辑」(改 SKU 归属/料号/单耗/单位)或「删除」。"
        description="异常中心报的「一码挂多产品」可在这里按产品编码找出来，把错挂的行改 SKU 编码或删掉；整产品删除去「产品总表」。"
      />
      <Space wrap>
        <Input.Search placeholder="产品名称（如 榉木餐桌, 支持模糊）" allowClear style={{ width: 250 }} onSearch={setProductName} />
        <Input.Search placeholder="产品编码（如 PPS24210070901）" allowClear style={{ width: 280 }} onSearch={setProductCode} />
        <Input.Search placeholder="物料编码（如 AC-0074）" allowClear style={{ width: 220 }} onSearch={setMaterialCode} />
        <Select allowClear showSearch placeholder="按类目筛" style={{ width: 180 }} value={category}
          onChange={setCategory} options={categories.map((c) => ({ value: c, label: c }))} />
        <FieldPresetBar tableKey="bom" allFields={BOM_FIELDS} defaults={BOM_PRESETS} onChange={setVisibleKeys} />
        <Button type="primary" icon={<PlusOutlined />} onClick={openAdd}>新增 BOM 行</Button>
      </Space>
      <Card size="small">
        <Image.PreviewGroup>
          <Table<BomLineRow>
            rowKey="id" loading={isLoading} dataSource={data} columns={applyView(columns) as any}
            size="small" scroll={{ x: 'max-content' }}
            pagination={{ defaultPageSize: 100, showSizeChanger: true, pageSizeOptions: [50, 100, 200] }}
            locale={{ emptyText: '没有匹配的 BOM 行（默认显示最近 500 条，可按编码筛选）' }}
          />
        </Image.PreviewGroup>
      </Card>

      <Modal
        title={`编辑 BOM 行${editRow ? ` — ${editRow.product_code}` : ''}`}
        open={!!editRow}
        onCancel={() => { setEditRow(null); form.resetFields(); }}
        onOk={() => form.submit()}
        confirmLoading={editMut.isPending}
        destroyOnClose
      >
        <Form
          form={form} layout="vertical"
          onFinish={(v) => editMut.mutate({ id: editRow!.id, patch: {
            sku_code: v.sku_code || null, sku: v.sku || null,
            material_code: v.material_code || undefined, material_name: v.material_name || null,
            unit: v.unit || null, qty_per_product: v.qty_per_product,
          } })}
        >
          <Form.Item name="sku_code" label="SKU 编码" tooltip="改这里可把错挂的行归到正确的 SKU（解决一码挂多产品）">
            <Input placeholder="如 PPS2421007090112" />
          </Form.Item>
          <Form.Item name="sku" label="SKU 描述"><Input /></Form.Item>
          <Form.Item name="material_code" label="物料编码" tooltip="改料号需该物料已在物料库存在"><Input /></Form.Item>
          <Form.Item name="material_name" label="物料名称（改这里＝改物料库，全站同步）"
            tooltip="这是物料库的单一名称。改它会同步到物料库、所有用到该物料的 BOM 行、看板配件、下单图。重名会报错。">
            <Input />
          </Form.Item>
          <Space size="large">
            <Form.Item name="unit" label="单位"><Input style={{ width: 120 }} /></Form.Item>
            <Form.Item name="qty_per_product" label="单耗(每件用量)"><InputNumber min={0} style={{ width: 160 }} /></Form.Item>
          </Space>
        </Form>
      </Modal>

      {/* 图2: 行内新增 BOM 行 — 选已有物料编码, 或新建物料(系统自动给下一个编码) */}
      <Modal
        title="新增 BOM 行"
        open={addOpen}
        onCancel={() => { setAddOpen(false); addForm.resetFields(); setNextCode(''); setAddNewMat(false); }}
        onOk={() => addForm.submit()}
        confirmLoading={addMut.isPending}
        destroyOnClose
      >
        <Form
          form={addForm} layout="vertical"
          onFinish={(v) => addMut.mutate({
            product_code: v.product_code,
            sku_code: v.sku_code || undefined,
            material_code: addNewMat ? undefined : (v.material_code || undefined),
            new_material_name: addNewMat ? (v.new_material_name || undefined) : undefined,
            material_prefix: addNewMat ? (v.material_prefix || 'AC') : undefined,
            unit: v.unit || '套',
            qty_per_product: v.qty_per_product ?? 1,
          })}
        >
          <Form.Item name="product_code" label="产品编码" rules={[{ required: true, message: '请填产品编码' }]}
            tooltip="填产品总表里的产品级编码 (PPS+11位)，不是SKU码。系统会校验它真实存在，敲错会拦下，不会再建出挂空产品的BOM行。">
            <Input placeholder="如 PPS26380040225（产品级，非SKU码）" />
          </Form.Item>
          <Form.Item name="sku_code" label="SKU 编码(选填)"><Input placeholder="挂到具体 SKU; 留空则挂产品级" /></Form.Item>
          <Form.Item label="物料来源">
            <Select value={addNewMat ? 'new' : 'exist'} onChange={(v) => setAddNewMat(v === 'new')}
              options={[{ value: 'exist', label: '选已有物料编码' }, { value: 'new', label: '新建物料(自动给编码)' }]} />
          </Form.Item>
          {!addNewMat ? (
            <Form.Item name="material_code" label="物料编码" rules={[{ required: !addNewMat, message: '请填物料编码' }]}>
              <Input placeholder="如 AC-0064 (须已在物料库)" />
            </Form.Item>
          ) : (
            <>
              <Form.Item name="new_material_name" label="新物料名称" rules={[{ required: addNewMat, message: '请填新物料名称' }]}>
                <Input placeholder="如 榉木床头柜-金属侧板(宽板)" />
              </Form.Item>
              <Space align="end">
                <Form.Item name="material_prefix" label="编码前缀" style={{ marginBottom: 0 }}>
                  <Select style={{ width: 120 }}
                    options={['AC', 'MP', 'MW', 'SP'].map((p) => ({ value: p, label: p }))} />
                </Form.Item>
                <Button onClick={fetchNextCode}>取下一个编码</Button>
                {nextCode && <Typography.Text type="success">将用: <code>{nextCode}</code></Typography.Text>}
              </Space>
              <Typography.Paragraph type="secondary" style={{ fontSize: 12, marginTop: 4 }}>
                提交时系统自动生成该前缀下一个可用编码并建物料, 无需先去物料库。
              </Typography.Paragraph>
            </>
          )}
          <Space size="large">
            <Form.Item name="unit" label="单位"><Input style={{ width: 120 }} /></Form.Item>
            <Form.Item name="qty_per_product" label="单耗(每件用量)"><InputNumber min={0} style={{ width: 160 }} /></Form.Item>
          </Space>
        </Form>
      </Modal>
    </Space>
  );
}
