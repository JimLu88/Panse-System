import { useRef, useState } from 'react';
import {
  Alert,
  Button,
  Descriptions,
  Form,
  Image,
  Input,
  Modal,
  Segmented,
  Select,
  Space,
  Spin,
  Table,
  Tag,
  Typography,
  message,
} from 'antd';
import { EditOutlined, PlusOutlined } from '@ant-design/icons';
import { Link } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import FullColumnView from '../components/FullColumnView';
import { PricingSku, Product, createProduct, listProducts, listProductSkus, updateProduct } from '../api/client';

function SkuExpandedRow({ productCode }: { productCode: string }) {
  const { data, isLoading } = useQuery({
    queryKey: ['product-skus', productCode],
    queryFn: () => listProductSkus(productCode),
  });

  if (isLoading) return <Spin size="small" />;
  if (!data || data.length === 0) return <Typography.Text type="secondary">暂无定价 SKU</Typography.Text>;

  return (
    <Image.PreviewGroup>
      <Table<PricingSku>
        rowKey="id"
        dataSource={data}
        size="small"
        pagination={false}
        columns={[
          {
            title: '图片', width: 64,
            render: (_: unknown, r: PricingSku) =>
              r.image_url
                ? <Image src={r.image_url} width={48} height={48} style={{ objectFit: 'cover', borderRadius: 4 }} />
                : <span style={{ color: '#ddd', fontSize: 12 }}>无图</span>,
          },
          { title: 'SKU 编码', dataIndex: 'sku_code', width: 120 },
          { title: 'SKU', dataIndex: 'sku', ellipsis: true },
          { title: '尺寸分类', dataIndex: 'size_category', width: 100 },
          { title: '日常价', dataIndex: 'daily_price', width: 90, render: (v: number) => v != null ? `¥${v}` : '-' },
          { title: '小促价', dataIndex: 'small_promo', width: 90, render: (v: number) => v != null ? `¥${v}` : '-' },
          { title: '大促价', dataIndex: 'big_promo', width: 90, render: (v: number) => v != null ? `¥${v}` : '-' },
          {
            title: '毛利率', dataIndex: 'gross_margin_rate', width: 80,
            render: (v: number) => v != null
              ? <Tag color={v >= 0.3 ? 'green' : v >= 0.15 ? 'orange' : 'red'}>{(v * 100).toFixed(1)}%</Tag>
              : '-',
          },
        ]}
      />
    </Image.PreviewGroup>
  );
}

function ProductDetailSection({ product }: { product: Product }) {
  const hasExtra = product.custom_scope || product.size_detail || product.aux_material || product.description;
  if (!hasExtra) return null;
  return (
    <Descriptions size="small" column={1} style={{ marginTop: 8 }}>
      {product.custom_scope && (
        <Descriptions.Item label="定制范围">
          <Typography.Paragraph ellipsis={{ expandable: true, rows: 2 }} style={{ marginBottom: 0 }}>
            {product.custom_scope}
          </Typography.Paragraph>
        </Descriptions.Item>
      )}
      {product.size_detail && (
        <Descriptions.Item label="尺寸明细">
          <Typography.Paragraph ellipsis={{ expandable: true, rows: 2 }} style={{ marginBottom: 0 }}>
            {product.size_detail}
          </Typography.Paragraph>
        </Descriptions.Item>
      )}
      {product.aux_material && (
        <Descriptions.Item label="辅材介绍">
          <Typography.Paragraph ellipsis={{ expandable: true, rows: 2 }} style={{ marginBottom: 0 }}>
            {product.aux_material}
          </Typography.Paragraph>
        </Descriptions.Item>
      )}
      {product.description && (
        <Descriptions.Item label="产品文案">
          <Typography.Paragraph ellipsis={{ expandable: true, rows: 2 }} style={{ marginBottom: 0 }}>
            {product.description}
          </Typography.Paragraph>
        </Descriptions.Item>
      )}
    </Descriptions>
  );
}

const CATEGORY_OPTIONS = [
  { value: '14', label: '14 客厅-茶几' },
  { value: '15', label: '15 客厅-柜' },
  { value: '16', label: '16 客厅-沙发' },
  { value: '21', label: '21 餐厅-餐桌' },
  { value: '22', label: '22 餐厅-椅凳' },
  { value: '25', label: '25 餐厅-餐边柜' },
  { value: '33', label: '33 卧室-床' },
  { value: '35', label: '35 卧室-柜' },
  { value: '38', label: '38 卧室-床头柜' },
  { value: '41', label: '41 书房-书桌' },
  { value: '45', label: '45 书房-书柜' },
  { value: '55', label: '55 玄关-柜' },
  { value: '78', label: '78 餐厅-岛台' },
  { value: '99', label: '99 其它' },
];

