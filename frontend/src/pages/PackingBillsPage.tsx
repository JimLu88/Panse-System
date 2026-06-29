import { useEffect, useMemo, useState } from 'react';
import {
  Alert, Button, Checkbox, DatePicker, Input, InputNumber, Modal, Popconfirm, Select, Space, Statistic, Table, Tag,
  Typography, Upload, message,
} from 'antd';
import { DeleteOutlined, DownloadOutlined, EditOutlined, InboxOutlined, PlusOutlined, ReloadOutlined, SaveOutlined } from '@ant-design/icons';
import dayjs from 'dayjs';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import {
  commitPackingBill, listPackingBills, packingSummary, parsePackingBill, updatePackingBill,
  deletePackingBill, packingMatchCandidates,
  type PackingRowParsed, type PackingBillRow, type PackingCandidate,
} from '../api/screenshots';
import { downloadCsv } from './LogisticsBillsPage';
import FeeVariancePanel from '../components/FeeVariancePanel';

const MATCH_LABEL: Record<string, { text: string; color: string }> = {
  order_no: { text: '单号匹配', color: 'green' },
  name_unique: { text: '客户名唯一', color: 'green' },
  name_addr: { text: '姓名+地址', color: 'cyan' },
  multi: { text: '多候选待人工', color: 'orange' },
  manual: { text: '人工指定', color: 'blue' },
  none: { text: '未能自动匹配', color: 'red' },
};

function thisMonth(): string {
  return new Date().toISOString().slice(0, 7);
}

