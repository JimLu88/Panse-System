import axios from 'axios';

export const api = axios.create({
  baseURL: '/',
  headers: { 'Content-Type': 'application/json' },
});

export interface Material {
  id: number;
  code: string;
  name: string;
  size_type: string | null;
  unit: string | null;
  price: string | null;
  remark: string | null;
  is_custom: boolean;
}

export interface PartInventory {
  id: number;
  warehouse: string;
  material_code: string;
  spec: string | null;
  unit: string | null;
  physical_qty: number;
  locked_qty: number;
  available_qty: number;
  remark: string | null;
}

export interface PartInventoryAddResponse {
  inventory: PartInventory;
  material_code: string;
  material_name: string;
  material_created: boolean;
}

export interface DataException {
  id: number;
  source_table: string;
  source_pk: string | null;
  exception_type: string;
  severity: string;
  description: string;
  suggestion_action: string | null;
  context: Record<string, unknown> | null;
  status: string;
  created_at: string;
}

export const listMaterials = (q?: string, isCustom?: boolean) =>
  api
    .get<Material[]>('/api/materials', {
      params: { q, is_custom: isCustom, limit: 500 },
    })
    .then((r) => r.data);

export const updateMaterial = (id: number, patch: Partial<Material>) =>
  api.patch<Material>(`/api/materials/${id}`, patch).then((r) => r.data);

export const listPartInventory = () =>
  api.get<PartInventory[]>('/api/inventory/parts').then((r) => r.data);

export const addPartInventoryRow = (payload: {
  warehouse: string;
  material_code?: string;
  material_name?: string;
  physical_qty?: number;
  locked_qty?: number;
  spec?: string;
  unit?: string;
  remark?: string;
}) =>
  api
    .post<PartInventoryAddResponse>('/api/inventory/parts', payload)
    .then((r) => r.data);

export const listExceptions = (status?: string) =>
  api
    .get<DataException[]>('/api/exceptions', { params: { status } })
    .then((r) => r.data);

export const resolveException = (id: number, status: 'resolved' | 'ignored') =>
  api
    .patch<DataException>(`/api/exceptions/${id}/resolve`, { status })
    .then((r) => r.data);