// 可爱占位图 (小猫脸): 图片缺失或加载失败时显示, 替代难看的"裂图"图标
const CUTE_IMG =
  'data:image/svg+xml;charset=utf-8,' +
  encodeURIComponent(
    "<svg xmlns='http://www.w3.org/2000/svg' width='120' height='120'>" +
    "<rect width='120' height='120' rx='14' fill='#fafafa'/>" +
    "<path d='M38 32 L52 52 L30 52 Z' fill='#ffd6a5'/><path d='M82 32 L90 52 L68 52 Z' fill='#ffd6a5'/>" +
    "<circle cx='60' cy='62' r='30' fill='#ffe8cc'/>" +
    "<circle cx='50' cy='58' r='3.6' fill='#595959'/><circle cx='70' cy='58' r='3.6' fill='#595959'/>" +
    "<path d='M60 64 l-4 4 h8 z' fill='#ff9c9c'/>" +
    "<path d='M60 68 q-6 6 -12 2 M60 68 q6 6 12 2' fill='none' stroke='#ffb3b3' stroke-width='2' stroke-linecap='round'/>" +
    "<g stroke='#d9d9d9' stroke-width='2' stroke-linecap='round'><path d='M30 60 h-14 M30 66 h-13'/><path d='M90 60 h14 M90 66 h13'/></g>" +
    "<text x='60' y='112' text-anchor='middle' font-size='11' fill='#bfbfbf' font-family='sans-serif'>暂无图片</text>" +
    "</svg>",
  );

// 可拖拽列宽的表头单元格 (无需 react-resizable 依赖; 拖右边缘改宽)
function ResizableTitle(props: any) {
  const { onResize, width, children, ...rest } = props;
  const start = useRef<{ x: number; w: number } | null>(null);
  if (!width) return <th {...rest}>{children}</th>;
  const onMouseDown = (e: any) => {
    e.preventDefault();
    e.stopPropagation();
    start.current = { x: e.clientX, w: width };
    const move = (ev: MouseEvent) => {
      if (!start.current) return;
      onResize(Math.max(60, start.current.w + (ev.clientX - start.current.x)));
    };
    const up = () => {
      start.current = null;
      document.removeEventListener('mousemove', move);
      document.removeEventListener('mouseup', up);
    };
    document.addEventListener('mousemove', move);
    document.addEventListener('mouseup', up);
  };
  return (
    <th {...rest} style={{ ...(rest.style || {}), position: 'relative' }}>
      {children}
      <span
        onMouseDown={onMouseDown}
        style={{
          position: 'absolute', right: -4, top: 0, bottom: 0, width: 9,
          cursor: 'col-resize', zIndex: 1, userSelect: 'none',
        }}
      />
    </th>
  );
}

