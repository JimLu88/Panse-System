import { api } from './base';

// 销售排行榜 (/api/reports/sales/ranking)
export interface RankRow {
  rank: number;
  product_code: string | null;
  product_name: string;
  qty: number;
  revenue: number;
  order_count: number;
}

export interface RankPeriod {
  period: string;
  champion_name: string | null;
  champion_qty: number;
  champion_revenue: number;
  total_qty: number;
  total_revenue: number;
  product_kinds: number;
}

export interface SalesRanking {
  granularity: 'month' | 'year';
  metric: 'revenue' | 'qty';
  selected_period: string | null;
  periods: RankPeriod[];
  ranking: RankRow[];
  excluded_non_product: number;
  refund_excluded?: boolean;   // #25 销售额=实付-退款 (已去退款)
}

export const fetchSalesRanking = (params: {
  granularity?: string; metric?: string; period?: string; limit?: number;
}) =>
  api.get<SalesRanking>('/api/reports/sales/ranking', { params }).then((r) => r.data);

// 月度经营 (/api/reports/monthly-pnl) — 工厂口径利润/ROI + 对账完成度标识
export interface MonthlyPnlRow {
  period: string;
  total_revenue: number | null;
  real_revenue: number | null;
  refill_revenue: number | null;
  total_expense: number | null;
  net_profit: number | null;
  net_profit_rate: number | null;
  recon_status: 'accurate' | 'reference_only';
  unbalanced_factories: number;
  factory_recon_count: number;
  promo_roi: number | null;
  promo_spend: number | null;
  promo_spend_ratio: number | null;
  cumulative_profit: number;
  recovery_rate: number | null;
}
export interface MonthlyPnl {
  total_investment: number;
  rows: MonthlyPnlRow[];
}
export const fetchMonthlyPnl = () =>
  api.get<MonthlyPnl>('/api/reports/monthly-pnl').then((r) => r.data);

// 月度销售占比 (/api/reports/sales-mix) — 饼图数据
export interface SalesMixSlice { name: string; revenue: number; qty: number; pct: number; }
export interface SalesMix {
  period: string; by: string; total_revenue: number; total_qty: number; slices: SalesMixSlice[];
}
export const fetchSalesMix = (year: number, month: number, by = 'product') =>
  api.get<SalesMix>('/api/reports/sales-mix', { params: { year, month, by } }).then((r) => r.data);
