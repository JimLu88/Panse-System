import { api } from './base';

// ----- 新品开发(NPD)板块 -----

export interface NpdStage {
  id: number;
  code: string;
  name: string;
  group: string;
  sequence: number;
  color: string | null;
  is_gate: boolean;
  is_default: boolean;
  is_final: boolean;
  requires_mass_production: boolean;
  default_sla_days: number;
}

export interface NpdProject {
  id: number;
  code: string;
  name: string;
  category: string | null;
  brand: string | null;
  product_line: string | null;
  current_stage_id: number | null;
  current_stage_code: string | null;
  current_stage_name: string | null;
  current_stage_group: string | null;
  state: string;
  kanban_state: string;
  owner: string | null;
  priority: string;
  target_launch_date: string | null;
  percent_done: number;
  target_price: string | null;
  target_margin_rate: string | null;
  product_code: string | null;
  remark: string | null;
  deadline: string | null;
  created_at: string | null;
  updated_at: string | null;
}

export interface NpdProjectIn {
  name: string;
  category?: string | null;
  brand?: string | null;
  product_line?: string | null;
  owner?: string | null;
  priority?: string;
  target_launch_date?: string | null;
  target_price?: string | number | null;
  target_margin_rate?: string | number | null;
  remark?: string | null;
}

export interface NpdSettings {
  mass_production_enabled: boolean;
  min_supplier_candidates: number;
}

export const listNpdStages = (includeMassProduction?: boolean) =>
  api.get<NpdStage[]>('/api/npd/stages', {
    params: includeMassProduction === undefined ? {} : { include_mass_production: includeMassProduction },
  }).then((r) => r.data);

export const listNpdProjects = (state?: string) =>
  api.get<NpdProject[]>('/api/npd/projects', { params: state ? { state } : {} }).then((r) => r.data);

export const createNpdProject = (payload: NpdProjectIn) =>
  api.post<NpdProject>('/api/npd/projects', payload).then((r) => r.data);

export const updateNpdProject = (id: number, patch: Partial<NpdProjectIn> & Record<string, unknown>) =>
  api.put<NpdProject>(`/api/npd/projects/${id}`, patch).then((r) => r.data);

export const moveNpdProject = (id: number, stageId: number) =>
  api.put<NpdProject>(`/api/npd/projects/${id}/move`, { stage_id: stageId }).then((r) => r.data);

export const getNpdSettings = () =>
  api.get<NpdSettings>('/api/npd/settings').then((r) => r.data);
