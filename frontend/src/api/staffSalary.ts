import { api } from './base';

// ---- 人员/工资管理 (G) ----
// 外包成本口径挂钩: 月度外包预估 = Σ 当月在职人员月工资 (替代写死 ¥10000)。
export interface StaffSalary {
  id: number;
  name: string;
  monthly_cost: number;
  role: string | null;
  active_from: string | null; // YYYY-MM-DD
  active_to: string | null;   // YYYY-MM-DD | null=至今
  remark: string | null;
}

export interface StaffSalaryList {
  rows: StaffSalary[];
  count: number;
  current_month_total: number;
  current_year: number;
  current_month: number;
}

export interface StaffSalaryInput {
  name: string;
  monthly_cost: number;
  active_from: string;
  role?: string | null;
  active_to?: string | null;
  remark?: string | null;
}

export interface MonthlyTotal {
  year: number;
  month: number;
  total: number;
  active_count: number;
  active: StaffSalary[];
}

export const fetchStaffSalaries = () =>
  api.get<StaffSalaryList>('/api/staff-salaries').then((r) => r.data);

export const createStaffSalary = (body: StaffSalaryInput) =>
  api.post<StaffSalary>('/api/staff-salaries', body).then((r) => r.data);

export const updateStaffSalary = (id: number, body: Partial<StaffSalaryInput>) =>
  api.put<StaffSalary>(`/api/staff-salaries/${id}`, body).then((r) => r.data);

export const deleteStaffSalary = (id: number) =>
  api.delete(`/api/staff-salaries/${id}`).then((r) => r.data);

export const fetchMonthlyTotal = (year: number, month: number) =>
  api
    .get<MonthlyTotal>('/api/staff-salaries/monthly-total', { params: { year, month } })
    .then((r) => r.data);
