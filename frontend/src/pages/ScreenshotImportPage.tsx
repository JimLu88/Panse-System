/**
 * 截图录单 (Phase 3, 业务需求 1/6).
 *
 * 两个 tab:
 *   - 千牛订单截图 → AI 解析 → 用户编辑 → commit 入 Orders
 *   - 进货单截图  → AI 解析 → 用户编辑 → commit 入 PartPurchase
 */
import { useState } from 'react';
import {
  Alert,
  Button,
  Card,
  Form,
  Input,
  InputNumber,
  Modal,
  Segmented,
  Space,
  Spin,
  Table,
  Tabs,
  Tag,
  Tooltip,
  Upload,
  message,
} from 'antd';
import { CloudUploadOutlined, FileImageOutlined, InboxOutlined, PrinterOutlined, SearchOutlined } from '@ant-design/icons';
import { useMutation } from '@tanstack/react-query';
import {
  FactoryReconParseResp,
  FactoryReconRowParsed,
  FactoryReconExcelResp,
  FactorySheet,
  ProductMatchResult,
  PurchaseLineParsed,
  PurchaseParseResp,
  PurchaseParsed,
  QianniuOrderParsed,
  QianniuParseResp,
  commitFactoryReconScreenshot,
  commitPurchaseScreenshot,
  commitQianniuOrders,
  matchProduct,
  parseFactoryReconScreenshot,
  parseFactoryReconExcel,
  parsePurchaseScreenshot,
  parseQianniuScreenshot,
  previewQianniuFactorySheet,
} from '../api/client';
import FactorySheetCard from '../components/FactorySheetCard';

const { Dragger } = Upload;

export default function ScreenshotImportPage() {
  return (
    <Tabs items={[
      { key: 'qianniu', label: '千牛订单截图', children: <QianniuTab /> },
      { key: 'purchase', label: '进货单截图', children: <PurchaseTab /> },
      { key: 'factory-recon', label: '工厂对账单截图', children: <FactoryReconTab /> },
    ]} />
  );
}

// ----------------------------- 千牛 ----------------------------- //

interface MatchState {
  loading: boolean;
  result?: ProductMatchResult;
}

function editCell(
  value: string | undefined,
  onChange: (v: string) => void,
  placeholder?: string,
) {
  return (
    <Input
      value={value ?? ''}
      size="small"
      style={{ minWidth: 80 }}
      placeholder={placeholder}
      onChange={(e) => onChange(e.target.value)}
    />
  );
}

