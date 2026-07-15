/**
 * 价格对账页: 千牛现价 vs ERP 应有值 (只读, 不改任何数据)。ERP 价为唯一标准, 漂移的去千牛改回。
 * 两个 tab:
 *  - 标价对账: 上传千牛「商品导出/发布模板」→ 千牛一口价 vs ERP日常价÷0.75 (容差1元)。
 *  - 券后价对账: 上传千牛「超级立减已报商品列表」→ 活动普惠券后价 vs ERP中促到手 (容差0.01, 一分钱不差)。
 * (2026-07-15: 长期"改ERP、千牛没同步"致全店半数漂移, 本页做常驻对账。)
 */
import { useState } from 'react';
import {
  Alert, Button, Card, Space, Statistic, Table, Tabs, Tag, Typography, Upload, message,
} from 'antd';
import { InboxOutlined, DownloadOutlined } from '@ant-design/icons';
import { api } from '../api/base';

const { Title, Paragraph } = Typography;

// ============================ 标价对账 ============================
interface Mismatch {
  taobao_item_id: string; sku_code: string; sku_name: string;
  qn_price: number; erp_should: number; diff: number; direction: string;
}
interface Incoherent {
  sku_code: string; sku_name: string; list_price: number; daily_price: number; should: number;
}
interface ReconResult {
  mismatches: Mismatch[]; mismatch_count: number; matched: number;
  qn_total: number; parsed_qn_sku: number;
  incoherent: Incoherent[]; incoherent_count: number;
}

function ListPriceRecon() {
  const [result, setResult] = useState<ReconResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [file, setFile] = useState<File | null>(null);

  async function runRecon(f: File) {
    setLoading(true); setFile(f); setResult(null);
    try {
      const fd = new FormData(); fd.append('file', f);
      const r = await api.post<ReconResult>('/api/pricing-skus/recon', fd, { timeout: 120000 });
      setResult(r.data);
      message.success(`标价对账完成: ${r.data.mismatch_count} 个漂移`);
    } catch (e: any) {
      message.error(e?.response?.data?.detail || e?.message || '对账失败');
    } finally { setLoading(false); }
  }

  async function downloadFix() {
    if (!file) return;
    try {
      const fd = new FormData(); fd.append('file', file);
      const r = await api.post('/api/pricing-skus/recon/fix-xlsx', fd, { responseType: 'blob', timeout: 120000 });
      const url = URL.createObjectURL(r.data as Blob);
      const a = document.createElement('a'); a.href = url; a.download = '标价返修表.xlsx'; a.click();
      URL.revokeObjectURL(url);
    } catch { message.error('下载失败'); }
  }

  const columns = [
    { title: '淘宝链接ID', dataIndex: 'taobao_item_id', width: 130 },
    { title: 'SKU编码', dataIndex: 'sku_code', width: 160 },
    { title: 'SKU名', dataIndex: 'sku_name', ellipsis: true },
    { title: '千牛现价', dataIndex: 'qn_price', width: 100, render: (v: number) => `¥${v}` },
    { title: 'ERP应有(日常÷0.75)', dataIndex: 'erp_should', width: 160, render: (v: number) => `¥${v}` },
    { title: '差', dataIndex: 'diff', width: 100, render: (v: number) => (
      <span style={{ color: v > 0 ? '#cf1322' : '#d46b08' }}>{v > 0 ? '+' : ''}{v}</span>) },
    { title: '方向', dataIndex: 'direction', width: 110, render: (v: string) => (
      <Tag color={v.includes('抬') ? 'red' : 'orange'}>{v}</Tag>) },
  ];

  return (
    <Space direction="vertical" style={{ width: '100%' }} size="middle">
      <Paragraph type="secondary">
        上传千牛「商品导出 / 发布模板」xlsx → 列出所有「千牛一口价 ≠ ERP 日常价÷0.75」的 SKU。
        锚点严格按第一铁律 = 日常价÷0.75。ERP 价为唯一标准，漂移的去千牛改回。只读，不改任何数据。
      </Paragraph>
      <Upload.Dragger accept=".xlsx,.xls" showUploadList={false} disabled={loading}
        beforeUpload={(f) => { runRecon(f as File); return false; }}>
        <p className="ant-upload-drag-icon"><InboxOutlined /></p>
        <p className="ant-upload-text">{loading ? '对账中…' : '点击或拖拽千牛全店发布模板 xlsx 到这里'}</p>
      </Upload.Dragger>
      {result && (
        <>
          <Space size="large" wrap>
            <Statistic title="解析千牛SKU" value={result.parsed_qn_sku} />
            <Statistic title="对得上" value={result.matched} valueStyle={{ color: '#3f8600' }} />
            <Statistic title="★漂移" value={result.mismatch_count} valueStyle={{ color: '#cf1322' }} />
            <Button type="primary" icon={<DownloadOutlined />} onClick={downloadFix} disabled={!result.mismatch_count}>
              下载返修表
            </Button>
          </Space>
          {result.incoherent_count > 0 && (
            <Alert type="warning" showIcon
              message={`ERP 内部不自洽: ${result.incoherent_count} 个 SKU 的 list_price ≠ 日常价÷0.75 (该 SKU 需在定价表 recompute)`}
              description={result.incoherent.slice(0, 8).map((x) => (
                <div key={x.sku_code}>{x.sku_code} {x.sku_name}: list={x.list_price} / 应有={x.should}</div>
              ))}
            />
          )}
          {result.mismatch_count === 0 ? (
            <Alert type="success" message="标价全部对得上，没有漂移 🎉" showIcon />
          ) : (
            <Table rowKey="sku_code" size="small" columns={columns as any}
              dataSource={result.mismatches} pagination={{ pageSize: 50, showSizeChanger: true }}
              scroll={{ x: 920 }} />
          )}
        </>
      )}
    </Space>
  );
}

