import { useEffect, useState } from 'react';
import type { Dispatch, SetStateAction } from 'react';
import {
  Alert,
  AutoComplete,
  Button,
  Card,
  Descriptions,
  Form,
  Input,
  InputNumber,
  Modal,
  Select,
  Space,
  Spin,
  Table,
  Tabs,
  Tag,
  Typography,
  Upload,
  message,
} from 'antd';
import { InboxOutlined, RobotOutlined, SettingOutlined } from '@ant-design/icons';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useNavigate, useSearchParams } from 'react-router-dom';
import {
  AiQuoteResult,
  BoardQuoteResult,
  CompetitorRow,
  QuoteBoard,
  QuoteConfig,
  aiCustomizationQuote,
  boardQuote,
  competitorsTop,
  addCompetitor,
  refreshCompetitor,
  extractBoards,
  fuzzyMatch,
  getQuoteConfig,
  updateQuoteConfig,
} from '../api/client';
import { CustomizationDialog } from '../components/CustomizationDialog';
import { FirstVisitTip } from '../components/FirstVisitTip';

const { Dragger } = Upload;

function AiQuoteTab() {
  const [result, setResult] = useState<AiQuoteResult | null>(null);

  const quoteMut = useMutation({
    mutationFn: (file: File) => aiCustomizationQuote(file),
    onSuccess: (res) => {
      setResult(res);
      if (res.error && !res.ai_used) {
        message.warning('AI 未配置，已返回基础估价');
      }
    },
    onError: (e: any) => message.error(e?.response?.data?.detail ?? '报价失败'),
  });

  return (
    <Space direction="vertical" style={{ width: '100%' }} size="middle">
      <Alert
        type="info"
        showIcon
        icon={<RobotOutlined />}
        message="AI 截图报价"
        description="上传客户发来的定制截图（含尺寸/材质要求）→ AI 自动识别要求并估算价格。AI 未配置时回退手动向导。"
      />
      <Card size="small">
        <Dragger
          accept="image/*"
          showUploadList={false}
          beforeUpload={(f) => { quoteMut.mutate(f); return false; }}
          disabled={quoteMut.isPending}
          multiple={false}
        >
          <p className="ant-upload-drag-icon"><InboxOutlined /></p>
          <p className="ant-upload-text">
            {quoteMut.isPending ? <Spin tip="AI 分析中..." /> : '点击或拖入定制截图'}
          </p>
          <p className="ant-upload-hint">支持 JPG / PNG / WEBP</p>
        </Dragger>
      </Card>

      {result && (
        <Card
          size="small"
          title={
            <Space>
              <span>报价结果</span>
              {result.ai_used && <Tag color="blue">AI 分析</Tag>}
              {result.model && <Tag color="default" style={{ fontSize: 11 }}>{result.model}</Tag>}
            </Space>
          }
        >
          {result.error && (
            <Alert type="warning" message="AI 提示" description={result.error} style={{ marginBottom: 8 }} />
          )}
          <Descriptions size="small" column={2} bordered style={{ marginBottom: 12 }}>
            <Descriptions.Item label="匹配产品">{result.base_product || '-'}</Descriptions.Item>
            <Descriptions.Item label="基础 SKU">{result.base_sku || '-'}</Descriptions.Item>
            <Descriptions.Item label="尺寸分类">{result.base_size || '-'}</Descriptions.Item>
            <Descriptions.Item label="估算总价">
              {result.est_price != null
                ? <Tag color="green" style={{ fontSize: 14, fontWeight: 600 }}>¥{result.est_price.toLocaleString()}</Tag>
                : <Tag color="default">暂无估价</Tag>}
            </Descriptions.Item>
          </Descriptions>

          {result.changes.length > 0 && (
            <div style={{ marginBottom: 12 }}>
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>识别到的变更：</Typography.Text>
              <div style={{ marginTop: 4 }}>
                {result.changes.map((c, i) => <Tag key={i} color="orange">{c}</Tag>)}
              </div>
            </div>
          )}

          {result.breakdown.length > 0 && (
            <Table
              size="small"
              pagination={false}
              rowKey="label"
              dataSource={result.breakdown}
              columns={[
                { title: '项目', dataIndex: 'label' },
                {
                  title: '金额',
                  dataIndex: 'amount',
                  align: 'right' as const,
                  render: (v: number) => (
                    <span style={{ color: v >= 0 ? '#3f8600' : '#cf1322', fontWeight: 500 }}>
                      {v >= 0 ? '+' : ''}¥{v.toLocaleString()}
                    </span>
                  ),
                },
                { title: '说明', dataIndex: 'note', ellipsis: true },
              ]}
            />
          )}
        </Card>
      )}
    </Space>
  );
}

