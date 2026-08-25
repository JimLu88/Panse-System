import { api } from './base';

// ----- Admin: AI Integrations (业务需求扩展) -----
export interface IntegrationConfig {
  provider: string;
  base_url: string;
  api_key_masked: string;
  api_key_set: boolean;
  model: string;
}

export interface SupportedProvider {
  value: string;
  label: string;
  model_hint: string;
  base_url_hint: string;
}

export interface Integrations {
  diagnose: IntegrationConfig;
  ocr: IntegrationConfig;
  custom: IntegrationConfig;
  supported_providers: SupportedProvider[];
}

export const fetchIntegrations = () =>
  api.get<Integrations>('/api/admin/integrations').then((r) => r.data);

export const updateIntegrations = (payload: {
  diagnose?: Partial<{ provider: string; base_url: string; api_key: string; model: string }>;
  ocr?: Partial<{ provider: string; base_url: string; api_key: string; model: string }>;
  custom?: Partial<{ provider: string; base_url: string; api_key: string; model: string }>;
}) => api.put<Integrations>('/api/admin/integrations', payload).then((r) => r.data);

export const testIntegration = (kind: 'diagnose' | 'ocr' | 'custom') =>
  api
    .post<{ ok: boolean; provider: string; model: string; sample?: string; error?: string }>(
      '/api/admin/integrations/test',
      { kind },
    )
    .then((r) => r.data);

// ----- 活动系统 AI (DeepSeek/千问, 2026-07-17): key 加密落库, 读取只回状态+尾4位 -----
export interface CampaignAiConfig {
  provider: string; // none | deepseek | qwen
  model: string;
  api_key_set: boolean;
  api_key_tail: string;
  providers: { value: string; label: string; default_model: string }[];
}

export const fetchCampaignAi = () =>
  api.get<CampaignAiConfig>('/api/admin/campaign-ai').then((r) => r.data);

export const updateCampaignAi = (payload: { provider?: string; model?: string; api_key?: string }) =>
  api.put<CampaignAiConfig>('/api/admin/campaign-ai', payload).then((r) => r.data);

export const testCampaignAi = () =>
  api
    .post<{ ok: boolean; provider: string; model: string; sample?: string; error?: string }>(
      '/api/admin/campaign-ai/test',
      {},
    )
    .then((r) => r.data);

// ----- 系统监控 / 看门狗 (业务需求) -----
export interface HealthCheck {
  name: string;
  status: 'ok' | 'warn' | 'fail';
  detail: string;
  duration_ms: number;
}

export interface SystemStatus {
  uptime_sec: number;
  process_started_at: string;
  version_sha: string;
  python_version: string;
  db_ok: boolean;
  db_latency_ms: number | null;
  pending_migrations: number;
  disk_total_gb: number;
  disk_free_gb: number;
  disk_used_pct: number;
  mem_total_mb: number;
  mem_available_mb: number;
  mem_used_pct: number;
  storage_used_mb: number;
  recent_checks: HealthCheck[];
}

export interface HealthLog {
  id: number;
  check_name: string;
  status: string;
  detail: string | null;
  duration_ms: number | null;
  created_at: string;
}

export const fetchSystemStatus = () =>
  api.get<SystemStatus>('/api/admin/system-status').then((r) => r.data);

export const fetchHealthLogs = (limit = 100, check_name?: string) =>
  api
    .get<HealthLog[]>('/api/admin/system-health-logs', {
      params: { limit, ...(check_name ? { check_name } : {}) },
    })
    .then((r) => r.data);

// ----- 运行日志 (内存环形缓冲, 排查同步/导入等错误) -----
export interface RuntimeLog {
  ts: string;
  level: string;
  logger: string;
  msg: string;
}

export const fetchRecentLogs = (params?: {
  limit?: number;
  level?: string;
  contains?: string;
  logger_prefix?: string;
}) =>
  api
    .get<{ logs: RuntimeLog[] }>('/api/logs/recent', { params })
    .then((r) => r.data.logs);

export const restartApi = () =>
  api.post('/api/admin/restart-api', { confirm: 'RESTART' }).then((r) => r.data);

// ----- 清空业务数据 (保留账号/设置/配置) -----
export interface ResetDataResult {
  cleared: boolean;
  total_deleted: number;
  deleted: Record<string, number>;
  feishu_cleared: boolean;
  feishu_deleted: Record<string, number>;
  feishu_error: string | null;
}

export const fetchResetDataTables = () =>
  api.get<{ tables: string[] }>('/api/admin/reset-data/tables').then((r) => r.data.tables);

