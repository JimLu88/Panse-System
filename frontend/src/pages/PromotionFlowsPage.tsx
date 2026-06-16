import { useState } from 'react';
import { Alert, Button, Segmented, Space, Statistic, Table, Tag, Typography, Upload, message } from 'antd';
import { DownloadOutlined, InboxOutlined, SyncOutlined } from '@ant-design/icons';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '../api/client';
import FullColumnView from '../components/FullColumnView';
import PresetTable from '../components/PresetTable';

interface PromotionFlow {
  id: number;
  transaction_date: string | null;
  flow_type: string | null;   // 充值 / 支出 / 退款
  amount: number;
  alipay_flow_no: string | null;
  remark: string | null;
}

interface ImportResult {
  inserted: number;
  skipped_invalid: number;
  errors: string[];
}

const TYPE_COLOR: Record<string, string> = { 充值: 'green', 收入: 'green', 支出: 'red', 退款: 'blue' };
// 旧数据进账标签是「收入」, 新导入统一成「充值」; 统计两者都算进账, 免「充值2万vs支出10万」错觉
const RECHARGE_TYPES = ['充值', '收入'];

export default function PromotionFlowsPage() {
  const qc = useQueryClient();
  const [importing, setImporting] = useState(false);
  const [viewMode, setViewMode] = useState<'curated' | 'full'>('curated');

  const { data = [], isLoading } = useQuery<PromotionFlow[]>({
    queryKey: ['promotion-flows'],
    queryFn: () => api.get('/api/finance/promotion-flows').then((r) => r.data),
  });

  const handleImport = async (file: File) => {
    setImporting(true);
    const fd = new FormData();
    fd.append('file', file);
    try {
      const r = await api.post<ImportResult>('/api/finance/promotion-flows/import-csv', fd, {
        headers: { 'Content-Type': 'multipart/form-data' },
      });
      message.success(`导入成功：新增 ${r.data.inserted} 条，跳过 ${r.data.skipped_invalid} 条无效行`);
      qc.invalidateQueries({ queryKey: ['promotion-flows'] });
    } catch (e: any) {
      message.error(e?.response?.data?.detail ?? '导入失败');
    } finally {
      setImporting(false);
    }
    return false;
  };

  const columns = [
    { title: '交易日期', dataIndex: 'transaction_date', width: 110 },
    { title: '类型', dataIndex: 'flow_type', width: 90,
      render: (v: string | null) => (v ? <Tag color={TYPE_COLOR[v]}>{v}</Tag> : '-') },
    { title: '金额', dataIndex: 'amount', width: 120, align: 'right' as const,
      render: (v: number) => `¥${Number(v).toFixed(2)}` },
    { title: '支付宝流水号', dataIndex: 'alipay_flow_no', width: 200, ellipsis: true, render: (v: string | null) => v || '-' },
    { title: '备注', dataIndex: 'remark', ellipsis: true, render: (v: string | null) => v || '-' },
  ];

  const recharge = data.filter((r) => RECHARGE_TYPES.includes(r.flow_type ?? '')).reduce((s, r) => s + Number(r.amount), 0);
  const spend = data.filter((r) => r.flow_type === '支出').reduce((s, r) => s + Number(r.amount), 0);

  return (
    <Space direction="vertical" style={{ width: '100%' }} size="middle">
      <Space align="center">
        <Typography.Title level={4} style={{ margin: 0 }}>推广费流水</Typography.Title>
        <Tag color="purple">推广</Tag>
      </Space>

      <Alert type="info" showIcon
        message="从淘宝推广后台(直通车/万相台)按月导出充值+消耗流水 CSV，导入后用于推广ROI核算与对账。列: 交易日期/类型(充值/支出/退款)/金额/支付宝流水号/备注。" />

      <Segmented
        value={viewMode}
        onChange={(v) => setViewMode(v as 'curated' | 'full')}
        options={[{ label: '精选视图', value: 'curated' }, { label: '全部列', value: 'full' }]}
      />
      {viewMode === 'full' && <FullColumnView entity="promotion_flow" />}
      {viewMode === 'curated' && (<>
        <Space wrap>
          <Upload accept=".csv" showUploadList={false} beforeUpload={handleImport}>
            <Button icon={<InboxOutlined />} loading={importing}>导入 CSV</Button>
          </Upload>
          <Button icon={<DownloadOutlined />}
            onClick={() => window.open('/api/finance/promotion-flows/template.csv')}>
            下载模板
          </Button>
          <Button icon={<SyncOutlined />} onClick={() => qc.invalidateQueries({ queryKey: ['promotion-flows'] })}>
            刷新
          </Button>
        </Space>

        {data.length > 0 && (
          <Space size="large">
            <Statistic title="充值合计" value={recharge} precision={2} prefix="¥" valueStyle={{ color: '#3f8600' }} />
            <Statistic title="支出合计" value={spend} precision={2} prefix="¥" valueStyle={{ color: '#cf1322' }} />
            <Statistic title="流水笔数" value={data.length} />
          </Space>
        )}

        <PresetTable
          tableKey="promotion_flow"
          size="small" loading={isLoading} rowKey="id" dataSource={data} columns={columns}
          pagination={{ defaultPageSize: 100, showSizeChanger: true }} scroll={{ x: 700 }}
        />
      </>)}
    </Space>
  );
}
