import { useRef, useState } from 'react';
import {
  Alert,
  Button,
  Descriptions,
  Dropdown,
  Form,
  Image,
  Input,
  InputNumber,
  Modal,
  Popconfirm,
  Segmented,
  Select,
  Space,
  Spin,
  Switch,
  Table,
  Tag,
  Typography,
  message,
} from 'antd';
import { DeleteOutlined, DownOutlined, EditOutlined, PlusOutlined } from '@ant-design/icons';
import { Link } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import FullColumnView from '../components/FullColumnView';
import GalleryModal from '../components/GalleryModal';
import { CUTE_IMG } from '../components/ProductThumb';
import { PricingSku, Product, createProduct, deleteProduct, listProductCategories, listProducts, listProductSkus, updateProduct, updateSkuShippingMeasurements } from '../api/client';
import FieldPresetBar, { type PresetField } from '../components/FieldPresetBar';
import ResponsiveTable from '../components/ResponsiveTable';
import { CatalogCard } from '../components/MobileCards';
import ProductDimensionFinalActions from '../components/ProductDimensionFinalActions';

const PRODUCT_FIELDS: PresetField[] = [
  { key: 'code', label: '编码', group: '字段' },
  { key: 'name', label: '名称', group: '字段' },
  { key: 'brand', label: '品牌', group: '字段' },
  { key: 'category', label: '类目', group: '字段' },
  { key: 'remark', label: '备注', group: '字段' },
  { key: 'image', label: '图片', group: '字段' },
];
const PRODUCT_PRESETS = [
  { name: '常用', fields: ['code', 'name', 'category', 'image'] },
  { name: '名称备注', fields: ['code', 'name', 'brand', 'category', 'remark', 'image'] },
];

function SkuMeasurementEditor({ row }: { row: PricingSku }) {
  const [open, setOpen] = useState(false);
  const [form] = Form.useForm();
  const queryClient = useQueryClient();
  const mutation = useMutation({
    mutationFn: (payload: Record<string, number | null>) =>
      updateSkuShippingMeasurements(row.product_code, row.id, payload),
    onSuccess: () => {
      message.success('重量体积已保存');
      setOpen(false);
      queryClient.invalidateQueries({ queryKey: ['product-skus', row.product_code] });
    },
    onError: () => message.error('保存失败，请重试'),
  });
  const show = () => {
    form.setFieldsValue({
      product_weight_kg: row.product_weight_kg == null ? null : Number(row.product_weight_kg),
      packaged_weight_kg: row.packaged_weight_kg == null ? null : Number(row.packaged_weight_kg),
      product_volume_m3: row.product_volume_m3 == null ? null : Number(row.product_volume_m3),
      packaged_volume_m3: row.packaged_volume_m3 == null ? null : Number(row.packaged_volume_m3),
    });
    setOpen(true);
  };
  const save = async () => {
    const values = await form.validateFields();
    const keys = ['product_weight_kg', 'packaged_weight_kg', 'product_volume_m3', 'packaged_volume_m3'];
    const payload = Object.fromEntries(keys.map((key) => [key, values[key] ?? null]));
    mutation.mutate(payload);
  };
  return (
    <>
      <Button size="small" icon={<EditOutlined />} onClick={show}>编辑</Button>
      <Modal title={`${row.sku || row.sku_code} · 重量体积`} open={open} onCancel={() => setOpen(false)}
        onOk={save} confirmLoading={mutation.isPending} destroyOnClose>
        <Alert type="info" showIcon style={{ marginBottom: 16 }}
          message="账单只会自动更新打包重量和打包体积；产品裸重、裸品体积需人工填写。手改打包值后自动回填不会覆盖。" />
        <Form form={form} layout="vertical">
          <Form.Item name="product_weight_kg" label="产品重量（裸重）">
            <InputNumber min={0} precision={3} style={{ width: '100%' }} addonAfter="kg" />
          </Form.Item>
          <Form.Item name="packaged_weight_kg" label="打包重量（包裹实际重量）">
            <InputNumber min={0} precision={3} style={{ width: '100%' }} addonAfter="kg" />
          </Form.Item>
          <Form.Item name="product_volume_m3" label="产品体积（裸品）">
            <InputNumber min={0} precision={4} style={{ width: '100%' }} addonAfter="m³" />
          </Form.Item>
          <Form.Item name="packaged_volume_m3" label="打包体积">
            <InputNumber min={0} precision={4} style={{ width: '100%' }} addonAfter="m³" />
          </Form.Item>
        </Form>
      </Modal>
    </>
  );
}