function ManualQuoteTab() {
  const nav = useNavigate();
  const [skuCode, setSkuCode] = useState('');
  const [orderNo, setOrderNo] = useState('');
  const [search, setSearch] = useState('');
  const [open, setOpen] = useState(false);

  const { data: candidates } = useQuery({
    queryKey: ['match', 'sku', search],
    queryFn: () => fuzzyMatch(search, 'sku', 10),
    enabled: search.length > 0,
  });

  return (
    <Space direction="vertical" style={{ width: '100%' }} size="middle">
      <FirstVisitTip
        storageKey="customization"
        title="如何用"
        description={
          <ol style={{ marginBottom: 0 }}>
            <li>选一个已有 SKU 作为「基础」(普通链接的标准型号)</li>
            <li>填客户要求的目标尺寸 (长/宽/高 mm) — 只填要变的</li>
            <li>预览 BOM 哪些料会随尺寸变, 用户确认</li>
            <li>系统生成 改NN 后缀的新 SKU + 克隆 BOM, 自动入库</li>
          </ol>
        }
      />

      <Card>
        <Form layout="vertical">
          <Form.Item label="基础 SKU 编码 (搜索)" required>
            <AutoComplete
              value={skuCode}
              onChange={setSkuCode}
              onSearch={setSearch}
              options={(candidates ?? []).map((c) => ({
                value: c.code,
                label: `${c.code}  ${c.name}`,
              }))}
              placeholder="按编码或名称搜索, 如 榉木无边床"
              style={{ width: 480 }}
            />
          </Form.Item>
          <Form.Item label="关联订单号 (可选)">
            <Input
              value={orderNo}
              onChange={(e) => setOrderNo(e.target.value)}
              placeholder="如有客户订单, 把订单号填进来留痕"
              style={{ width: 320 }}
            />
          </Form.Item>
          <Button
            type="primary"
            disabled={!skuCode}
            onClick={() => setOpen(true)}
          >
            开始定制
          </Button>
        </Form>
      </Card>

      <CustomizationDialog
        open={open}
        baseSkuCode={skuCode}
        orderNo={orderNo || undefined}
        onCancel={() => setOpen(false)}
        onConfirmed={() => {
          setOpen(false);
          nav('/products');
        }}
      />
    </Space>
  );
}

