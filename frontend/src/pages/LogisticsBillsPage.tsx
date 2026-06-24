import { useMemo, useState } from 'react';
import { Alert, Button, Input, Segmented, Space, Table, Tag, Typography, Upload, message } from 'antd';
import { DownloadOutlined, InboxOutlined } from '@ant-design/icons';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '../api/client';
import FullColumnView from '../components/FullColumnView';
import PresetTable from '../components/PresetTable';
import FeeVariancePanel from '../components/FeeVariancePanel';

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
  order_customer_name: string | null;
  order_customer_address: string | null;
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
  name_addr: { text: '姓名+地址', color: 'cyan' },
  multi: { text: '多候选待人工', color: 'orange' },
  manual: { text: '人工指定', color: 'blue' },
  none: { text: '未能自动匹配', color: 'red' },
};

function ym(d: string | null): string {
  return d ? d.slice(0, 7) : '未知月';
}

// 逐行可编辑订单号 — 人工核对/纠正匹配。填=人工指定(manual); 空=取消匹配。
function OrderNoCell({ row, onSaved }: { row: LogisticsBill; onSaved: () => void }) {
  const [editing, setEditing] = useState(false);
  const [val, setVal] = useState(row.order_no ?? '');
  const [saving, setSaving] = useState(false);
  const m = row.match_method ? METHOD_LABEL[row.match_method] : null;
  if (row.row_type === 'summary') return <Typography.Text type="secondary">-</Typography.Text>;
  const save = async () => {
    setSaving(true);
    try {
      await api.patch(`/api/finance/logistics-bills/${row.id}/match`,
        { order_no: val.trim() || null });
      message.success(val.trim() ? '已指定订单号' : '已取消匹配');
      setEditing(false);
      onSaved();
    } catch (e: any) {
      message.error(e?.response?.data?.detail ?? '保存失败');
    } finally {
      setSaving(false);
    }
  };
  if (editing) {
    return (
      <Space.Compact style={{ width: '100%' }}>
        <Input size="small" value={val} autoFocus placeholder="订单号(留空=取消匹配)"
          onChange={e => setVal(e.target.value)} onPressEnter={save} style={{ width: 180 }} />
        <Button size="small" type="primary" loading={saving} onClick={save}>存</Button>
        <Button size="small" onClick={() => { setEditing(false); setVal(row.order_no ?? ''); }}>×</Button>
      </Space.Compact>
    );
  }
  return (
    <Space size={4}>
      {row.order_no
        ? <Typography.Text copyable={{ text: row.order_no }} style={{ fontSize: 12 }}>{row.order_no}</Typography.Text>
        : <Typography.Text type="secondary">未匹配</Typography.Text>}
      {m && <Tag color={m.color} title={row.match_note ?? ''} style={{ marginInlineEnd: 0 }}>{m.text}</Tag>}
      <Button size="small" type="link" style={{ padding: 0 }}
        onClick={() => { setVal(row.order_no ?? ''); setEditing(true); }}>改</Button>
    </Space>
  );
}

