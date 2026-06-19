import { useState } from 'react';
import { Alert, Button, Segmented, Space, Table, Tag, Typography, Upload, message } from 'antd';
import { DownloadOutlined, InboxOutlined, SyncOutlined } from '@ant-design/icons';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '../api/client';
import FullColumnView from '../components/FullColumnView';
import PresetTable from '../components/PresetTable';

interface LogisticsBill {
  id: number;
  bill_date: string | null;
  carrier: string | null;
  tracking_no: string | null;
  order_no: string | null;
  weight_kg: number | null;
  freight_amount: number;
  remark: string | null;
}

interface ImportResult {
  inserted: number;
  skipped_invalid: number;
  errors: string[];
}

export default function LogisticsBillsPage() {
  const qc = useQueryClient();
  const [importing, setImporting] = useState(false);
  const [viewMode, setViewMode] = useState<'curated' | 'full'>('curated');

  const { data = [], isLoading } = useQuery<LogisticsBill[]>({
    queryKey: ['logistics-bills'],
    queryFn: () => api.get('/api/finance/logistics-bills').then(r => r.data),
  });

  const handleImport = async (file: File) => {
    setImporting(true);
    const fd = new FormData();
    fd.append('file', file);
    try {
      const r = await api.post<ImportResult>('/api/finance/logistics-bills/import-csv', fd, {
        headers: { 'Content-Type': 'multipart/form-data' },
      });
      message.success(`导入成功：新增 ${r.data.inserted} 条，跳过 ${r.data.skipped_invalid} 条无效行`);
      qc.invalidateQueries({ queryKey: ['logistics-bills'] });
    } catch (e: any) {
      message.error(e?.response?.data?.detail ?? '导入失败');
    } finally {
      setImporting(false);
    }
    return false;
  };

  // 物流账单 xlsx 统一导入: 文件名含「德邦」=逐运单; 否则壹米滴答月结(总额取自文件名「…14540元」)
  const handleImportXlsx = async (file: File) => {
    setImporting(true);
    const fd = new FormData();
    fd.append('file', file);
    try {
      const r = await api.post<ImportResult & { skipped_duplicate?: number }>(
        '/api/finance/logistics-bills/import-xlsx', fd,
        { headers: { 'Content-Type': 'multipart/form-data' } },
      );
      const dup = r.data.skipped_duplicate ?? 0;
      message.success(`导入成功：新增 ${r.data.inserted} 条，去重 ${dup} 条，跳过 ${r.data.skipped_invalid} 条`);
      qc.invalidateQueries({ queryKey: ['logistics-bills'] });
    } catch (e: any) {
      message.error(e?.response?.data?.detail ?? 'xlsx 导入失败');
    } finally {
      setImporting(false);
    }
    return false;
  };

  const columns = [
    { title: '账单日期', dataIndex: 'bill_date', width: 110 },
    { title: '承运商', dataIndex: 'carrier', width: 100,
      render: (v: string | null) => v ? <Tag>{v}</Tag> : '-' },
    { title: '运单号', dataIndex: 'tracking_no', width: 160, ellipsis: true },
    { title: '订单号', dataIndex: 'order_no', width: 160, ellipsis: true },
    { title: '重量(kg)', dataIndex: 'weight_kg', width: 90, align: 'right' as const,
      render: (v: number | string | null) => v != null ? Number(v).toFixed(3) : '-' },
    { title: '运费', dataIndex: 'freight_amount', width: 100, align: 'right' as const,
      render: (v: number) => <span style={{ color: '#cf1322' }}>¥{Number(v).toFixed(2)}</span> },
    { title: '备注', dataIndex: 'remark', ellipsis: true },
  ];

  const total = data.reduce((s, r) => s + Number(r.freight_amount), 0);

  return (
    <Space direction="vertical" style={{ width: '100%' }} size="middle">
      <Space align="center">
        <Typography.Title level={4} style={{ margin: 0 }}>物流费账单</Typography.Title>
        <Tag color="cyan">物流</Tag>
      </Space>

      <Alert type="info" showIcon
        message="从物流公司后台按月导出月结账单 CSV，导入后用于「物流费对账」规则的应付口径。已同步飞书。" />

      <Segmented
        value={viewMode}
        onChange={(v) => setViewMode(v as 'curated' | 'full')}
        options={[
          { label: '精选视图', value: 'curated' },
          { label: '全部列', value: 'full' },
        ]}
      />
      {viewMode === 'full' && <FullColumnView entity="logistics_bill" />}
      {viewMode === 'curated' && (<>
      <Space wrap>
        <Upload accept=".csv" showUploadList={false} beforeUpload={handleImport}>
          <Button icon={<InboxOutlined />} loading={importing}>导入 CSV</Button>
        </Upload>
        <Upload accept=".xlsx" multiple showUploadList={false} beforeUpload={handleImportXlsx}>
          <Button type="primary" icon={<InboxOutlined />} loading={importing}>导入账单 xlsx (壹米滴答/德邦)</Button>
        </Upload>
        <Button icon={<DownloadOutlined />}
          onClick={() => window.open('/api/finance/logistics-bills/template.csv')}>
          下载模板
        </Button>
        <Button icon={<SyncOutlined />} onClick={() => qc.invalidateQueries({ queryKey: ['logistics-bills'] })}>
          刷新
        </Button>
        {data.length > 0 && (
          <Typography.Text type="secondary">
            共 {data.length} 条 · 合计运费 <strong>¥{total.toLocaleString('zh', { minimumFractionDigits: 2 })}</strong>
          </Typography.Text>
        )}
      </Space>

      <PresetTable
        tableKey="logistics_bill"
        size="small"
        loading={isLoading}
        rowKey="id"
        dataSource={data}
        columns={columns}
        pagination={{ defaultPageSize: 100, showSizeChanger: true }}
        scroll={{ x: 800 }}
      />
      </>)}
    </Space>
  );
}
