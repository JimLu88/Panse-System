import { useState } from 'react';
import { Alert, Button, Segmented, Space, Table, Tabs, Tag, Typography, Upload, message } from 'antd';
import { DownloadOutlined, InboxOutlined, SyncOutlined } from '@ant-design/icons';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '../api/client';
import FullColumnView from '../components/FullColumnView';
import PresetTable from '../components/PresetTable';
import FeeVariancePanel from '../components/FeeVariancePanel';

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
  const [viewMode, setViewMode] = useState<'curated' | 'full'>('curated');

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

  // 注意: billsTab 是 JSX 变量不是组件 — 避免父组件 setState 时整树重挂载
  const billsTab = (
    <Space direction="vertical" style={{ width: '100%' }} size="middle">
      <Alert type="info" showIcon
        message="从万师傅后台按月导出 CSV，导入后用于「安装费对账」规则的应付口径。已同步飞书。" />

      <Segmented
        value={viewMode}
        onChange={(v) => setViewMode(v as 'curated' | 'full')}
        options={[
          { label: '精选视图', value: 'curated' },
          { label: '全部列', value: 'full' },
        ]}
      />
      {viewMode === 'full' && <FullColumnView entity="wanshifu_bill" />}
      {viewMode === 'curated' && (<>
      <Space wrap>
        <Upload accept=".csv,.xlsx,.xls" showUploadList={false} beforeUpload={handleImport}>
          <Button icon={<InboxOutlined />} loading={importing}>导入 CSV / Excel</Button>
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

      <PresetTable
        tableKey="wanshifu_bill"
        size="small"
        loading={isLoading}
        rowKey="id"
        dataSource={data}
        columns={columns}
        pagination={{ defaultPageSize: 100, showSizeChanger: true }}
        scroll={{ x: 700 }}
      />
      </>)}
    </Space>
  );

  return (
    <Space direction="vertical" style={{ width: '100%' }} size="middle">
      <Space align="center">
        <Typography.Title level={4} style={{ margin: 0 }}>万师傅</Typography.Title>
        <Tag color="blue">物流 · 安装</Tag>
      </Space>
      <Tabs
        defaultActiveKey="orders"
        destroyInactiveTabPane
        items={[
          { key: 'orders', label: '安装订单档案 (配对淘宝订单)', children: <WanshifuOrdersTab /> },
          { key: 'bills', label: '月结账单 (对账, 充值制可不用)', children: billsTab },
        ]}
      />
      <FeeVariancePanel url="/api/finance/install/variance" label="安装费" queryKey="install-variance" />
    </Space>
  );
}

interface WanshifuOrderRow {
  id: number;
  wsf_order_no: string;
  status: string | null;
  service_type: string | null;
  product_category: string | null;
  customer_name: string | null;
  customer_phone: string | null;
  region: string;
  address: string | null;
  net_amount: number | null;
  service_fee: number | null;
  created_time: string | null;
  matched_order_no: string | null;
  match_method: string | null;
  match_note: string | null;
}

