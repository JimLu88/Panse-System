// 评价资产台账 API (Plan1 v2)
import { api } from './base';

export interface ReviewAssetRow {
  id: number;
  order_id: number | null;
  order_no: string;
  shop: string | null;
  product_code: string | null;
  sku_name: string | null;
  review_date: string | null;
  image_count: number;
  rating: number | null;
  review_text: string | null;
  fold_due_date: string | null;
  days_to_fold: number | null;
  status: string;
  source: string;
  screenshot_file_id: number | null;
  remark: string | null;
  created_at: string | null;
}

export interface ReviewStats {
  near_fold: number;
  pending_overdue: number;
  new_this_month: number;
  low_coverage_products: number;
  fold_days: number;
  pending_timeout_days: number;
  coverage_min: number;
}

export interface ReviewCoverageRow {
  product_code: string;
  active_image_reviews: number;
  last_review_date: string | null;
  next_fold_date: string | null;
  below_min: boolean;
  coverage_min: number;
}

export interface ReviewSettings {
  fold_days: number;
  pending_timeout_days: number;
  coverage_min: number;
}

export interface ReviewImportResult {
  inserted: number;
  skipped_duplicate: number;
  skipped_invalid: number;
  unlinked: number;
  archived: boolean;
}

export const listReviewAssets = (params?: {
  status?: string;
  product_code?: string;
  shop?: string;
  source?: string;
  due_in_days?: number;
  keyword?: string;
  limit?: number;
  offset?: number;
}) => api.get<{ items: ReviewAssetRow[] }>('/api/review-assets', { params }).then((r) => r.data.items);

export const reviewStats = () =>
  api.get<ReviewStats>('/api/review-assets/stats').then((r) => r.data);

export const reviewCoverage = () =>
  api.get<{ items: ReviewCoverageRow[] }>('/api/review-assets/coverage').then((r) => r.data.items);

export const createReviewAsset = (payload: {
  order_no: string;
  review_date?: string | null;
  image_count?: number;
  rating?: number | null;
  review_text?: string | null;
  product_code?: string | null;
  sku_name?: string | null;
  shop?: string | null;
  source?: string | null;
  remark?: string | null;
}) => api.post<ReviewAssetRow>('/api/review-assets', payload).then((r) => r.data);

export const patchReviewAsset = (id: number, payload: Record<string, unknown>) =>
  api.patch<ReviewAssetRow>(`/api/review-assets/${id}`, payload).then((r) => r.data);

export const deleteReviewAsset = (id: number) =>
  api.delete(`/api/review-assets/${id}`).then((r) => r.data);

export const reviewFromOrder = (orderId: number) =>
  api
    .post<{ created: boolean; asset: ReviewAssetRow }>(`/api/review-assets/from-order/${orderId}`)
    .then((r) => r.data);

export const importReviewAssets = (file: File) => {
  const fd = new FormData();
  fd.append('file', file);
  return api
    .post<ReviewImportResult>('/api/review-assets/import', fd, {
      headers: { 'Content-Type': 'multipart/form-data' },
    })
    .then((r) => r.data);
};

export const getReviewSettings = () =>
  api.get<ReviewSettings>('/api/review-assets/settings').then((r) => r.data);

export const putReviewSettings = (payload: Partial<ReviewSettings>) =>
  api.put<ReviewSettings>('/api/review-assets/settings', payload).then((r) => r.data);

// 模板下载: 直接开新窗走浏览器下载 (端点无鉴权副作用, 照 refill 模式 window.open)
export const reviewTemplateUrl = '/api/review-assets/import/template';
