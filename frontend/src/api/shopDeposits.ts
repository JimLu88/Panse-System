import { api } from './base';

// ---- 店铺/平台保证金条目 (multi-entry shop deposits) ----
export interface ShopDeposit {
  id: number;
  platform: string | null;
  shop_name: string;
  amount: number;
  remark: string | null;
}

export interface ShopDepositList {
  rows: ShopDeposit[];
  total: number;
  count: number;
}

export interface ShopDepositInput {
  shop_name: string;
  amount: number;
  platform?: string | null;
  remark?: string | null;
}

export const listShopDeposits = () =>
  api.get<ShopDepositList>('/api/finance/shop-deposits').then((r) => r.data);

export const createShopDeposit = (body: ShopDepositInput) =>
  api.post<ShopDeposit>('/api/finance/shop-deposits', body).then((r) => r.data);

export const updateShopDeposit = (id: number, body: Partial<ShopDepositInput>) =>
  api.put<ShopDeposit>(`/api/finance/shop-deposits/${id}`, body).then((r) => r.data);

export const deleteShopDeposit = (id: number) =>
  api.delete(`/api/finance/shop-deposits/${id}`).then((r) => r.data);
