/**
 * 活动生命周期系统 API 调用层（P3 前端）。
 * 权威 spec = docs/活动生命周期系统_执行plan.md（2026-07-17 拍板）。
 * ★契约对齐（2026-07-17 收尾）：字段名/枚举一律以后端 backend/app/api/campaigns.py 实际返回为准——
 *   计划字段 campaign_type / qn_campaign_title；列表包 {items, types}；
 *   动销分组用后端原样中文键（有动销/无动销 + item_names）；
 *   预检返回 {plan, checks[], has_error}（checks.level ∈ pass|info|warn|error）；
 *   推立减两段式 phase=stage|commit（query 参数，R12 每步确认制）；推报名一次 stage 即生效；
 *   核对 verdict 是后端中文判定串（一分不差/贴线让X/超2元报警/偏差/J未刷新/占位/无映射）。
 * 定价铁则（spec 二节）：报名价恒 = ERP 日常价；每场活动只变单品立减；
 *   官方立减向上取整到元；无动销品到手永远 = 中促价 + 1 元。
 */
import { api } from './base';

// ── 基础枚举（与后端 campaign_service.CAMPAIGN_TYPES 逐字一致） ──

/** 活动类型（创建区点选按钮组，spec 四.3） */
export type CampaignType = 'super_reduce' | 'big88' | 'big38' | 'big_other' | 'big618' | 'big11';
/** 官方立减力度档：mid=10% / big=12% / big618=15%（后端由类型派生，前端只读展示） */
export type CampaignTier = 'mid' | 'big' | 'big618';
/** 计划状态机（spec 五节 CampaignPlan.status） */
export type CampaignStatus =
  | 'draft' | 'precheck' | 'discount_pushed' | 'signup_pushed' | 'reconciled' | 'alarmed';

/** 活动类型元数据（前端展示；档位/到手口径 = spec 二节） */
export interface CampaignTypeDef {
  value: CampaignType;
  label: string;
  tier: CampaignTier;
  rate: string;                          // 官方立减力度（人话）
  targetLabel: string;                   // 顾客到手目标价（人话）
}
export const CAMPAIGN_TYPES: CampaignTypeDef[] = [
  { value: 'super_reduce', label: '超级立减', tier: 'mid', rate: '10%', targetLabel: '中促价' },
  { value: 'big88', label: '88VIP大促', tier: 'big', rate: '12%', targetLabel: '大促价' },
  { value: 'big38', label: '38大促', tier: 'big', rate: '12%', targetLabel: '大促价' },
  { value: 'big_other', label: '其他大促', tier: 'big', rate: '12%', targetLabel: '大促价' },
  { value: 'big618', label: '618大促', tier: 'big618', rate: '15%', targetLabel: '大促价' },
  { value: 'big11', label: '双11大促', tier: 'big618', rate: '15%', targetLabel: '大促价' },
];

/** 立减公式（spec 二节，写给运营看的人话，直接进辅助文字） */
export const TIER_FORMULA: Record<CampaignTier, string> = {
  mid: '单品立减 = 日常价 − 官方立减（日常价×10%，向上取整到元）− 中促价',
  big: '单品立减 = 日常价 − 官方立减（日常价×12%，向上取整到元）− 大促价',
  big618: '单品立减 = 日常价 − 官方立减（日常价×15%，向上取整到元）− 大促价',
};
export const NO_SALES_FORMULA =
  '无动销品：单品立减 = 日常价 − (中促价 + 1)，顾客到手永远 = 中促价 + 1 元（+1 防零头导致未来报名撞线）';
export const SIGNUP_PRICE_RULE = '报名价（活动价）= ERP 日常价（= 标价 × 0.75），全店所有场次统一，永不再变';

/** 状态展示映射（Tag 用） */
export const CAMPAIGN_STATUS_LABEL: Record<CampaignStatus, { label: string; color: string }> = {
  draft: { label: '草稿', color: 'default' },
  precheck: { label: '已预检', color: 'blue' },
  discount_pushed: { label: '立减已推', color: 'cyan' },
  signup_pushed: { label: '报名已推', color: 'geekblue' },
  reconciled: { label: '核对通过', color: 'green' },
  alarmed: { label: '有报警', color: 'red' },
};

// ── CampaignPlan CRUD（后端 _plan_out 字段原样） ──

export interface CampaignPlan {
  id: number;
  name: string;
  campaign_type: CampaignType;
  campaign_type_name?: string;           // 后端补的人话名
  tier: CampaignTier;
  start_at: string | null;               // 'YYYY-MM-DD HH:mm:ss'（档期精确到秒）
  end_at: string | null;
  qn_campaign_title: string | null;      // 千牛活动标题（核对头部校验用，防推错活动）
  price_protection_days: number;         // 规则未确认时默认19，可逐场手动修改
  price_protection_rule_url: string | null;
  price_protection_confirmed_at: string | null;
  price_protection_until: string | null; // 活动结束 + 当前价保天数
  status: CampaignStatus;
  remark?: string | null;
}
export interface CampaignPlanPayload {
  name: string;
  campaign_type: CampaignType;
  start_at: string;
  end_at: string | null;
  qn_campaign_title?: string | null;
  price_protection_days?: number;
  price_protection_rule_url?: string | null;
  remark?: string | null;
}
export interface CampaignListResult {
  items: CampaignPlan[];
  types: Record<string, string>;         // campaign_type → 人话名
}

