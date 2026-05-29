import { useState } from 'react';
import {
  Alert,
  AutoComplete,
  Button,
  Card,
  Divider,
  Form,
  Input,
  InputNumber,
  Space,
  Table,
  Typography,
  message,
} from 'antd';
import { DeleteOutlined, PlusOutlined, SaveOutlined } from '@ant-design/icons';
import { useMutation } from '@tanstack/react-query';
import {
  ComposeBomLine,
  ComposePricingSku,
  composeProduct,
  listMaterials,
  listProducts,
  loadProductReference,
} from '../api/client';

let _rowSeq = 1;
const nextKey = () => `r${_rowSeq++}`;
type BomRow = ComposeBomLine & { _key: string };
type SkuRow = ComposePricingSku & { _key: string };

const emptyBom = (): BomRow => ({ _key: nextKey(), material_code: '', qty_per_product: 1 });
const emptySku = (): SkuRow => ({ _key: nextKey(), sku_code: '' });

export default function NewProductComposerPage() {
  const [form] = Form.useForm();
  const [bom, setBom] = useState<BomRow[]>([emptyBom()]);
  const [skus, setSkus] = useState<SkuRow[]>([emptySku()]);

  // 参考产品搜索
  const [refOptions, setRefOptions] = useState<{ value: string; label: string }[]>([]);
  // 物料搜索 (BOM 物料编码联想)
  const [matOptions, setMatOptions] = useState<{ value: string; label: string; name: string; unit: string | null }[]>([]);

  const searchRef = async (kw: string) => {
    if (!kw) { setRefOptions([]); return; }
    const list = await listProducts(kw);
    setRefOptions(list.slice(0, 20).map((p) => ({ value: p.code, label: `${p.code} — ${p.name}` })));
  };

  const searchMat = async (kw: string) => {
    if (!kw) { setMatOptions([]); return; }
    const list = await listMaterials(kw);
    setMatOptions(list.slice(0, 20).map((m) => ({
      value: m.code, label: `${m.code} — ${m.name}`, name: m.name, unit: m.unit,
    })));
  };

  const refMut = useMutation({
    mutationFn: (code: string) => loadProductReference(code),
    onSuccess: (r) => {
      form.setFieldsValue({
        name: r.product.name,
        remark: r.product.remark ?? undefined,
      });
      setBom(r.bom_lines.length ? r.bom_lines.map((b) => ({ ...b, _key: nextKey() })) : [emptyBom()]);
      setSkus(r.pricing_skus.length ? r.pricing_skus.map((s) => ({ ...s, _key: nextKey() })) : [emptySku()]);
      message.success(`已带入参考产品 ${r.product.code} 的 BOM(${r.bom_lines.length}) 与定价(${r.pricing_skus.length})，品牌/类目请重新选择`);
    },
    onError: (e: any) => message.error(e?.response?.data?.detail ?? '加载参考失败'),
  });

  const saveMut = useMutation({
    mutationFn: composeProduct,
    onSuccess: (r) => {
      message.success(`已创建产品 ${r.product_code}（BOM ${r.bom_lines} 行，定价 ${r.pricing_skus} 个 SKU）`);
      form.resetFields();
      setBom([emptyBom()]);
      setSkus([emptySku()]);
    },
    onError: (e: any) => message.error(e?.response?.data?.detail ?? '创建失败'),
  });

  const setBomRow = (key: string, patch: Partial<BomRow>) =>
    setBom((prev) => prev.map((r) => (r._key === key ? { ...r, ...patch } : r)));
  const setSkuRow = (key: string, patch: Partial<SkuRow>) =>
    setSkus((prev) => prev.map((r) => (r._key === key ? { ...r, ...patch } : r)));

  const onSave = async () => {
    const vals = await form.validateFields();
    const cleanBom = bom.filter((b) => b.material_code?.trim());
    const cleanSku = skus.filter((s) => s.sku_code?.trim());
    if (cleanSku.length === 0) {
      message.warning('至少填写一个定价 SKU（含 SKU 编码）');
      return;
    }
    saveMut.mutate({
      name: vals.name,
      brand: (vals.brand || '').toUpperCase(),
      category: vals.category,
      category_label: vals.category_label || undefined,
      remark: vals.remark || undefined,
      taobao_id: vals.taobao_id || undefined,
      bom_lines: cleanBom.map(({ _key, ...rest }) => rest),
      pricing_skus: cleanSku.map(({ _key, ...rest }) => rest),
    });
  };

  const numInput = (
    v: string | number | null | undefined,
    on: (n: number | null) => void,
    width = 100,
  ) => (
    <InputNumber
      value={v == null || v === '' ? undefined : Number(v)}
      size="small" min={0} step={0.01} style={{ width }}
      onChange={(n) => on(n == null ? null : Number(n))}
    />
  );

  const bomColumns = [
    {
      title: '物料编码', dataIndex: 'material_code', width: 220,
      render: (_: any, row: BomRow) => (
        <AutoComplete
          value={row.material_code}
          size="small"
          style={{ width: 200 }}
          options={matOptions}
          onSearch={searchMat}
          onChange={(val) => setBomRow(row._key, { material_code: val })}
          onSelect={(val) => {
            const opt = matOptions.find((o) => o.value === val);
            setBomRow(row._key, { material_code: val, material_name: opt?.name, unit: opt?.unit ?? undefined });
          }}
          placeholder="搜编码/名称"
        />
      ),
    },
    { title: '物料名称', dataIndex: 'material_name', width: 160,
      render: (_: any, row: BomRow) => (
        <Input size="small" value={row.material_name ?? ''} onChange={(e) => setBomRow(row._key, { material_name: e.target.value })} />
      ) },
    { title: '单位', dataIndex: 'unit', width: 70,
      render: (_: any, row: BomRow) => (
        <Input size="small" value={row.unit ?? ''} onChange={(e) => setBomRow(row._key, { unit: e.target.value })} />
      ) },
    { title: '单耗', dataIndex: 'qty_per_product', width: 90,
      render: (_: any, row: BomRow) => numInput(row.qty_per_product, (n) => setBomRow(row._key, { qty_per_product: n ?? 1 }), 80) },
    { title: '尺寸类型', dataIndex: 'size_type', width: 110,
      render: (_: any, row: BomRow) => (
        <Input size="small" value={row.size_type ?? ''} placeholder="组合/个数" onChange={(e) => setBomRow(row._key, { size_type: e.target.value })} />
      ) },
    { title: '', width: 40,
      render: (_: any, row: BomRow) => (
        <Button size="small" type="text" danger icon={<DeleteOutlined />}
                onClick={() => setBom((prev) => prev.filter((r) => r._key !== row._key))} />
      ) },
  ];

  const skuColumns = [
    { title: 'SKU编码*', dataIndex: 'sku_code', width: 140,
      render: (_: any, row: SkuRow) => (
        <Input size="small" value={row.sku_code} onChange={(e) => setSkuRow(row._key, { sku_code: e.target.value })} placeholder="必填" />
      ) },
    { title: 'SKU描述', dataIndex: 'sku', width: 150,
      render: (_: any, row: SkuRow) => (
        <Input size="small" value={row.sku ?? ''} onChange={(e) => setSkuRow(row._key, { sku: e.target.value })} />
      ) },
    { title: '大小类型', dataIndex: 'size_category', width: 90,
      render: (_: any, row: SkuRow) => (
        <Input size="small" value={row.size_category ?? ''} placeholder="小/中/大型" onChange={(e) => setSkuRow(row._key, { size_category: e.target.value })} />
      ) },
    { title: '标价', dataIndex: 'list_price', width: 90,
      render: (_: any, row: SkuRow) => numInput(row.list_price, (n) => setSkuRow(row._key, { list_price: n })) },
    { title: '日常价', dataIndex: 'daily_price', width: 90,
      render: (_: any, row: SkuRow) => numInput(row.daily_price, (n) => setSkuRow(row._key, { daily_price: n })) },
    { title: '小促', dataIndex: 'small_promo', width: 90,
      render: (_: any, row: SkuRow) => numInput(row.small_promo, (n) => setSkuRow(row._key, { small_promo: n })) },
    { title: '中促', dataIndex: 'mid_promo', width: 90,
      render: (_: any, row: SkuRow) => numInput(row.mid_promo, (n) => setSkuRow(row._key, { mid_promo: n })) },
    { title: '大促', dataIndex: 'big_promo', width: 90,
      render: (_: any, row: SkuRow) => numInput(row.big_promo, (n) => setSkuRow(row._key, { big_promo: n })) },
    { title: '会计成本', dataIndex: 'accounting_cost', width: 100,
      render: (_: any, row: SkuRow) => numInput(row.accounting_cost, (n) => setSkuRow(row._key, { accounting_cost: n })) },
    { title: '物理成本', dataIndex: 'physical_cost', width: 100,
      render: (_: any, row: SkuRow) => numInput(row.physical_cost, (n) => setSkuRow(row._key, { physical_cost: n })) },
    { title: '', width: 40,
      render: (_: any, row: SkuRow) => (
        <Button size="small" type="text" danger icon={<DeleteOutlined />}
                onClick={() => setSkus((prev) => prev.filter((r) => r._key !== row._key))} />
      ) },
  ];

  return (
    <Space direction="vertical" style={{ width: '100%' }} size="middle">
      <Space style={{ justifyContent: 'space-between', width: '100%' }}>
        <Typography.Title level={4} style={{ margin: 0 }}>新产品综合录入</Typography.Title>
        <Button type="primary" icon={<SaveOutlined />} loading={saveMut.isPending} onClick={onSave}>
          一键创建（产品 + BOM + 定价）
        </Button>
      </Space>

      <Alert
        type="info"
        showIcon
        message="一个界面录完产品主数据、BOM 物料清单、定价 SKU，提交时在同一事务里创建，任一步失败全部回滚。"
        description="可先选「参考已有产品」把 BOM 和定价带进来，改改再存为新品。产品编码按 品牌+年份+类目 自动生成。"
      />

      <Card size="small" title="参考已有产品（可选）">
        <AutoComplete
          style={{ width: 420 }}
          options={refOptions}
          onSearch={searchRef}
          onSelect={(code) => refMut.mutate(code)}
          placeholder="搜产品编码 / 名称，选中即带入 BOM 与定价"
        />
      </Card>

      <Card size="small" title="① 产品主数据">
        <Form form={form} layout="inline" size="small" style={{ rowGap: 12 }}>
          <Form.Item name="name" label="产品名称" rules={[{ required: true, message: '必填' }]}>
            <Input style={{ width: 240 }} />
          </Form.Item>
          <Form.Item name="brand" label="品牌码" rules={[{ required: true, len: 2, message: '2 字母' }]}>
            <Input style={{ width: 80 }} placeholder="PS" maxLength={2} />
          </Form.Item>
          <Form.Item name="category" label="类目码" rules={[{ required: true, len: 2, message: '2 位数字' }]}>
            <Input style={{ width: 80 }} placeholder="33" maxLength={2} />
          </Form.Item>
          <Form.Item name="category_label" label="类目名">
            <Input style={{ width: 140 }} placeholder="卧室-床" />
          </Form.Item>
          <Form.Item name="taobao_id" label="淘宝商品ID">
            <Input style={{ width: 160 }} />
          </Form.Item>
          <Form.Item name="remark" label="备注">
            <Input style={{ width: 200 }} />
          </Form.Item>
        </Form>
      </Card>

      <Card
        size="small"
        title="② BOM 物料清单"
        extra={<Button size="small" icon={<PlusOutlined />} onClick={() => setBom((p) => [...p, emptyBom()])}>加一行</Button>}
      >
        <Table rowKey="_key" size="small" dataSource={bom} columns={bomColumns as any} pagination={false} scroll={{ x: 800 }} />
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          物料编码须在物料库已存在（不存在请先去「物料单价库」建档）。
        </Typography.Text>
      </Card>

      <Card
        size="small"
        title="③ 定价 SKU"
        extra={<Button size="small" icon={<PlusOutlined />} onClick={() => setSkus((p) => [...p, emptySku()])}>加一行</Button>}
      >
        <Table rowKey="_key" size="small" dataSource={skus} columns={skuColumns as any} pagination={false} scroll={{ x: 1100 }} />
      </Card>

      <Divider style={{ margin: '4px 0' }} />
      <Button type="primary" size="large" icon={<SaveOutlined />} loading={saveMut.isPending} onClick={onSave} block>
        一键创建产品 + BOM + 定价
      </Button>
    </Space>
  );
}
