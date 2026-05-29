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
  Popover,
  Space,
  Table,
  Tag,
  Typography,
  message,
} from 'antd';
import { BulbOutlined, DeleteOutlined, PlusOutlined, SaveOutlined } from '@ant-design/icons';
import { useMutation, useQuery } from '@tanstack/react-query';
import {
  ComposeBomLine,
  ComposePricingSku,
  RatioHints,
  composeProduct,
  getRatioHints,
  listMaterials,
  listProducts,
  listRecentProducts,
  loadProductReference,
} from '../api/client';

let _rowSeq = 1;
const nextKey = () => `r${_rowSeq++}`;
type BomRow = ComposeBomLine & { _key: string };
type SkuRow = ComposePricingSku & { _key: string };

const emptyBom = (): BomRow => ({ _key: nextKey(), material_code: '', qty_per_product: 1 });
const emptySku = (): SkuRow => ({ _key: nextKey(), sku_code: '' });

// 大促到手价单元格: 输入框 + 聚焦时浮出「历史比例参考」, 点某条按 成本/比例 回填
function BigPromoCell({
  row,
  hints,
  category,
  onChange,
}: {
  row: SkuRow;
  hints?: RatioHints;
  category?: string;
  onChange: (n: number | null) => void;
}) {
  const [open, setOpen] = useState(false);
  const accounting = row.accounting_cost == null || row.accounting_cost === '' ? null : Number(row.accounting_cost);
  const physical = row.physical_cost == null || row.physical_cost === '' ? null : Number(row.physical_cost);

  // 各口径对应这一行能用来回填的成本值 (出厂成本本表未录入, 仅作参考展示)
  const costFor: Record<string, number | null> = { accounting, physical, factory: null };

  const fill = (costField: string, ratio: number) => {
    const cost = costFor[costField];
    if (cost == null) {
      message.warning('本行该口径成本未填, 无法按比例回填 (可手动输入到手价)');
      return;
    }
    if (ratio <= 0) return;
    onChange(Math.round((cost / ratio) * 100) / 100);
    setOpen(false);
  };

  const hintContent = () => {
    if (!hints) return <Typography.Text type="secondary">加载中…</Typography.Text>;
    const calibers = Object.entries(hints.calibers).filter(([, c]) => c.sample > 0);
    if (calibers.length === 0) {
      return <Typography.Text type="secondary">暂无历史数据可参考</Typography.Text>;
    }
    return (
      <div style={{ width: 320 }}>
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          比例 = 成本 ÷ 大促到手价。点某条按「该口径成本 ÷ 比例」回填到手价。
          {hints.category ? `（类目: ${hints.category}）` : '（全部产品）'}
        </Typography.Text>
        {calibers.map(([key, c]) => {
          const canFill = costFor[key] != null;
          return (
            <div key={key} style={{ marginTop: 10 }}>
              <Space size={6}>
                <Typography.Text strong style={{ fontSize: 13 }}>{c.label}</Typography.Text>
                <Typography.Text type="secondary" style={{ fontSize: 11 }}>
                  样本 {c.sample}{c.used_global ? ' · 全局' : ''}
                </Typography.Text>
              </Space>
              <div style={{ marginTop: 4 }}>
                {c.top.map((t) => (
                  <Tag
                    key={t.ratio}
                    color={canFill ? 'blue' : 'default'}
                    style={{ cursor: canFill ? 'pointer' : 'not-allowed', marginBottom: 4 }}
                    onClick={() => canFill && fill(key, t.ratio)}
                  >
                    {Math.round(t.ratio * 100)}% · {t.pct}% 产品
                  </Tag>
                ))}
                {c.range && (
                  <Typography.Text type="secondary" style={{ fontSize: 11, marginLeft: 4 }}>
                    中间 80% 落在 {Math.round(c.range.low * 100)}%–{Math.round(c.range.high * 100)}%
                  </Typography.Text>
                )}
              </div>
            </div>
          );
        })}
      </div>
    );
  };

  return (
    <Popover
      open={open}
      onOpenChange={setOpen}
      trigger={[]}
      placement="bottomLeft"
      title={
        <Space size={4}>
          <BulbOutlined style={{ color: '#faad14' }} />
          <span style={{ fontSize: 13 }}>历史比例参考{category ? `（${category}）` : ''}</span>
        </Space>
      }
      content={hintContent()}
    >
      <InputNumber
        value={row.big_promo == null || row.big_promo === '' ? undefined : Number(row.big_promo)}
        size="small"
        min={0}
        step={0.01}
        style={{ width: 100 }}
        onFocus={() => setOpen(true)}
        onBlur={() => setTimeout(() => setOpen(false), 200)}
        onChange={(n) => onChange(n == null ? null : Number(n))}
      />
    </Popover>
  );
}

export default function NewProductComposerPage() {
  const [form] = Form.useForm();
  const [bom, setBom] = useState<BomRow[]>([emptyBom()]);
  const [skus, setSkus] = useState<SkuRow[]>([emptySku()]);
  const categoryLabel = Form.useWatch('category_label', form) as string | undefined;

  // 参考产品搜索 (空输入时显示最近更新)
  const [refOptions, setRefOptions] = useState<{ value: string; label: string }[]>([]);
  // 物料搜索 (BOM 物料编码联想)
  const [matOptions, setMatOptions] = useState<{ value: string; label: string; name: string; unit: string | null }[]>([]);

  // 比例参考分布 (按当前类目, 类目变了自动重拉)
  const { data: ratioHints } = useQuery({
    queryKey: ['ratio-hints', categoryLabel ?? ''],
    queryFn: () => getRatioHints(categoryLabel || undefined),
    staleTime: 5 * 60 * 1000,
  });

  const showRecent = async () => {
    const list = await listRecentProducts(10);
    setRefOptions(list.map((p) => ({ value: p.code, label: `${p.code} — ${p.name}（最近更新）` })));
  };

  const searchRef = async (kw: string) => {
    if (!kw) { showRecent(); return; }
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
    { title: <span>大促 <BulbOutlined style={{ color: '#faad14' }} /></span>, dataIndex: 'big_promo', width: 110,
      render: (_: any, row: SkuRow) => (
        <BigPromoCell
          row={row}
          hints={ratioHints}
          category={categoryLabel}
          onChange={(n) => setSkuRow(row._key, { big_promo: n })}
        />
      ) },
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
        description="可先选「参考已有产品」把 BOM 和定价带进来，改改再存为新品。大促价填写时点 💡 看同类目历史比例参考。产品编码按 品牌+年份+类目 自动生成。"
      />

      <Card size="small" title="参考已有产品（可选）">
        <AutoComplete
          style={{ width: 460 }}
          options={refOptions}
          onSearch={searchRef}
          onFocus={() => { if (refOptions.length === 0) showRecent(); }}
          onSelect={(code) => refMut.mutate(code)}
          placeholder="不知道编码? 直接点这里看最近更新的产品，或搜编码 / 名称"
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
        <Table rowKey="_key" size="small" dataSource={skus} columns={skuColumns as any} pagination={false} scroll={{ x: 1120 }} />
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          💡 大促价: 点输入框看「会计/物理/出厂」三口径的历史比例分布，点蓝色标签按 成本÷比例 自动回填。
        </Typography.Text>
      </Card>

      <Divider style={{ margin: '4px 0' }} />
      <Button type="primary" size="large" icon={<SaveOutlined />} loading={saveMut.isPending} onClick={onSave} block>
        一键创建产品 + BOM + 定价
      </Button>
    </Space>
  );
}