export default function ProductsPage() {
  const qc = useQueryClient();
  const [q, setQ] = useState('');
  const [open, setOpen] = useState(false);
  const [form] = Form.useForm();
  const [pageSize, setPageSize] = useState(20);
  const [editTarget, setEditTarget] = useState<Product | null>(null);
  const [editForm] = Form.useForm();
  const [viewMode, setViewMode] = useState<'curated' | 'full'>('curated');
  // 各列宽度 (可拖拽改); 表头拖右边缘即可
  const [colW, setColW] = useState<Record<string, number>>({
    code: 150, name: 240, brand: 90, category: 140, remark: 170, image: 110, actions: 150,
  });
  const handleResize = (key: string) => (w: number) =>
    setColW((prev) => ({ ...prev, [key]: w }));

  const { data, isLoading } = useQuery({
    queryKey: ['products', q],
    queryFn: () => listProducts(q || undefined),
  });

  const updateMut = useMutation({
    mutationFn: ({ id, payload }: { id: number; payload: Parameters<typeof updateProduct>[1] }) =>
      updateProduct(id, payload),
    onSuccess: () => {
      message.success('已更新');
      setEditTarget(null);
      editForm.resetFields();
      qc.invalidateQueries({ queryKey: ['products'] });
    },
    onError: (e: any) => message.error(e?.response?.data?.detail ?? '更新失败'),
  });

  const createMut = useMutation({
    mutationFn: createProduct,
    onSuccess: (p) => {
      Modal.success({
        title: '产品创建成功',
        content: (
          <div>
            <p>
              已分配产品编码：<b>{p.code}</b>
            </p>
            <p style={{ color: '#888' }}>下一步可以去「BOM」补登物料清单。</p>
          </div>
        ),
      });
      setOpen(false);
      form.resetFields();
      qc.invalidateQueries({ queryKey: ['products'] });
    },
    onError: (e: any) => message.error(e?.response?.data?.detail ?? '创建失败'),
  });

  const mkResize = (key: string) => () => ({ width: colW[key], onResize: handleResize(key) });
  const columns = [
    {
      title: '编码', dataIndex: 'code', key: 'code', width: colW.code, ellipsis: true,
      onHeaderCell: mkResize('code'),
      render: (v: string) => <code style={{ fontSize: 12 }}>{v}</code>,
    },
    {
      title: '名称', dataIndex: 'name', key: 'name', width: colW.name, ellipsis: true,
      onHeaderCell: mkResize('name'),
      render: (v: string, row: Product) => (
        <Typography.Text
          style={{ width: '100%' }}
          editable={{
            tooltip: '点击编辑名称',
            onChange: (val) => {
              const t = val.trim();
              if (t && t !== v) updateMut.mutate({ id: row.id, payload: { name: t } });
            },
          }}
        >
          {v}
        </Typography.Text>
      ),
    },
    {
      title: '品牌', dataIndex: 'brand', key: 'brand', width: colW.brand,
      onHeaderCell: mkResize('brand'),
      render: (v: string | null) => (v ? <Tag color="blue">{v}</Tag> : '-'),
    },
    {
      title: '类目', dataIndex: 'category', key: 'category', width: colW.category, ellipsis: true,
      onHeaderCell: mkResize('category'),
    },
    {
      title: '备注', dataIndex: 'remark', key: 'remark', width: colW.remark, ellipsis: true,
      onHeaderCell: mkResize('remark'),
      render: (v: string | null, row: Product) => (
        <Typography.Text
          type={v ? undefined : 'secondary'}
          editable={{
            tooltip: '点击编辑备注',
            onChange: (val) => {
              const t = val.trim();
              if (t !== (v ?? '')) updateMut.mutate({ id: row.id, payload: { remark: t || undefined } });
            },
          }}
        >
          {v || '—'}
        </Typography.Text>
      ),
    },
    {
      title: '图片', dataIndex: 'image_url', key: 'image', width: colW.image, align: 'center' as const,
      onHeaderCell: mkResize('image'),
      render: (v: string | null) =>
        v
          ? <Image src={v} width={88} height={88} style={{ objectFit: 'cover', borderRadius: 8 }} fallback={CUTE_IMG} />
          : <img src={CUTE_IMG} width={88} height={88} style={{ borderRadius: 8 }} alt="暂无图片" />,
    },
    {
      title: '操作', key: 'actions', width: colW.actions,
      onHeaderCell: mkResize('actions'),
      render: (_: unknown, row: Product) => (
        <Space>
          <Link to={`/bom/${row.code}`}>查看 BOM</Link>
          <Button
            size="small"
            icon={<EditOutlined />}
            onClick={() => {
              setEditTarget(row);
              editForm.setFieldsValue({
                name: row.name,
                remark: row.remark,
                image_url: row.image_url ?? '',
                custom_scope: row.custom_scope ?? '',
                size_detail: row.size_detail ?? '',
                aux_material: row.aux_material ?? '',
                description: row.description ?? '',
              });
            }}
          >
            编辑
          </Button>
        </Space>
      ),
    },
  ];

  return (
    <Space direction="vertical" style={{ width: '100%' }} size="middle">
      <Space style={{ justifyContent: 'space-between', width: '100%' }}>
        <Typography.Title level={4} style={{ margin: 0 }}>
          产品总表 (1)
        </Typography.Title>
        <Space>
          <Input.Search placeholder="按编码或名称" allowClear style={{ width: 280 }} onSearch={setQ} />
          <Button type="primary" icon={<PlusOutlined />} onClick={() => setOpen(true)}>
            新建产品
          </Button>
        </Space>
      </Space>

      <Alert
        type="info"
        showIcon
        message="产品编码由系统按 P + 品牌 + 年份 + 类目 + 计数 + 月日 自动生成，不需要手填。"
        description="精选视图：名称 / 备注 可直接点单元格编辑；拖表头右边缘可调列宽；点图片可放大。"
      />

      <Segmented
        value={viewMode}
        onChange={(v) => setViewMode(v as 'curated' | 'full')}
        options={[
          { label: '精选视图（可编辑）', value: 'curated' },
          { label: '全部列', value: 'full' },
        ]}
      />

      {viewMode === 'full' && <FullColumnView entity="product" defaultShowAll />}

      {viewMode === 'curated' && (
      <Image.PreviewGroup>
      <Table<Product>
        rowKey="id"
        loading={isLoading}
        dataSource={data}
        columns={columns as any}
        components={{ header: { cell: ResizableTitle } }}
        scroll={{ x: 'max-content' }}
        pagination={{
          pageSize,
          showSizeChanger: true,
          pageSizeOptions: [20, 50, 100],
          onShowSizeChange: (_, size) => setPageSize(size),
        }}
        expandable={{
          expandedRowRender: (record) => (
            <div style={{ padding: '8px 0' }}>
              <ProductDetailSection product={record} />
              <SkuExpandedRow productCode={record.code} />
            </div>
          ),
        }}
      />
      </Image.PreviewGroup>
      )}

      <Modal
        title={`编辑产品 — ${editTarget?.code ?? ''}`}
        open={!!editTarget}
        onCancel={() => { setEditTarget(null); editForm.resetFields(); }}
        onOk={() => editForm.submit()}
        confirmLoading={updateMut.isPending}
        destroyOnClose
      >
        <Form
          form={editForm}
          layout="vertical"
          onFinish={(v) =>
            updateMut.mutate({
              id: editTarget!.id,
              payload: {
                name: v.name || undefined,
                remark: v.remark || undefined,
                image_url: v.image_url || null,
                custom_scope: v.custom_scope || null,
                size_detail: v.size_detail || null,
                aux_material: v.aux_material || null,
                description: v.description || null,
              },
            })
          }
        >
          <Form.Item name="name" label="产品名称" rules={[{ required: true }]}>
            <Input />
          </Form.Item>
          <Form.Item name="image_url" label="图片 URL">
            <Input placeholder="https://... 留空则清除图片" />
          </Form.Item>
          <Form.Item name="remark" label="备注">
            <Input />
          </Form.Item>
          <Form.Item name="custom_scope" label="定制范围">
            <Input.TextArea rows={2} />
          </Form.Item>
          <Form.Item name="size_detail" label="尺寸明细">
            <Input.TextArea rows={2} />
          </Form.Item>
          <Form.Item name="aux_material" label="辅材介绍">
            <Input.TextArea rows={2} />
          </Form.Item>
          <Form.Item name="description" label="产品文案">
            <Input.TextArea rows={3} />
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        title="新建产品"
        open={open}
        onCancel={() => setOpen(false)}
        onOk={() => form.submit()}
        confirmLoading={createMut.isPending}
        destroyOnClose
      >
        <Form
          form={form}
          layout="vertical"
          onFinish={(v) => {
            const cat = CATEGORY_OPTIONS.find((c) => c.value === v.category);
            createMut.mutate({
              ...v,
              category_label: cat?.label.replace(/^\d+\s+/, ''),
            });
          }}
          initialValues={{ brand: 'PS' }}
        >
          <Form.Item name="name" label="产品名称" rules={[{ required: true }]}>
            <Input placeholder="如：畔色榉木无边床 |榉木金属腿床" />
          </Form.Item>
          <Space size="middle" style={{ width: '100%' }}>
            <Form.Item name="brand" label="品牌" rules={[{ required: true }]}>
              <Select
                style={{ width: 140 }}
                options={[
                  { value: 'PS', label: 'PS 畔色' },
                  { value: 'FG', label: 'FG 孚格' },
                ]}
              />
            </Form.Item>
            <Form.Item name="category" label="类目" rules={[{ required: true }]} style={{ flex: 1 }}>
              <Select style={{ width: 260 }} options={CATEGORY_OPTIONS} showSearch />
            </Form.Item>
          </Space>
          <Form.Item name="remark" label="备注">
            <Input />
          </Form.Item>
          <Form.Item name="image_url" label="图片 URL（选填）">
            <Input placeholder="https://..." />
          </Form.Item>
          <Form.Item name="custom_scope" label="定制范围（选填）">
            <Input.TextArea rows={2} />
          </Form.Item>
          <Form.Item name="size_detail" label="尺寸明细（选填）">
            <Input.TextArea rows={2} />
          </Form.Item>
          <Form.Item name="aux_material" label="辅材介绍（选填）">
            <Input.TextArea rows={2} />
          </Form.Item>
          <Form.Item name="description" label="产品文案（选填）">
            <Input.TextArea rows={3} />
          </Form.Item>
        </Form>
      </Modal>
    </Space>
  );
}