function QianniuTab() {
  const [resp, setResp] = useState<QianniuParseResp | null>(null);
  const [orders, setOrders] = useState<QianniuOrderParsed[]>([]);
  const [matchStates, setMatchStates] = useState<Record<number, MatchState>>({});
  const [sheet, setSheet] = useState<FactorySheet | null>(null);

  const sheetMut = useMutation({
    mutationFn: (o: QianniuOrderParsed) => previewQianniuFactorySheet(o),
    onSuccess: (s) => setSheet(s),
    onError: (e: any) => message.error(e?.response?.data?.detail ?? '生成下单图失败'),
  });

  const parseMut = useMutation({
    mutationFn: (file: File) => parseQianniuScreenshot(file),
    onSuccess: (r) => {
      setResp(r);
      setOrders(r.orders);
      setMatchStates({});
      message.success(`AI 识别出 ${r.orders.length} 条订单`);
    },
    onError: (e: any) => message.error(e?.response?.data?.detail ?? 'OCR 失败'),
  });

  const commitMut = useMutation({
    mutationFn: () => commitQianniuOrders(orders),
    onSuccess: (r) => {
      message.success(`入库 ${r.inserted} 条; 跳过已存在 ${r.skipped_existing.length}`);
      setResp(null);
      setOrders([]);
      setMatchStates({});
    },
    onError: (e: any) => message.error(e?.response?.data?.detail ?? '入库失败'),
  });

  function setOrder(i: number, patch: Partial<QianniuOrderParsed>) {
    setOrders(prev => { const n = [...prev]; n[i] = { ...n[i], ...patch }; return n; });
  }

  async function runMatch(i: number) {
    const o = orders[i];
    const pname = o.product_name ?? '';
    if (!pname) return;
    setMatchStates(prev => ({ ...prev, [i]: { loading: true } }));
    try {
      const res = await matchProduct(pname, o.sku ?? undefined);
      setMatchStates(prev => ({ ...prev, [i]: { loading: false, result: res } }));
      if (res.product_code) {
        setOrder(i, {
          product_name: res.product_name ?? o.product_name,
          product_code: res.product_code,
          sku_code: res.sku_code ?? undefined,
          sku: res.sku ?? o.sku,
        });
      }
    } catch {
      setMatchStates(prev => ({ ...prev, [i]: { loading: false } }));
    }
  }

  const columns = [
    {
      title: '订单号', dataIndex: 'order_no', width: 160,
      render: (v: string, _r: QianniuOrderParsed, i: number) =>
        editCell(v, (val) => setOrder(i, { order_no: val })),
    },
    {
      title: '下单时间', dataIndex: 'order_date', width: 120,
      render: (v: string | undefined, _r: QianniuOrderParsed, i: number) =>
        editCell(v, (val) => setOrder(i, { order_date: val }), 'YYYY-MM-DD'),
    },
    {
      title: '商品名称', dataIndex: 'product_name', width: 180,
      render: (v: string | undefined, _r: QianniuOrderParsed, i: number) => (
        <Space.Compact size="small">
          <Input
            value={v ?? ''}
            size="small"
            style={{ minWidth: 120 }}
            onChange={(e) => setOrder(i, { product_name: e.target.value })}
          />
          <Tooltip title="匹配产品">
            <Button
              size="small"
              icon={<SearchOutlined />}
              loading={matchStates[i]?.loading}
              onClick={() => runMatch(i)}
            />
          </Tooltip>
        </Space.Compact>
      ),
    },
    {
      title: 'SKU', dataIndex: 'sku', width: 160,
      render: (v: string | undefined, _r: QianniuOrderParsed, i: number) =>
        editCell(v, (val) => setOrder(i, { sku: val })),
    },
    {
      title: '产品匹配', width: 140,
      render: (_: unknown, _r: QianniuOrderParsed, i: number) => {
        const ms = matchStates[i];
        if (!ms?.result) return <span style={{ color: '#bbb', fontSize: 12 }}>-</span>;
        const { product_code, sku_code, confidence } = ms.result;
        if (!product_code) return <Tag color="red">无匹配</Tag>;
        return (
          <Tooltip title={`置信度 ${(confidence * 100).toFixed(0)}%`}>
            <Space size={2} wrap>
              <Tag color={confidence >= 0.8 ? 'green' : 'orange'}>{product_code}</Tag>
              {sku_code && <Tag color="blue">{sku_code}</Tag>}
            </Space>
          </Tooltip>
        );
      },
    },
    {
      title: '数量', dataIndex: 'qty', width: 70,
      render: (v: number, _r: QianniuOrderParsed, i: number) => (
        <InputNumber
          value={v ?? 1} size="small" min={1} style={{ width: 60 }}
          onChange={(n) => setOrder(i, { qty: Number(n || 1) })}
        />
      ),
    },
    {
      title: '客户', dataIndex: 'customer_name', width: 90,
      render: (v: string | undefined, _r: QianniuOrderParsed, i: number) =>
        editCell(v, (val) => setOrder(i, { customer_name: val })),
    },
    {
      title: '电话', dataIndex: 'customer_phone', width: 120,
      render: (v: string | undefined, _r: QianniuOrderParsed, i: number) =>
        editCell(v, (val) => setOrder(i, { customer_phone: val })),
    },
    {
      title: '地址', dataIndex: 'customer_address', width: 200,
      render: (v: string | undefined, _r: QianniuOrderParsed, i: number) =>
        editCell(v, (val) => setOrder(i, { customer_address: val })),
    },
    {
      title: '实付', dataIndex: 'paid_amount', width: 90,
      render: (v: number | undefined, _r: QianniuOrderParsed, i: number) => (
        <InputNumber
          value={v} size="small" min={0} step={0.01} style={{ width: 80 }}
          onChange={(n) => setOrder(i, { paid_amount: Number(n ?? 0) })}
        />
      ),
    },
    {
      title: '优惠', dataIndex: 'discount', width: 80,
      render: (v: number | undefined, _r: QianniuOrderParsed, i: number) => (
        <InputNumber
          value={v} size="small" min={0} step={0.01} style={{ width: 70 }}
          onChange={(n) => setOrder(i, { discount: Number(n ?? 0) })}
        />
      ),
    },
    {
      title: '平台佣金', dataIndex: 'platform_fee', width: 80,
      render: (v: number | undefined, _r: QianniuOrderParsed, i: number) => (
        <InputNumber
          value={v} size="small" min={0} step={0.01} style={{ width: 70 }}
          onChange={(n) => setOrder(i, { platform_fee: Number(n ?? 0) })}
        />
      ),
    },
    {
      title: '客户备注', dataIndex: 'remark', width: 160,
      render: (v: string | undefined, _r: QianniuOrderParsed, i: number) =>
        editCell(v, (val) => setOrder(i, { remark: val }), '备注'),
    },
    {
      title: '置信度', dataIndex: 'confidence', width: 75,
      render: (v: number) => v != null
        ? <Tag color={v > 0.85 ? 'green' : v > 0.6 ? 'orange' : 'red'}>{(v * 100).toFixed(0)}%</Tag>
        : '-',
    },
    {
      title: '识别警告', dataIndex: 'warnings', width: 80,
      render: (ws: string[]) => (ws ?? []).length > 0
        ? <Tag color="orange">{ws.length} 项</Tag> : '-',
    },
    {
      title: '下单图', fixed: 'right' as const, width: 96,
      render: (_: unknown, r: QianniuOrderParsed) => (
        <Tooltip title={r.product_code ? '生成发给工厂的下单图' : '请先匹配产品编码再生成'}>
          <Button
            size="small"
            icon={<FileImageOutlined />}
            loading={sheetMut.isPending && sheetMut.variables === r}
            disabled={!r.product_code && !r.sku}
            onClick={() => sheetMut.mutate(r)}
          >
            下单图
          </Button>
        </Tooltip>
      ),
    },
  ];

  return (
    <Space direction="vertical" style={{ width: '100%' }} size="middle">
      <Alert
        type="info"
        showIcon
        message="千牛后台订单截图 → AI 自动解析订单字段, 用户预览确认后入库"
        description="支持全字段可编辑。点商品名旁的🔍可自动匹配系统产品编码。OCR 结果不准确字段会高亮 warnings。"
      />
      <Card size="small">
        <Dragger
          accept="image/*"
          showUploadList={false}
          beforeUpload={(f) => { parseMut.mutate(f); return false; }}
          disabled={parseMut.isPending}
          multiple={false}
        >
          <p className="ant-upload-drag-icon"><InboxOutlined /></p>
          <p className="ant-upload-text">
            {parseMut.isPending ? 'AI 解析中, 请稍候...' : '点击或拖入千牛后台截图'}
          </p>
          <p className="ant-upload-hint">单张图最大 20MB</p>
        </Dragger>
      </Card>

      {resp && resp.ocr_warnings.length > 0 && (
        <Alert type="warning" message="OCR 整体提示"
               description={<ul>{resp.ocr_warnings.map((w, i) => <li key={i}>{w}</li>)}</ul>} />
      )}

      {orders.length > 0 && (
        <Card
          size="small"
          title={`待入库订单 (${orders.length})`}
          extra={
            <Button
              type="primary"
              icon={<CloudUploadOutlined />}
              loading={commitMut.isPending}
              onClick={() => commitMut.mutate()}
            >
              确认入库
            </Button>
          }
        >
          <Table
            size="small"
            rowKey={(_, i) => String(i)}
            dataSource={orders}
            pagination={false}
            scroll={{ x: 2100 }}
            columns={columns as any}
          />
        </Card>
      )}

      {/* 下单图预览弹窗 */}
      <Modal
        open={!!sheet || sheetMut.isPending}
        title="工厂下单图"
        width={860}
        onCancel={() => setSheet(null)}
        footer={
          sheet ? [
            <Button key="close" onClick={() => setSheet(null)}>关闭</Button>,
            <Button
              key="print"
              type="primary"
              icon={<PrinterOutlined />}
              disabled={sheet.warnings.some((w) => w.severity === 'error')}
              onClick={() => {
                const html = document.getElementById('qn-sheet-print')?.innerHTML;
                if (!html) return;
                const w = window.open('', '_blank', 'width=900,height=700');
                if (!w) { message.warning('请允许弹窗以打印'); return; }
                w.document.write(`<html><head><title>下单图 ${sheet.order_no}</title></head><body>${html}</body></html>`);
                w.document.close();
                w.focus();
                setTimeout(() => { w.print(); }, 300);
              }}
            >
              打印 / 另存为图片
            </Button>,
          ] : null
        }
      >
        {sheetMut.isPending ? (
          <div style={{ textAlign: 'center', padding: 48 }}><Spin tip="生成中..."><div style={{ minHeight: 40 }} /></Spin></div>
        ) : sheet ? (
          <>
            {sheet.warnings.filter((w) => w.severity === 'error').map((w) => (
              <Alert key={w.code} type="error" showIcon style={{ marginBottom: 8 }} message={w.message} />
            ))}
            {sheet.warnings.filter((w) => w.severity !== 'error').map((w) => (
              <Alert key={w.code} type="warning" showIcon style={{ marginBottom: 8 }} message={w.message} />
            ))}
            <div id="qn-sheet-print">
              <FactorySheetCard data={sheet} />
            </div>
          </>
        ) : null}
      </Modal>
    </Space>
  );
}