const measureText = (value: number | null | undefined, digits: number, unit: string) =>
  value == null ? '-' : `${Number(value).toFixed(digits)} ${unit}`;

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
        scroll={{ x: 1450 }}
        columns={[
          {
            title: '图片', width: 64,
            // SKU 图全部图库优先 (用户拍板 2026-06-12); 图库没有才回退淘宝 image_url
            render: (_: unknown, r: PricingSku) => {
              const src = (r as any).gallery_image_url || r.image_url;
              return src
                ? <Image src={src} width={48} height={48} style={{ objectFit: 'cover', borderRadius: 4 }} fallback={CUTE_IMG} />
                : <img src={CUTE_IMG} width={48} height={48} alt="暂无图片" />;
            },
          },
          { title: 'SKU 编码', dataIndex: 'sku_code', width: 120 },
          { title: 'SKU', dataIndex: 'sku', ellipsis: true },
          { title: '尺寸分类', dataIndex: 'size_category', width: 100 },
          { title: '产品重量', dataIndex: 'product_weight_kg', width: 110,
            render: (v: number) => measureText(v, 3, 'kg') },
          { title: '打包重量', dataIndex: 'packaged_weight_kg', width: 110,
            render: (v: number) => measureText(v, 3, 'kg') },
          { title: '产品体积', dataIndex: 'product_volume_m3', width: 110,
            render: (v: number) => measureText(v, 4, 'm³') },
          { title: '打包体积', dataIndex: 'packaged_volume_m3', width: 110,
            render: (v: number) => measureText(v, 4, 'm³') },
          {
            title: '数据来源', width: 170,
            render: (_: unknown, r: PricingSku) => {
              if (!r.shipping_measure_source_tracking_no) return '-';
              const auto = r.packaged_weight_source === 'bill' || r.packaged_volume_source === 'bill';
              return <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                {auto ? '物流账单' : '人工'} · {r.shipping_measure_source_tracking_no}
                {r.shipping_measure_sample_count ? ` · ${r.shipping_measure_sample_count}次` : ''}
              </Typography.Text>;
            },
          },
          { title: '日常价', dataIndex: 'daily_price', width: 90, render: (v: number) => v != null ? `¥${v}` : '-' },
          { title: '小促价', dataIndex: 'small_promo', width: 90, render: (v: number) => v != null ? `¥${v}` : '-' },
          { title: '大促价', dataIndex: 'big_promo', width: 90, render: (v: number) => v != null ? `¥${v}` : '-' },
          {
            title: '毛利率', dataIndex: 'gross_margin_rate', width: 80,
            render: (v: number) => v != null
              ? <Tag color={v >= 0.3 ? 'green' : v >= 0.15 ? 'orange' : 'red'}>{(v * 100).toFixed(1)}%</Tag>
              : '-',
          },
          { title: '操作', fixed: 'right', width: 90, render: (_: unknown, r: PricingSku) => <SkuMeasurementEditor row={r} /> },
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
  { value: '45', label: '45 书房-柜' },
  { value: '55', label: '55 玄关-柜' },
  { value: '78', label: '78 餐厅-岛台' },
  { value: '99', label: '99 其它' },
];

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
  const [category, setCategory] = useState<string | undefined>(undefined);
  const [brand, setBrand] = useState<string | undefined>(undefined);
  const [open, setOpen] = useState(false);
  const [form] = Form.useForm();
  const [pageSize, setPageSize] = useState(20);
  const [editTarget, setEditTarget] = useState<Product | null>(null);
  // 产品图库弹窗 (按编码匹配 D:\畔色 产品图库 的文件夹)
  const [galleryFor, setGalleryFor] = useState<string | null>(null);
  // 安装说明书: storage/manuals/{编码} 实时列取 (放群晖, 直接增改文件即生效); 单份直接开, 多份弹选择
  const openManual = async (code: string) => {
    try {
      const { api } = await import('../api/client');
      const r = await api.get(`/api/manuals/${code}`);
      const files: { name: string; lang: string; url: string }[] = r.data?.files ?? [];
      if (files.length === 0) {
        message.info('该产品还没有说明书 (群晖 storage/manuals 下按编码建文件夹放 PDF 即可)');
        return;
      }
      if (files.length === 1) {
        window.open(files[0].url, '_blank', 'noopener,noreferrer');
        return;
      }
      const label = (f: { name: string; lang: string }) =>
        f.lang === 'cn' ? '中文版' : f.lang === 'en' ? '英文版' : f.name;
      Modal.info({
        title: '打开说明书',
        content: (
          <Space direction="vertical">
            {files.map((f) => (
              <Button key={f.name} onClick={() => window.open(f.url, '_blank', 'noopener,noreferrer')}>
                {label(f)}
              </Button>
            ))}
          </Space>
        ),
        okText: '关闭',
      });
    } catch (e: any) {
      message.error(e?.response?.data?.detail ?? '打开说明书失败');
    }
  };
  const [editForm] = Form.useForm();
  const [viewMode, setViewMode] = useState<'curated' | 'full'>('curated');
  // 各列宽度 (可拖拽改); 表头拖右边缘即可
  const [colW, setColW] = useState<Record<string, number>>({
    code: 150, name: 240, brand: 90, category: 140, status: 92, remark: 170, image: 110, actions: 340,
  });
  const handleResize = (key: string) => (w: number) =>
    setColW((prev) => ({ ...prev, [key]: w }));
  const [visibleKeys, setVisibleKeys] = useState<string[] | null>(null);
  const applyView = (cols: any[]) =>
    visibleKeys === null ? cols : cols.filter((c: any) => c.key === 'actions' || visibleKeys.includes(c.key));

  const { data, isLoading } = useQuery({
    queryKey: ['products', q, category, brand],
    queryFn: () => listProducts(q || undefined, { category, brand }),
  });
  const { data: categories = [] } = useQuery({
    queryKey: ['product-categories'], queryFn: listProductCategories, staleTime: 5 * 60 * 1000,
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

  const deleteMut = useMutation({
    mutationFn: ({ id, force }: { id: number; force?: boolean }) => deleteProduct(id, force),
    onSuccess: (r) => {
      message.success(`已删除 ${r.deleted_product}（BOM ${r.deleted_bom_lines} 行 / 定价 ${r.deleted_pricing_skus} 条）`);
      qc.invalidateQueries({ queryKey: ['products'] });
    },
    onError: (e: any, vars) => {
      if (e?.response?.status === 409) {   // 被订单引用 → 让用户二次确认强删
        Modal.confirm({
          title: '该产品被订单引用',
          content: e?.response?.data?.detail ?? '删除会影响这些订单，仍要删吗？',
          okText: '仍然删除', okButtonProps: { danger: true }, cancelText: '取消',
          onOk: () => deleteMut.mutate({ id: vars.id, force: true }),
        });
      } else {
        message.error(e?.response?.data?.detail ?? '删除失败');
      }
    },
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
      // 上架状态 (用户需求 2026-07-10): 来源=产品档案 listing_status;
      // 导入千牛「出售中」商品导出(定价页·淘宝标题导入)时见到即自动置「在售」, 下架需手改档案。
      title: '上架状态', dataIndex: 'listing_status', key: 'status', width: colW.status, align: 'center' as const,
      onHeaderCell: mkResize('status'),
      render: (v: string | null) =>
        v === '在售' ? <Tag color="green">在售</Tag>
        : v === '下架' ? <Tag color="red">下架</Tag>
        : v ? <Tag>{v}</Tag> : <Typography.Text type="secondary">—</Typography.Text>,
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
      // 产品行图片图库优先 (用户拍板 2026-06-12); 图库没有才回退淘宝 image_url
      render: (v: string | null, row: Product) => {
        const src = (row as any).gallery_image_url || v;
        return src
          ? <Image src={src} width={88} height={88} style={{ objectFit: 'cover', borderRadius: 8 }} fallback={CUTE_IMG} />
          : <img src={CUTE_IMG} width={72} height={72} alt="暂无图片" />;
      },
    },
    {
      title: '操作', key: 'actions', width: colW.actions,
      onHeaderCell: mkResize('actions'),
      render: (_: unknown, row: Product) => (
        <Space wrap size={4}>
          <Link to={`/bom/${row.code}`}>查看 BOM</Link>
          <Button size="small" onClick={() => setGalleryFor(row.code)}>图库</Button>
          <Button size="small" onClick={() => openManual(row.code)}>说明书</Button>
          <ProductDimensionFinalActions productCode={row.code} assetCount={row.dimension_asset_count} />
          <Button
            size="small"
            icon={<EditOutlined />}
            onClick={() => {
              setEditTarget(row);
              editForm.setFieldsValue({
                name: row.name,
                sub_name: row.sub_name ?? '',
                brand: row.brand ?? '',
                category: row.category ?? '',
                priority: row.priority ?? 'mid',
                remark: row.remark,
                image_url: row.image_url ?? '',
                custom_scope: row.custom_scope ?? '',
                size_value: row.size_value ?? '',
                size_detail: row.size_detail ?? '',
                main_material: row.main_material ?? '',
                aux_material: row.aux_material ?? '',
                accessory_desc: row.accessory_desc ?? '',
                description: row.description ?? '',
              });
            }}
          >
            编辑
          </Button>
          <Popconfirm
            title={`删除产品 ${row.code}？`}
            description="会一并删它的 BOM 行和定价 SKU（被订单引用会再次确认）。"
            okText="删除" okButtonProps={{ danger: true }} cancelText="取消"
            onConfirm={() => deleteMut.mutate({ id: row.id })}
          >
            <Button size="small" danger icon={<DeleteOutlined />} loading={deleteMut.isPending}>删除</Button>
          </Popconfirm>
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
        <Space wrap>
          <Input.Search placeholder="按编码或名称" allowClear style={{ width: 240 }} onSearch={setQ} />
          <Select allowClear showSearch placeholder="按类目筛" style={{ width: 180 }} value={category}
            onChange={setCategory} options={categories.map((c) => ({ value: c, label: c }))} />
          <Select allowClear placeholder="按品牌筛" style={{ width: 130 }} value={brand} onChange={setBrand}
            options={[{ value: 'PS', label: 'PS 畔色' }, { value: 'FG', label: 'FG 孚格' }]} />
          <Dropdown
            menu={{
              items: [
                { key: 'refresh', label: '刷新图库配图 — 把图库新图刷进表格图片列' },
                { key: 'scan', label: '扫描图库建档 — 发现产品表缺的新文件夹' },
                { key: 'coverage', label: '图库体检 — SKU 配图覆盖率 (不进异常)' },
              ],
              onClick: async ({ key }) => {
                const { api } = await import('../api/client');
                try {
                  if (key === 'refresh') {
                    const r = await api.post('/api/gallery/refresh-images');
                    message.success(
                      `刷新完成: 补上 ${r.data.filled_products} 个产品主图、`
                      + `${r.data.filled_skus} 个 SKU 图 (只填空缺, 已有图不动)`, 6);
                    qc.invalidateQueries({ queryKey: ['products'] });
                    return;
                  }
                  if (key === 'scan') {
                    const r = await api.post('/api/gallery/scan');
                    const news: { folder: string; code: string; name: string; image_count: number }[] =
                      r.data.new_folders ?? [];
                    if (news.length === 0) {
                      message.success('图库扫描完成: 没有发现产品表缺的新文件夹');
                      return;
                    }
                    Modal.confirm({
                      title: `图库里有 ${news.length} 个文件夹还没有产品档案`,
                      width: 560,
                      content: (
                        <ul style={{ maxHeight: 300, overflow: 'auto', paddingLeft: 18 }}>
                          {news.map((n) => (
                            <li key={n.code}>
                              <code>{n.code}</code> {n.name}
                              <span style={{ color: '#999' }}>（{n.image_count} 张图）</span>
                            </li>
                          ))}
                        </ul>
                      ),
                      okText: '一键建档',
                      onOk: async () => {
                        const r2 = await api.post('/api/gallery/scan', null, { params: { create: true } });
                        message.success(`已建 ${r2.data.created} 个产品档案 (备注: 图库扫描自动建档), 请补全类目/定价`);
                        qc.invalidateQueries({ queryKey: ['products'] });
                      },
                    });
                    return;
                  }
                  // 图库体检
                  const r = await api.get('/api/gallery/coverage');
                  const { products: rows, no_folder, totals } = r.data as any;
                  const lacking = rows.filter((p: any) => p.missing.length > 0);
                  Modal.info({
                    title: `图库体检 — SKU 配图 ${totals.with_image}/${totals.sku_total} (实时检查, 不进异常)`,
                    width: 680,
                    content: (
                      <div style={{ maxHeight: 420, overflow: 'auto' }}>
                        {lacking.length === 0 && no_folder.length === 0 && (
                          <p>全部 SKU 都有配图，图库很健康。</p>
                        )}
                        {lacking.length > 0 && (
                          <>
                            <p style={{ margin: '8px 0 4px' }}><b>缺 SKU 图的产品（{lacking.length} 个，缺 {totals.missing} 款）:</b></p>
                            {lacking.map((p: any) => (
                              <div key={p.code} style={{ marginBottom: 6, fontSize: 13 }}>
                                <code>{p.code}</code> {p.name}
                                <Tag style={{ marginLeft: 6 }}>{p.with_image}/{p.total}</Tag>
                                <div style={{ color: '#999', fontSize: 12, paddingLeft: 12 }}>
                                  缺: {p.missing.join('、')}
                                </div>
                              </div>
                            ))}
                          </>
                        )}
                        {no_folder.length > 0 && (
                          <>
                            <p style={{ margin: '12px 0 4px' }}><b>图库里没有文件夹的产品（{no_folder.length} 个）:</b></p>
                            {no_folder.map((p: any) => (
                              <div key={p.code} style={{ fontSize: 13 }}>
                                <code>{p.code}</code> {p.name} <span style={{ color: '#999' }}>({p.sku_count} 款 SKU)</span>
                              </div>
                            ))}
                          </>
                        )}
                        <p style={{ color: '#999', fontSize: 12, marginTop: 12 }}>
                          实时读图库文件夹计算——补图/改名后再点一次即是最新结果；老产品缺图不会进异常中心。
                        </p>
                      </div>
                    ),
                  });
                } catch (e: any) {
                  message.error(e?.response?.data?.detail ?? '图库操作失败');
                }
              },
            }}
          >
            <Button>图库设置 <DownOutlined /></Button>
          </Dropdown>
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
          <FieldPresetBar tableKey="product" allFields={PRODUCT_FIELDS} defaults={PRODUCT_PRESETS} onChange={setVisibleKeys} />
        )}
      </Space>

      {viewMode === 'full' && <FullColumnView entity="product" defaultShowAll />}

      {viewMode === 'curated' && (
      <ResponsiveTable<Product>
        desktop={
          <Image.PreviewGroup>
          <Table<Product>
            rowKey="id"
            loading={isLoading}
            dataSource={data}
            columns={applyView(columns) as any}
            components={{ header: { cell: ResizableTitle } }}
            scroll={{ x: 'max-content' }}
            pagination={{
              pageSize,
              showSizeChanger: true,
              pageSizeOptions: [20, 50, 100, 200],
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
        }
        data={data ?? []}
        rowKey={(r) => r.id}
        loading={isLoading}
        emptyText="暂无产品"
        renderCard={(p) => (
          <CatalogCard
            image={(p as any).gallery_image_url || p.image_url}
            category={p.category}
            title={p.name}
            code={p.code}
            brand={p.brand}
            meta={p.category ?? undefined}
            onGallery={() => setGalleryFor(p.code)}
            dimensionActions={(
              <ProductDimensionFinalActions productCode={p.code} assetCount={p.dimension_asset_count} />
            )}
            onEdit={() => {
              setEditTarget(p);
              editForm.setFieldsValue({
                name: p.name, sub_name: p.sub_name ?? '', brand: p.brand ?? '',
                category: p.category ?? '', priority: p.priority ?? 'mid', remark: p.remark,
                image_url: p.image_url ?? '', custom_scope: p.custom_scope ?? '',
                size_value: p.size_value ?? '', size_detail: p.size_detail ?? '',
                main_material: p.main_material ?? '', aux_material: p.aux_material ?? '',
                accessory_desc: p.accessory_desc ?? '', description: p.description ?? '',
                semi_finished_eligible: (p as any).semi_finished_eligible ?? false,
                semi_group: (p as any).semi_group ?? '',
              });
            }}
            renderExpand={() => (
              <div>
                <ProductDetailSection product={p} />
                <SkuExpandedRow productCode={p.code} />
              </div>
            )}
          />
        )}
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
                sub_name: v.sub_name || null,
                brand: v.brand || null,
                category: v.category || null,
                priority: v.priority || null,
                remark: v.remark || undefined,
                image_url: v.image_url || null,
                custom_scope: v.custom_scope || null,
                size_value: v.size_value || null,
                size_detail: v.size_detail || null,
                main_material: v.main_material || null,
                aux_material: v.aux_material || null,
                accessory_desc: v.accessory_desc || null,
                description: v.description || null,
                semi_finished_eligible: !!v.semi_finished_eligible,
                semi_group: v.semi_group || null,
              },
            })
          }
        >
          <Alert type="info" showIcon style={{ marginBottom: 12 }}
            message="产品主数据为单一来源 —— 保存后该产品所有 SKU / 订单的下单图、核算自动用新值(即「一键覆盖所有SKU」)。"
            description="每个字段的改动会自动留存最近 30 份修改档案(谁/何时/旧值→新值), 可在「工具→修改档案」回看。" />
          <Form.Item name="name" label="产品名称" rules={[{ required: true }]}>
            <Input />
          </Form.Item>
          <Space style={{ display: 'flex' }} size="middle">
            <Form.Item name="sub_name" label="副名称" style={{ flex: 1 }}><Input /></Form.Item>
            <Form.Item name="brand" label="品牌" style={{ width: 120 }}><Input /></Form.Item>
            <Form.Item name="priority" label="重要程度" style={{ width: 120 }}>
              <Select options={[{ value: 'high', label: '高' }, { value: 'mid', label: '中' }, { value: 'low', label: '低' }]} />
            </Form.Item>
          </Space>
          <Form.Item name="category" label="类目"><Input placeholder="如 餐厅-餐桌" /></Form.Item>
          <Form.Item name="image_url" label="图片 URL">
            <Input placeholder="https://... 留空则清除图片" />
          </Form.Item>
          <Form.Item name="remark" label="备注">
            <Input />
          </Form.Item>
          <Form.Item name="main_material" label="主材介绍（下单图先写主材）">
            <Input.TextArea rows={2} />
          </Form.Item>
          <Form.Item name="aux_material" label="辅材介绍（下单图再写辅材）">
            <Input.TextArea rows={2} />
          </Form.Item>
          <Form.Item name="custom_scope" label="定制范围">
            <Input.TextArea rows={2} />
          </Form.Item>
          <Space style={{ display: 'flex' }} size="middle">
            <Form.Item name="size_value" label="尺寸值(mm)" style={{ width: 200 }}><Input /></Form.Item>
            <Form.Item name="accessory_desc" label="外配件说明" style={{ flex: 1 }}><Input /></Form.Item>
          </Space>
          <Form.Item name="size_detail" label="尺寸明细">
            <Input.TextArea rows={2} />
          </Form.Item>
          <Form.Item name="description" label="产品文案">
            <Input.TextArea rows={3} />
          </Form.Item>
          <div style={{ borderTop: '1px dashed #eee', paddingTop: 8 }}>
            <Space size="large" align="baseline">
              <Form.Item name="semi_finished_eligible" label="可做白坯(半成品)" valuePropName="checked"
                tooltip="R5 半成品/白坯备货能力(默认关)。打勾表示该产品前段可做成通用白坯; 需在库存备货设置里打开开关后才生效。">
                <Switch />
              </Form.Item>
              <Form.Item name="semi_group" label="白坯分组码" style={{ width: 220 }}
                tooltip="共享同一白坯的产品填相同分组码(如 榉木餐桌1.4白坯), 备货建议按组池化算白坯备货量。">
                <Input placeholder="留空=用产品编码单独成组" />
              </Form.Item>
            </Space>
          </div>
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
      <GalleryModal productCode={galleryFor} onClose={() => setGalleryFor(null)} />
    </Space>
  );
}
