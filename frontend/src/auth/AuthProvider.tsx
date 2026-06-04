import { createContext, useCallback, useContext, useEffect, useState } from 'react';
import { MeUser, fetchMe, login as apiLogin } from '../api/client';

interface AuthCtx {
  user: MeUser | null;
  loading: boolean;
  login: (username: string, password: string) => Promise<void>;
  logout: () => void;
  refreshUser: () => Promise<void>;
}

const Ctx = createContext<AuthCtx | null>(null);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<MeUser | null>(null);
  const [loading, setLoading] = useState(true);

  // 启动时如果本地有 token, 尝试拉一次 /me 验证
  useEffect(() => {
    const token = localStorage.getItem('panse_token');
    if (!token) {
      setLoading(false);
      return;
    }
    fetchMe()
      .then((u) => setUser(u))
      .catch(() => {
        localStorage.removeItem('panse_token');
      })
      .finally(() => setLoading(false));
  }, []);

  // 监听 401 事件 (axios interceptor 触发)
  useEffect(() => {
    const handler = () => setUser(null);
    window.addEventListener('panse:unauthorized', handler);
    return () => window.removeEventListener('panse:unauthorized', handler);
  }, []);

  const login = useCallback(async (username: string, password: string) => {
    const r = await apiLogin(username, password);
    localStorage.setItem('panse_token', r.token);
    // Phase 13: 同时保存 refresh_token (用于 401 自动续)
    if ((r as any).refresh_token) {
      localStorage.setItem('panse_refresh_token', (r as any).refresh_token);
    }
    setUser(r.user);
  }, []);

  const logout = useCallback(() => {
    localStorage.removeItem('panse_token');
    localStorage.removeItem('panse_refresh_token');
    setUser(null);
  }, []);

  // 改密成功后重新拉 /me, 清掉 must_change_password 标记
  const refreshUser = useCallback(async () => {
    try {
      setUser(await fetchMe());
    } catch {
      /* 忽略: 拉取失败保持原状 */
    }
  }, []);

  return (
    <Ctx.Provider value={{ user, loading, login, logout, refreshUser }}>{children}</Ctx.Provider>
  );
}

export function useAuth(): AuthCtx {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error('useAuth must be used within AuthProvider');
  return ctx;
}
