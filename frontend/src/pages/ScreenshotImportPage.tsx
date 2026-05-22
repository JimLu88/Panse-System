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
  Space,
  Table,
  Tabs,
  Tag,
  Typography,
  Upload,
  message,
} from 'antd';
import { CloudUploadOutlined, InboxOutlined } from '@ant-design/icons';
import { useMutation } from '@tanstack/react-query';
import {
  PurchaseLineParsed,
  PurchaseParseResp,
  PurchaseParsed,
  QianniuOrderParsed,
  QianniuParseResp,
  commitPurchaseScreenshot,
  commitQianniuOrders,
  parsePurchaseScreenshot,
  parseQianniuScreenshot,
} from '../api/client';

const { Dragger } = Upload;

export default function ScreenshotImportPage() {
  return (
    <Tabs items={[
      { key: 'qianniu', label: '千牛订单截图', children: <QianniuTab /> },
      { key: 'purchase', label: '进货单截图', children: <PurchaseTab /> },
    ]} />
  );
}

// ----------------------------- 千牛 ----------------------------- //

function QianniuTab() {
  const [resp, setResp] = useState<QianniuParseResp | null>(null);
  const [orders, setOrders] = useState<QianniuOrderParsed[]>([]);

  const parseMut = useMutation({
    mutationFn: (file: File) => parseQianniuScreenshot(file),
    onSuccess: (r) => {
      setResp(r);
      setOrders(r.orders);
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
    },
    onError: (e: any) => message.error(e?.response?.data?.detail ?? '入库失败'),
  });

  return (
    <Space direction="vertical" style={{ width: '100%' }} size="middle">
      <Alert
        type="info"
        showIcon
        message="千牛后台订单截图 → AI 自动解析订单字段, 用户预览确认后入库"
        description="支持订单号、SKU、数量、客户、地址、价格、优惠、平台佣金等全字段。识别后可手工修改, 不准确字段会高亮 warnings。"
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
        <Card size="small" title={`待入库订单 (${orders.length})`}
              extra={
                <Button type="primary" icon={<CloudUploadOutlined />}
                        loading={commitMut.isPending}
                        onClick={() => commitMut.mutate()}>
                  确认入库
                </Button>
              }>
          <Table
            size="small"
            rowKey={(_, i) => String(i)}
            dataSource={orders}
            pagination={false}
            scroll={{ x: 1500 }}
            columns={[
              { title: '订单号', dataIndex: 'order_no', width: 180,
                render: (v: string, _r: QianniuOrderParsed, i: number) => (
                  <Input value={v} size="small" onChange={(e) => {
                    const next = [...orders]; next[i] = { ...next[i], order_no: e.target.value };
                    setOrders(next);
                  }} />
                ),
              },
              { title: '商品', dataIndex: 'product_name', width: 180,
                render: (v: any, _r: QianniuOrderParsed, i: number) => (
                  <Input value={v ?? ''} size="small" onChange={(e) => {
                    const next = [...orders]; next[i] = { ...next[i], product_name: e.target.value };
                    setOrders(next);
                  }} />
                ),
              },
              { title: 'SKU', dataIndex: 'sku', width: 180 },
              { title: '数量', dataIndex: 'qty', width: 80,
                render: (v: number, _r: QianniuOrderParsed, i: number) => (
                  <InputNumber value={v ?? 1} size="small" min={1} onChange={(n) => {
                    const next = [...orders]; next[i] = { ...next[i], qty: Number(n || 1) };
                    setOrders(next);
                  }} />
                ),
              },
              { title: '客户', dataIndex: 'customer_name', width: 100 },
              { title: '电话', dataIndex: 'customer_phone', width: 130 },
              { title: '地址', dataIndex: 'customer_address', ellipsis: true },
              { title: '实付', dataIndex: 'paid_amount', width: 90 },
              { title: '优惠', dataIndex: 'discount', width: 80 },
              { title: '平台佣金', dataIndex: 'platform_fee', width: 90 },
              { title: '置信度', dataIndex: 'confidence', width: 80,
                render: (v: number) => v != null ?
                  <Tag color={v > 0.85 ? 'green' : v > 0.6 ? 'orange' : 'red'}>
                    {(v * 100).toFixed(0)}%
                  </Tag> : '-',
              },
              { title: '识别警告', dataIndex: 'warnings',
                render: (ws: string[]) => (ws ?? []).length > 0 ?
                  <Tag color="orange">{ws.length} 项</Tag> : '-',
              },
            ]}
          />
        </Card>
      )}
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
