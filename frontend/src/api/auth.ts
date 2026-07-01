import { api } from './base';

// ----- Auth (Phase 6) -----
export interface MeUser {
  id: number;
  username: string;
  display_name: string | null;
  role: string;
  is_active: boolean;
  must_change_password?: boolean;
  // 子账号页面权限: null/缺省=不受限(全看); string[]=只可见这些页面 permKey
  page_perms?: string[] | null;
}

export const login = (username: string, password: string) =>
  api
    .post<{
      token: string;
      access_token?: string;
      refresh_token?: string;
      user: MeUser;
    }>('/api/auth/login', { username, password })
    .then((r) => r.data);

export const fetchMe = () => api.get<MeUser>('/api/auth/me').then((r) => r.data);

export const listAuthUsers = () =>
  api.get<MeUser[]>('/api/auth/users').then((r) => r.data);

export const createUser = (payload: {
  username: string;
  password: string;
  role: string;
  display_name?: string;
  page_perms?: string[] | null;
}) => api.post<MeUser>('/api/auth/users', payload).then((r) => r.data);

export const updateUser = (
  id: number,
  payload: {
    username?: string; display_name?: string; role?: string; is_active?: boolean;
    page_perms?: string[] | null;
  },
) => api.patch<MeUser>(`/api/auth/users/${id}`, payload).then((r) => r.data);

export const adminResetPassword = (id: number, newPassword: string) =>
  api.post(`/api/auth/users/${id}/password`, { new_password: newPassword }).then((r) => r.data);

export const changeMyPassword = (oldPassword: string, newPassword: string) =>
  api
    .post('/api/auth/me/password', { old_password: oldPassword, new_password: newPassword })
    .then((r) => r.data);

export const fetchRoles = () =>
  api
    .get<{ roles: string[]; descriptions: Record<string, string> }>('/api/auth/roles')
    .then((r) => r.data);
