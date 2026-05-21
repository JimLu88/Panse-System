import { createContext, useCallback, useContext, useEffect, useState } from 'react';
import { MeUser, fetchMe, login as apiLogin } from '../api/client';

interface AuthCtx {
  user: MeUser | null;
  loading: boolean;
  login: (username: string, password: string) => Promise<void>;
  logout: () => void;
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
    setUser(r.user);
  }, []);

  const logout = useCallback(() => {
    localStorage.removeItem('panse_token');
    setUser(null);
  }, []);

  return <Ctx.Provider value={{ user, loading, login, logout }}>{children}</Ctx.Provider>;
}

export function useAuth(): AuthCtx {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error('useAuth must be used within AuthProvider');
  return ctx;
}
