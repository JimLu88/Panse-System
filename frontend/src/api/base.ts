import axios from 'axios';

export const api = axios.create({
  baseURL: '/',
  headers: { 'Content-Type': 'application/json' },
  // 全局 30s 超时: 后端卡住时前端不会无限转圈 (大文件导入/AI 调用各自覆盖更长超时)
  timeout: 30000,
});

// 幂等防重复 (优化 #2): 给写请求自动带 Idempotency-Key。3 秒内完全相同的请求
// (同方法+URL+body) 复用同一 key, 让后端 409 拦掉双击/弱网重试的重复提交;
// 不同操作各自新 key, 互不影响。失败后端会释放 key, 真重试不受阻。
const _recentKeys = new Map<string, { key: string; ts: number }>();
function _idempotencyKey(method: string, url: string, data: unknown): string {
  const fp = `${method} ${url} ${typeof data === 'string' ? data : JSON.stringify(data ?? '')}`;
  const now = Date.now();
  const prev = _recentKeys.get(fp);
  const key = prev && now - prev.ts < 3000
    ? prev.key
    : (typeof crypto !== 'undefined' && crypto.randomUUID ? crypto.randomUUID() : `${now}-${Math.random()}`);
  _recentKeys.set(fp, { key, ts: now });
  if (_recentKeys.size > 500) {
    for (const [k, v] of _recentKeys) if (now - v.ts > 10000) _recentKeys.delete(k);
  }
  return key;
}

// 自动从 localStorage 取 token 加到所有请求 + 写请求带幂等 key
api.interceptors.request.use((config) => {
  config.headers = config.headers ?? {};
  const token = localStorage.getItem('panse_token');
  if (token) {
    (config.headers as Record<string, string>).Authorization = `Bearer ${token}`;
  }
  const method = (config.method || 'get').toLowerCase();
  // 跳过文件上传 (FormData 无法序列化指纹, 否则两次不同上传会误判重复)
  const isUpload = typeof FormData !== 'undefined' && config.data instanceof FormData;
  if (!isUpload && (method === 'post' || method === 'put' || method === 'patch')) {
    (config.headers as Record<string, string>)['Idempotency-Key'] =
      _idempotencyKey(method, config.url || '', config.data);
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
    // 错误关联 (优化 #8): 记下后端请求 ID, 前后端日志可对上号 (后端请求日志已带同一 ID)
    const rid = err?.response?.headers?.['x-request-id'];
    if (rid) {
      // eslint-disable-next-line no-console
      console.error(
        `[请求失败] ${(config?.method || '').toUpperCase()} ${config?.url || ''} `
        + `状态=${err?.response?.status} 请求ID=${rid}`,
      );
      err.requestId = rid;
    }
    return Promise.reject(err);
  },
);
