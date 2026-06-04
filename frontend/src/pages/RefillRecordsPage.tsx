import { useState } from 'react';
import { Alert, Button, Segmented, Space, Table, Tag, Typography, Upload, message } from 'antd';
import { DownloadOutlined, InboxOutlined, SyncOutlined } from '@ant-design/icons';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '../api/client';
import FullColumnView from '../components/FullColumnView';

interface RefillRecord {
  id: number;
  order_no: string;
  buyer_nick: string | null;
  refill_date: string | null;
  product_code: string | null;
  product_name: string | null;
  sku: string | null;
  qty: number;
  order_amount: number | null;
  refill_cost: number | null;
  total_cost: number | null;
}

interface ImportResult {
  inserted: number;
  skipped_invalid: number;
  errors: string[];
}

export default function RefillRecordsPage() {
  const qc = useQueryClient();
  const [importing, setImporting] = useState(false);
  const [viewMode, setViewMode] = useState<'curated' | 'full'>('curated');

  const { data = [], isLoading } = useQuery<RefillRecord[]>({
    queryKey: ['refill-records'],
    queryFn: () => api.get('/api/finance/refill-records').then(r => r.data),
  });

  const handleImport = async (file: File) => {
    setImporting(true);
    const fd = new FormData();
    fd.append('file', file);
    try {
      const r = await api.post<ImportResult>('/api/finance/refill-records/import-csv', fd, {
        headers: { 'Content-Type': 'multipart/form-data' },
      });
      message.success(`导入成功：新增 ${r.data.inserted} 条，跳过 ${r.data.skipped_invalid} 条无效行`);
      qc.invalidateQueries({ queryKey: ['refill-records'] });
    } catch (e: any) {
      message.error(e?.response?.data?.detail ?? '导入失败');
    } finally {
      setImporting(false);
    }
    return false;
  };

  const money = (v: number | null) =>
    v != null ? `¥${Number(v).toFixed(2)}` : '-';

  const columns = [
    { title: '补单日期', dataIndex: 'refill_date', width: 110 },
    { title: '订单号', dataIndex: 'order_no', width: 160, ellipsis: true },
    { title: '买家昵称', dataIndex: 'buyer_nick', width: 110, ellipsis: true },
    { title: '产品名称', dataIndex: 'product_name', width: 160, ellipsis: true },
    { title: 'SKU', dataIndex: 'sku', width: 140, ellipsis: true },
    { title: '数量', dataIndex: 'qty', width: 65, align: 'right' as const },
    { title: '订单金额', dataIndex: 'order_amount', width: 100, align: 'right' as const,
      render: money },
    { title: '补单成本', dataIndex: 'refill_cost', width: 100, align: 'right' as const,
      render: money },
    { title: '总成本', dataIndex: 'total_cost', width: 100, align: 'right' as const,
      render: (v: number | null) => v != null
        ? <Tag color="red">¥{Number(v).toFixed(2)}</Tag> : '-' },
  ];

  const totalCost = data.reduce((s, r) => s + (Number(r.total_cost) || 0), 0);

  return (
    <Space direction="vertical" style={{ width: '100%' }} size="middle">
      <Space align="center">
        <Typography.Title level={4} style={{ margin: 0 }}>补单记录</Typography.Title>
        <Tag color="orange">财务</Tag>
      </Space>

      <Alert type="info" showIcon
        message="补单 = 已发货后额外补发的订单成本（含补发运费、佣金）。导入后参与利润核算。已同步飞书。" />

      <Space wrap>
        <Upload accept=".csv" showUploadList={false} beforeUpload={handleImport}>
          <Button icon={<InboxOutlined />} loading={importing}>导入 CSV</Button>
        </Upload>
        <Button icon={<DownloadOutlined />}
          onClick={() => window.open('/api/finance/refill-records/template.csv')}>
          下载模板
        </Button>
        <Button icon={<SyncOutlined />} onClick={() => qc.invalidateQueries({ queryKey: ['refill-records'] })}>
          刷新
        </Button>
        {data.length > 0 && (
          <Typography.Text type="secondary">
            共 {data.length} 条 · 总成本 <strong style={{ color: '#cf1322' }}>
              ¥{totalCost.toLocaleString('zh', { minimumFractionDigits: 2 })}
            </strong>
          </Typography.Text>
        )}
      </Space>

      <Segmented
        value={viewMode}
        onChange={(v) => setViewMode(v as 'curated' | 'full')}
        options={[
          { label: '精选视图（可编辑）', value: 'curated' },
          { label: '全部列', value: 'full' },
        ]}
      />

      {viewMode === 'full' && <FullColumnView entity="refill_record" defaultShowAll />}

      {viewMode === 'curated' && (
      <Table
        size="small"
        loading={isLoading}
        rowKey="id"
        dataSource={data}
        columns={columns}
        pagination={{ pageSize: 50, showSizeChanger: true }}
        scroll={{ x: 900 }}
      />
      )}
    </Space>
  );
}