// 安装订单档案: 万师傅后台「订单导出」xlsx (38列, 2026-06 起默认格式)。
// 表里没有淘宝订单号 — 导入后系统按 手机号/姓名+城市/物流单号 自动配对。
function WanshifuOrdersTab() {
  const qc = useQueryClient();
  const [importing, setImporting] = useState(false);
  const { data = [], isLoading } = useQuery<WanshifuOrderRow[]>({
    queryKey: ['wanshifu-orders'],
    queryFn: () => api.get('/api/finance/wanshifu-orders').then(r => r.data),
  });

  const handleImport = async (file: File) => {
    setImporting(true);
    const fd = new FormData();
    fd.append('file', file);
    try {
      const r = await api.post('/api/finance/wanshifu-orders/import', fd, {
        headers: { 'Content-Type': 'multipart/form-data' },
      });
      const m = r.data.match ?? {};
      message.success(
        `导入完成: 解析 ${r.data.parsed} 单, 新增 ${r.data.inserted}, 更新 ${r.data.updated}; ` +
        `自动配对 ${m.matched ?? 0} 单, 多候选 ${m.multi ?? 0}, 未匹配 ${m.none ?? 0}`,
      );
      qc.invalidateQueries({ queryKey: ['wanshifu-orders'] });
    } catch (e: any) {
      message.error(e?.response?.data?.detail ?? '导入失败');
    } finally {
      setImporting(false);
    }
    return false;
  };

  const exportAnnotated = async () => {
    const resp = await api.get('/api/finance/wanshifu-orders/export-annotated', { responseType: 'blob' });
    const url = window.URL.createObjectURL(resp.data as Blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = '万师傅订单_匹配批注.xlsx';
    document.body.appendChild(a);
    a.click();
    a.remove();
    window.URL.revokeObjectURL(url);
  };

  const matched = data.filter(r => r.matched_order_no).length;

  return (
    <Space direction="vertical" style={{ width: '100%' }} size="middle">
      <Alert type="info" showIcon
        message="万师傅表里没有淘宝订单号, 系统按 手机号 → 物流单号 → 姓名+城市 自动配对。"
        description="只有唯一命中才写订单号; 多候选/未匹配的看「批注」列, 可导出批注表人工核对。建议: 以后在万师傅下单时把淘宝订单号写进备注, 配对率可到 100%。配对成功的交易成功单自动分流: 安装费写进对应订单(固定成本), 维修/换件进售后表(变动成本)。" />
      <Space wrap>
        <Upload accept=".xlsx" showUploadList={false} beforeUpload={handleImport}>
          <Button icon={<InboxOutlined />} loading={importing}>导入订单导出 xlsx</Button>
        </Upload>
        <Button icon={<SyncOutlined />} onClick={async () => {
          const r = await api.post('/api/finance/wanshifu-orders/match');
          message.success(`重新配对: 配上 ${r.data.matched}, 多候选 ${r.data.multi}, 未匹配 ${r.data.none}`);
          qc.invalidateQueries({ queryKey: ['wanshifu-orders'] });
        }}>
          重新配对
        </Button>
        <Button icon={<DownloadOutlined />} onClick={exportAnnotated}>导出批注表</Button>
        {data.length > 0 && (
          <Typography.Text type="secondary">
            共 {data.length} 单 · 已配对 <strong>{matched}</strong> · 未配对 {data.length - matched}
          </Typography.Text>
        )}
      </Space>
      <Table<WanshifuOrderRow>
        rowKey="id"
        size="small"
        loading={isLoading}
        dataSource={data}
        pagination={{ defaultPageSize: 50, showSizeChanger: true }}
        scroll={{ x: 1100 }}
        columns={[
          { title: '万师傅单号', dataIndex: 'wsf_order_no', width: 130,
            render: (v: string) => <code style={{ fontSize: 11 }}>{v}</code> },
          { title: '下单时间', dataIndex: 'created_time', width: 100,
            render: (v: string | null) => v ? v.slice(0, 10) : '-' },
          { title: '状态', dataIndex: 'status', width: 110, ellipsis: true,
            render: (v: string | null) => v ? <Tag color={v === '交易成功' ? 'green' : v.includes('关闭') ? 'default' : 'orange'}>{v}</Tag> : '-' },
          { title: '服务', dataIndex: 'service_type', width: 130,
            // 用户拍板: 安装=固定成本(进订单安装费); 维修等=售后变动成本(进售后表)
            render: (v: string | null) => {
              if (!v) return '-';
              const isInstall = v.includes('安装');
              return (
                <Tag color={isInstall ? 'blue' : 'volcano'}>
                  {isInstall ? '安装 · 固定成本' : `售后(${v.split('|').pop()}) · 变动成本`}
                </Tag>
              );
            } },
          { title: '类别', dataIndex: 'product_category', width: 110, ellipsis: true },
          { title: '客户', dataIndex: 'customer_name', width: 80 },
          { title: '地区', dataIndex: 'region', width: 120, ellipsis: true },
          { title: '服务费', dataIndex: 'service_fee', width: 80, align: 'right' as const,
            render: (v: number | null) => v != null ? `¥${Math.round(v)}` : '-' },
          { title: '匹配订单', dataIndex: 'matched_order_no', width: 170,
            render: (v: string | null) => v
              ? <a href={`/orders?q=${v}`}><Tag color="green">{v}</Tag></a>
              : <Tag>未配对</Tag> },
          { title: '方式', dataIndex: 'match_method', width: 100 },
          { title: '批注', dataIndex: 'match_note', ellipsis: true },
        ]}
      />
    </Space>
  );
}
