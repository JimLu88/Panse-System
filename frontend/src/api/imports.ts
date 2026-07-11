import { api } from './base';

// 导入档案 (/api/imports)
export interface ImportedFileRow {
  id: number;
  kind: string;
  original_filename: string | null;
  size_bytes: number | null;
  source: string;
  row_summary: Record<string, unknown> | null;
  uploaded_by: string | null;
  created_at: string | null;
  folder: string | null;   // 主机(PC)上的归档文件夹路径, 供「打开文件夹」展示/复制
}

export interface ImportedFileList {
  total: number;
  files: ImportedFileRow[];
}

export interface ImportedFileSummary {
  total: number;
  by_kind: Record<string, number>;
  imports_root?: string | null;   // 归档根目录(主机路径)
}

export const fetchImportFiles = (params: { kind?: string; month?: string; limit?: number; offset?: number }) =>
  api.get<ImportedFileList>('/api/imports/files', { params }).then((r) => r.data);

export const fetchImportFileSummary = () =>
  api.get<ImportedFileSummary>('/api/imports/files/summary').then((r) => r.data);

// 工厂下单图 → 飞书 手动补推 (修复: 旧逻辑只推"本次新生成"被每小时补生成抢空)
export interface OrderSheetPushStatus {
  configured: boolean;     // 飞书推送群是否配好 (feishu_push_chat_id)
  pending_total: number;   // 含历史基线的全部未推 (手动按钮可推的总量)
  pending_new: number;     // 不含历史基线 (18:00 自动会推的量, 平时应接近 0)
}

export interface OrderSheetPushResult {
  pushed: number;
  failed: number;
  remaining: number;
  order_nos: string[];
  reason?: string;
}

export const fetchOrderSheetPushStatus = () =>
  api.get<OrderSheetPushStatus>('/api/imports/order-sheets/push-status').then((r) => r.data);

export const pushOrderSheets = (limit = 20) =>
  api.post<OrderSheetPushResult>('/api/imports/order-sheets/push', { limit }).then((r) => r.data);

// 工厂下单图推送设置: 补差/加价单不推的金额门槛 + 补差关键词
export interface PushConfig { min_amount: number; topup_keywords: string[]; }
export const fetchPushConfig = () =>
  api.get<PushConfig>('/api/imports/order-sheets/push-config').then((r) => r.data);
export const savePushConfig = (minAmount: number) =>
  api.post<{ ok: boolean; min_amount: number }>(
    '/api/imports/order-sheets/push-config', { min_amount: minAmount }).then((r) => r.data);

// 通过带鉴权的 axios 实例取 blob 再触发下载 (直链会丢 Authorization 头 → 401)
export async function downloadImportFile(id: number, filename: string) {
  const resp = await api.get(`/api/imports/files/${id}/download`, { responseType: 'blob' });
  const url = window.URL.createObjectURL(resp.data as Blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename || `import-${id}`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  window.URL.revokeObjectURL(url);
}