// 客户端生成 CSV 下载 (带 BOM, Excel 中文不乱码)
export function downloadCsv(filename: string, headers: string[], rows: (string | number)[][]) {
  const esc = (v: string | number) => `"${String(v ?? '').replace(/"/g, '""')}"`;
  const csv = [headers.map(esc).join(','), ...rows.map(r => r.map(esc).join(','))].join('\r\n');
  const blob = new Blob(['﻿' + csv], { type: 'text/csv;charset=utf-8' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = filename;
  a.click();
  URL.revokeObjectURL(a.href);
}

export default function LogisticsBillsPage() {
  const qc = useQueryClient();
  const [importing, setImporting] = useState(false);
  const [viewMode, setViewMode] = useState<'curated' | 'full'>('curated');
  const [reviewFilter, setReviewFilter] = useState<'all' | 'todo' | 'done'>('all');

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

  // 导入账单: 一个按钮通吃 CSV/Excel, 按扩展名走对应导入(系统自动识别承运商 + 导入后自动配单)
  const handleImportAny = (file: File) => {
    const n = file.name.toLowerCase();
    return n.endsWith('.csv') ? handleImport(file) : handleImportXlsx(file);
  };

  // 导出「未能匹配」的逐单 → CSV (供人工补订单号)
  const handleExportUnmatched = () => {
    const un = lineRows.filter(r => !r.order_no && (r.match_method === 'none' || r.match_method === 'multi'));
    if (!un.length) { message.info('没有未匹配的逐单，全部配上了'); return; }
    downloadCsv('物流账单_未匹配.csv',
      ['账单日期', '承运商', '运单号', '收货人', '目的地', '运费', '匹配情况', '请填订单号'],
      un.map(r => [r.bill_date ?? '', r.carrier ?? '', r.tracking_no ?? '', r.recipient_name ?? '',
        r.destination ?? '', Number(r.freight_amount).toFixed(2),
        r.match_method === 'multi' ? '多候选待人工' : '未能匹配', '']));
    message.success(`已导出 ${un.length} 条未匹配，填好订单号后可人工核对`);
  };

  // 逐单行 (line) 与月结汇总行 (summary) 分开: 逐单进主表, 汇总挪表底
  const lineRows = useMemo(() => data.filter(r => r.row_type !== 'summary'), [data]);
  const summaryRows = useMemo(() => data.filter(r => r.row_type === 'summary'), [data]);
  // 待核查 = 未匹配/多候选; 已匹配 = 有订单号 (含人工/自动, 供复核收货人是否对得上)
  const displayRows = useMemo(() => {
    if (reviewFilter === 'todo') return lineRows.filter(r => !r.order_no);
    if (reviewFilter === 'done') return lineRows.filter(r => !!r.order_no);
    return lineRows;
  }, [lineRows, reviewFilter]);

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
    { title: '订单号 / 匹配 (可改)', dataIndex: 'order_no', width: 250,
      render: (_: string | null, row: LogisticsBill) =>
        <OrderNoCell row={row} onSaved={() => qc.invalidateQueries({ queryKey: ['logistics-bills'] })} /> },
    { title: '匹配到的订单客户 (核对)', dataIndex: 'order_customer_name', width: 200,
      render: (v: string | null, row: LogisticsBill) => {
        if (row.row_type === 'summary' || !row.order_no) return <Typography.Text type="secondary">-</Typography.Text>;
        if (!v) return <Tag color="warning">订单库无此单</Tag>;
        return (
          <div style={{ fontSize: 12, lineHeight: 1.3 }}>
            <div>{v}</div>
            <Typography.Text type="secondary" style={{ fontSize: 11 }} ellipsis title={row.order_customer_address ?? ''}>
              {row.order_customer_address ?? ''}
            </Typography.Text>
          </div>
        );
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
        message="德邦 / 壹米滴答逐单账单导入后自动按『运单号 / 收货人+省市』配淘宝订单。点订单号旁「改」可人工指定/纠正匹配（填=人工指定，空=取消）；右侧「匹配到的订单客户」用来核对收货人/目的地是否真对得上。配单表带『匹配订单号』列的，导入时直接采用。月结汇总行挪到表底核对总额。" />

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
        <Upload accept=".csv,.xlsx,.xls" multiple showUploadList={false} beforeUpload={handleImportAny}>
          <Button type="primary" icon={<InboxOutlined />} loading={importing}>导入账单 (壹米滴答/德邦，CSV/Excel 均可)</Button>
        </Upload>
        <Button icon={<DownloadOutlined />} onClick={handleExportUnmatched}>
          导出未匹配
        </Button>
        {lineRows.length > 0 && (
          <Segmented
            value={reviewFilter}
            onChange={(v) => setReviewFilter(v as 'all' | 'todo' | 'done')}
            options={[
              { label: `全部 ${lineRows.length}`, value: 'all' },
              { label: `待核查 ${lineRows.filter(r => !r.order_no).length}`, value: 'todo' },
              { label: `已匹配 ${lineRows.filter(r => !!r.order_no).length}`, value: 'done' },
            ]}
          />
        )}
        {lineRows.length > 0 && (
          <Typography.Text type="secondary">
            逐单 {lineRows.length} 条 · 合计运费 <strong>¥{lineTotal.toLocaleString('zh', { minimumFractionDigits: 2 })}</strong>
            {unmatched > 0 && <span style={{ color: '#cf1322' }}> · 待人工 {unmatched} 单（导入时已自动配单）</span>}
          </Typography.Text>
        )}
      </Space>

      <PresetTable
        tableKey="logistics_bill"
        size="small"
        loading={isLoading}
        rowKey="id"
        dataSource={displayRows}
        columns={columns}
        pagination={{ defaultPageSize: 100, showSizeChanger: true }}
        scroll={{ x: 1200 }}
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

      <FeeVariancePanel url="/api/finance/logistics-bills/variance" label="物流费" queryKey="logistics-variance" />
      </>)}
    </Space>
  );
}