function QuoteSettingsTab() {
  const qc = useQueryClient();
  const { data: cfg, isLoading } = useQuery({ queryKey: ['quote-config'], queryFn: getQuoteConfig });
  const [draft, setDraft] = useState<QuoteConfig | null>(null);
  const c = draft ?? cfg ?? null;

  const saveMut = useMutation({
    mutationFn: (patch: Partial<QuoteConfig>) => updateQuoteConfig(patch),
    onSuccess: (res) => {
      message.success('参数已保存');
      qc.setQueryData(['quote-config'], res);
      setDraft(null);
    },
    onError: (e: any) => message.error(e?.response?.data?.detail ?? '保存失败'),
  });

  if (isLoading || !c) return <Spin />;

  const upd = (patch: Partial<QuoteConfig>) => setDraft({ ...c, ...patch });
  const updLabor = (type: string, idx: number, v: number) => {
    const labor = { ...c.labor, [type]: [...c.labor[type]] };
    labor[type][idx] = v;
    upd({ labor });
  };
  const updRule = (type: string, idx: number, v: number) => {
    const size_rules = { ...c.size_rules, [type]: [...c.size_rules[type]] };
    size_rules[type][idx] = v;
    upd({ size_rules });
  };

  const laborRows = Object.keys(c.labor).map((t) => ({
    key: t, type: t,
    small: c.labor[t][0], mid: c.labor[t][1], big: c.labor[t][2],
    ruleBig: c.size_rules[t]?.[0] ?? 0, ruleMid: c.size_rules[t]?.[1] ?? 0,
  }));

  const numCell = (val: number, onCh: (v: number) => void) => (
    <InputNumber size="small" value={val} min={0} style={{ width: 80 }}
      onChange={(v) => onCh(Number(v ?? 0))} />
  );

  return (
    <Space direction="vertical" style={{ width: '100%' }} size="middle">
      <Alert type="info" showIcon message="这里改的是全定制报价用的参数, 保存后立即生效。人工费按「品类 × 小/中/大」, 大小由长度阈值判定。" />
      <Card size="small" title="利润系数 / 投影对照">
        <Space wrap size="large">
          <span>工厂利润系数 {numCell(c.factory_profit_rate, (v) => upd({ factory_profit_rate: v }))}</span>
          <span>畔色利润系数 {numCell(c.panse_profit_rate, (v) => upd({ panse_profit_rate: v }))}</span>
          <span>保守系数(宁高不低) {numCell(c.safety_rate, (v) => upd({ safety_rate: v }))}</span>
          <span>竞品通用券率 {numCell(c.competitor_coupon_rate, (v) => upd({ competitor_coupon_rate: v }))}</span>
          <span>投影口径
            <Select size="small" style={{ width: 110, marginLeft: 6 }} value={c.projection_type}
              onChange={(v) => upd({ projection_type: v })}
              options={[{ value: 'front', label: '正面(宽×高)' }, { value: 'top', label: '俯视(宽×深)' }]} />
          </span>
          <span>投影系数(元/㎡) {numCell(c.projection_rate, (v) => upd({ projection_rate: v }))}</span>
        </Space>
        <div style={{ marginTop: 12 }}>
          打包费 小 {numCell(c.packing[0], (v) => upd({ packing: [v, c.packing[1], c.packing[2]] }))}
          {' '}中 {numCell(c.packing[1], (v) => upd({ packing: [c.packing[0], v, c.packing[2]] }))}
          {' '}大 {numCell(c.packing[2], (v) => upd({ packing: [c.packing[0], c.packing[1], v] }))}
        </div>
      </Card>

      <Card size="small" title="人工费表 + 大小判定 (按品类)">
        <Table
          size="small" pagination={false} dataSource={laborRows} scroll={{ y: 360 }}
          columns={[
            { title: '品类', dataIndex: 'type', width: 90, fixed: 'left' as const },
            { title: '小型', width: 95, render: (_: any, r: any) => numCell(r.small, (v) => updLabor(r.type, 0, v)) },
            { title: '中型', width: 95, render: (_: any, r: any) => numCell(r.mid, (v) => updLabor(r.type, 1, v)) },
            { title: '大型', width: 95, render: (_: any, r: any) => numCell(r.big, (v) => updLabor(r.type, 2, v)) },
            { title: '大型阈值(m)', width: 110, render: (_: any, r: any) => numCell(r.ruleBig, (v) => updRule(r.type, 0, v)) },
            { title: '中型阈值(m)', width: 110, render: (_: any, r: any) => numCell(r.ruleMid, (v) => updRule(r.type, 1, v)) },
          ] as any}
        />
        <div style={{ marginTop: 6, color: '#999', fontSize: 12 }}>
          长度 ≥ 大型阈值 → 大型; ≥ 中型阈值 → 中型; 否则小型。
        </div>
      </Card>

      <Space>
        <Button type="primary" disabled={!draft} loading={saveMut.isPending}
          onClick={() => draft && saveMut.mutate(draft)}>保存</Button>
        <Button disabled={!draft} onClick={() => setDraft(null)}>撤销改动</Button>
      </Space>
    </Space>
  );
}

