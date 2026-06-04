import { api } from './base';

// ----- Excel 通用 importer (业务需求) -----
export interface EntityField {
  name: string;
  type: string;
  required: boolean;
  desc: string;
  aliases: string[];
}

export interface EntityType {
  value: string;
  label: string;
  description: string;
  fields: EntityField[];
}

export interface SheetPreview {
  sheet_name: string;
  row_count: number;
  column_names: string[];
  sample_rows: any[][];
  suggested_entity: string | null;
  suggested_mapping: Record<string, string>;
  notes: string[];
}

export interface ImporterPreviewResp {
  file_b64: string;
  sheets: SheetPreview[];
}

export interface ImportReport {
  entity_type: string;
  sheet_name: string;
  total_rows: number;
  inserted_parents: number;
  inserted_children: number;
  skipped_rows: number;
  matched_lines: number;
  auto_created_suppliers: string[];
  errors: string[];
  warnings: string[];
}

export const fetchEntityTypes = () =>
  api.get<EntityType[]>('/api/importer/entity-types').then((r) => r.data);

export const previewImporter = (file: File, entityType?: string) => {
  const form = new FormData();
  form.append('file', file);
  const params = entityType ? { entity_type: entityType } : {};
  return api
    .post<ImporterPreviewResp>('/api/importer/preview', form, {
      headers: { 'Content-Type': 'multipart/form-data' },
      params,
      timeout: 120000,
    })
    .then((r) => r.data);
};

export const commitImporter = (payload: {
  file_b64: string;
  sheet_name: string;
  entity_type: string;
  mapping: Record<string, string>;
  auto_create_suppliers?: boolean;
  auto_match_orders?: boolean;
  dry_run?: boolean;
}) => api.post<ImportReport>('/api/importer/commit', payload).then((r) => r.data);

// ----- 异步导入作业 (业务需求 6) -----
export interface ImportJob {
  id: number;
  user_id: number | null;
  entity_type: string;
  sheet_name: string;
  status: 'pending' | 'running' | 'done' | 'failed' | 'cancelled';
  total_rows: number;
  processed_rows: number;
  progress_pct: number;
  error: string | null;
  report: any | null;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
}

export const commitImporterAsync = (payload: {
  file_b64: string;
  sheet_name: string;
  entity_type: string;
  mapping: Record<string, string>;
  auto_create_suppliers?: boolean;
  auto_match_orders?: boolean;
}) =>
  api
    .post<{ job_id: number; status: string; sheet_name: string; entity_type: string }>(
      '/api/importer/commit-async',
      payload,
      { timeout: 180000 },
    )
    .then((r) => r.data);

export const fetchImportJob = (id: number) =>
  api.get<ImportJob>(`/api/importer/jobs/${id}`).then((r) => r.data);

export const fetchImportJobs = (limit = 50) =>
  api.get<ImportJob[]>('/api/importer/jobs', { params: { limit } }).then((r) => r.data);

export const cancelImportJob = (id: number) =>
  api.post<ImportJob>(`/api/importer/jobs/${id}/cancel`).then((r) => r.data);

// ----- 智能 Excel 导入 (Phase 14) -----
export interface SheetAnalysis {
  sheet_name: string;
  total_rows: number;
  header_row: number;
  columns: string[];
  sample_rows: any[][];
  suggested_entity: string | null;
  entity_label: string | null;
  confidence: number;
  mapping: Record<string, string>;
  skipped_columns: string[];
  quality: 'good' | 'needs_review' | 'messy';
  quality_score: number;
  issues: Array<{ row_offset: number; column: string; value: any; problem: string; fix: string }>;
  notes: string[];
}

export interface SmartAnalysisResp {
  file_b64: string;
  sheets: SheetAnalysis[];
}

export const smartAnalyzeExcel = (file: File) => {
  const fd = new FormData();
  fd.append('file', file);
  return api
    .post<SmartAnalysisResp>('/api/importer/smart-analyze', fd, {
      headers: { 'Content-Type': 'multipart/form-data' },
      timeout: 180000,
    })
    .then((r) => r.data);
};

export interface ImportConflict {
  source_table: string;
  source_pk: string | null;
  diffs: Array<{ field: string; old: any; new: any }>;
}

export interface SmartCommitReport {
  sheet_name: string;
  entity_type?: string;
  total_rows?: number;
  inserted_parents?: number;
  inserted_children?: number;
  skipped_rows?: number;
  errors?: string[];
  warnings?: string[];
  conflicts?: ImportConflict[];
  unmapped_columns?: string[];
  skipped?: boolean;
  reason?: string;
  error?: string;
}

export interface PostImportResult {
  logic_issues: number;
  analysis: string | null;
  ai_used: boolean;
}

export const smartCommitExcel = (payload: {
  file_b64: string;
  plan: Array<{
    sheet_name: string;
    entity_type: string;
    mapping: Record<string, string>;
    header_row: number;
    dry_run?: boolean;
    on_conflict?: 'ask' | 'overwrite' | 'keep';
    sheet_account?: string | null;
  }>;
}) =>
  api
    .post<{ reports: SmartCommitReport[]; post_import: PostImportResult }>(
      '/api/importer/smart-commit',
      payload,
      { timeout: 300000 },
    )
    .then((r) => r.data);

// ---- 校验导出 ----
export const validateExportExcel = async (file: File): Promise<Blob> => {
  const form = new FormData();
  form.append('file', file);
  const resp = await api.post('/api/importer/validate-export', form, {
    headers: { 'Content-Type': 'multipart/form-data' },
    responseType: 'blob',
    timeout: 120000,
  });
  return resp.data as Blob;
};
