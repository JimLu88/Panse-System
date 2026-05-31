import { useState } from 'react';
import { Alert, Button, Space, Table, Tag, Typography, Upload, message } from 'antd';
import { DownloadOutlined, InboxOutlined, SyncOutlined } from '@ant-design/icons';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '../api/client';

interface WanshifuBill {
  id: number;
  bill_date: string | null;
  order_no: string | null;
  service_type: string | null;
  amount: number;
  status: string | null;
  remark: string | null;
}

interface ImportResult {
  inserted: number;
  skipped_invalid: number;
  errors: string[];
}

export default function WanshifuBillsPage() {
  const qc = useQueryClient();
  const [importing, setImporting] = useState(false);

  const { data = [], isLoading } = useQuery<WanshifuBill[]>({
    queryKey: ['wanshifu-bills'],
    queryFn: () => api.get('/api/finance/wanshifu-bills').then(r => r.data),
  });

  const handleImport = async (file: File) => {
    setImporting(true);
    const fd = new FormData();
    fd.append('file', file);
    try {
      const r = await api.post<ImportResult>('/api/finance/wanshifu-bills/import-csv', fd, {
        headers: { 'Content-Type': 'multipart/form-data' },
      });
      message.success(`导入成功：新增 ${r.data.inserted} 条，跳过 ${r.data.skipped_invalid} 条无效行`);
      qc.invalidateQueries({ queryKey: ['wanshifu-bills'] });
    } catch (e: any) {
      message.error(e?.response?.data?.detail ?? '导入失败');
    } finally {
      setImporting(false);
    }
    return false;
  };

  const columns = [
    { title: '账单日期', dataIndex: 'bill_date', width: 110 },
    { title: '订单号', dataIndex: 'order_no', width: 160, ellipsis: true },
    { title: '服务类型', dataIndex: 'service_type', width: 100,
      render: (v: string | null) => v ? <Tag>{v}</Tag> : '-' },
    { title: '金额', dataIndex: 'amount', width: 100, align: 'right' as const,
      render: (v: number) => <span style={{ color: '#cf1322' }}>¥{Number(v).toFixed(2)}</span> },
    { title: '结算状态', dataIndex: 'status', width: 100,
      render: (v: string | null) => v
        ? <Tag color={v === '已结算' ? 'green' : 'orange'}>{v}</Tag> : '-' },
    { title: '备注', dataIndex: 'remark', ellipsis: true },
  ];

  const total = data.reduce((s, r) => s + Number(r.amount), 0);

  return (
    <Space direction="vertical" style={{ width: '100%' }} size="middle">
      <Space align="center">
        <Typography.Title level={4} style={{ margin: 0 }}>万师傅对账单</Typography.Title>
        <Tag color="blue">物流 · 安装</Tag>
      </Space>

      <Alert type="info" showIcon
        message="从万师傅后台按月导出 CSV，导入后用于「安装费对账」规则的应付口径。已同步飞书。" />

      <Space wrap>
        <Upload accept=".csv" showUploadList={false} beforeUpload={handleImport}>
          <Button icon={<InboxOutlined />} loading={importing}>导入 CSV</Button>
        </Upload>
        <Button icon={<DownloadOutlined />}
          onClick={() => window.open('/api/finance/wanshifu-bills/template.csv')}>
          下载模板
        </Button>
        <Button icon={<SyncOutlined />} onClick={() => qc.invalidateQueries({ queryKey: ['wanshifu-bills'] })}>
          刷新
        </Button>
        {data.length > 0 && (
          <Typography.Text type="secondary">
            共 {data.length} 条 · 合计 <strong>¥{total.toLocaleString('zh', { minimumFractionDigits: 2 })}</strong>
          </Typography.Text>
        )}
      </Space>

      <Table
        size="small"
        loading={isLoading}
        rowKey="id"
        dataSource={data}
        columns={columns}
        pagination={{ pageSize: 50, showSizeChanger: true }}
        scroll={{ x: 700 }}
      />
    </Space>
  );
}