// ----------------------------- 进货单 ---------------------------- //

function PurchaseTab() {
  const [resp, setResp] = useState<PurchaseParseResp | null>(null);
  const [purchase, setPurchase] = useState<PurchaseParsed | null>(null);

  const parseMut = useMutation({
    mutationFn: (file: File) => parsePurchaseScreenshot(file),
    onSuccess: (r) => {
      setResp(r);
      setPurchase(r.purchase);
      message.success(`AI 识别出 ${r.purchase.lines?.length ?? 0} 行采购明细`);
    },
    onError: (e: any) => message.error(e?.response?.data?.detail ?? 'OCR 失败'),
  });

  const commitMut = useMutation({
    mutationFn: () => commitPurchaseScreenshot({
      supplier: purchase?.supplier_name,
      purchase_date: purchase?.purchase_date,
      purchase_no: purchase?.purchase_no,
      tracking_no: purchase?.tracking_no,
      carrier: purchase?.carrier,
      freight: purchase?.freight,
      total_amount: purchase?.total_amount,
      remark: purchase?.remark,
      lines: purchase?.lines ?? [],
    }),
    onSuccess: (r) => {
      message.success(
        `入 ${r.inserted} 行 (单号 ${r.purchase_no})${r.has_tracking ? '' : ', 没填快递单号 - 持续弹窗提醒'}`,
      );
      setPurchase(null); setResp(null);
    },
  });

  const setLine = (i: number, patch: Partial<PurchaseLineParsed>) => {
    if (!purchase) return;
    const next = [...(purchase.lines ?? [])];
    next[i] = { ...next[i], ...patch };
    setPurchase({ ...purchase, lines: next });
  };

  return (
    <Space direction="vertical" style={{ width: '100%' }} size="middle">
      <Alert
        type="info" showIcon
        message="进货单截图 → 自动入 PartPurchase + 快递单号"
        description="不填快递单号会持续弹窗提醒, 直到补上为止。"
      />
      <Card size="small">
        <Dragger
          accept="image/*" showUploadList={false}
          beforeUpload={(f) => { parseMut.mutate(f); return false; }}
          disabled={parseMut.isPending} multiple={false}
        >
          <p className="ant-upload-drag-icon"><InboxOutlined /></p>
          <p className="ant-upload-text">
            {parseMut.isPending ? '解析中...' : '点击或拖入进货单截图'}
          </p>
        </Dragger>
      </Card>

      {purchase && (
        <Card size="small" title="解析结果 (可编辑)"
              extra={
                <Button type="primary" icon={<CloudUploadOutlined />}
                        loading={commitMut.isPending} onClick={() => commitMut.mutate()}>
                  确认入库
                </Button>
              }>
          <Form layout="vertical" size="small">
            <Space wrap>
              <Form.Item label="供应商" style={{ minWidth: 200 }}>
                <Input value={purchase.supplier_name ?? ''}
                       onChange={(e) => setPurchase({ ...purchase, supplier_name: e.target.value })} />
              </Form.Item>
              <Form.Item label="采购日期">
                <Input value={purchase.purchase_date ?? ''}
                       onChange={(e) => setPurchase({ ...purchase, purchase_date: e.target.value })}
                       placeholder="YYYY-MM-DD" />
              </Form.Item>
              <Form.Item label="快递单号">
                <Input value={purchase.tracking_no ?? ''}
                       status={!purchase.tracking_no ? 'warning' : undefined}
                       onChange={(e) => setPurchase({ ...purchase, tracking_no: e.target.value })}
                       placeholder="未填会持续弹窗" />
              </Form.Item>
              <Form.Item label="快递公司">
                <Input value={purchase.carrier ?? ''}
                       onChange={(e) => setPurchase({ ...purchase, carrier: e.target.value })} />
              </Form.Item>
              <Form.Item label="总金额">
                <InputNumber value={purchase.total_amount}
                             onChange={(v) => setPurchase({ ...purchase, total_amount: Number(v || 0) })} />
              </Form.Item>
            </Space>
          </Form>
          <Table
            size="small"
            rowKey={(_r, i) => String(i)}
            dataSource={purchase.lines ?? []}
            pagination={false}
            columns={[
              { title: '物料名', dataIndex: 'material_name',
                render: (v: any, _r: PurchaseLineParsed, i: number) => (
                  <Input value={v ?? ''} size="small"
                         onChange={(e) => setLine(i, { material_name: e.target.value })} />
                ),
              },
              { title: '规格', dataIndex: 'spec' },
              { title: '数量', dataIndex: 'qty', width: 100,
                render: (v: number, _r: PurchaseLineParsed, i: number) => (
                  <InputNumber size="small" value={v ?? 1}
                               onChange={(n) => setLine(i, { qty: Number(n || 1) })} />
                ),
              },
              { title: '单价', dataIndex: 'unit_price', width: 100 },
              { title: '金额', dataIndex: 'amount', width: 100 },
            ]}
          />
        </Card>
      )}
    </Space>
  );
}

