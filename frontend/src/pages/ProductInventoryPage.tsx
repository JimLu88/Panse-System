import { useState } from 'react';
import ProducibilityPage from './ProducibilityPage';
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
  ForecastConfig,
  ProductInventoryRow,
  addProductInventoryRow,
  fetchForecastConfig,
  listProductInventory,
  listProducts,
  refreshProductInventoryStats,
  saveForecastConfig,
  syncProductInventoryParams,
  updateProductInventory,
} from '../api/client';
import FullColumnView from '../components/FullColumnView';

// 「销量公式」按钮 + 配置弹窗 + 大促备货提示 (用户拍板: 默认加权公式, 大促时段可增减)
function FormulaButton() {
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  const { data: cfg } = useQuery({ queryKey: ['forecast-config'], queryFn: fetchForecastConfig });
  const [draft, setDraft] = useState<ForecastConfig | null>(null);
  const saveMut = useMutation({
    mutationFn: (c: Partial<ForecastConfig>) => saveForecastConfig(c),
    onSuccess: () => {
      message.success('公式已保存, 点「刷新统计」后按新公式计算');
      qc.invalidateQueries({ queryKey: ['forecast-config'] });
      setOpen(false);
    },
    onError: (e: any) => message.error(e?.response?.data?.detail ?? '保存失败'),
  });
  const d = draft ?? cfg ?? null;
  const promoNotice = [
    ...(cfg?.promo?.active ?? []).map((p) => `${p.name} 进行中 (${p.start}~${p.end})`),
    ...(cfg?.promo?.upcoming ?? []).map((p) => `${p.name} 还有 ${p.days_to_start} 天开始, 已进入备货窗口`),
  ];
  return (
    <>
      {promoNotice.length > 0 && (
        <Tag color="orange">⚡ {promoNotice.join('; ')}</Tag>
      )}
      <Button size="small" onClick={() => { setDraft(cfg ? { ...cfg, promo_periods: [...cfg.promo_periods] } : null); setOpen(true); }}>
        备货规则 ⚙
      </Button>
      <Modal
        open={open} title="统一备货规则 / 大促时段"
        onCancel={() => setOpen(false)}
        onOk={() => d && saveMut.mutate({
          promo_periods: d.promo_periods,
          enable_semi_finished: d.enable_semi_finished,
        })}
        confirmLoading={saveMut.isPending}
      >
        {d && (
          <Space direction="vertical" style={{ width: '100%' }}>
            <Alert type="info" showIcon message="统一口径：7/15/30/60/90天近端加权；大促去峰；春节单列；最终建议=目标库存−现货−自由在产。" />
            <Typography.Text strong>大促时段 (月-日, 每年重复, 可增减):</Typography.Text>
            {d.promo_periods.map((p, i) => (
              <Space key={i}>
                <Input style={{ width: 110 }} value={p.name} placeholder="名称"
                  onChange={(e) => { const ps = [...d.promo_periods]; ps[i] = { ...p, name: e.target.value }; setDraft({ ...d, promo_periods: ps }); }} />
                <Input style={{ width: 80 }} value={p.start} placeholder="05-13"
                  onChange={(e) => { const ps = [...d.promo_periods]; ps[i] = { ...p, start: e.target.value }; setDraft({ ...d, promo_periods: ps }); }} />
                <span>~</span>
                <Input style={{ width: 80 }} value={p.end} placeholder="06-18"
                  onChange={(e) => { const ps = [...d.promo_periods]; ps[i] = { ...p, end: e.target.value }; setDraft({ ...d, promo_periods: ps }); }} />
                <Button size="small" danger onClick={() => setDraft({ ...d, promo_periods: d.promo_periods.filter((_, j) => j !== i) })}>删</Button>
              </Space>
            ))}
            <Button size="small" icon={<PlusOutlined />}
              onClick={() => setDraft({ ...d, promo_periods: [...d.promo_periods, { name: '新时段', start: '01-01', end: '01-07' }] })}>
              加一个时段
            </Button>
            <div style={{ borderTop: '1px solid #f0f0f0', paddingTop: 8, marginTop: 4 }}>
              <Space>
                <Switch checked={!!d.enable_semi_finished}
                  onChange={(v) => setDraft({ ...d, enable_semi_finished: v })} />
                <span>启用「半成品 / 白坯」备货 (默认关)</span>
              </Space>
              <Typography.Paragraph type="secondary" style={{ fontSize: 12, marginTop: 4, marginBottom: 0 }}>
                关闭 = 统一按标品备货(现状)。打开后: 可给产品打标「可做白坯」、按共享白坯池化出「半成品备货计划」。
                这是「以后量大 + 与工厂协商好」再开的能力(现在囤白坯工厂会有意见), 先建好、默认不启用。
              </Typography.Paragraph>
            </div>
          </Space>
        )}
      </Modal>
    </>
  );
}