export default function PackingBillsPage() {
  const qc = useQueryClient();
  const [billMonth, setBillMonth] = useState<string>('');
  const [parsing, setParsing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [rows, setRows] = useState<PackingRowParsed[]>([]);
  const [declaredTotal, setDeclaredTotal] = useState<number | null>(null);
  const [ocrWarnings, setOcrWarnings] = useState<string[]>([]);
  // 已入库行的手动编辑 (用户 2026-06-24): 改 客户名/打包费/配单
  const [editRow, setEditRow] = useState<PackingBillRow | null>(null);
  const [eName, setEName] = useState('');
  const [eFee, setEFee] = useState<number | null>(null);
  const [eOrder, setEOrder] = useState('');
  const [eNote, setENote] = useState('');             // 备注 (用户 2026-06-29)
  const [eBillMonth, setEBillMonth] = useState('');   // 改账期 YYYY-MM (错填月份时挪正确账期)
  const [savingEdit, setSavingEdit] = useState(false);
  const [cands, setCands] = useState<PackingCandidate[]>([]);
  const [candLoading, setCandLoading] = useState(false);

  const loadCands = async (id: number, nameOverride?: string) => {
    setCandLoading(true);
    try { setCands(await packingMatchCandidates(id, nameOverride, 5)); }
    catch { setCands([]); }
    finally { setCandLoading(false); }
  };
  const openEdit = (r: PackingBillRow) => {
    setEditRow(r);
    setEName(r.customer_name ?? '');
    setEFee(r.packing_fee ?? null);
    setEOrder(r.matched_order_no ?? r.order_no ?? '');
    setENote(r.note ?? '');
    setEBillMonth(r.bill_month ?? '');
    setCands([]);
    loadCands(r.id);   // 进来就按客户名列候选订单(下拉自选)
  };

  const saveEdit = async () => {
    if (!editRow) return;
    setSavingEdit(true);
    try {
      const o = eOrder.trim();
      // 填了订单号=人工配到该单; 留空=清空配单。不再自动按名乱配(用户反馈会配错/配到多单)。
      const patch: Parameters<typeof updatePackingBill>[1] =
        { customer_name: eName, packing_fee: eFee, matched_order_no: o, note: eNote };
      const bm = eBillMonth.trim();
      if (bm && bm !== (editRow.bill_month ?? '')) patch.bill_month = bm;  // 改了账期才传
      await updatePackingBill(editRow.id, patch);
      message.success(o ? '已保存并配单' : '已保存');
      setEditRow(null);
      qc.invalidateQueries({ queryKey: ['packing-bills', billMonth] });
      qc.invalidateQueries({ queryKey: ['packing-summary', billMonth] });
      qc.invalidateQueries({ queryKey: ['packing-all'] });
    } catch (e: any) {
      message.error(e?.response?.data?.detail ?? '保存失败');
    } finally {
      setSavingEdit(false);
    }
  };

  // 删除一行打包费账单 (用户 2026-06-29): 清理重复导入/错行; 删后回退该单实际打包费
  const handleDelete = async (r: PackingBillRow) => {
    try {
      await deletePackingBill(r.id);
      message.success('已删除该行');
      qc.invalidateQueries({ queryKey: ['packing-bills', billMonth] });
      qc.invalidateQueries({ queryKey: ['packing-summary', billMonth] });
      qc.invalidateQueries({ queryKey: ['packing-all'] });
    } catch (e: any) {
      message.error(e?.response?.data?.detail ?? '删除失败');
    }
  };


  const { data: saved = [] } = useQuery<PackingBillRow[]>({
    queryKey: ['packing-bills', billMonth],
    queryFn: () => listPackingBills(billMonth),
    enabled: !!billMonth,
  });
  const { data: summary } = useQuery({
    queryKey: ['packing-summary', billMonth],
    queryFn: () => packingSummary(billMonth),
    enabled: !!billMonth,
  });

  // 有数据的账期(默认跳到最近有数据的月, 免得停在空白当月)
  const { data: allBills = [], isFetched: allFetched } = useQuery<PackingBillRow[]>({
    queryKey: ['packing-all'],
    queryFn: () => listPackingBills(),
  });
  const availableMonths = useMemo(() => {
    const s = new Set<string>();
    allBills.forEach(b => { if (b.bill_month) s.add(b.bill_month); });
    return Array.from(s).sort().reverse();
  }, [allBills]);
  // 自动进入: 优先停在「最近有数据的月」; 数据还没拉到时先别退回空白当月(否则会卡在没数据的当月) (用户 2026-06-24)
  useEffect(() => {
    if (billMonth) return;
    if (availableMonths.length > 0) setBillMonth(availableMonths[0]);
    else if (allFetched) setBillMonth(thisMonth());
  }, [availableMonths, billMonth, allFetched]);

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

  // 导出「未配单」行 → CSV (供人工补订单号)
  const handleExportUnmatched = () => {
    const un = saved.filter(r => !r.matched_order_no && !r.excluded);
    if (!un.length) { message.info('没有未配单的行'); return; }
    downloadCsv(`打包费账单_未匹配_${billMonth}.csv`,
      ['账期', '日期', '客户', '打包费', '备注', '请填订单号'],
      un.map(r => [r.bill_month ?? '', r.row_date ?? '', r.customer_name ?? '',
        Number(r.packing_fee ?? 0).toFixed(2), r.note ?? '', '']));
    message.success(`已导出 ${un.length} 条未配单，填好订单号后可人工核对`);
  };

  // 导出所有月份账单 (用户 2026-06-24): 含手写识别的客户/打包费/配单/匹配方式, 离线逐行核对
  const handleExportAll = () => {
    if (!allBills.length) { message.info('暂无账单数据'); return; }
    const sorted = [...allBills].sort((a, b) =>
      (b.bill_month ?? '').localeCompare(a.bill_month ?? '')
      || (b.row_date ?? '').localeCompare(a.row_date ?? ''));
    downloadCsv('打包费账单_全部月份.csv',
      ['账期', '日期', '客户', '打包费', '配单订单号', '匹配方式', '是否计入', '剔除原因', '备注'],
      sorted.map(r => [
        r.bill_month ?? '', r.row_date ?? '', r.customer_name ?? '',
        Number(r.packing_fee ?? 0).toFixed(2),
        r.matched_order_no ?? '',
        r.match_method ? (MATCH_LABEL[r.match_method]?.text ?? r.match_method) : '',
        r.excluded ? '不计入' : '计入',
        r.exclude_reason ?? '',
        r.note ?? '',
      ]));
    message.success(`已导出全部 ${sorted.length} 条打包费账单（${availableMonths.length} 个月）`);
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
      const r = await commitPackingBill({
        bill_month: billMonth, declared_total: declaredTotal ?? undefined, rows });
      message.success(
        `入库 ${r.inserted} 行（去重 ${r.skipped}）· 配单 ${r.matched} · 剔除 ${r.excluded} · ` +
        `当月应付 ¥${r.payable_total.toFixed(2)}`);
      if (declaredTotal != null && Math.abs(declaredTotal - r.payable_total) > 0.5) {
        message.warning(`本子合计 ¥${declaredTotal} 与系统应付 ¥${r.payable_total.toFixed(0)} 对不上，已挂异常待核对`);
      }
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
    { title: '操作', dataIndex: 'op', width: 140, fixed: 'right' as const, render: (_: any, r: PackingBillRow) => (
      <Space size={4}>
        <Button size="small" icon={<EditOutlined />} onClick={() => openEdit(r)}>编辑</Button>
        <Popconfirm title="删除这行打包费?" description="删后会回退该单的实际打包费, 不可撤销"
          okText="删除" okButtonProps={{ danger: true }} cancelText="取消"
          onConfirm={() => handleDelete(r)}>
          <Button size="small" type="text" danger icon={<DeleteOutlined />} />
        </Popconfirm>
      </Space>) },
  ];

  return (
    <Space direction="vertical" style={{ width: '100%' }} size="middle">
      <Space align="center">
        <Typography.Title level={4} style={{ margin: 0 }}>打包费手写账单</Typography.Title>
        <Tag color="gold">打包</Tag>
        <Typography.Text type="secondary">账期</Typography.Text>
        <DatePicker picker="month" allowClear={false} placeholder="选择月份" style={{ width: 140 }}
          value={billMonth ? dayjs(billMonth + '-01') : null}
          onChange={(d) => setBillMonth(d ? d.format('YYYY-MM') : '')} />
        {availableMonths.length > 0 && (
          <Space size={4}>
            <Typography.Text type="secondary">有数据:</Typography.Text>
            {availableMonths.map(m => (
              <Tag key={m} color={m === billMonth ? 'blue' : 'default'}
                style={{ cursor: 'pointer' }} onClick={() => setBillMonth(m)}>{m}</Tag>
            ))}
          </Space>
        )}
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
        <Button size="small" icon={<DownloadOutlined />} onClick={handleExportUnmatched}>导出未匹配</Button>
        <Button size="small" icon={<DownloadOutlined />} onClick={handleExportAll}>导出所有月账单</Button>
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

      <FeeVariancePanel url="/api/finance/packing-bills/variance" label="打包费" queryKey="packing-variance" />

      <Modal
        title={`编辑打包费 · ${editRow?.row_date ?? ''}`}
        open={!!editRow}
        onCancel={() => setEditRow(null)}
        onOk={saveEdit}
        confirmLoading={savingEdit}
        okText="保存"
        destroyOnClose
      >
        <Space direction="vertical" style={{ width: '100%' }} size="middle">
          <div>
            <div style={{ marginBottom: 4, color: '#666' }}>客户/收货人</div>
            <Input value={eName} placeholder="客户姓名 (识别错可改)" onChange={e => setEName(e.target.value)} />
          </div>
          <div>
            <div style={{ marginBottom: 4, color: '#666' }}>打包费</div>
            <InputNumber value={eFee ?? undefined} min={0} prefix="¥" style={{ width: '100%' }}
              onChange={v => setEFee((v as number) ?? null)} />
          </div>
          <div>
            <div style={{ marginBottom: 4, color: '#666' }}>备注</div>
            <Input.TextArea value={eNote} rows={2} placeholder="本子上的批注/省份/产品等"
              onChange={e => setENote(e.target.value)} />
          </div>
          <div>
            <div style={{ marginBottom: 4, color: '#666' }}>配单订单号</div>
            <Input value={eOrder} placeholder="手填订单号, 或从下方候选里选" onChange={e => setEOrder(e.target.value)} style={{ marginBottom: 8 }} />
            <div style={{ marginBottom: 4, color: '#666' }}>候选订单（按客户名匹配度高→低）</div>
            <Space.Compact style={{ width: '100%' }}>
              <Select
                style={{ flex: 1 }}
                loading={candLoading}
                value={cands.some(c => c.order_no === eOrder) ? eOrder : undefined}
                placeholder={candLoading ? '加载候选中…' : (cands.length ? '选一个候选订单填入' : '暂无候选（改对客户名后点右侧刷新）')}
                onChange={(v) => setEOrder(v)}
                options={cands.map(c => ({
                  value: c.order_no,
                  label: `${Math.round(c.score * 100)}% · ${c.customer_name} · ${c.order_no}${c.product_name ? ' · ' + c.product_name : ''}`,
                }))}
              />
              <Button icon={<ReloadOutlined />} loading={candLoading}
                onClick={() => editRow && loadCands(editRow.id, eName)}>按客户名找候选</Button>
            </Space.Compact>
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              候选按客户名相似度排序取前 5；选中即填入上面订单号，保存后人工配到该单。改了客户名点「按客户名找候选」刷新候选。
            </Typography.Text>
          </div>
          <div>
            <div style={{ marginBottom: 4, color: '#666' }}>账期(改账期)</div>
            <DatePicker picker="month" style={{ width: '100%' }} placeholder="挪到正确月份"
              value={eBillMonth ? dayjs(eBillMonth + '-01') : null}
              onChange={(d) => setEBillMonth(d ? d.format('YYYY-MM') : '')} />
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              手写本错填月份/OCR 识别错月时, 把这行挪到正确账期(留空不改)。
            </Typography.Text>
          </div>
        </Space>
      </Modal>
    </Space>
  );
}
