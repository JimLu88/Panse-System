/**
 * 价格对账页: 上传千牛「商品导出/发布模板」xlsx → 千牛现价 vs ERP 应有值的漂移清单 + 一键返修表。
 * ERP 价为唯一标准; 漂移的去千牛改回。只读, 不改任何数据。
 * (2026-07-15: 长期"改ERP、千牛没同步"致全店半数标价漂移, 本页做常驻对账。)
 */
import { useState } from 'react';
import {
  Alert, Button, Card, Space, Statistic, Table, Tag, Typography, Upload, message,
} from 'antd';
import { InboxOutlined, DownloadOutlined } from '@ant-design/icons';
import { api } from '../api/base';

const { Title, Paragraph } = Typography;

interface Mismatch {
  taobao_item_id: string;
  sku_code: string;
  sku_name: string;
  qn_price: number;
  erp_should: number;
  diff: number;
  direction: string;
}
interface ReconResult {
  mismatches: Mismatch[];
  mismatch_count: number;
  matched: number;
  qn_total: number;
  parsed_qn_sku: number;
}

export default function PriceReconPage() {
  const [result, setResult] = useState<ReconResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [file, setFile] = useState<File | null>(null);

  async function runRecon(f: File) {
    setLoading(true);
    setFile(f);
    setResult(null);
    try {
      const fd = new FormData();
      fd.append('file', f);
      const r = await api.post<ReconResult>('/api/pricing-skus/recon', fd, { timeout: 120000 });
      setResult(r.data);
      message.success(`对账完成: ${r.data.mismatch_count} 个漂移`);
    } catch (e: any) {
      message.error(e?.response?.data?.detail || e?.message || '对账失败');
    } finally {
      setLoading(false);
    }
  }

  async function downloadFix() {
    if (!file) return;
    try {
      const fd = new FormData();
      fd.append('file', file);
      const r = await api.post('/api/pricing-skus/recon/fix-xlsx', fd, {
        responseType: 'blob', timeout: 120000,
      });
      const url = URL.createObjectURL(r.data as Blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = '标价返修表.xlsx';
      a.click();
      URL.revokeObjectURL(url);
    } catch {
      message.error('下载失败');
    }
  }

  const columns = [
    { title: '淘宝链接ID', dataIndex: 'taobao_item_id', width: 130 },
    { title: 'SKU编码', dataIndex: 'sku_code', width: 160 },
    { title: 'SKU名', dataIndex: 'sku_name', ellipsis: true },
    { title: '千牛现价', dataIndex: 'qn_price', width: 100, render: (v: number) => `¥${v}` },
    { title: 'ERP应有(日常÷0.75)', dataIndex: 'erp_should', width: 160, render: (v: number) => `¥${v}` },
    {
      title: '差', dataIndex: 'diff', width: 100,
      render: (v: number) => (
        <span style={{ color: v > 0 ? '#cf1322' : '#d46b08' }}>{v > 0 ? '+' : ''}{v}</span>
      ),
    },
    {
      title: '方向', dataIndex: 'direction', width: 110,
      render: (v: string) => <Tag color={v.includes('抬') ? 'red' : 'orange'}>{v}</Tag>,
    },
  ];

  return (
    <div style={{ padding: 16 }}>
      <Title level={4}>价格对账 · 千牛现价 vs ERP 应有值</Title>
      <Paragraph type="secondary">
        上传一份千牛「商品导出 / 发布模板」xlsx → 自动列出所有「千牛一口价 ≠ ERP 日常价÷0.75」的 SKU。
        ERP 价为唯一标准，漂移的去千牛改回。只读，不改任何数据。
      </Paragraph>
      <Card>
        <Space direction="vertical" style={{ width: '100%' }} size="middle">
          <Upload.Dragger
            accept=".xlsx,.xls"
            showUploadList={false}
            disabled={loading}
            beforeUpload={(f) => { runRecon(f as File); return false; }}
          >
            <p className="ant-upload-drag-icon"><InboxOutlined /></p>
            <p className="ant-upload-text">{loading ? '对账中…' : '点击或拖拽千牛全店导出 xlsx 到这里'}</p>
          </Upload.Dragger>
          {result && (
            <>
              <Space size="large" wrap>
                <Statistic title="解析千牛SKU" value={result.parsed_qn_sku} />
                <Statistic title="对得上" value={result.matched} valueStyle={{ color: '#3f8600' }} />
                <Statistic title="★漂移" value={result.mismatch_count} valueStyle={{ color: '#cf1322' }} />
                <Button
                  type="primary" icon={<DownloadOutlined />} onClick={downloadFix}
                  disabled={!result.mismatch_count}
                >
                  下载返修表
                </Button>
              </Space>
              {result.mismatch_count === 0 ? (
                <Alert type="success" message="全部对得上，没有价格漂移 🎉" showIcon />
              ) : (
                <Table
                  rowKey="sku_code"
                  size="small"
                  columns={columns as any}
                  dataSource={result.mismatches}
                  pagination={{ pageSize: 50, showSizeChanger: true }}
                  scroll={{ x: 920 }}
                />
              )}
            </>
          )}
        </Space>
      </Card>
    </div>
  );
}
