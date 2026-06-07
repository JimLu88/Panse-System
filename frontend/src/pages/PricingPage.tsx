import { useEffect, useRef, useState, type Key } from 'react';
import {
  Button,
  Card,
  Dropdown,
  Form,
  Input,
  InputNumber,
  Modal,
  Segmented,
  Select,
  Space,
  Table,
  Tag,
  Tooltip,
  Typography,
  message,
} from 'antd';
import { DownloadOutlined, EditOutlined, ExportOutlined, PlusOutlined } from '@ant-design/icons';
import FullColumnView from '../components/FullColumnView';
import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  PricingSku,
  createPricingSku,
  downloadPricingTemplate,
  listPricingSkus,
  listPricingTemplates,
  updatePricingSku,
  listTaobaoExportTypes,
  downloadTaobaoExport,
} from '../api/client';

const PAGE_SIZE = 50;

function money(v: number | null) {
  return v === null || v === undefined ? '-' : `¥${Number(v).toLocaleString()}`;
}

// 可拖拽列宽的表头单元格 (拖右边缘改宽)
function ResizableTitle(props: any) {
  const { onResize, width, children, ...rest } = props;
  const start = useRef<{ x: number; w: number } | null>(null);
  if (!width || !onResize) return <th {...rest}>{children}</th>;
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
        style={{ position: 'absolute', right: -4, top: 0, bottom: 0, width: 9, cursor: 'col-resize', zIndex: 1, userSelect: 'none' }}
      />
    </th>
  );
}

// 可点击编辑的数字格: 点一下变输入框, 失焦/回车保存
function EditableNumberCell({ value, onSave }: { value: number | null; onSave: (v: number | null) => void }) {
  const [editing, setEditing] = useState(false);
  const [val, setVal] = useState<number | null>(value);
  useEffect(() => { setVal(value); }, [value]);
  if (!editing) {
    return (
      <span onClick={() => setEditing(true)} style={{ cursor: 'pointer', display: 'inline-block', minWidth: 40 }} title="点击编辑">
        {value === null || value === undefined
          ? <Typography.Text type="secondary">—</Typography.Text>
          : `¥${Number(value).toLocaleString()}`}
      </span>
    );
  }
  const commit = () => { setEditing(false); if (val !== value) onSave(val); };
  return (
    <InputNumber
      size="small" autoFocus value={val} precision={2} min={0}
      onChange={setVal} onBlur={commit} onPressEnter={commit}
      style={{ width: '100%' }}
    />
  );
}

// 毛利率格: 彩色 Tag; 点击后按目标毛利率% 反算日常价 (服务端再自动算回毛利率)
function MarginCell({ row, onSaveDaily }: { row: PricingSku; onSaveDaily: (dailyPrice: number) => void }) {
  const v = row.gross_margin_rate;
  const [editing, setEditing] = useState(false);
  const [pct, setPct] = useState<number | null>(v != null ? Number((Number(v) * 100).toFixed(1)) : null);
  useEffect(() => { setPct(v != null ? Number((Number(v) * 100).toFixed(1)) : null); }, [v]);
  if (!editing) {
    const tag = v === null || v === undefined
      ? <Typography.Text type="secondary">—</Typography.Text>
      : <Tag color={Number(v) >= 0.3 ? 'green' : Number(v) >= 0.15 ? 'orange' : 'red'}>{(Number(v) * 100).toFixed(1)}%</Tag>;
    return <span onClick={() => setEditing(true)} style={{ cursor: 'pointer' }} title="点击按目标毛利率反算日常价">{tag}</span>;
  }
  const commit = () => {
    setEditing(false);
    if (pct === null || pct === undefined) return;
    if (!row.accounting_cost) { message.error('该 SKU 缺会计成本，无法按毛利率反算日常价'); return; }
    const cost = Number(row.accounting_cost);
    const tax = Number(row.tax ?? 0);
    const pfr = Number(row.platform_fee_rate ?? 0);
    const denom = 1 - pfr - pct / 100;
    if (denom <= 0) { message.error('毛利率过高，无法反算（1 − 平台费率 − 毛利率 ≤ 0）'); return; }
    onSaveDaily(Math.round(((cost + tax) / denom) * 100) / 100);
  };
  return (
    <InputNumber
      size="small" autoFocus value={pct} min={0} max={99} precision={1}
      onChange={setPct} onBlur={commit} onPressEnter={commit}
      style={{ width: '100%' }}
    />
  );
}

