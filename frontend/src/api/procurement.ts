import { api } from './base';

export type ProcurementCategory = 'daily' | 'photo' | 'production';
export type ProcurementChannel = 'taobao' | '1688' | 'xiaohongshu';
export type ProcurementExecutionMode = 'assisted' | 'agent';

export interface ProcurementTask {
  id: number;
  task_no: string;
  title: string;
  category: ProcurementCategory;
  item_name: string;
  specification: string | null;
  quantity: number | string;
  unit: string;
  target_unit_price: number | string | null;
  requirements: string | null;
  execution_mode: ProcurementExecutionMode;
  taobao_client_mode: 'desktop' | 'chrome';
  channels: ProcurementChannel[];
  channel_daily_limits: Record<string, number>;
  followup_intervals_hours: Record<string, number>;
  planned_merchant_count: number;
  max_followup_rounds: number;
  ab_test_enabled: boolean;
  ab_test_sample_size: number;
  script_a: string | null;
  script_b: string | null;
  script_a_ai_draft: string | null;
  script_b_ai_draft: string | null;
  scripts_reviewed_at: string | null;
  scripts_reviewed_by: string | null;
  winning_variant: 'A' | 'B' | null;
  ai_model: string | null;
  ai_suggestion_note: string | null;
  status: string;
  created_by: string | null;
  created_at: string;
  updated_at: string;
  counts: {
    total: number;
    sent: number;
    replied: number;
    needs_manual: number;
    completed: number;
  };
}

export interface ProcurementTaskInput {
  title: string;
  category: ProcurementCategory;
  item_name: string;
  specification?: string;
  quantity: number;
  unit: string;
  target_unit_price?: number;
  requirements?: string;
  execution_mode: ProcurementExecutionMode;
  taobao_client_mode: 'desktop' | 'chrome';
  channels: ProcurementChannel[];
  planned_merchant_count: number;
  max_followup_rounds: number;
  ab_test_enabled: boolean;
  ab_test_sample_size: number;
  channel_daily_limits?: Record<string, number>;
  followup_intervals_hours?: Record<string, number>;
  generate_scripts?: boolean;
}

export interface ProcurementInquiry {
  id: number;
  task_id: number;
  slot_no: number;
  channel: ProcurementChannel;
  merchant_name: string | null;
  merchant_url: string | null;
  product_url: string | null;
  message_variant: 'A' | 'B' | 'winner_pending' | 'manual';
  status: string;
  followup_round: number;
  first_sent_at: string | null;
  first_response_at: string | null;
  last_message_at: string | null;
  next_followup_at: string | null;
  last_outbound_message: string | null;
  last_inbound_message: string | null;
  requires_wechat: boolean;
  wechat_contact: string | null;
  manual_reason: string | null;
  quote_complete: boolean;
  quote_amount: number | string | null;
  normalized_unit_price: number | string | null;
  quote_payload: Record<string, unknown>;
  response_quality: number | null;
  leased_by: string | null;
  lease_until: string | null;
  execution_attempts: number;
  last_execution_error: string | null;
  last_observed_at: string | null;
  last_executor_mode: string | null;
  approved_message: string | null;
  approved_message_base: string | null;
  approved_action_key: string | null;
  message_reviewed_at: string | null;
  message_reviewed_by: string | null;
}

export interface ExperimentGroup {
  assigned: number;
  sent: number;
  replied: number;
  quote_complete: number;
  wechat_handoff: number;
  reply_rate: number;
  quote_rate: number;
  score: number;
}

export interface ProcurementExperiment {
  A: ExperimentGroup;
  B: ExperimentGroup;
  winner: 'A' | 'B' | null;
  reason: string;
}

export const listProcurementTasks = () =>
  api.get<ProcurementTask[]>('/api/procurement/tasks').then((r) => r.data);

export const createProcurementTask = (body: ProcurementTaskInput) =>
  api.post<ProcurementTask>('/api/procurement/tasks', body, { timeout: 150000 })
    .then((r) => r.data);

export const patchProcurementTask = (
  taskId: number,
  body: Partial<ProcurementTaskInput> & { script_a?: string; script_b?: string },
) => api.patch<ProcurementTask>(`/api/procurement/tasks/${taskId}`, body)
  .then((r) => r.data);

