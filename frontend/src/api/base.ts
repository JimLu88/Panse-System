import axios from 'axios';

export const api = axios.create({
  baseURL: '/',
  headers: { 'Content-Type': 'application/json' },
  // 全局 30s 超时: 后端卡住时前端不会无限转圈 (大文件导入/AI 调用各自覆盖更长超时)
  timeout: 30000,
});

// 自动从 localStorage 取 token 加到所有请求
api.interceptors.request.use((config) => {
  const token = localStorage.getItem('panse_token');
  if (token) {
    config.headers = config.headers ?? {};
    (config.headers as Record<string, string>).Authorization = `Bearer ${token}`;
  }
  return config;
});

// Phase 13: 401 时先尝试用 refresh_token 续, 失败才跳登录
let refreshing: Promise<string | null> | null = null;

async function tryRefresh(): Promise<string | null> {
  const rt = localStorage.getItem('panse_refresh_token');
  if (!rt) return null;
  try {
    const r = await axios.post<{ access_token: string }>(
      '/api/auth/refresh', { refresh_token: rt },
    );
    localStorage.setItem('panse_token', r.data.access_token);
    return r.data.access_token;
  } catch {
    localStorage.removeItem('panse_token');
    localStorage.removeItem('panse_refresh_token');
    return null;
  }
}

api.interceptors.response.use(
  (r) => r,
  async (err) => {
    const config = err?.config;
    if (err?.response?.status === 401 && config && !config._retried) {
      config._retried = true;
      // 同一时刻只发一次 refresh 请求
      if (!refreshing) refreshing = tryRefresh();
      const newToken = await refreshing;
      refreshing = null;
      if (newToken) {
        config.headers.Authorization = `Bearer ${newToken}`;
        return api.request(config);
      }
      window.dispatchEvent(new Event('panse:unauthorized'));
    }
    return Promise.reject(err);
  },
);