function SkuFormFields() {
  return (
    <>
      <Space wrap style={{ width: '100%' }}>
        <Form.Item name="product_code" label="产品编码" rules={[{ required: true }]} style={{ minWidth: 180 }}>
          <Input placeholder="如 PS-24-21-001-0814" />
        </Form.Item>
        <Form.Item name="sku" label="SKU 描述">
          <Input placeholder="如 榉木餐桌-1.4米" style={{ minWidth: 200 }} />
        </Form.Item>
        <Form.Item name="sku_code" label="SKU 编码">
          <Input placeholder="系统自动生成可留空" style={{ minWidth: 160 }} />
        </Form.Item>
        <Form.Item name="size_category" label="尺寸分类">
          <Select style={{ width: 100 }} options={[
            { value: '小型', label: '小型' },
            { value: '中型', label: '中型' },
            { value: '大型', label: '大型' },
          ]} allowClear />
        </Form.Item>
      </Space>
      <Space wrap style={{ width: '100%' }}>
        <Form.Item name="list_price" label="标价">
          <InputNumber min={0} step={0.01} prefix="¥" style={{ width: 120 }} />
        </Form.Item>
        <Form.Item name="daily_price" label="日常价">
          <InputNumber min={0} step={0.01} prefix="¥" style={{ width: 120 }} />
        </Form.Item>
        <Form.Item name="small_promo" label="小促价">
          <InputNumber min={0} step={0.01} prefix="¥" style={{ width: 120 }} />
        </Form.Item>
        <Form.Item name="mid_promo" label="中促价">
          <InputNumber min={0} step={0.01} prefix="¥" style={{ width: 120 }} />
        </Form.Item>
        <Form.Item name="big_promo" label="大促价">
          <InputNumber min={0} step={0.01} prefix="¥" style={{ width: 120 }} />
        </Form.Item>
      </Space>
      <Space wrap style={{ width: '100%' }}>
        <Form.Item name="accounting_cost" label="会计成本">
          <InputNumber min={0} step={0.01} prefix="¥" style={{ width: 120 }} />
        </Form.Item>
        <Form.Item name="physical_cost" label="物理成本">
          <InputNumber min={0} step={0.01} prefix="¥" style={{ width: 120 }} />
        </Form.Item>
        <Form.Item name="platform_fee_rate" label="平台佣金率">
          <InputNumber min={0} max={1} step={0.01} style={{ width: 100 }} placeholder="如 0.05" />
        </Form.Item>
        <Form.Item name="tax" label="税率">
          <InputNumber min={0} max={1} step={0.01} style={{ width: 100 }} placeholder="如 0.03" />
        </Form.Item>
      </Space>
      <Form.Item name="image_url" label="图片 URL（选填）">
        <Input placeholder="https://..." />
      </Form.Item>
    </>
  );
}