// ============================ 券后价对账 ============================
interface CouponMismatch {
  sku_code: string; taobao_sku_id: string; sku_name: string;
  qn_coupon: number; erp_should: number; diff: number; direction: string;
  qn_activity: number | null; erp_daily: number | null;
}
interface CouponResult {
  mismatches: CouponMismatch[]; mismatch_count: number; matched: number;
  unmapped: number; no_target: number; skipped_custom: number;
  qn_total: number; parsed_qn_sku: number;
}

function CouponRecon() {
  const [result, setResult] = useState<CouponResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [file, setFile] = useState<File | null>(null);

  async function runRecon(f: File) {
    setLoading(true); setFile(f); setResult(null);
    try {
      const fd = new FormData(); fd.append('file', f);
      const r = await api.post<CouponResult>('/api/pricing-skus/recon-coupon', fd, { timeout: 120000 });
      setResult(r.data);
      message.success(`券后价对账完成: ${r.data.mismatch_count} 个漂移`);
    } catch (e: any) {
      message.error(e?.response?.data?.detail || e?.message || '对账失败');
    } finally { setLoading(false); }
  }

  async function downloadFix() {
    if (!file) return;
    try {
      const fd = new FormData(); fd.append('file', file);
      const r = await api.post('/api/pricing-skus/recon-coupon/fix-xlsx', fd, { responseType: 'blob', timeout: 120000 });
      const url = URL.createObjectURL(r.data as Blob);
      const a = document.createElement('a'); a.href = url; a.download = '券后价返修表.xlsx'; a.click();
      URL.revokeObjectURL(url);
    } catch { message.error('下载失败'); }
  }

  const columns = [
    { title: 'SKUID', dataIndex: 'taobao_sku_id', width: 140 },
    { title: 'SKU编码', dataIndex: 'sku_code', width: 160 },
    { title: 'SKU名', dataIndex: 'sku_name', ellipsis: true },
    { title: '千牛现券后价', dataIndex: 'qn_coupon', width: 110, render: (v: number) => `¥${v}` },
    { title: 'ERP应有(中促到手)', dataIndex: 'erp_should', width: 150, render: (v: number) => `¥${v}` },
    { title: '差', dataIndex: 'diff', width: 100, render: (v: number) => (
      <span style={{ color: v > 0 ? '#cf1322' : '#d46b08' }}>{v > 0 ? '+' : ''}{v}</span>) },
    { title: '方向', dataIndex: 'direction', width: 110, render: (v: string) => (
      <Tag color={v.includes('抬') ? 'red' : 'orange'}>{v}</Tag>) },
    { title: 'ERP日常价', dataIndex: 'erp_daily', width: 100, render: (v: number | null) => v == null ? '-' : `¥${v}` },
  ];

  return (
    <Space direction="vertical" style={{ width: '100%' }} size="middle">
      <Paragraph type="secondary">
        上传千牛「超级立减 · 已报商品列表」xlsx → 列出所有「活动普惠券后价 ≠ ERP 中促到手」的 SKU。
        对标 ERP <b>mid_buyer_price</b>（超级立减10%场买家到手），容差 <b>0.01（一分钱不差）</b>。只读，不改任何数据。
      </Paragraph>
      <Alert type="info" showIcon style={{ marginBottom: 0 }}
        message="使用前提: 需活动生效期导出的新鲜表; 且改价后要先重推活动, 线上券后价才会对齐新 ERP。否则会满屏假漂移(线上还是旧值)。" />
      <Upload.Dragger accept=".xlsx,.xls" showUploadList={false} disabled={loading}
        beforeUpload={(f) => { runRecon(f as File); return false; }}>
        <p className="ant-upload-drag-icon"><InboxOutlined /></p>
        <p className="ant-upload-text">{loading ? '对账中…' : '点击或拖拽千牛「超级立减已报商品列表」xlsx 到这里'}</p>
      </Upload.Dragger>
      {result && (
        <>
          <Space size="large" wrap>
            <Statistic title="解析已报SKU" value={result.parsed_qn_sku} />
            <Statistic title="对得上" value={result.matched} valueStyle={{ color: '#3f8600' }} />
            <Statistic title="★漂移" value={result.mismatch_count} valueStyle={{ color: '#cf1322' }} />
            <Statistic title="映射不上" value={result.unmapped} valueStyle={{ color: '#8c8c8c' }} />
            <Statistic title="ERP无中促到手" value={result.no_target} valueStyle={{ color: '#8c8c8c' }} />
            <Button type="primary" icon={<DownloadOutlined />} onClick={downloadFix} disabled={!result.mismatch_count}>
              下载返修表
            </Button>
          </Space>
          {result.unmapped > 0 && (
            <Alert type="warning" showIcon
              message={`${result.unmapped} 个 SKUID 在 ERP 里映射不上 (taobao_sku_id 映射过期/缺失) → 需刷新 SKU 映射, 否则这些券后价无法对账。`} />
          )}
          {result.mismatch_count === 0 ? (
            <Alert type="success" message="券后价全部对得上，没有漂移 🎉" showIcon />
          ) : (
            <Table rowKey="taobao_sku_id" size="small" columns={columns as any}
              dataSource={result.mismatches} pagination={{ pageSize: 50, showSizeChanger: true }}
              scroll={{ x: 1020 }} />
          )}
        </>
      )}
    </Space>
  );
}

// ============================ 页 ============================
export default function PriceReconPage() {
  return (
    <div style={{ padding: 16 }}>
      <Title level={4}>价格对账 · 千牛现价 vs ERP 应有值</Title>
      <Card>
        <Tabs
          items={[
            { key: 'list', label: '标价对账', children: <ListPriceRecon /> },
            { key: 'coupon', label: '券后价对账', children: <CouponRecon /> },
          ]}
        />
      </Card>
    </div>
  );
}