function FullCustomTab() {
  const { data: cfg } = useQuery({ queryKey: ['quote-config'], queryFn: getQuoteConfig });
  const [productType, setProductType] = useState('餐边柜');
  const [lengthM, setLengthM] = useState(2.1);
  const [widthM, setWidthM] = useState<number | undefined>();
  const [heightM, setHeightM] = useState<number | undefined>();
  const [factoryQuote, setFactoryQuote] = useState<number | undefined>();
  const [boards, setBoards] = useState<QuoteBoard[]>([
    { part: '顶板', material: '黑胡桃木-2.2cm', length_cm: 210, width_cm: 45, qty: 1 },
  ]);
  const [result, setResult] = useState<BoardQuoteResult | null>(null);
  const [competitors, setCompetitors] = useState<CompetitorRow[]>([]);

  const typeOpts = Object.keys(cfg?.labor ?? {}).map((t) => ({ value: t, label: t }));
  const matOpts = Object.keys(cfg?.prices ?? {}).map((m) => ({ value: m, label: m }));

  const quoteMut = useMutation({
    mutationFn: () => boardQuote({
      product_type: productType, length_m: lengthM,
      overall_width_m: widthM, overall_height_m: heightM,
      boards, factory_quote: factoryQuote,
    }),
    onSuccess: async (res) => {
      setResult(res);
      const wood = boards[0]?.material?.split('-')[0] ?? '';
      try {
        setCompetitors(await competitorsTop(`${wood}${productType} ${lengthM}米`, 10));
      } catch { setCompetitors([]); }
    },
    onError: (e: any) => message.error(e?.response?.data?.detail ?? '算价失败'),
  });

  const extractMut = useMutation({
    mutationFn: (f: File) => extractBoards(f),
    onSuccess: (r) => {
      if (r.product_type) setProductType(r.product_type);
      if (r.overall?.length_mm) setLengthM(r.overall.length_mm / 1000);
      if (r.overall?.width_mm) setWidthM(r.overall.width_mm / 1000);
      if (r.overall?.height_mm) setHeightM(r.overall.height_mm / 1000);
      if (r.boards?.length) setBoards(r.boards);
      message[r.ai_used ? 'success' : 'warning'](
        r.ai_used ? `AI 识别 ${r.boards.length} 块板, 请核对` : `AI 未配置(${r.error ?? ''}), 请手动录入`,
      );
    },
  });

  const setB = (i: number, patch: Partial<QuoteBoard>) =>
    setBoards((p) => p.map((b, j) => (j === i ? { ...b, ...patch } : b)));
  const addRow = () => setBoards((p) => [...p, { part: '', material: '黑胡桃木-2.2cm', length_cm: 0, width_cm: 0, qty: 1 }]);
  const delRow = (i: number) => setBoards((p) => p.filter((_, j) => j !== i));

  const money = (v: number | null | undefined) => (v == null ? '—' : `¥${v.toFixed(0)}`);

  return (
    <Space direction="vertical" style={{ width: '100%' }} size="middle">
      <Alert type="info" showIcon message="逐块板按面积算材料 + 品类×大小查人工 → (材料+人工)×工厂利润 = 工厂木作价, 与工厂报价比对; 旁边给投影面积对照防漏算。单价/人工/系数都在『报价参数设置』里调。" />
      <Space wrap>
        <Upload accept="image/*" showUploadList={false}
          beforeUpload={(f) => { extractMut.mutate(f as File); return false; }}>
          <Button icon={<InboxOutlined />} loading={extractMut.isPending}>上传设计图 AI 抽板</Button>
        </Upload>
        <span>品类 <Select size="small" style={{ width: 110 }} value={productType} onChange={setProductType}
          options={typeOpts} showSearch /></span>
        <span>长度(m) <InputNumber size="small" value={lengthM} min={0} step={0.1} style={{ width: 80 }}
          onChange={(v) => setLengthM(Number(v ?? 0))} /></span>
        <span>整宽(m) <InputNumber size="small" value={widthM} min={0} step={0.01} style={{ width: 80 }}
          onChange={(v) => setWidthM(v ? Number(v) : undefined)} /></span>
        <span>整高(m) <InputNumber size="small" value={heightM} min={0} step={0.01} style={{ width: 80 }}
          onChange={(v) => setHeightM(v ? Number(v) : undefined)} /></span>
        <span>工厂报价 <InputNumber size="small" value={factoryQuote} min={0} style={{ width: 90 }}
          onChange={(v) => setFactoryQuote(v ? Number(v) : undefined)} /></span>
      </Space>

      <Table<QuoteBoard>
        size="small" pagination={false} rowKey={(_, i) => String(i)} dataSource={boards}
        columns={[
          { title: '部位', dataIndex: 'part', render: (v: string, _r: QuoteBoard, i: number) => (
            <Input size="small" value={v} onChange={(e) => setB(i, { part: e.target.value })} />) },
          { title: '材料', dataIndex: 'material', width: 170, render: (v: string, _r: QuoteBoard, i: number) => (
            <Select size="small" style={{ width: 160 }} value={v} options={matOpts} showSearch
              onChange={(val) => setB(i, { material: val })} />) },
          { title: '长cm', dataIndex: 'length_cm', width: 80, render: (v: number, _r: QuoteBoard, i: number) => (
            <InputNumber size="small" value={v} min={0} style={{ width: 70 }}
              onChange={(val) => setB(i, { length_cm: Number(val ?? 0) })} />) },
          { title: '宽cm', dataIndex: 'width_cm', width: 80, render: (v: number, _r: QuoteBoard, i: number) => (
            <InputNumber size="small" value={v} min={0} style={{ width: 70 }}
              onChange={(val) => setB(i, { width_cm: Number(val ?? 0) })} />) },
          { title: '数量', dataIndex: 'qty', width: 65, render: (v: number, _r: QuoteBoard, i: number) => (
            <InputNumber size="small" value={v} min={0} style={{ width: 55 }}
              onChange={(val) => setB(i, { qty: Number(val ?? 1) })} />) },
          { title: '轨道', dataIndex: 'is_drawer_rail', width: 50, render: (v: boolean, _r: QuoteBoard, i: number) => (
            <input type="checkbox" checked={!!v} onChange={(e) => setB(i, { is_drawer_rail: e.target.checked, unit: e.target.checked ? '付' : '平方米' })} />) },
          { title: '', width: 40, render: (_: unknown, _r: QuoteBoard, i: number) => (
            <Button size="small" danger type="text" onClick={() => delRow(i)}>×</Button>) },
        ] as any}
      />
      <Space>
        <Button onClick={addRow}>+ 加一块板</Button>
        <Button type="primary" loading={quoteMut.isPending} onClick={() => quoteMut.mutate()}>计算报价</Button>
      </Space>

      {result && (
        <Space align="start" wrap size="large">
          <Card size="small" title="工厂木作对比" style={{ minWidth: 280 }}>
            <Descriptions size="small" column={1}>
              <Descriptions.Item label="材料(逐板)">{money(result.wood_cost)}</Descriptions.Item>
              <Descriptions.Item label={`人工(${productType}·${result.size_class}型)`}>{money(result.labor_fee)}</Descriptions.Item>
              <Descriptions.Item label="厂内总成本">{money(result.factory_in_cost)}</Descriptions.Item>
              <Descriptions.Item label="工厂利润(×系数)">{money(result.factory_profit)}</Descriptions.Item>
              <Descriptions.Item label="抽屉轨道">{money(result.drawer_rail_total)}</Descriptions.Item>
              <Descriptions.Item label="我算工厂价(原始)">{money(result.factory_quote_compare)}</Descriptions.Item>
              <Descriptions.Item label={<b>保守价(×{result.safety_rate})</b>}>
                <b style={{ color: '#d46b08' }}>{money(result.factory_quote_conservative)}</b>
              </Descriptions.Item>
              {result.factory_quote != null && (
                <Descriptions.Item label="工厂实报">
                  {money(result.factory_quote)}
                  <Tag color={(result.factory_diff ?? 0) >= 0 ? 'green' : 'red'}>
                    {(result.factory_diff ?? 0) >= 0 ? '我方低' : '我方高'} {Math.abs(result.factory_diff ?? 0).toFixed(0)}
                  </Tag>
                </Descriptions.Item>
              )}
            </Descriptions>
            <div style={{ color: '#999', fontSize: 12, marginTop: 4 }}>保守价宁高不低, 防低估亏本(系数在设置里调)</div>
          </Card>
          <Card size="small" title="投影面积对照(防漏算)" style={{ minWidth: 240 }}>
            <Descriptions size="small" column={1}>
              <Descriptions.Item label="正面投影">{result.projection_area_m2 ?? '—'} ㎡</Descriptions.Item>
              <Descriptions.Item label="估算价">{money(result.projection_estimate)}</Descriptions.Item>
              <Descriptions.Item label="对账面价">{money(result.factory_wood_total)}</Descriptions.Item>
            </Descriptions>
            <div style={{ color: '#999', fontSize: 12, marginTop: 6 }}>两者差太多 → 可能漏板/算错</div>
          </Card>
          <Card size="small" title="畔色最终售价" style={{ minWidth: 220 }}>
            <Descriptions size="small" column={1}>
              <Descriptions.Item label="配件">{money(result.accessory_total)}</Descriptions.Item>
              <Descriptions.Item label="打包/运费/安装">{money(result.packing_fee + result.freight + result.install_fee)}</Descriptions.Item>
              <Descriptions.Item label="畔色成本">{money(result.panse_cost)}</Descriptions.Item>
              <Descriptions.Item label={<b>最终报价</b>}>
                <b style={{ color: '#1677ff', fontSize: 16 }}>{money(result.final_quote)}</b>
              </Descriptions.Item>
            </Descriptions>
          </Card>
        </Space>
      )}

      {result && competitors.length > 0 && (
        <Card size="small" title={`竞品参考 Top-10 (按匹配度; 券后价已减通用平台券 ${((cfg?.competitor_coupon_rate ?? 0.08) * 100).toFixed(0)}%)`}>
          <Alert type="warning" showIcon style={{ marginBottom: 8 }}
            message="淘宝反爬, 最新价为尽力抓取; 抓不到(blocked)时请点链接核对或手动更新。价格均为叠券前, 券后价已注明减额。" />
          <Table
            size="small" pagination={false} rowKey="id" dataSource={competitors}
            columns={competitorColumns(setCompetitors)}
          />
        </Card>
      )}
    </Space>
  );
}

