import { api } from './base';

// ----- Feishu -----
export interface FeishuBinding {
  id: number;
  system_table: string;
  feishu_app_token: string;
  feishu_table_id: string;
  direction: string;
  enabled: boolean;
  field_mapping: string | null;
}

export interface FeishuStatus {
  system_table: string;
  feishu_table_id: string;
  direction: string;
  enabled: boolean;
  mapped_rows: number;
}

export const listFeishuBindings = () =>
  api.get<FeishuBinding[]>('/api/feishu/bindings').then((r) => r.data);

export const createFeishuBinding = (payload: Omit<FeishuBinding, 'id'>) =>
  api.post<FeishuBinding>('/api/feishu/bindings', payload).then((r) => r.data);

export const updateFeishuBinding = (
  id: number,
  payload: Partial<Omit<FeishuBinding, 'id' | 'system_table'>>,
) => api.patch<FeishuBinding>(`/api/feishu/bindings/${id}`, payload).then((r) => r.data);

export const deleteFeishuBinding = (id: number) =>
  api.delete(`/api/feishu/bindings/${id}`).then((r) => r.data);

export const feishuStatus = () =>
  api.get<FeishuStatus[]>('/api/feishu/status').then((r) => r.data);

export const feishuSupportedTables = () =>
  api.get<{ tables: string[] }>('/api/feishu/supported-tables').then((r) => r.data);

export interface FeishuCredentials {
  app_id: string;
  app_secret_masked: string;
  configured: boolean;
  verification_token_set?: boolean;
  encrypt_key_set?: boolean;
}

export const getFeishuCredentials = () =>
  api.get<FeishuCredentials>('/api/feishu/credentials').then((r) => r.data);

export const putFeishuCredentials = (payload: {
  app_id?: string; app_secret?: string;
  verification_token?: string; encrypt_key?: string;
}) =>
  api.put<FeishuCredentials>('/api/feishu/credentials', payload).then((r) => r.data);

export const testFeishuConnection = () =>
  api.post<{ ok: boolean; error?: string }>('/api/feishu/test').then((r) => r.data);

export interface FeishuSyncResult {
  system_table: string;
  pushed: number;
  pulled: number;
  created_feishu: number;
  created_system: number;
  conflicts: number;
  errors: string[];
}

// 后台执行: 立即返回 started / already_running, 进度去运行日志看
export const triggerFeishuSync = (system_table?: string) =>
  api
    .post<{ status: string; detail: string }>('/api/feishu/sync', { system_table })
    .then((r) => r.data);

export interface FeishuSyncStatus {
  running: boolean;
  started_at: string | null;
  finished_at: string | null;
  scope: string | null;
  summary: Record<string, number> | null;
  error: string | null;
}

export const getFeishuSyncStatus = () =>
  api.get<FeishuSyncStatus>('/api/feishu/sync/status').then((r) => r.data);

export interface FeishuConflict {
  id: number;
  system_table: string;
  source_pk: string | null;
  description: string;
  context: {
    diffs?: Array<{ field: string; system: any; feishu: any }>;
    system_updated_at?: string | null;
    feishu_updated_at?: any;
  } | null;
  created_at: string | null;
}

export const listFeishuConflicts = () =>
  api.get<FeishuConflict[]>('/api/feishu/conflicts').then((r) => r.data);

export const resolveFeishuConflict = (id: number, keep: 'system' | 'feishu') =>
  api.post(`/api/feishu/conflicts/${id}/resolve`, { keep }).then((r) => r.data);

// 字段级合并裁决: {字段: 'system'|'feishu'}
export const resolveFeishuConflictFields = (id: number, field_choices: Record<string, 'system' | 'feishu'>) =>
  api.post(`/api/feishu/conflicts/${id}/resolve`, { field_choices }).then((r) => r.data);

// 飞书多余列冲突
export interface FeishuExtraField {
  id: number;
  system_table: string;
  source_pk: string | null;
  description: string;
  context: { extra_fields?: string[]; feishu_table_id?: string } | null;
  created_at: string | null;
}

export const listFeishuExtraFields = () =>
  api.get<FeishuExtraField[]>('/api/feishu/extra-fields').then((r) => r.data);

export const resolveFeishuExtraFields = (id: number, action: 'delete' | 'keep') =>
  api.post(`/api/feishu/extra-fields/${id}/resolve`, { action }).then((r) => r.data);

// Wiki 节点 token → Bitable App Token
export const resolveFeishuWiki = (wiki_token: string) =>
  api.get<{ app_token: string }>('/api/feishu/resolve-wiki', { params: { wiki_token } }).then((r) => r.data);

// 查询飞书多维表字段列表
export const getFeishuTableFields = (app_token: string, table_id: string) =>
  api.get<{ fields: Array<{ field_name: string; type: number }> }>(
    '/api/feishu/table-fields', { params: { app_token, table_id } }
  ).then((r) => r.data);

// 一键导入预设绑定 (23 表)
export const setupFeishuPreset = (wiki_token: string, enabled = false, overwrite = false) =>
  api.post<{app_token:string; created:number; skipped:number; updated:number; items:Array<{system_table:string;label:string;feishu_table_id:string;action:string}>}>(
    '/api/feishu/setup-preset', { wiki_token, enabled, overwrite }).then(r => r.data);
