import { useState } from 'react';
import { Alert, Button, Card, Space, Typography, message } from 'antd';
import { api } from '../api/base';
import { triggerBlobDownload } from '../utils/download';

type Bundle = {
  plan_id: number; shop: string; campaign_title: string; price_version: string;
  all_erp_rows: unknown[]; legacy_protection: { attempt_id: string; state: string }[];
  execution_ready: boolean; live_enabled: boolean;
};

export default function CampaignPipelineConnection({ planId }: { planId: number }) {
  const [bundle, setBundle] = useState<Bundle>();
  const [busy, setBusy] = useState(false);
  async function load() {
    setBusy(true); setBundle(undefined);
    try { setBundle((await api.get<Bundle>(`/api/campaigns/${planId}/pipeline-bundle`)).data); }
    catch (e) {
      const error = e as { response?: { data?: { detail?: string } } };
      message.error(error.response?.data?.detail || '活动数据读取失败');
    } finally { setBusy(false); }
  }
  return <Card size="small" title="新活动程序 · ERP 对接" style={{ marginBottom: 16 }}>
    <Space direction="vertical" style={{ width: '100%' }}>
      <Typography.Text>使用本场当前ERP价格、可报规格和首次定制底线；已有任务仍保留原记录。</Typography.Text>
      <Button loading={busy} onClick={load}>读取本场数据与执行状态</Button>
      {bundle && <>
        <Typography.Text>{bundle.shop} · {bundle.campaign_title} · 可报 {bundle.all_erp_rows.length} 条SKU</Typography.Text>
        <Alert showIcon type={bundle.legacy_protection.length ? 'warning' : 'info'}
          message={bundle.legacy_protection.length
            ? `原系统已有 ${bundle.legacy_protection.length} 条提交记录，请核对原任务，不启动第二份。`
            : !bundle.execution_ready ? '本场不是可启动的新草稿，或没有可报规格；保留原任务处理。'
            : bundle.live_enabled ? '执行开关已启用；仍须由本机固定程序提交。' : '数据接口已连接，真实提交开关尚未启用。'} />
        <Button onClick={() => triggerBlobDownload(JSON.stringify(bundle, null, 2),
          `活动-${planId}-ERP数据.json`, 'application/json')}>下载本场数据</Button>
        <Typography.Text type="secondary">读取和下载不会提交淘宝。实际执行由本机统一入口自动取数，无需手工上传这份JSON。</Typography.Text>
      </>}
    </Space>
  </Card>;
}