export const resetBusinessData = (
  password: string,
  opts: { clearFeishu?: boolean } = {},
) =>
  api
    .post<ResetDataResult>('/api/admin/reset-data', {
      password,
      confirm: 'DELETE',
      clear_feishu: !!opts.clearFeishu,
      confirm_feishu: opts.clearFeishu ? 'DELETE FEISHU' : '',
    })
    .then((r) => r.data);

// ----- 数据备份 / 一键导出 Excel -----
export interface BackupConfig {
  auto_enabled: boolean;
  interval_days: number;
  dir: string;
  start_date: string | null;
  last_run_at: string | null;
  next_run_at: string | null;
  max_backups: number;
}

export interface BackupFile {
  filename: string;
  size_mb: number;
  created_at: string;
}

export interface BackupRunResult {
  file: string;
  size_mb: number;
  deleted_old: number;
  uploaded_s3: boolean;
}

export const fetchBackupConfig = () =>
  api.get<BackupConfig>('/api/admin/backup/config').then((r) => r.data);

export const updateBackupConfig = (payload: Partial<{
  auto_enabled: boolean;
  interval_days: number;
  dir: string;
  start_date: string;
}>) => api.put<BackupConfig>('/api/admin/backup/config', payload).then((r) => r.data);

export const fetchBackupList = () =>
  api.get<BackupFile[]>('/api/admin/backup/list').then((r) => r.data);

export const runBackupNow = () =>
  api.post<BackupRunResult>('/api/admin/backup/run').then((r) => r.data);

