import { useMemo, useState } from 'react';
import {
  Alert, Button, Checkbox, Input, InputNumber, Space, Statistic, Table, Tag,
  Typography, Upload, message,
} from 'antd';
import { DeleteOutlined, InboxOutlined, PlusOutlined, SaveOutlined, SyncOutlined } from '@ant-design/icons';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import {
  commitPackingBill, listPackingBills, packingSummary, parsePackingBill,
  type PackingRowParsed, type PackingBillRow,
} from '../api/screenshots';

const MATCH_LABEL: Record<string, { text: string; color: string }> = {
  order_no: { text: '单号匹配', color: 'green' },
  name_unique: { text: '客户名唯一', color: 'green' },
  multi: { text: '多候选待人工', color: 'orange' },
  manual: { text: '人工指定', color: 'blue' },
  none: { text: '未能自动匹配', color: 'red' },
};

function thisMonth(): string {
  return new Date().toISOString().slice(0, 7);
}

export default function PackingBillsPage() {
  const qc = useQueryClient();
  const [billMonth, setBillMonth] = useState<string>(thisMonth());
  const [parsing, setParsing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [rows, setRows] = useState<PackingRowParsed[]>([]);
  const [declaredTotal, setDeclaredTotal] = useState<number | null>(null);
  const [ocrWarnings, setOcrWarnings] = useState<string[]>([]);

  const { data: saved = [] } = useQuery<PackingBillRow[]>({
    queryKey: ['packing-bills', billMonth],
    queryFn: () => listPackingBills(billMonth),
  });
  const { data: summary } = useQuery({
    queryKey: ['packing-summary', billMonth],
    queryFn: () => packingSummary(billMonth),
  });

  const handleParse = async (file: File) => {
    setParsing(true);
    try {
      const r = await parsePackingBill(file);
      setRows(r.rows || []);
      setDeclaredTotal(r.declared_total ?? null);
      setOcrWarnings(r.ocr_warnings || []);
      message.success(`识别到 ${r.rows?.length ?? 0} 行，请逐行复核姓名/金额后再入库`);
    } catch (e: any) {
      message.error(e?.response?.data?.detail ?? '识别失败（OCR 主用+本地兜底都不可用？）');
    } finally {
      setParsing(false);
    }
    return false;
  };

  const setCell = (i: number, patch: Partial<PackingRowParsed>) =>
    setRows(rows.map((r, idx) => (idx === i ? { ...r, ...patch } : r)));
  const delRow = (i: number) => setRows(rows.filter((_, idx) => idx !== i));
  const addRow = () => setRows([...rows, { customer_name: '', packing_fee: null, excluded: false }]);

  const previewTotals = useMemo(() => {
    const payable = rows.filter(r => !r.excluded).reduce((s, r) => s + Number(r.packing_fee || 0), 0);
    const excluded = rows.filter(r => r.excluded).reduce((s, r) => s + Number(r.packing_fee || 0), 0);
    return { payable: +payable.toFixed(2), excluded: +excluded.toFixed(2), cnt: rows.length };
  }, [rows]);

  // 本子「合计」vs 各行相加(未剔除) 是否相符
  const declaredDiff = declaredTotal != null ? +(declaredTotal - previewTotals.payable).toFixed(2) : null;

  const handleCommit = async () => {
    if (!rows.length) { message.warning('没有可入库的行'); return; }
    setSaving(true);
    try {
      const r = await commitPackingBill({ bill_month: billMonth, rows });
      message.success(
        `入库 ${r.inserted} 行（去重 ${r.skipped}）· 配单 ${r.matched} · 剔除 ${r.excluded} · ` +
        `当月应付 ¥${r.payable_total.toFixed(2)}`);
      setRows([]);
      setDeclaredTotal(null);
      setOcrWarnings([]);
      qc.invalidateQueries({ queryKey: ['packing-bills', billMonth] });
      qc.invalidateQueries({ queryKey: ['packing-summary', billMonth] });
    } catch (e: any) {
      message.error(e?.response?.data?.detail ?? '入库失败');
    } finally {
      setSaving(false);
    }
  };

  const previewColumns = [
    { title: '日期', width: 120, render: (_: any, r: PackingRowParsed, i: number) => (
      <Input size="small" placeholder="2026-06-05" value={r.row_date ?? ''}
        onChange={e => setCell(i, { row_date: e.target.value })} />) },
    { title: '客户/收货人', width: 130, render: (_: any, r: PackingRowParsed, i: number) => (
      <Input size="small" value={r.customer_name ?? ''} status={!r.customer_name ? 'warning' : undefined}
        onChange={e => setCell(i, { customer_name: e.target.value })} />) },
    { title: '订单号(可空)', width: 150, render: (_: any, r: PackingRowParsed, i: number) => (
      <Input size="small" value={r.order_no ?? ''}
        onChange={e => setCell(i, { order_no: e.target.value })} />) },
    { title: '打包费', width: 100, render: (_: any, r: PackingRowParsed, i: number) => (
      <InputNumber size="small" min={0} value={r.packing_fee ?? undefined} style={{ width: '100%' }}
        onChange={v => setCell(i, { packing_fee: v as number })} />) },
    { title: '不计入', width: 120, render: (_: any, r: PackingRowParsed, i: number) => (
      <Checkbox checked={!!r.excluded} onChange={e => setCell(i, { excluded: e.target.checked })}>
        {r.exclude_reason ? <Tag color="red">{r.exclude_reason}</Tag> : '剔除'}
      </Checkbox>) },
    { title: '把握', width: 70, render: (_: any, r: PackingRowParsed) =>
      r.confidence != null
        ? <Tag color={r.confidence >= 0.8 ? 'green' : 'orange'}>{Math.round(r.confidence * 100)}%</Tag>
        : '-' },
    { title: '备注', render: (_: any, r: PackingRowParsed, i: number) => (
      <Input size="small" value={r.note ?? ''} onChange={e => setCell(i, { note: e.target.value })} />) },
    { title: '', width: 40, render: (_: any, __: PackingRowParsed, i: number) => (
      <Button size="small" type="text" danger icon={<DeleteOutlined />} onClick={() => delRow(i)} />) },
  ];

  const savedColumns = [
    { title: '日期', dataIndex: 'row_date', width: 100, render: (v: string | null) => v || '-' },
    { title: '客户', dataIndex: 'customer_name', width: 110, render: (v: string | null) => v || '-' },
    { title: '打包费', dataIndex: 'packing_fee', width: 90, align: 'right' as const,
      render: (v: number | null, r: PackingBillRow) => (
        <span style={{ color: r.excluded ? '#bbb' : '#cf1322', textDecoration: r.excluded ? 'line-through' : undefined }}>
          ¥{Number(v ?? 0).toFixed(2)}</span>) },
    { title: '配单', dataIndex: 'matched_order_no', width: 190, render: (v: string | null, r: PackingBillRow) => {
      const m = r.match_method ? MATCH_LABEL[r.match_method] : null;
      if (v) return <Space size={4}><Typography.Text style={{ fontSize: 12 }} copyable={{ text: v }}>{v}</Typography.Text>{m && <Tag color={m.color}>{m.text}</Tag>}</Space>;
      return m ? <Tag color={m.color} title={r.match_note ?? ''}>{m.text}</Tag> : '-';
    } },
    { title: '剔除', dataIndex: 'excluded', width: 110, render: (v: boolean, r: PackingBillRow) =>
      v ? <Tag color="red">{r.exclude_reason || '不计入'}</Tag> : <Tag color="green">计入</Tag> },
    { title: '备注', dataIndex: 'note', ellipsis: true },
  ];

  return (
    <Space direction="vertical" style={{ width: '100%' }} size="middle">
      <Space align="center">
        <Typography.Title level={4} style={{ margin: 0 }}>打包费手写账单</Typography.Title>
        <Tag color="gold">打包</Tag>
        <Input addonBefore="账期" value={billMonth} style={{ width: 180 }}
          placeholder="2026-06" onChange={e => setBillMonth(e.target.value)} />
      </Space>

      <Alert type="warning" showIcon
        message="手写中文姓名识别准确率约 60–80%，识别后请务必逐行核对姓名与金额再入库。"
        description="本子上标了「改客户/不计入/作废」的行会自动勾上「不计入」并从应付总额里剔除；可手动改。配单按订单号优先、否则客户名唯一匹配（自动排除关闭单）。" />

      <Upload accept="image/*" showUploadList={false} beforeUpload={handleParse}>
        <Button type="primary" icon={<InboxOutlined />} loading={parsing}>上传手写账单照片识别</Button>
      </Upload>

      {ocrWarnings.length > 0 && (
        <Alert type="info" showIcon message="识别提示" description={<ul style={{ margin: 0, paddingLeft: 18 }}>{ocrWarnings.map((w, i) => <li key={i}>{w}</li>)}</ul>} />
      )}

      {rows.length > 0 && (
        <Space direction="vertical" style={{ width: '100%' }} size="small">
          <Space wrap>
            <Statistic title="应付(未剔除)" value={previewTotals.payable} prefix="¥" valueStyle={{ fontSize: 18 }} />
            <Statistic title="剔除金额" value={previewTotals.excluded} prefix="¥" valueStyle={{ fontSize: 18, color: '#bbb' }} />
            {declaredTotal != null && (
              <Statistic title="本子合计" value={declaredTotal} prefix="¥" valueStyle={{ fontSize: 18 }} />
            )}
            {declaredDiff != null && (
              <Tag color={Math.abs(declaredDiff) < 0.01 ? 'success' : 'error'} style={{ alignSelf: 'center', height: 24, lineHeight: '22px' }}>
                {Math.abs(declaredDiff) < 0.01 ? '✓ 与本子合计相符' : `✗ 与本子差 ¥${declaredDiff.toFixed(2)}`}
              </Tag>
            )}
            <Button icon={<PlusOutlined />} onClick={addRow}>加一行</Button>
            <Button type="primary" icon={<SaveOutlined />} loading={saving} onClick={handleCommit}>确认入库 ({rows.length} 行)</Button>
          </Space>
          <Table rowKey={(_, i) => `pre-${i}`} size="small" pagination={false}
            dataSource={rows} columns={previewColumns as any} scroll={{ x: 900 }} />
        </Space>
      )}

      <Space align="center" style={{ marginTop: 8 }}>
        <Typography.Title level={5} style={{ margin: 0 }}>本月已入库 ({billMonth})</Typography.Title>
        <Button size="small" icon={<SyncOutlined />}
          onClick={() => { qc.invalidateQueries({ queryKey: ['packing-bills', billMonth] }); qc.invalidateQueries({ queryKey: ['packing-summary', billMonth] }); }}>刷新</Button>
        {summary && (
          <Typography.Text type="secondary">
            应付 <strong style={{ color: '#cf1322' }}>¥{summary.payable_total.toFixed(2)}</strong>
            · 剔除 ¥{summary.excluded_total.toFixed(2)} ({summary.excluded_rows}行)
            {summary.unmatched_rows > 0 && <span style={{ color: '#cf1322' }}> · 未配单 {summary.unmatched_rows}</span>}
          </Typography.Text>
        )}
      </Space>
      <Table rowKey="id" size="small" dataSource={saved} columns={savedColumns}
        pagination={{ defaultPageSize: 50 }} scroll={{ x: 760 }} />
    </Space>
  );
}
