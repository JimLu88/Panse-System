import { useState } from 'react';
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

export default function ProductsPage() {
  const qc = useQueryClient();
  const [q, setQ] = useState('');
  const [open, setOpen] = useState(false);
  const [form] = Form.useForm();
  const [pageSize, setPageSize] = useState(20);
  const [editTarget, setEditTarget] = useState<Product | null>(null);
  const [editForm] = Form.useForm();
  const [viewMode, setViewMode] = useState<'curated' | 'full'>('curated');

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

  const columns = [
    { title: '编码', dataIndex: 'code', width: 160 },
    {
      title: '名称', dataIndex: 'name', ellipsis: true,
      render: (v: string, row: Product) => (
        <Space direction="vertical" size={0}>
          <span>{v}</span>
          {row.image_url && <Image src={row.image_url} width={32} height={32} style={{ objectFit: 'cover', borderRadius: 3 }} />}
        </Space>
      ),
    },
    { title: '品牌', dataIndex: 'brand', width: 80 },
    { title: '类目', dataIndex: 'category', width: 140 },
    {
      title: '操作',
      width: 150,
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
      <Table<Product>
        rowKey="id"
        loading={isLoading}
        dataSource={data}
        columns={columns as any}
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
