import { useMemo, useState } from 'react';
import { Alert, Button, Segmented, Space, Table, Tag, Typography, Upload, message } from 'antd';
import { DownloadOutlined, InboxOutlined, LinkOutlined, SyncOutlined } from '@ant-design/icons';
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
  recipient_name: string | null;
  destination: string | null;
  match_method: string | null;
  match_note: string | null;
  row_type: string;
}

interface ImportResult {
  inserted: number;
  skipped_invalid: number;
  errors: string[];
}

const METHOD_LABEL: Record<string, { text: string; color: string }> = {
  track: { text: '运单号匹配', color: 'green' },
  name_prov: { text: '姓名+省市', color: 'green' },
  name_unique: { text: '姓名唯一', color: 'cyan' },
  multi: { text: '多候选待人工', color: 'orange' },
  manual: { text: '人工指定', color: 'blue' },
  none: { text: '未能自动匹配', color: 'red' },
};

function ym(d: string | null): string {
  return d ? d.slice(0, 7) : '未知月';
}

export default function LogisticsBillsPage() {
  const qc = useQueryClient();
  const [importing, setImporting] = useState(false);
  const [matching, setMatching] = useState(false);
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
      message.success(`导入成功：新增 ${r.data.inserted} 条，去重 ${dup} 条，跳过 ${r.data.skipped_invalid} 条（已自动配单）`);
      qc.invalidateQueries({ queryKey: ['logistics-bills'] });
    } catch (e: any) {
      message.error(e?.response?.data?.detail ?? 'xlsx 导入失败');
    } finally {
      setImporting(false);
    }
    return false;
  };

  // 手动重新配单 (运单号 / 收货人+省市 → 订单号)
  const handleMatch = async () => {
    setMatching(true);
    try {
      const r = await api.post<{ matched: number; multi: number; none: number }>(
        '/api/finance/logistics-bills/match');
      message.success(`配单完成：命中 ${r.data.matched} 单，多候选 ${r.data.multi} 单，未能匹配 ${r.data.none} 单`);
      qc.invalidateQueries({ queryKey: ['logistics-bills'] });
    } catch (e: any) {
      message.error(e?.response?.data?.detail ?? '配单失败');
    } finally {
      setMatching(false);
    }
  };

  // 逐单行 (line) 与月结汇总行 (summary) 分开: 逐单进主表, 汇总挪表底
  const lineRows = useMemo(() => data.filter(r => r.row_type !== 'summary'), [data]);
  const summaryRows = useMemo(() => data.filter(r => r.row_type === 'summary'), [data]);

  // 核对: 每个 (承运商, 月) 的逐单相加 vs 月结汇总行声明的总额是否相等
  const crossCheck = useMemo(() => {
    const lineSum = new Map<string, { sum: number; cnt: number }>();
    for (const r of lineRows) {
      const k = `${r.carrier ?? '?'}|${ym(r.bill_date)}`;
      const cur = lineSum.get(k) ?? { sum: 0, cnt: 0 };
      cur.sum += Number(r.freight_amount);
      cur.cnt += 1;
      lineSum.set(k, cur);
    }
    return summaryRows.map(s => {
      const k = `${s.carrier ?? '?'}|${ym(s.bill_date)}`;
      const line = lineSum.get(k) ?? { sum: 0, cnt: 0 };
      const declared = Number(s.freight_amount);
      const diff = +(declared - line.sum).toFixed(2);
      return {
        key: s.id, carrier: s.carrier, month: ym(s.bill_date),
        declared, lineSum: +line.sum.toFixed(2), lineCnt: line.cnt, diff,
        equal: line.cnt > 0 && Math.abs(diff) < 0.01,
        hasLines: line.cnt > 0,
      };
    });
  }, [lineRows, summaryRows]);

  const columns = [
    { title: '账单日期', dataIndex: 'bill_date', width: 110 },
    { title: '承运商', dataIndex: 'carrier', width: 90,
      render: (v: string | null) => v ? <Tag>{v}</Tag> : '-' },
    { title: '运单号', dataIndex: 'tracking_no', width: 150, ellipsis: true },
    { title: '收货人', dataIndex: 'recipient_name', width: 80, render: (v: string | null) => v || '-' },
    { title: '目的地', dataIndex: 'destination', width: 130, ellipsis: true, render: (v: string | null) => v || '-' },
    { title: '订单号 / 匹配', dataIndex: 'order_no', width: 200,
      render: (v: string | null, row: LogisticsBill) => {
        const m = row.match_method ? METHOD_LABEL[row.match_method] : null;
        if (v) {
          return (
            <Space size={4}>
              <Typography.Text copyable={{ text: v }} style={{ fontSize: 12 }}>{v}</Typography.Text>
              {m && <Tag color={m.color} style={{ marginInlineEnd: 0 }}>{m.text}</Tag>}
            </Space>
          );
        }
        if (m) {
          return <Tag color={m.color} title={row.match_note ?? ''}>{m.text}</Tag>;
        }
        return <Typography.Text type="secondary">-</Typography.Text>;
      } },
    { title: '重量(kg)', dataIndex: 'weight_kg', width: 80, align: 'right' as const,
      render: (v: number | string | null) => v != null ? Number(v).toFixed(3) : '-' },
    { title: '运费', dataIndex: 'freight_amount', width: 90, align: 'right' as const,
      render: (v: number) => <span style={{ color: '#cf1322' }}>¥{Number(v).toFixed(2)}</span> },
    { title: '备注', dataIndex: 'remark', ellipsis: true },
  ];

  const lineTotal = lineRows.reduce((s, r) => s + Number(r.freight_amount), 0);
  const unmatched = lineRows.filter(r => !r.order_no && (r.match_method === 'none' || r.match_method === 'multi')).length;

  return (
    <Space direction="vertical" style={{ width: '100%' }} size="middle">
      <Space align="center">
        <Typography.Title level={4} style={{ margin: 0 }}>物流费账单</Typography.Title>
        <Tag color="cyan">物流</Tag>
      </Space>

      <Alert type="info" showIcon
        message="德邦逐单账单导入后自动按『运单号 / 收货人+省市』配淘宝订单；配不到的显示「未能自动匹配」。月结汇总行挪到表底，并核对『月结总额 vs 各单相加』。已同步飞书。" />

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
        <Button icon={<LinkOutlined />} loading={matching} onClick={handleMatch}>
          重新配单
        </Button>
        <Button icon={<DownloadOutlined />}
          onClick={() => window.open('/api/finance/logistics-bills/template.csv')}>
          下载模板
        </Button>
        <Button icon={<SyncOutlined />} onClick={() => qc.invalidateQueries({ queryKey: ['logistics-bills'] })}>
          刷新
        </Button>
        {lineRows.length > 0 && (
          <Typography.Text type="secondary">
            逐单 {lineRows.length} 条 · 合计运费 <strong>¥{lineTotal.toLocaleString('zh', { minimumFractionDigits: 2 })}</strong>
            {unmatched > 0 && <span style={{ color: '#cf1322' }}> · 待人工 {unmatched} 单</span>}
          </Typography.Text>
        )}
      </Space>

      <PresetTable
        tableKey="logistics_bill"
        size="small"
        loading={isLoading}
        rowKey="id"
        dataSource={lineRows}
        columns={columns}
        pagination={{ defaultPageSize: 100, showSizeChanger: true }}
        scroll={{ x: 1000 }}
      />

      {/* 月结汇总 + 核对: 挪到表格最下方 */}
      {summaryRows.length > 0 && (
        <Space direction="vertical" style={{ width: '100%' }} size="small">
          <Typography.Title level={5} style={{ margin: '8px 0 0' }}>月结汇总核对</Typography.Title>
          <Table
            size="small"
            rowKey="key"
            pagination={false}
            dataSource={crossCheck}
            columns={[
              { title: '承运商', dataIndex: 'carrier', width: 100, render: (v) => v ? <Tag>{v}</Tag> : '-' },
              { title: '月份', dataIndex: 'month', width: 100 },
              { title: '月结账单总额', dataIndex: 'declared', align: 'right' as const,
                render: (v: number) => <strong style={{ color: '#cf1322' }}>¥{v.toFixed(2)}</strong> },
              { title: '逐单相加', dataIndex: 'lineSum', align: 'right' as const,
                render: (v: number, row: any) => row.hasLines
                  ? <span>¥{v.toFixed(2)} <Typography.Text type="secondary">({row.lineCnt}单)</Typography.Text></span>
                  : <Typography.Text type="secondary">无逐单明细</Typography.Text> },
              { title: '差额', dataIndex: 'diff', align: 'right' as const,
                render: (v: number, row: any) => !row.hasLines
                  ? '-'
                  : <span style={{ color: row.equal ? '#389e0d' : '#cf1322', fontWeight: 600 }}>¥{v.toFixed(2)}</span> },
              { title: '核对', dataIndex: 'equal', width: 140,
                render: (_: boolean, row: any) => !row.hasLines
                  ? <Tag color="default">仅总额(无逐单可核)</Tag>
                  : row.equal
                    ? <Tag color="success">✓ 相符</Tag>
                    : <Tag color="error">✗ 不符，请核查</Tag> },
            ]}
          />
        </Space>
      )}
      </>)}
    </Space>
  );
}