function competitorColumns(setRows: Dispatch<SetStateAction<CompetitorRow[]>>) {
  return [
    { title: '匹配', dataIndex: 'confidence', width: 55,
      render: (v: number) => <Tag color={v >= 0.5 ? 'green' : 'orange'}>{(v * 100).toFixed(0)}%</Tag> },
    { title: '店铺', dataIndex: 'store', width: 80, ellipsis: true },
    { title: 'SKU', dataIndex: 'sku_name', ellipsis: true },
    { title: '木材', dataIndex: 'wood', width: 70 },
    { title: '我表价', dataIndex: 'daily_price', width: 80, align: 'right' as const,
      render: (v: number | null) => (v == null ? '—' : `¥${v.toFixed(0)}`) },
    { title: '最新价', width: 110, align: 'right' as const,
      render: (_: unknown, r: CompetitorRow) => (
        <span>
          {r.latest_price != null ? `¥${r.latest_price.toFixed(0)}` : <span style={{ color: '#bbb' }}>—</span>}
          {r.fetch_status && r.fetch_status !== 'ok' && r.fetch_status !== 'manual' && (
            <Tag color="red" style={{ marginLeft: 4 }}>{r.fetch_status}</Tag>)}
          <Button size="small" type="link" onClick={async () => {
            try { const u = await refreshCompetitor(r.id);
              setRows((p) => p.map((x) => x.id === u.id ? u : x)); }
            catch { message.error('刷新失败'); }
          }}>刷新</Button>
        </span>) },
    { title: '券后价(减额)', width: 120, align: 'right' as const,
      render: (_: unknown, r: CompetitorRow) => (
        r.after_coupon == null ? '—' :
        <span>¥{r.after_coupon.toFixed(0)} <Tag color="blue">−{r.coupon_cut.toFixed(0)}</Tag></span>) },
    { title: '链接', width: 50,
      render: (_: unknown, r: CompetitorRow) => r.link
        ? <a href={r.link} target="_blank" rel="noreferrer">看</a> : '—' },
  ] as any;
}

