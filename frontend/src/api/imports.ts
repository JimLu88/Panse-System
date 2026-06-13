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
