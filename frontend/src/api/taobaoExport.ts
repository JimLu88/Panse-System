import { api } from './base';

export interface TaobaoExportType {
  key: string;
  label: string;
}

export const listTaobaoExportTypes = () =>
  api.get<TaobaoExportType[]>('/api/taobao-export/types').then((r) => r.data);

export const downloadTaobaoExport = (exportType: string, category?: string) =>
  api
    .get(`/api/taobao-export/${exportType}/download`, {
      params: { category },
      responseType: 'blob',
    })
    .then((r) => r.data as Blob);
