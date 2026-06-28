/**
 * BOM 尺寸复核 API (配件 epic 阶段1d)。
 * AI 按 SKU 推演的面积料(岩板/玻璃/洞石)尺寸 → 人工核对/编辑/二次确认。
 * 口径: est_size 只是预估(size_status=inferred), 不动原 remark; 确认后 confirmed。
 */
import { api } from './base';

export interface SizeReviewRow {
  id: number;
  product_code: string;
  product_name?: string | null;
  sku?: string | null;
  material_code: string;
  material_name?: string | null;
  category?: string | null;
  remark?: string | null;
  est_size?: string | null;
  size_status?: string | null;
  area?: number | null;
}

export interface SizeInferResult {
  ok: boolean;
  categories: string[];
  missing: number;
  inferred: number;
  applied: number;
  by_source: Record<string, number>;
  items: Array<Record<string, unknown>>;
}

export async function listSizeReview(
  status: 'inferred' | 'confirmed' | 'all' = 'inferred',
  category?: string,
): Promise<SizeReviewRow[]> {
  const params: Record<string, string> = { status };
  if (category) params.category = category;
  return api.get<SizeReviewRow[]>('/api/bom/size-review', { params }).then((r) => r.data);
}

export async function patchSizeReview(
  lineId: number,
  estSize: string,
  confirm: boolean,
): Promise<SizeReviewRow> {
  return api
    .patch<SizeReviewRow>(`/api/bom/size-review/${lineId}`, { est_size: estSize, confirm })
    .then((r) => r.data);
}

export async function runSizeInference(
  categories: string[] | null,
  apply: boolean,
  useAi = false,
): Promise<SizeInferResult> {
  return api
    .post<SizeInferResult>('/api/bom/size-review/run', { categories, apply, use_ai: useAi })
    .then((r) => r.data);
}