export const generateProcurementScripts = (taskId: number) =>
  api.post<{
    script_a: string;
    script_b: string;
    ai_used: boolean;
    model: string | null;
    note: string;
  }>(`/api/procurement/tasks/${taskId}/generate-scripts`, null, { timeout: 150000 })
    .then((r) => r.data);

export const reviewProcurementScripts = (
  taskId: number,
  body: { script_a: string; script_b?: string },
) => api.post<ProcurementTask>(
  `/api/procurement/tasks/${taskId}/review-scripts`,
  body,
).then((r) => r.data);

export const prepareProcurementQueue = (taskId: number) =>
  api.post<ProcurementInquiry[]>(
    `/api/procurement/tasks/${taskId}/prepare-queue`,
    { merchants: [] },
  ).then((r) => r.data);

export const listProcurementInquiries = (taskId: number) =>
  api.get<ProcurementInquiry[]>(`/api/procurement/tasks/${taskId}/inquiries`)
    .then((r) => r.data);

export const patchProcurementInquiry = (
  inquiryId: number,
  body: Partial<Pick<
    ProcurementInquiry,
    'channel' | 'merchant_name' | 'merchant_url' | 'product_url' | 'status' | 'manual_reason'
  >>,
) => api.patch<ProcurementInquiry>(`/api/procurement/inquiries/${inquiryId}`, body)
  .then((r) => r.data);

export const getProcurementExperiment = (taskId: number) =>
  api.get<ProcurementExperiment>(`/api/procurement/tasks/${taskId}/experiment`)
    .then((r) => r.data);

export const applyProcurementWinner = (taskId: number, variant?: 'A' | 'B') =>
  api.post(`/api/procurement/tasks/${taskId}/apply-winner`, { variant: variant ?? null })
    .then((r) => r.data);

export const markProcurementSent = (inquiryId: number, content?: string) =>
  api.post(`/api/procurement/inquiries/${inquiryId}/mark-sent`, { content: content || null })
    .then((r) => r.data);

export const reviewProcurementMessage = (
  inquiryId: number,
  content: string,
) => api.post<{
  inquiry_id: number;
  action_key: string;
  approved_message: string;
  reviewed_at: string;
  reviewed_by: string;
}>(`/api/procurement/inquiries/${inquiryId}/review-message`, { content })
  .then((r) => r.data);

export interface ProcurementReplyInput {
  content: string;
  quote_complete: boolean;
  quote_amount?: number;
  normalized_unit_price?: number;
  response_quality?: number;
  wechat_contact?: string;
}

export const recordProcurementReply = (
  inquiryId: number,
  body: ProcurementReplyInput,
) => api.post(`/api/procurement/inquiries/${inquiryId}/reply`, body)
  .then((r) => r.data);

export interface ProcurementDueAction {
  task_id: number;
  inquiry_id: number;
  channel: ProcurementChannel;
  action: 'initial_message' | 'follow_up' | 'check_reply_then_follow_up';
  suggested_message: string;
  approved_message: string | null;
  review_required: boolean;
  action_key: string;
  message_reviewed_at: string | null;
  followup_round: number;
  max_followup_rounds: number;
  daily_limit: number;
  requires_confirmed_send_callback: boolean;
}

export const listProcurementDueActions = (taskId: number) =>
  api.get<ProcurementDueAction[]>('/api/procurement/due-actions', {
    params: { task_id: taskId },
  }).then((r) => r.data);

export interface ProcurementAgentRuntime {
  token_configured: boolean;
  active_leases: number;
  agents: Array<{
    agent_id: string;
    display_name: string | null;
    host_label: string | null;
    version: string | null;
    mode: 'dry_run' | 'review' | 'live';
    status: string;
    online: boolean;
    capabilities: string[];
    current_inquiry_id: number | null;
    last_seen_at: string;
    last_error: string | null;
    counters: Record<string, number>;
  }>;
}

export const getProcurementAgentStatus = () =>
  api.get<ProcurementAgentRuntime>('/api/procurement/agent-status')
    .then((r) => r.data);
