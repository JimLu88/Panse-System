import { useEffect, useState } from 'react';
import { Alert, Button, Collapse, Space, Table, Typography, Upload, message } from 'antd';
import { api } from '../api/base';

export type PipelineComparisonResult = {
  scope_note: string; export_rows: number; file_sha256: string;
  legacy: { rows: { sku_id: string; item_id: string; verdict: string }[]; verdicts: Record<string, number>; ambiguities: unknown[] };
  pipeline: { erp_checked_skus: number; erp_mismatches_count: number } | null;
  pipeline_error: string | null; pipeline_skip_reasons: Record<string, number>;
};

export default function CampaignPipelineComparison({ planId, automatic, automaticError }: {
  planId: number; automatic?: PipelineComparisonResult; automaticError?: string;
}) {
  const [file, setFile] = useState<File>();
  const [result, setResult] = useState<PipelineComparisonResult>();
  useEffect(() => { setResult(automatic); }, [automatic]);
  const [busy, setBusy] = useState(false);
  async function compare() {
    if (!file) return;
    setBusy(true); setResult(undefined);
    try {
      const form = new FormData(); form.append('activity_file', file);
      const response = await api.post<PipelineComparisonResult>(`/api/campaigns/${planId}/pipeline-comparison`, form,
        { headers: { 'Content-Type': 'multipart/form-data' }, timeout: 120000 });
      setResult(response.data);
    } catch { message.error('对比失败，请检查文件格式和 ERP 映射'); }
    finally { setBusy(false); }
  }
  return <Collapse style={{ marginBottom: 16 }} items={[{
    key: 'comparison', label: '交接初版核对器 · 历史对比', children: <Space direction="vertical" style={{ width: '100%' }}>
      <Typography.Text>这里固定保留交接初版的核对行为用于历史比较，不代表第五轮修复版。正常核对会复用同一文件；正式报名保持四步顺序。</Typography.Text>
      {automaticError && <Alert type="warning" message={automaticError} />}
      <Upload accept=".xlsx" maxCount={1} beforeUpload={f => { setFile(f); setResult(undefined); return false; }}
        onRemove={() => { setFile(undefined); setResult(undefined); }}>
        <Button>选择已有官方已报商品表</Button>
      </Upload>
      <Button onClick={compare} loading={busy} disabled={!file}>开始只读对比</Button>
      {result && <>
        <Alert type="warning" showIcon message={result.scope_note} />
        {result.legacy.ambiguities.length > 0 && <Alert type="error"
          message={`有 ${result.legacy.ambiguities.length} 款存在多个有效营销记录，不能确定当前报名范围。`} />}
        <Typography.Text>文件共 {result.export_rows} 行；原核对器判定 {result.legacy.rows.length} 行；
          新核对器检查价格 {result.pipeline?.erp_checked_skus ?? 0} 行，偏差 {result.pipeline?.erp_mismatches_count ?? '未知'} 行。</Typography.Text>
        {result.pipeline_error && <Alert type="error" message={result.pipeline_error} />}
        <Table size="small" pagination={false} rowKey="reason" columns={[
          { title: '新核对器跳过原因', dataIndex: 'reason' }, { title: '行数', dataIndex: 'count' },
        ]} dataSource={Object.entries(result.pipeline_skip_reasons).map(([reason, count]) => ({ reason, count }))} />
        <Table size="small" pagination={false} rowKey="reason" columns={[
          { title: '原核对器判定', dataIndex: 'reason' }, { title: '行数', dataIndex: 'count' },
        ]} dataSource={Object.entries(result.legacy.verdicts).map(([reason, count]) => ({ reason, count }))} />
      </>}
    </Space>,
  }]} />;
}
