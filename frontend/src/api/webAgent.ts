import { api } from './base';

export interface AgentFreshness {
  category: string;
  last_success: string | null;
  interval_days: number;
  status: 'fresh' | 'due' | 'stale' | 'missing';
}

export interface AgentTask {
  id: string;
  title: string;
  has_session: boolean;
  cadence?: string;
  skip_reason?: string | null;
}

export interface IngestFile {
  path: string;
  category: string;
  status: string;
  summary?: Record<string, unknown>;
}

export interface WebAgentStatus {
  agent: { online: boolean; error?: string | null; url: string; token_configured: boolean };
  tasks: AgentTask[];
  freshness: AgentFreshness[];
  last_ingest: {
    scanned?: number; imported?: number; skipped_known?: number;
    pending?: number; errors?: number; files?: IngestFile[];
  };
  orchestration: Record<string, unknown> & { running: boolean };
  not_ready: { item: string; reason: string }[];
  shipping_password?: { configured: boolean; received_at: string | null; hint: string };
}

export interface WebAgentSettings {
  interval_orders_days: number;
  interval_balance_days: number;
  schedule_time: string;       // 每日触发时刻 HH:MM
  schedule_enabled: boolean;
  token_configured: boolean;
  agent_url: string;
}

export async function getWebAgentStatus(): Promise<WebAgentStatus> {
  return (await api.get('/api/web-agent/status')).data;
}

export async function runWebAgentNow(): Promise<{ started: boolean }> {
  return (await api.post('/api/web-agent/run')).data;
}

export async function resumeWebAgentScans(): Promise<{ started: boolean; tasks?: string[] }> {
  return (await api.post('/api/web-agent/resume-scans')).data;
}

export async function submitShippingPassword(password: string): Promise<{
  accepted: boolean; tried: number; imported: number; failed: number; updated: number;
  failure_reason?: string | null;
}> {
  return (await api.post('/api/web-agent/shipping-password', { password })).data;
}

export async function ingestNow(): Promise<WebAgentStatus['last_ingest']> {
  return (await api.post('/api/web-agent/ingest')).data;
}

export async function pullOrders(): Promise<{ started: boolean }> {
  return (await api.post('/api/web-agent/pull-orders')).data;
}

export async function getWebAgentSettings(): Promise<WebAgentSettings> {
  return (await api.get('/api/web-agent/settings')).data;
}

export async function putWebAgentSettings(payload: {
  interval_orders_days?: number;
  interval_balance_days?: number;
  schedule_time?: string;        // HH:MM
  schedule_enabled?: boolean;
  token?: string;
}): Promise<WebAgentSettings> {
  return (await api.put('/api/web-agent/settings', payload)).data;
}