export const listCampaigns = () =>
  api.get<CampaignListResult>('/api/campaigns').then((r) => r.data);
export const createCampaign = (payload: CampaignPlanPayload) =>
  api.post<CampaignPlan>('/api/campaigns', payload).then((r) => r.data);
export const getCampaign = (id: number) =>
  api.get<CampaignPlan>(`/api/campaigns/${id}`).then((r) => r.data);
export const updateCampaign = (id: number, patch: Partial<CampaignPlanPayload>) =>
  api.put<CampaignPlan>(`/api/campaigns/${id}`, patch).then((r) => r.data);
export const deleteCampaign = (id: number) =>
  api.delete<{ ok: boolean }>(`/api/campaigns/${id}`).then((r) => r.data);
export const remindCampaignPriceProtectionRule = (id: number) =>
  api.post<{ needed: boolean; sent: boolean; deduped?: boolean }>(
    `/api/campaigns/${id}/price-protection/remind`).then((r) => r.data);

// ── 动销分组（spec 四.1/四.2；后端 group_by_sales 原样中文键） ──

export interface NoSalesGroup {
  有动销: string[];                      // 淘宝商品ID 列表
  无动销: string[];
  days: number;                          // 动销窗口（60）
  newly_registered: string[];            // 本次新登记的零动销品
  promote_candidates: string[];          // 已出单的登记品 → 提示转正（不自动移除，R6 单行道）
  registered: string[];                  // no_sales 登记表全量
  item_names: Record<string, string>;    // 商品ID → 产品名
}

export const fetchNoSalesGroup = () =>
  api.get<NoSalesGroup>('/api/campaigns/no-sales-group').then((r) => r.data);
/** 无动销名单一键导出（xlsx blob：产品名/产品编码/淘宝商品ID/近60天单量/建议动作） */
export const downloadNoSalesGroupXlsx = () =>
  api.get('/api/campaigns/no-sales-group/export.xlsx', { responseType: 'blob' }).then((r) => r.data);
/** 无动销名单一键推送飞书（给运营促成交） */
export const pushNoSalesGroupFeishu = () =>
  api.post<{ ok: boolean; sent: boolean; count?: number; message?: string }>(
    '/api/campaigns/no-sales-group/push-feishu')
    .then((r) => r.data);

// ── 预检（spec 三节 R1~R12；后端 preflight checks 原样） ──

export type PrecheckLevel = 'pass' | 'info' | 'warn' | 'error';
/** 单条规则输出（items 结构随规则不同：R6 是商品ID字符串数组，其余是对象数组） */
export interface PrecheckCheck {
  rule: string;                          // 'R1'..'R12'
  level: PrecheckLevel;
  title: string;
  items: unknown[];
  audit?: unknown[];                     // R2 贴线让幅在案记录
}
export interface CampaignPrecheckResult {
  plan: CampaignPlan;                    // 预检后计划（status → precheck）
  checks: PrecheckCheck[];
  has_error: boolean;                    // true = 有阻塞项（level=error），先修再推
}
export const runCampaignPrecheck = (id: number) =>
  api.post<CampaignPrecheckResult>(`/api/campaigns/${id}/precheck`, null, { timeout: 60000 })
    .then((r) => r.data);

/** 行预览：kind=signup 报名行 / discount 单品立减行（stats.rows = 行数） */
export interface CampaignRowsPreview {
  rows: unknown[];
  stats: { rows: number } & Record<string, unknown>;
}
export const fetchCampaignRows = (id: number, kind: 'signup' | 'discount') =>
  api.get<CampaignRowsPreview>(`/api/campaigns/${id}/rows`, { params: { kind }, timeout: 60000 })
    .then((r) => r.data);

// ── 推立减 / 推报名（web_agent 上传编排；R12 导入即生效 → 立减走两段式） ──

/** 推立减阶段：stage=挂文件停在提交前（回千牛校验结果）；commit=★不可逆★真提交 */
export type PushPhase = 'stage' | 'commit';

