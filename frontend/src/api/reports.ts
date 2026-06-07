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
}

export const fetchSalesRanking = (params: {
  granularity?: string; metric?: string; period?: string; limit?: number;
}) =>
  api.get<SalesRanking>('/api/reports/sales/ranking', { params }).then((r) => r.data);