const STATUS_CONFIG: Record<string, { color: string; label: string }> = {
  ok:       { color: 'success', label: '正常' },
  warning:  { color: 'warning', label: '即将不足' },
  danger:   { color: 'error',   label: '低于预警线' },
  critical: { color: 'error',   label: '库存告急' },
  excess:   { color: 'default', label: '滞销/超量' },
  mto:      { color: 'processing', label: '按需生产' },   // 定制/长尾: 接单再产, 不备成品
};

export default function ProductInventoryPage() {
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  const [form] = Form.useForm();
  const [productSearch, setProductSearch] = useState('');
  const [editId, setEditId] = useState<number | null>(null);
  // 一键同步: 当前编辑行所属产品 + 是否把参数同步到该产品全部 SKU
  const [editProductCode, setEditProductCode] = useState<string | null>(null);
  const [syncAllSkus, setSyncAllSkus] = useState(false);
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
      title: (
        <Tooltip title="与销售→备货建议、月底飞书共用同一备货引擎。大件按单生产；小/中件达到90天热销门槛后才备成品。">
          备货策略
        </Tooltip>
      ),
      dataIndex: 'restock_policy',
      width: 118,
      render: (policy: string | null, r: ProductInventoryRow) => {
        if (policy === '小件热销备货') return <Tag color="green">小件热销</Tag>;
        if (policy === '中件少量备货') return <Tag color="blue">中件少量</Tag>;
        if (policy === '大件按单生产') return <Tag color="default">大件按单</Tag>;
        return <Tag color="default">{r.qualified_hot ? '备货' : '按需'}</Tag>;
      },
    },
    {
      title: (
        <Tooltip title="现货 = 手动台账的快照(上次导入/盘点填的值), 不会自动倒扣历史订单。从今起发货会自动减、备货工厂单到货自动加(R3)。若这些其实早卖光了, 盘点一次改成真实数(纯接单生产不囤成品就填 0), 之后系统自动维护。可用 = 现货 − 已锁定。">现货 / 可用</Tooltip>
      ),
      width: 118,
      render: (_: any, r: ProductInventoryRow) => (
        <Space direction="vertical" size={0}>
          <span>现货 {Number(r.physical_qty).toFixed(0)}</span>
          <Typography.Text type={r.available_qty < 0 ? 'danger' : 'secondary'} style={{ fontSize: 12 }}>
            可用 {r.available_qty.toFixed(0)}
          </Typography.Text>
          {(r.in_production_free ?? 0) > 0 && (
            <Tooltip title="备货单在产(不挂客户), 到货进可售库存 → 推荐备货已扣掉。">
              <Typography.Text style={{ fontSize: 12, color: '#1677ff' }}>
                备货在产 {Number(r.in_production_free).toFixed(0)}
              </Typography.Text>
            </Tooltip>
          )}
          {(r.in_production_allocated ?? 0) > 0 && (
            <Tooltip title="已卖给下单客户的量, 到货即发走, 不算未来可用库存(不抵推荐备货)。">
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                客户单在产 {Number(r.in_production_allocated).toFixed(0)}
              </Typography.Text>
            </Tooltip>
          )}
        </Space>
      ),
    },
    {
      title: (
        <Tooltip title="该尺寸自己近期真实订单的日均发货量(不含补单)。按 sku 名里的尺寸口令(如 1.4米/1.6米)匹配对应订单, 每个尺寸各算各的; sku 名里抽不到尺寸时才退回按整个产品算。">日均销量</Tooltip>
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
        <Tooltip title="唯一备货引擎给出的目标成品库存；小件最多6件、中件最多2件、大件为0。">目标库存</Tooltip>
      ),
      width: 90,
      render: (_: any, r: ProductInventoryRow) => (
        <span>{Number(r.target_stock ?? r.reorder_point_computed ?? 0).toFixed(0)}</span>
      ),
    },
    {
      title: (
        <Tooltip title="统一清洗后的近90天销量达到8件，才进入成品热销备货；未达标和大件继续按单生产。">热销达标</Tooltip>
      ),
      width: 90,
      render: (_: any, r: ProductInventoryRow) => (
        r.qualified_hot ? <Tag color="green">已达标</Tag> : <Tag>按需</Tag>
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
        <Tooltip title="统一公式：目标库存 − 当前现货 − 自由在产。同产品多SKU按清洗日均分摊，各SKU合计严格等于销售→备货建议里的产品数量。">推荐备货</Tooltip>
      ),
      dataIndex: 'auto_reorder_qty',
      width: 96,
      render: (v: number, r: ProductInventoryRow) => {
        const productTotal = Number(r.product_restock_total ?? v ?? 0);
        const totalHint = productTotal !== Number(v ?? 0) ? (
          <Tooltip title="订单备货页按产品展示；本页按SKU分摊。">
            <div style={{ fontSize: 11, color: '#1677ff' }}>产品合计 {productTotal.toFixed(0)}</div>
          </Tooltip>
        ) : null;
        return v > 0
          ? <Space direction="vertical" size={0}><Tag color="blue">{v.toFixed(0)}</Tag>{totalHint}</Space>
          : <Space direction="vertical" size={0}><Typography.Text type="secondary">充足</Typography.Text>{totalHint}</Space>;
      },
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
          setEditProductCode(r.product_code || null);
          setSyncAllSkus(false);
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

  // 「需关注」不含 正常(ok) 和 按需生产(mto: 定制/长尾, 缺货是常态, 不报警)
  const _calm = (s: string) => s === 'ok' || s === 'mto';
  const warningCount = data?.filter(r => !_calm(r.warning_status)).length ?? 0;

  // 三类: ① 需预警(全显示) ② 已建库存但不预警/按需(折叠) ③ 还没建库存行的产品(折叠)
  const rows = data ?? [];
  const alertRows = rows.filter((r) => !_calm(r.warning_status));
  const normalRows = rows.filter((r) => r.has_inventory !== false && _calm(r.warning_status));
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
          <FormulaButton />
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

      <Alert
        type="info"
        showIcon
        message="库存页、销售备货页、月底飞书已共用唯一备货计划"
        description={
          <>
            唯一公式：<b>目标库存 − 当前现货 − 自由在产</b>。订单先统一清洗，再按7/15/30/60/90天加权预测；
            618、双11、双12去峰，春节单列。近90天清洗销量达到8件后，小件覆盖7天且最多6件，
            中件覆盖5天且最多2件；餐边柜等大件和定制单继续按单生产，不压成品库存。
            <br />
            同产品多SKU在本页按日均分摊，SKU合计与「销售 → 备货建议」产品数量完全一致。
          </>
        }
      />


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
          onFinish={async (v) => {
            if (!editId) return;
            // 勾选同步时: 参数项批量铺到本产品全部 SKU, 数量仍只改当前行
            if (syncAllSkus && editProductCode) {
              try {
                const r = await syncProductInventoryParams(editProductCode, {
                  safety_stock: v.safety_stock,
                  lead_time_days: v.lead_time_days,
                  slow_moving_days: v.slow_moving_days,
                  reorder_point: v.reorder_point,
                });
                message.success(r.message);
              } catch (e: any) {
                message.error(e?.response?.data?.detail ?? '批量同步失败');
                return;
              }
            }
            editMut.mutate({
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
            });
          }}
        >
          <Form.Item label={`参数同步到本产品全部 SKU${editProductCode ? ` (${editProductCode})` : ''}`}
                     tooltip="只同步 安全库存/提前期/预警线/滞销阈值 四个参数；现货/锁定数量各 SKU 不同，不会被同步">
            <Switch checked={syncAllSkus} onChange={setSyncAllSkus}
                    checkedChildren="同步全部 SKU" unCheckedChildren="仅这一行" />
          </Form.Item>
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
      {/* 可生产数计算 — 从独立页合并进来 (用户需求 2026-06-22) */}
      <ProducibilityPage />
    </Space>
  );
}