/** 千牛回执（R10：真相以千牛批量操作记录为准；与既有 activity-upload 回执同构） */
export interface CampaignPushValidation {
  raw?: string;
  ok: number | null;
  failed: number | null;
  failed_sku_codes?: string[];
  total_items?: number;
  unit?: string;
  failed_reasons?: { reason: string; items: number; codes?: string[] }[];
}
export interface CampaignPushResult {
  ok: boolean;
  error?: string | null;
  message?: string | null;
  need_scan?: boolean;                   // 淘宝登录态过期，需先扫码
  job?: string;
  submitted?: boolean;
  validation?: CampaignPushValidation | null;
  screenshot_base64?: string | null;
  stats?: { rows?: number } & Record<string, unknown>;   // builder 统计（行数/剔除原因）
}
/** 推单品立减：先 phase=stage 看校验，用户确认后 phase=commit（R12 每步确认制） */
export const pushCampaignDiscount = (id: number, phase: PushPhase) =>
  api.post<CampaignPushResult>(`/api/campaigns/${id}/push-discount`, null,
    { params: { phase }, timeout: 260000 })
    .then((r) => r.data);
/** 推报名：一次 stage 即生效（promo_signup 导入即报名成功，无第二步提交，R12） */
export const pushCampaignSignup = (id: number) =>
  api.post<CampaignPushResult>(`/api/campaigns/${id}/push-signup`, null, { timeout: 260000 })
    .then((r) => r.data);

// ── 核对（spec 四.6：不带文件=自动 WA 导出；带文件=手动上传兜底） ──

/** 后端判定串：一分不差 / 贴线让X.XX / 超2元报警 / 偏差 / J未刷新 / 占位 / 无映射 */
export const reconVerdictKey = (v: string): string => (v.startsWith('贴线让') ? '贴线' : v);
export const RECON_VERDICT_META: Record<string, { color: string; order: number }> = {
  超2元报警: { color: 'red', order: 0 },
  偏差: { color: 'orange', order: 1 },
  J未刷新: { color: 'gold', order: 2 },
  无映射: { color: 'volcano', order: 3 },
  贴线: { color: 'blue', order: 4 },
  占位: { color: 'default', order: 5 },
  一分不差: { color: 'green', order: 6 },
};
export const reconVerdictMeta = (v: string) =>
  RECON_VERDICT_META[reconVerdictKey(v)] ?? { color: 'default', order: 9 };

export interface CampaignReconRow {
  sku_id: string | null;
  item_id: string | null;
  sku_code: string | null;
  actual: number | null;                 // 千牛「活动商品导出」J列 活动普惠券后价（实际到手）
  target: number | null;                 // 目标到手（大促价/中促价/中促+1）
  diff: number | null;                   // 实际 − 目标
  activity_price: number | null;         // P列 活动价
  verdict: string;
  concession?: number;                   // 贴线让幅（0~1 元在案）
  signup_price_ok?: boolean | null;      // b维度：活动价P列 vs 报名价(=日常价)
}
export interface CampaignReconSummary {
  total: number;
  verdicts: Record<string, number>;      // 中文判定 → 计数（贴线已归并）
  alarm: number;                         // 超2元报警数
  coverage_missing: string[];            // d维度：应报未报 skuId
  coverage_extra: string[];              // 多报 skuId
  discount_mismatch: { sku_id: string | null; expected: number | null;
    actual: number | null; item_id?: string | null }[];   // c维度：立减出入
  title_ok: boolean | null;              // 活动名称头部校验（false=疑推错活动，已报警）
  product_rows_parsed?: number;
}
export interface CampaignReconResult {
  ok: boolean;
  report_id: number;
  summary: CampaignReconSummary;
  rows: CampaignReconRow[];
  alarm_count: number;
}

/** 自动核对：后端驱动 WA 按活动标题找活动→校验标题→导出已报商品→逐SKU比对 */
export const runCampaignRecon = (id: number) =>
  api.post<CampaignReconResult>(`/api/campaigns/${id}/recon`, null, { timeout: 300000 })
    .then((r) => r.data);

/** 手动上传兜底：三种千牛导出文件（spec 七节格式），multipart 同端点（字段名=后端形参） */
export interface ReconManualFiles {
  activity_file?: File;                  // 活动商品导出（跳3行表头，J列=活动普惠券后价）
  discount_file?: File;                  // 单品立减导出（1行表头）
  product_file?: File;                   // 商品批量导出（发布模板 sheet，跳3行）
}
export const runCampaignReconManual = (id: number, files: ReconManualFiles) => {
  const fd = new FormData();
  if (files.activity_file) fd.append('activity_file', files.activity_file);
  if (files.discount_file) fd.append('discount_file', files.discount_file);
  if (files.product_file) fd.append('product_file', files.product_file);
  return api.post<CampaignReconResult>(`/api/campaigns/${id}/recon`, fd, {
    headers: { 'Content-Type': 'multipart/form-data' }, timeout: 120000,
  }).then((r) => r.data);
};

/** 历史核对报告 */
export interface CampaignReconReportItem {
  id: number;
  source: 'auto' | 'manual';
  summary: CampaignReconSummary | null;
  alarm_count: number;
  created_at: string | null;
}
export const fetchCampaignReconReports = (id: number) =>
  api.get<{ items: CampaignReconReportItem[] }>(`/api/campaigns/${id}/recon-reports`)
    .then((r) => r.data);
