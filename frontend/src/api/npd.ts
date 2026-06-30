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

export interface NpdTask {
  id: number;
  title: string;
  category: string;
  is_required: boolean;
  status: string;
  assignee: string | null;
  stage_code: string | null;
  due_date: string | null;
  done_at: string | null;
  done_by: string | null;
  remark: string | null;
}

export interface NpdInspection {
  id: number;
  item_name: string;
  check_type: string;        // pass / numeric / text
  unit: string | null;
  min_val: string | null;
  max_val: string | null;
  expected: string | null;
  is_required: boolean;
  reading: string | null;
  result: string;            // pass / fail / pending
  remark: string | null;
}

export interface NpdTimelineItem {
  stage_id: number;
  code: string;
  name: string;
  group: string;
  is_gate: boolean;
  is_current: boolean;
  instance_status: string | null;
  entered_at: string | null;
  deadline: string | null;
  completed_at: string | null;
  tasks: NpdTask[];
  inspections: NpdInspection[];
}

export interface NpdCostGate {
  prototype_cost: string | null;
  est_mass_cost: string | null;
  target_price: string | null;
  target_margin: string | null;
  actual_margin: string | null;
  verdict: string;       // pass / fail / pending
  note: string | null;
}

export interface NpdCraftIssue {
  id: number;
  stage_code: string | null;
  title: string;
  description: string | null;
  root_cause: string | null;
  cost_impact: string | null;
  status: string;        // open / solved
  chosen_supplier: string | null;
}

export interface NpdSupplierCandidate {
  id: number;
  material_category: string | null;
  supplier_name: string;
  is_backup: boolean;
  quote_amount: string | null;
  quote_status: string;  // pending / quoted / chosen
  lead_time_days: number | null;
  can_solve_craft_issue: boolean;
  craft_solution: string | null;
  solved_cost: string | null;
  remark: string | null;
}

export interface NpdBomLine {
  id: number;
  material_code: string | null;
  material_name: string | null;
  category: string | null;
  unit: string | null;
  qty: string | null;
  unit_price: string | null;
  size_type: string | null;
  is_new: boolean;
  remark: string | null;
}

export interface NpdProjectDetail {
  project: NpdProject;
  timeline: NpdTimelineItem[];
  cost_gate: NpdCostGate | null;
  craft_issues: NpdCraftIssue[];
  suppliers: NpdSupplierCandidate[];
  bom_lines: NpdBomLine[];
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

export const moveNpdProject = (id: number, stageId: number, force = false) =>
  api.put<NpdProject>(`/api/npd/projects/${id}/move`, { stage_id: stageId, force }).then((r) => r.data);

export const getNpdProjectDetail = (id: number) =>
  api.get<NpdProjectDetail>(`/api/npd/projects/${id}/detail`).then((r) => r.data);

export const toggleNpdTask = (id: number, done: boolean) =>
  api.put<NpdTask>(`/api/npd/tasks/${id}`, { done }).then((r) => r.data);

export const saveNpdInspection = (
  id: number,
  payload: {
    reading?: string | null; result?: string | null;
    min_val?: number | string | null; max_val?: number | string | null;
    remark?: string | null;
  },
) => api.put<NpdInspection>(`/api/npd/inspections/${id}`, payload).then((r) => r.data);

export const saveNpdCostGate = (
  projectId: number,
  payload: { prototype_cost?: number | string | null; est_mass_cost?: number | string | null; note?: string | null },
) => api.put<NpdCostGate>(`/api/npd/projects/${projectId}/cost-gate`, payload).then((r) => r.data);

export const addNpdCraftIssue = (projectId: number, payload: Record<string, unknown>) =>
  api.post<NpdCraftIssue>(`/api/npd/projects/${projectId}/craft-issues`, payload).then((r) => r.data);

export const updateNpdCraftIssue = (id: number, payload: Record<string, unknown>) =>
  api.put<NpdCraftIssue>(`/api/npd/craft-issues/${id}`, payload).then((r) => r.data);

export const addNpdSupplier = (projectId: number, payload: Record<string, unknown>) =>
  api.post<NpdSupplierCandidate>(`/api/npd/projects/${projectId}/suppliers`, payload).then((r) => r.data);

export const updateNpdSupplier = (id: number, payload: Record<string, unknown>) =>
  api.put<NpdSupplierCandidate>(`/api/npd/suppliers/${id}`, payload).then((r) => r.data);

export const addNpdBomLine = (projectId: number, payload: Record<string, unknown>) =>
  api.post<NpdBomLine>(`/api/npd/projects/${projectId}/bom-lines`, payload).then((r) => r.data);

export const deleteNpdBomLine = (id: number) =>
  api.delete(`/api/npd/bom-lines/${id}`).then((r) => r.data);

export const materializeNpdProject = (projectId: number, payload: { brand: string; category_code: string }) =>
  api.post<{ product_code: string; sku_code: string; materials_created: number; bom_lines: number; physical_cost: string }>(
    `/api/npd/projects/${projectId}/materialize`, payload,
  ).then((r) => r.data);

export const getNpdSettings = () =>
  api.get<NpdSettings>('/api/npd/settings').then((r) => r.data);