// 触发一次导出, 然后浏览器下载该文件 (一键导出并下载)
export const exportAndDownload = async (): Promise<BackupRunResult> => {
  const result = await runBackupNow();
  const resp = await api.get(`/api/admin/backup/download/${encodeURIComponent(result.file)}`, {
    responseType: 'blob',
  });
  const url = window.URL.createObjectURL(resp.data as Blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = result.file;
  document.body.appendChild(a);
  a.click();
  a.remove();
  window.URL.revokeObjectURL(url);
  return result;
};

export const downloadBackup = async (filename: string) => {
  const resp = await api.get(`/api/admin/backup/download/${encodeURIComponent(filename)}`, {
    responseType: 'blob',
  });
  const url = window.URL.createObjectURL(resp.data as Blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  window.URL.revokeObjectURL(url);
};

// ----- 重启事件 (业务需求 5) -----
export interface SystemEvent {
  id: number;
  kind: string;
  actor: string | null;
  detail: string | null;
  snapshot_json: any | null;
  created_at: string;
}

export const fetchSystemEvents = (limit = 50) =>
  api
    .get<SystemEvent[]>('/api/admin/system-events', { params: { limit } })
    .then((r) => r.data);

// ----- 告警 / 通知中心 (Phase 1B) -----
export interface AlertItem {
  id: number;
  kind: string;
  severity: 'info' | 'warn' | 'critical';
  title: string;
  body: string | null;
  dedupe_key: string | null;
  related_url: string | null;
  context_json: Record<string, any> | null;
  sticky: boolean;
  resolved_at: string | null;
  resolved_by: string | null;
  auto_resolve_until: string | null;
  notified_at: string | null;
  created_at: string;
}

export interface AlertSummary {
  info: number;
  warn: number;
  critical: number;
}

export const fetchActiveAlerts = (params: { severity?: string; kind?: string; limit?: number } = {}) =>
  api.get<AlertItem[]>('/api/alerts/active', { params }).then((r) => r.data);

export const fetchAlertSummary = () =>
  api.get<AlertSummary>('/api/alerts/summary').then((r) => r.data);

export const dismissAlert = (id: number) =>
  api.post(`/api/alerts/${id}/dismiss`).then((r) => r.data);

export const fetchAlertHistory = (limit = 100, kind?: string) =>
  api.get<AlertItem[]>('/api/alerts/history', { params: { limit, kind } })
    .then((r) => r.data);

// ----- 定时任务 (Phase 1A, 业务需求 18 自动任务清单) -----
export interface SchedulerJob {
  job_id: string;
  label: string;
  kind: string;
  schedule: Record<string, any>;
  default_schedule?: Record<string, any> | null;
  enabled: boolean;
  next_run_at: string | null;
}

export interface SchedulerRun {
  id: number;
  job_id: string;
  job_label: string;
  status: string;
  duration_ms: number | null;
  error: string | null;
  result_summary: Record<string, any> | null;
  started_at: string | null;
  completed_at: string | null;
  created_at: string;
}

export type AutomationFailureCategory = 'order' | 'finance' | 'campaign';

export interface AutomationFailureEvent {
  id: number;
  date: string;
  category: AutomationFailureCategory;
  category_label: string;
  job_id: string;
  job_label: string;
  attempt_no: number;
  reason: string;
  state: 'open' | 'waiting_input' | 'recovered' | 'final';
  final: boolean;
  waiting_input: boolean;
  next_retry_at: string | null;
  recovered_at: string | null;
  started_at: string | null;
  completed_at: string | null;
  duration_ms: number | null;
  source_failures: Array<{ task: string; status: string; reason: string }>;
  result_summary: Record<string, any>;
}

export interface AutomationFailureRecorder {
  date: string;
  total: number;
  open_count: number;
  by_category: Record<AutomationFailureCategory, number>;
  items: AutomationFailureEvent[];
}

export const fetchSchedulerJobs = () =>
  api.get<SchedulerJob[]>('/api/scheduler/jobs').then((r) => r.data);

export const fetchSchedulerRuns = (limit = 100, job_id?: string) =>
  api.get<SchedulerRun[]>('/api/scheduler/runs', { params: { limit, job_id } })
    .then((r) => r.data);

export const fetchAutomationFailures = (params: {
  on?: string;
  category?: AutomationFailureCategory;
  limit?: number;
}) => api.get<AutomationFailureRecorder>('/api/scheduler/failures', { params })
  .then((r) => r.data);

export const triggerSchedulerJob = (job_id: string) =>
  api.post(`/api/scheduler/jobs/${job_id}/trigger`).then((r) => r.data);

export const updateSchedulerJob = (
  job_id: string,
  payload: { interval_minutes?: number; cron?: Record<string, any>; enabled?: boolean },
) => api.put<SchedulerJob>(`/api/scheduler/jobs/${job_id}/schedule`, payload).then((r) => r.data);

// ----- Phase 8: AI 简报 + 会计期间 + 供应商评分 -----
export interface DailyBriefing {
  id: number;
  for_date: string;
  content: string;
  highlights_json: any[] | null;
  model: string | null;
  generated_at: string | null;
}

export const fetchTodayBriefing = () =>
  api.get<DailyBriefing | null>('/api/briefings/today').then((r) => r.data);

export const fetchRecentBriefings = (limit = 14) =>
  api.get<DailyBriefing[]>('/api/briefings/recent', { params: { limit } }).then((r) => r.data);

export const triggerBriefing = (for_date?: string) =>
  api.post('/api/briefings/generate-now', null, { params: { for_date } }).then((r) => r.data);

export interface AccountingPeriod {
  id: number;
  year: number;
  month: number;
  status: 'open' | 'closed' | 'locked';
  closed_at: string | null;
  closed_by: string | null;
  remark: string | null;
}

export const fetchAccountingPeriods = () =>
  api.get<AccountingPeriod[]>('/api/accounting/periods').then((r) => r.data);

export const closeAccountingPeriod = (year: number, month: number) =>
  api.post<AccountingPeriod>('/api/accounting/periods/close', { year, month }).then((r) => r.data);

export const reopenAccountingPeriod = (year: number, month: number) =>
  api.post<AccountingPeriod>('/api/accounting/periods/reopen', { year, month }).then((r) => r.data);

export const lockAccountingPeriod = (year: number, month: number) =>
  api.post<AccountingPeriod>('/api/accounting/periods/lock', { year, month }).then((r) => r.data);

export interface SupplierScore {
  supplier_id: number;
  year: number;
  month: number;
  on_time_rate: number | null;
  return_rate: number | null;
  price_variance_pct: number | null;
  total_orders: number;
  total_amount: number | null;
  score: number | null;
  rank: number | null;
  detail_json: Record<string, any> | null;
}

export const fetchSupplierScores = (year: number, month: number) =>
  api.get<SupplierScore[]>(`/api/supplier-scores/${year}/${month}`).then((r) => r.data);

export const computeSupplierScores = (year: number, month: number) =>
  api.post(`/api/supplier-scores/compute/${year}/${month}`).then((r) => r.data);

// ----- 通知配置 (业务需求扩展: 看门狗触发时推 Slack/微信/钉钉/飞书) -----
export interface NotifyProvider {
  value: string;
  label: string;
}

export interface NotifyConfig {
  provider: string;
  webhook_masked: string;
  webhook_set: boolean;
  supported_providers: NotifyProvider[];
  text_channels: string;  // 纯文本通知渠道, 逗号分隔 (feishu,webhook)
  route_mode: 'legacy' | 'feishu_split';
  feishu_order_chat_id_masked: string;
  feishu_order_chat_set: boolean;
  feishu_alert_chat_id_masked: string;
  feishu_alert_chat_set: boolean;
}

export const fetchNotifyConfig = () =>
  api.get<NotifyConfig>('/api/admin/notify-config').then((r) => r.data);

export const updateNotifyConfig = (payload: {
  provider?: string;
  webhook?: string;
  text_channels?: string;
  route_mode?: 'legacy' | 'feishu_split';
  feishu_alert_chat_id?: string;
}) => api.put<NotifyConfig>('/api/admin/notify-config', payload).then((r) => r.data);

export const testNotifyConfig = () =>
  api
    .post<{ ok: boolean; detail: string }>('/api/admin/notify-config/test')
    .then((r) => r.data);

// ----- 企业微信自建应用入站：接收发货密码 -----
export interface WechatInboundConfig {
  enabled: boolean;
  corp_id: string;
  token_set: boolean;
  aes_key_set: boolean;
  allowed_users: string[];
  ready: boolean;
  callback_path: string;
  aibot_enabled: boolean;
  aibot_token_set: boolean;
  aibot_aes_key_set: boolean;
  aibot_name: string;
  aibot_ready: boolean;
  aibot_callback_path: string;
}

export const fetchWechatInboundConfig = () =>
  api.get<WechatInboundConfig>('/api/admin/wechat-inbound-config').then((r) => r.data);

export const updateWechatInboundConfig = (payload: {
  enabled?: boolean;
  corp_id?: string;
  token?: string;
  aes_key?: string;
  allowed_users?: string[];
  aibot_enabled?: boolean;
  aibot_token?: string;
  aibot_aes_key?: string;
  aibot_name?: string;
}) => api.put<WechatInboundConfig>('/api/admin/wechat-inbound-config', payload).then((r) => r.data);

// ----- 物流追踪配置 (快递100 / 快递鸟) -----
export interface LogisticsConfig {
  provider: string;
  customer: string;
  customer_set: boolean;
  key_masked: string;
  key_set: boolean;
  kdniao_ebusiness_id: string;
  kdniao_ebusiness_id_set: boolean;
  kdniao_key_masked: string;
  kdniao_key_set: boolean;
}

export const fetchLogisticsConfig = () =>
  api.get<LogisticsConfig>('/api/admin/logistics-config').then((r) => r.data);

export const updateLogisticsConfig = (payload: {
  provider?: string;
  customer?: string;
  key?: string;
  kdniao_ebusiness_id?: string;
  kdniao_key?: string;
}) => api.put<LogisticsConfig>('/api/admin/logistics-config', payload).then((r) => r.data);

// ----------------------------- 版本信息 ----------------------------- //
export interface VersionInfo {
  commit: string;            // 短哈希, 如 6aaf8ad
  commit_full: string;
  commit_date: string;       // commit 作者时间
  commit_message: string;
  branch: string;
  deployed_at: string;       // 看门狗 build 这版代码的时间 (容器里唯一可靠的"部署时间")
  source: string;            // build_file | runtime_git | unknown
}
export const getVersion = () =>
  api.get<VersionInfo>('/api/version').then((r) => r.data);

// ----- 运维工具页 (优化 #2/#3/#4/#10) -----
export interface OwnerHealth {
  open_exceptions: number;
  exceptions_by_severity: Record<string, number>;
  failing_jobs: string[];
  latest_backup_age_h: number | null;
  latest_backup_size_mb: number | null;
  backup_stale: boolean | null;
  healthy: boolean;
}
export const ownerHealth = () =>
  api.get<OwnerHealth>('/api/admin/owner-health').then((r) => r.data);

export interface RecycleBinItem { file: string; size_bytes: number }
export const recycleBinList = () =>
  api.get<{ items: RecycleBinItem[] }>('/api/importer/recycle-bin').then((r) => r.data.items);
export const recycleBinRestore = (filename: string) =>
  api.post(`/api/importer/recycle-bin/${encodeURIComponent(filename)}/restore`).then((r) => r.data);

export interface MonthlyFinancial {
  period: string; order_count: number; revenue: number;
  cost: number; gross_profit: number; net_profit: number;
}
export const monthlyFinancial = (year: number, month: number) =>
  api.get<MonthlyFinancial>('/api/reports/monthly-financial', { params: { year, month } }).then((r) => r.data);
export const monthlyFinancialXlsxUrl = (year: number, month: number) =>
  `/api/reports/monthly-financial?year=${year}&month=${month}&fmt=xlsx`;
