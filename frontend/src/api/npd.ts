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

export interface NpdProjectDetail {
  project: NpdProject;
  timeline: NpdTimelineItem[];
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

export const getNpdSettings = () =>
  api.get<NpdSettings>('/api/npd/settings').then((r) => r.data);