function CompetitorTab() {
  const [q, setQ] = useState('');
  const [rows, setRows] = useState<CompetitorRow[]>([]);
  const [loading, setLoading] = useState(false);
  const [addOpen, setAddOpen] = useState(false);
  const [addForm] = Form.useForm();
  const search = async () => {
    setLoading(true);
    // 空搜索 → 显示最近录入的竞品(进页面/导入后直接可见); 有词 → 按匹配度 Top-N
    try { setRows(await competitorsTop(q.trim(), q.trim() ? 20 : 100)); }
    catch { message.error('查询失败'); }
    finally { setLoading(false); }
  };
  // 进页面先加载最近录入的竞品(不必先搜)
  useEffect(() => { search(); }, []);  // eslint-disable-line react-hooks/exhaustive-deps
  const submitAdd = async (v: any) => {
    try {
      await addCompetitor(v);
      message.success('已添加竞品');
      setAddOpen(false); addForm.resetFields();
      search();
    } catch { message.error('添加失败'); }
  };
  return (
    <Space direction="vertical" style={{ width: '100%' }} size="middle">
      <Alert type="info" showIcon
        message="竞品价库: 不搜索默认显示最近录入; 按 产品/SKU/木材 搜索 Top-N (匹配度排序)。可点「添加竞品」手动录入; 最新价由外部采集回灌或点刷新尽力抓; 价格均为叠券前, 券后价已注明减额。" />
      <Space style={{ width: '100%', justifyContent: 'space-between' }} wrap>
        <Space.Compact style={{ width: '100%', maxWidth: 480 }}>
          <Input placeholder="如: 黑胡桃 餐边柜 1.5米" value={q}
            onChange={(e) => setQ(e.target.value)} onPressEnter={search} allowClear />
          <Button type="primary" onClick={search}>搜索</Button>
        </Space.Compact>
        <Space>
          <Upload accept=".xlsx" showUploadList={false} beforeUpload={async (file) => {
            const fd = new FormData();
            fd.append('file', file);
            try {
              const { api } = await import('../api/client');
              const r = await api.post('/api/customization/competitors/import', fd, {
                headers: { 'Content-Type': 'multipart/form-data' },
              });
              message.success(`竞品价库导入完成: 新增 ${r.data.inserted}, 更新 ${r.data.updated}, 跳过 ${r.data.skipped}; 原文件已存档`);
              search();
            } catch (e: any) {
              message.error(e?.response?.data?.detail ?? '导入失败');
            }
            return false;
          }}>
            <Button>导入 xlsx</Button>
          </Upload>
          <Button onClick={() => setAddOpen(true)}>+ 添加竞品</Button>
        </Space>
      </Space>
      <Table size="small" loading={loading} pagination={false} rowKey="id" dataSource={rows}
        columns={competitorColumns(setRows)} />

      <Modal title="添加竞品价" open={addOpen} onOk={() => addForm.submit()}
        onCancel={() => setAddOpen(false)} okText="保存" confirmLoading={false} destroyOnClose>
        <Form form={addForm} layout="vertical" onFinish={submitAdd}>
          <Form.Item name="product" label="产品" rules={[{ required: true, message: '填产品名' }]}>
            <Input placeholder="如: 黑胡桃餐边柜" />
          </Form.Item>
          <Space style={{ width: '100%' }} size="middle">
            <Form.Item name="store" label="店铺"><Input placeholder="竞品店铺名" /></Form.Item>
            <Form.Item name="wood" label="木材"><Input placeholder="如: 黑胡桃" /></Form.Item>
          </Space>
          <Form.Item name="sku_name" label="SKU 名"><Input placeholder="如: 1.5米 黑胡桃" /></Form.Item>
          <Space style={{ width: '100%' }} size="middle">
            <Form.Item name="daily_price" label="我表价(叠券前)"><InputNumber min={0} addonAfter="元" style={{ width: 160 }} /></Form.Item>
            <Form.Item name="latest_price" label="最新价(叠券前)"><InputNumber min={0} addonAfter="元" style={{ width: 160 }} /></Form.Item>
          </Space>
          <Form.Item name="link" label="链接"><Input placeholder="竞品商品链接(可选)" /></Form.Item>
        </Form>
      </Modal>
    </Space>
  );
}

export default function CustomizationPage() {
  const [params, setParams] = useSearchParams();
  const activeKey = params.get('tab') || 'full';
  return (
    <Space direction="vertical" style={{ width: '100%' }} size="middle">
      <Typography.Title level={4} style={{ margin: 0 }}>
        尺寸微定制 <Tag color="orange">业务需求 §2</Tag>
      </Typography.Title>
      <Tabs
        activeKey={activeKey}
        onChange={(k) => setParams(k === 'full' ? {} : { tab: k }, { replace: true })}
        items={[
          { key: 'full', label: <><RobotOutlined /> 全定制报价</>, children: <FullCustomTab /> },
          { key: 'ai', label: 'AI 截图报价', children: <AiQuoteTab /> },
          { key: 'manual', label: '手动定制向导', children: <ManualQuoteTab /> },
          { key: 'competitor', label: '竞品价库', children: <CompetitorTab /> },
          { key: 'settings', label: <><SettingOutlined /> 报价参数设置</>, children: <QuoteSettingsTab /> },
        ]}
      />
    </Space>
  );
}