// --------------------------- 工厂对账单 (Task 3) --------------------------- //

function FactoryReconTab() {
  const [mode, setMode] = useState<'image' | 'excel'>('image');
  const [resp, setResp] = useState<FactoryReconParseResp | null>(null);
  const [rows, setRows] = useState<FactoryReconRowParsed[]>([]);
  const [excelWarnings, setExcelWarnings] = useState<string[]>([]);

  const reset = () => { setResp(null); setRows([]); setExcelWarnings([]); };

  const parseMut = useMutation({
    mutationFn: (file: File) => parseFactoryReconScreenshot(file),
    onSuccess: (r) => {
      setResp(r);
      setRows(r.rows);
      setExcelWarnings([]);
      message.success(`AI 识别出 ${r.rows.length} 行对账记录`);
    },
    onError: (e: any) => message.error(e?.response?.data?.detail ?? 'OCR 失败'),
  });

  const excelMut = useMutation({
    mutationFn: (file: File) => parseFactoryReconExcel(file),
    onSuccess: (r: FactoryReconExcelResp) => {
      setResp(null);
      setRows(r.rows);
      setExcelWarnings(r.warnings ?? []);
      message.success(`AI 整理出 ${r.rows.length} 行对账记录`);
    },
    onError: (e: any) => message.error(e?.response?.data?.detail ?? 'Excel 解析失败'),
  });

  const commitMut = useMutation({
    mutationFn: () => commitFactoryReconScreenshot(rows),
    onSuccess: (r) => {
      message.success(`入库 ${r.inserted} 行工厂对账记录`);
      reset();
    },
    onError: (e: any) => message.error(e?.response?.data?.detail ?? '入库失败'),
  });

  const setRow = (i: number, patch: Partial<FactoryReconRowParsed>) =>
    setRows((prev) => { const n = [...prev]; n[i] = { ...n[i], ...patch }; return n; });

  const numCell = (
    v: number | null | undefined,
    on: (n: number | null) => void,
  ) => (
    <InputNumber
      value={v ?? undefined} size="small" min={0} step={0.01} style={{ width: 110 }}
      onChange={(n) => on(n == null ? null : Number(n))}
    />
  );

  const columns = [
    { title: '工厂名称', dataIndex: 'factory_name', width: 150,
      render: (v: string, _r: FactoryReconRowParsed, i: number) =>
        editCell(v, (val) => setRow(i, { factory_name: val }), '必填') },
    { title: '账期起', dataIndex: 'period_start', width: 120,
      render: (v: string | null | undefined, _r: FactoryReconRowParsed, i: number) =>
        editCell(v ?? undefined, (val) => setRow(i, { period_start: val }), 'YYYY-MM-DD') },
    { title: '账期止', dataIndex: 'period_end', width: 120,
      render: (v: string | null | undefined, _r: FactoryReconRowParsed, i: number) =>
        editCell(v ?? undefined, (val) => setRow(i, { period_end: val }), 'YYYY-MM-DD') },
    { title: '下单金额', dataIndex: 'order_amount', width: 120,
      render: (v: number | null | undefined, _r: FactoryReconRowParsed, i: number) =>
        numCell(v, (n) => setRow(i, { order_amount: n })) },
    { title: '账单金额', dataIndex: 'bill_amount', width: 120,
      render: (v: number | null | undefined, _r: FactoryReconRowParsed, i: number) =>
        numCell(v, (n) => setRow(i, { bill_amount: n })) },
    { title: '已支付', dataIndex: 'paid_amount', width: 120,
      render: (v: number | null | undefined, _r: FactoryReconRowParsed, i: number) =>
        numCell(v, (n) => setRow(i, { paid_amount: n })) },
    { title: '支付宝流水号', dataIndex: 'alipay_flow_no', width: 150,
      render: (v: string | null | undefined, _r: FactoryReconRowParsed, i: number) =>
        editCell(v ?? undefined, (val) => setRow(i, { alipay_flow_no: val })) },
    { title: '备注', dataIndex: 'remark', width: 150,
      render: (v: string | null | undefined, _r: FactoryReconRowParsed, i: number) =>
        editCell(v ?? undefined, (val) => setRow(i, { remark: val })) },
    { title: '识别警告', dataIndex: 'warnings', width: 80,
      render: (ws: string[] | undefined) => (ws ?? []).length > 0
        ? <Tag color="orange">{ws!.length} 项</Tag> : '-' },
  ];

  return (
    <Space direction="vertical" style={{ width: '100%' }} size="middle">
      <Alert
        type="info" showIcon
        message="工厂对账单 → AI 解析/整理每行对账 → 确认入库 FactoryReconciliation"
        description="账单金额-已支付 会自动算成差异。工厂表格较乱时，可直接在此上传 Excel，AI 会自动整理后再入库对账。"
      />
      <Segmented
        value={mode}
        onChange={(v) => { setMode(v as 'image' | 'excel'); reset(); }}
        options={[
          { label: '截图识别', value: 'image' },
          { label: 'Excel 上传 (AI 整理)', value: 'excel' },
        ]}
      />
      <Card size="small">
        {mode === 'image' ? (
          <Dragger
            accept="image/*" showUploadList={false}
            beforeUpload={(f) => { parseMut.mutate(f); return false; }}
            disabled={parseMut.isPending} multiple={false}
          >
            <p className="ant-upload-drag-icon"><InboxOutlined /></p>
            <p className="ant-upload-text">
              {parseMut.isPending ? 'AI 解析中, 请稍候...' : '点击或拖入工厂对账单截图'}
            </p>
            <p className="ant-upload-hint">单张图最大 20MB</p>
          </Dragger>
        ) : (
          <Dragger
            accept=".xlsx,.xls" showUploadList={false}
            beforeUpload={(f) => { excelMut.mutate(f); return false; }}
            disabled={excelMut.isPending} multiple={false}
          >
            <p className="ant-upload-drag-icon"><InboxOutlined /></p>
            <p className="ant-upload-text">
              {excelMut.isPending ? 'AI 整理中, 请稍候...' : '点击或拖入工厂对账 Excel (.xlsx/.xls)'}
            </p>
            <p className="ant-upload-hint">乱表也能用, AI 会自动整理列/格式; 最大 20MB, 取前 200 行</p>
          </Dragger>
        )}
      </Card>

      {resp && resp.ocr_warnings.length > 0 && (
        <Alert type="warning" message="OCR 整体提示"
               description={<ul>{resp.ocr_warnings.map((w, i) => <li key={i}>{w}</li>)}</ul>} />
      )}

      {excelWarnings.length > 0 && (
        <Alert type="warning" message="AI 整理提示"
               description={<ul>{excelWarnings.map((w, i) => <li key={i}>{w}</li>)}</ul>} />
      )}

      {rows.length > 0 && (
        <Card size="small" title={`待入库对账 (${rows.length})`}
              extra={
                <Button type="primary" icon={<CloudUploadOutlined />}
                        loading={commitMut.isPending} onClick={() => commitMut.mutate()}>
                  确认入库
                </Button>
              }>
          <Table
            size="small"
            rowKey={(_r, i) => String(i)}
            dataSource={rows}
            pagination={false}
            scroll={{ x: 1200 }}
            columns={columns as any}
          />
        </Card>
      )}
    </Space>
  );
}