export default function PricingPage() {
  const qc = useQueryClient();
  const [q, setQ] = useState('');
  const [sizeCategory, setSizeCategory] = useState<string | undefined>(undefined);
  const [page, setPage] = useState(1);
  const [createOpen, setCreateOpen] = useState(false);
  const [editRow, setEditRow] = useState<PricingSku | null>(null);
  const [viewMode, setViewMode] = useState<'curated' | 'full'>('curated');
  const [form] = Form.useForm();

  const { data, isFetching } = useQuery({
    queryKey: ['pricing-skus', q, sizeCategory, page],
    queryFn: () =>
      listPricingSkus({ q: q || undefined, size_category: sizeCategory, limit: PAGE_SIZE, offset: (page - 1) * PAGE_SIZE }),
    placeholderData: keepPreviousData,
  });

  const { data: templates } = useQuery({
    queryKey: ['pricing-templates'],
    queryFn: listPricingTemplates,
    staleTime: 60 * 60 * 1000,
  });

  async function handleDownloadTemplate(key: string, label: string) {
    try {
      const blob = await downloadPricingTemplate(key);
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `${label}.xlsx`;
      a.click();
      URL.revokeObjectURL(url);
    } catch {
      message.error('模板下载失败');
    }
  }

  const { data: exportTypes } = useQuery({
    queryKey: ['taobao-export-types'],
    queryFn: listTaobaoExportTypes,
    staleTime: 60 * 60 * 1000,
  });

  async function handleExport(exportType: string, label: string) {
    try {
      const blob = await downloadTaobaoExport(exportType);
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `淘宝-${label}.xlsx`;
      a.click();
      URL.revokeObjectURL(url);
    } catch {
      message.error('导出失败');
    }
  }

  const createMut = useMutation({
    mutationFn: createPricingSku,
    onSuccess: () => {
      message.success('定价 SKU 已创建');
      setCreateOpen(false);
      form.resetFields();
      qc.invalidateQueries({ queryKey: ['pricing-skus'] });
    },
    onError: (e: any) => message.error(e?.response?.data?.detail ?? '创建失败'),
  });

  const updateMut = useMutation({
    mutationFn: ({ id, patch }: { id: number; patch: Record<string, unknown> }) =>
      updatePricingSku(id, patch),
    onSuccess: () => {
      message.success('已更新');
      setEditRow(null);
      form.resetFields();
      qc.invalidateQueries({ queryKey: ['pricing-skus'] });
    },
    onError: (e: any) => message.error(e?.response?.data?.detail ?? '更新失败'),
  });

  function openEdit(row: PricingSku) {
    setEditRow(row);
    form.setFieldsValue(row);
  }

  // ── 列宽可拖 ──
  const [colW, setColW] = useState<Record<string, number>>({
    product_code: 110, sku_code: 120, sku: 160, size_category: 70,
    list_price: 90, daily_price: 90, small_promo: 90, mid_promo: 90, big_promo: 100,
    gross_margin_rate: 90, accounting_cost: 100, physical_cost: 100, actions: 70,
  });
  const mkResize = (key: string) => () => ({
    width: colW[key], onResize: (w: number) => setColW((p) => ({ ...p, [key]: w })),
  });
  const scrollX = Object.values(colW).reduce((a, b) => a + b, 0) + 70;

  const items = data?.items ?? [];

  // 内联保存单格 (改价/改成本后服务端自动重算毛利率)
  const saveField = (id: number, field: string, value: number | null) =>
    updateMut.mutate({ id, patch: { [field]: value } });

  // ── 多选 + 批量调价 ──
  const [selectedKeys, setSelectedKeys] = useState<Key[]>([]);
  const lastIdx = useRef<number | null>(null);
  const [batchField, setBatchField] = useState<string>('big_promo');
  const [batchMode, setBatchMode] = useState<'set' | 'multiply'>('multiply');
  const [batchValue, setBatchValue] = useState<number | null>(null);
  const [batchRunning, setBatchRunning] = useState(false);

  const BATCH_FIELDS = [
    { value: 'list_price', label: '标价' },
    { value: 'daily_price', label: '日常价' },
    { value: 'small_promo', label: '小促' },
    { value: 'mid_promo', label: '中促' },
    { value: 'big_promo', label: '大促' },
    { value: 'accounting_cost', label: '会计成本' },
    { value: 'physical_cost', label: '物理成本' },
  ];

  async function batchApply() {
    if (batchValue === null || batchValue === undefined) { message.warning('请输入数值'); return; }
    const ids = selectedKeys.map(Number);
    const byId = new Map(items.map((r) => [r.id, r]));
    const tasks: Promise<unknown>[] = [];
    let skipped = 0;
    for (const id of ids) {
      if (batchMode === 'set') {
        tasks.push(updatePricingSku(id, { [batchField]: batchValue }));
      } else {
        const row = byId.get(id);
        if (!row) { skipped += 1; continue; }
        const cur = Number((row as any)[batchField] ?? 0);
        tasks.push(updatePricingSku(id, { [batchField]: Math.round(cur * batchValue * 100) / 100 }));
      }
    }
    setBatchRunning(true);
    try {
      await Promise.all(tasks);
      message.success(`已套用 ${tasks.length} 个 SKU${skipped ? `（${skipped} 个跨页未加载，已跳过）` : ''}`);
      setSelectedKeys([]);
      qc.invalidateQueries({ queryKey: ['pricing-skus'] });
    } catch {
      message.error('批量套用失败');
    } finally {
      setBatchRunning(false);
    }
  }

  return (
    <Space direction="vertical" style={{ width: '100%' }} size="middle">
      <Space style={{ justifyContent: 'space-between', width: '100%' }}>
        <Typography.Title level={4} style={{ margin: 0 }}>定价总表</Typography.Title>
        <Space>
          <Dropdown
            disabled={!templates || templates.length === 0}
            menu={{
              items: (templates ?? []).map((t) => ({
                key: t.key,
                label: (
                  <Space direction="vertical" size={0}>
                    <span>{t.label}</span>
                    <Typography.Text type="secondary" style={{ fontSize: 11 }}>{t.desc}</Typography.Text>
                  </Space>
                ),
                onClick: () => handleDownloadTemplate(t.key, t.label),
              })),
            }}
          >
            <Button icon={<DownloadOutlined />}>一键模板下载</Button>
          </Dropdown>
          <Tooltip title="把当前系统定价数据填入淘宝后台批量格式, 下载后可直接上传淘宝后台">
            <Dropdown
              disabled={!exportTypes || exportTypes.length === 0}
              menu={{
                items: (exportTypes ?? []).map((t) => ({
                  key: t.key,
                  label: t.label,
                  onClick: () => handleExport(t.key, t.label),
                })),
              }}
            >
              <Button icon={<ExportOutlined />}>批量导出（填好数据）</Button>
            </Dropdown>
          </Tooltip>
          <Button type="primary" icon={<PlusOutlined />} onClick={() => { setCreateOpen(true); form.resetFields(); }}>
            新增定价
          </Button>
        </Space>
      </Space>
      <Card size="small">
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
            <>
              <Input.Search
                allowClear
                placeholder="搜产品编码 / SKU 编码 / 描述"
                style={{ width: 280 }}
                onSearch={(v) => { setQ(v); setPage(1); }}
              />
              <Select
                allowClear
                placeholder="大小分类"
                style={{ width: 140 }}
                value={sizeCategory}
                onChange={(v) => { setSizeCategory(v); setPage(1); }}
                options={[
                  { value: '小型', label: '小型' },
                  { value: '中型', label: '中型' },
                  { value: '大型', label: '大型' },
                ]}
              />
            </>
          )}
        </Space>
      </Card>

      {viewMode === 'full' && <FullColumnView entity="pricing_sku" defaultShowAll />}

      {viewMode === 'curated' && selectedKeys.length > 0 && (
        <div style={{ background: '#f5f7fa', border: '1px solid #e6eaf0', borderRadius: 8, padding: '8px 12px' }}>
          <Space wrap>
            <span>已选 <b>{selectedKeys.length}</b> 个 SKU</span>
            <Select size="small" style={{ width: 110 }} value={batchField} onChange={setBatchField} options={BATCH_FIELDS} />
            <Select
              size="small" style={{ width: 120 }} value={batchMode}
              onChange={(v) => setBatchMode(v as 'set' | 'multiply')}
              options={[{ value: 'multiply', label: '× 系数' }, { value: 'set', label: '设为固定值' }]}
            />
            <InputNumber
              size="small" style={{ width: 130 }} value={batchValue} onChange={setBatchValue}
              placeholder={batchMode === 'multiply' ? '如 0.95' : '如 1999'}
            />
            <Button size="small" type="primary" loading={batchRunning} onClick={batchApply}>套用</Button>
            <Button size="small" type="text" onClick={() => setSelectedKeys([])}>取消</Button>
            <Tooltip title="想按「公式规则」整批重算各档价格，去『公式规则』页点「批量重算」">
              <Typography.Text type="secondary" style={{ fontSize: 12, cursor: 'help' }}>按公式重算？</Typography.Text>
            </Tooltip>
          </Space>
        </div>
      )}

      {viewMode === 'curated' && (
      <Table<PricingSku>
        size="small"
        rowKey="id"
        loading={isFetching}
        dataSource={items}
        components={{ header: { cell: ResizableTitle } }}
        rowSelection={{
          selectedRowKeys: selectedKeys,
          onChange: setSelectedKeys,
          preserveSelectedRowKeys: true,
          onSelect: (record: PricingSku, selected: boolean, _rows: any, e: any) => {
            const idx = items.findIndex((r) => r.id === record.id);
            if (e?.shiftKey && lastIdx.current !== null && idx !== -1) {
              const a = Math.min(lastIdx.current, idx);
              const b = Math.max(lastIdx.current, idx);
              const range = items.slice(a, b + 1).map((r) => r.id);
              setSelectedKeys((prev) => {
                const set = new Set<Key>(prev);
                range.forEach((k) => (selected ? set.add(k) : set.delete(k)));
                return Array.from(set);
              });
            }
            lastIdx.current = idx;
          },
        }}
        scroll={{ x: scrollX }}
        pagination={{
          current: page,
          pageSize: PAGE_SIZE,
          total: data?.total ?? 0,
          showTotal: (t) => `共 ${t} 条`,
          onChange: setPage,
          showSizeChanger: false,
        }}
        columns={[
          { title: '产品编码', dataIndex: 'product_code', width: colW.product_code, onHeaderCell: mkResize('product_code') },
          { title: 'SKU 编码', dataIndex: 'sku_code', width: colW.sku_code, onHeaderCell: mkResize('sku_code') },
          { title: '描述', dataIndex: 'sku', width: colW.sku, ellipsis: true, onHeaderCell: mkResize('sku') },
          { title: '分类', dataIndex: 'size_category', width: colW.size_category, onHeaderCell: mkResize('size_category') },
          { title: <Tooltip title="公式：物理成本 ÷ 0.4 ｜ 点格子可直接改"><span style={{ borderBottom: '1px dotted #bbb', cursor: 'help' }}>标价</span></Tooltip>, dataIndex: 'list_price', width: colW.list_price, onHeaderCell: mkResize('list_price'), render: (v: number | null, r: PricingSku) => <EditableNumberCell value={v} onSave={(nv) => saveField(r.id, 'list_price', nv)} /> },
          { title: <Tooltip title="公式：标价 × 0.75 ｜ 点格子可直接改"><span style={{ borderBottom: '1px dotted #bbb', cursor: 'help' }}>日常价</span></Tooltip>, dataIndex: 'daily_price', width: colW.daily_price, onHeaderCell: mkResize('daily_price'), render: (v: number | null, r: PricingSku) => <EditableNumberCell value={v} onSave={(nv) => saveField(r.id, 'daily_price', nv)} /> },
          { title: <Tooltip title="公式：物理成本 ÷ (0.855 − 0.02 − 0.006) ｜ 点格子可直接改"><span style={{ borderBottom: '1px dotted #bbb', cursor: 'help' }}>小促</span></Tooltip>, dataIndex: 'small_promo', width: colW.small_promo, onHeaderCell: mkResize('small_promo'), render: (v: number | null, r: PricingSku) => <EditableNumberCell value={v} onSave={(nv) => saveField(r.id, 'small_promo', nv)} /> },
          { title: <Tooltip title="公式：物理成本 ÷ (0.88 × 0.855 − 0.02 − 0.006) ｜ 点格子可直接改"><span style={{ borderBottom: '1px dotted #bbb', cursor: 'help' }}>中促</span></Tooltip>, dataIndex: 'mid_promo', width: colW.mid_promo, onHeaderCell: mkResize('mid_promo'), render: (v: number | null, r: PricingSku) => <EditableNumberCell value={v} onSave={(nv) => saveField(r.id, 'mid_promo', nv)} /> },
          { title: <Tooltip title="公式：物理成本 ÷ (0.88 × 0.855 − 0.02 − 0.006) × 0.95 ｜ 竞品调价常用, 点格子或多选批量改"><span style={{ borderBottom: '1px dotted #bbb', cursor: 'help' }}>大促</span></Tooltip>, dataIndex: 'big_promo', width: colW.big_promo, onHeaderCell: mkResize('big_promo'), render: (v: number | null, r: PricingSku) => <EditableNumberCell value={v} onSave={(nv) => saveField(r.id, 'big_promo', nv)} /> },
          {
            title: <Tooltip title="公式：(日常价 − 会计成本 − 税费 − 日常价 × 平台费率) ÷ 日常价 ｜ 点格子按目标毛利率反算日常价"><span style={{ borderBottom: '1px dotted #bbb', cursor: 'help' }}>毛利率</span></Tooltip>,
            dataIndex: 'gross_margin_rate',
            width: colW.gross_margin_rate,
            onHeaderCell: mkResize('gross_margin_rate'),
            render: (_: unknown, r: PricingSku) => <MarginCell row={r} onSaveDaily={(dp) => saveField(r.id, 'daily_price', dp)} />,
          },
          { title: <Tooltip title="公式：总出厂成本 + 物流费 + 安装费 + 外采配件成本 ｜ 点格子可直接改"><span style={{ borderBottom: '1px dotted #bbb', cursor: 'help' }}>会计成本</span></Tooltip>, dataIndex: 'accounting_cost', width: colW.accounting_cost, onHeaderCell: mkResize('accounting_cost'), render: (v: number | null, r: PricingSku) => <EditableNumberCell value={v} onSave={(nv) => saveField(r.id, 'accounting_cost', nv)} /> },
          { title: <Tooltip title="所有实物成本合计，是各档价格的计算基数 ｜ 点格子可直接改"><span style={{ borderBottom: '1px dotted #bbb', cursor: 'help' }}>物理成本</span></Tooltip>, dataIndex: 'physical_cost', width: colW.physical_cost, onHeaderCell: mkResize('physical_cost'), render: (v: number | null, r: PricingSku) => <EditableNumberCell value={v} onSave={(nv) => saveField(r.id, 'physical_cost', nv)} /> },
          {
            title: '操作', width: colW.actions, fixed: 'right' as const,
            render: (_: unknown, row: PricingSku) => (
              <Button size="small" icon={<EditOutlined />} onClick={() => openEdit(row)}>编辑</Button>
            ),
          },
        ] as any}
      />
      )}

      {/* 新增弹窗 */}
      <Modal
        title="新增定价 SKU"
        open={createOpen}
        onCancel={() => { setCreateOpen(false); form.resetFields(); }}
        onOk={() => form.submit()}
        confirmLoading={createMut.isPending}
        width={720}
        destroyOnClose
      >
        <Form form={form} layout="vertical" onFinish={(v) => createMut.mutate(v)}>
          <SkuFormFields />
        </Form>
      </Modal>

      {/* 编辑弹窗 */}
      <Modal
        title={`编辑定价 — ${editRow?.sku_code}`}
        open={!!editRow}
        onCancel={() => { setEditRow(null); form.resetFields(); }}
        onOk={() => form.submit()}
        confirmLoading={updateMut.isPending}
        width={720}
        destroyOnClose
      >
        <Form
          form={form}
          layout="vertical"
          onFinish={(v) => editRow && updateMut.mutate({ id: editRow.id, patch: v })}
        >
          <SkuFormFields />
        </Form>
      </Modal>
    </Space>
  );
}
