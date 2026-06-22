/**
 * 主题上下文 — 浅色/深色切换 + 畔色 ERP 设计系统 AntD 主题 (2026-06-23 接入)。
 * 设计 token 见 styles/design-tokens.css; 这里把同一套主色/中性/圆角/深色映射成 AntD theme,
 * 并同步设置 <html data-theme> 让 CSS 变量(--primary 等)随之切换。模式持久化在 localStorage。
 */
import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from 'react';
import { ConfigProvider, theme as antdTheme } from 'antd';
import zhCN from 'antd/locale/zh_CN';

type Mode = 'light' | 'dark';
interface ThemeCtxValue { mode: Mode; toggle: () => void; setMode: (m: Mode) => void; }
const ThemeCtx = createContext<ThemeCtxValue>({ mode: 'light', toggle: () => {}, setMode: () => {} });
export const useThemeMode = () => useContext(ThemeCtx);

// 字体栈: Noto Sans SC 优先, 回退系统中文字体 (不远程加载, 见 design-tokens.css 说明)
const FONT_SANS =
  '"Noto Sans SC", -apple-system, BlinkMacSystemFont, "SF Pro Text", "PingFang SC", "Microsoft YaHei", "微软雅黑", system-ui, sans-serif';

function buildTheme(mode: Mode) {
  const dark = mode === 'dark';
  const navBg = dark ? '#0a2a5e' : '#174ea6';   // 顶栏深蓝 (--nav-bg)
  return {
    algorithm: dark ? antdTheme.darkAlgorithm : antdTheme.defaultAlgorithm,
    token: {
      fontFamily: FONT_SANS,
      fontSize: 15,
      lineHeight: 1.47,
      colorPrimary: dark ? '#8ab4f8' : '#1a73e8',   // Google 蓝 (深色提亮一档)
      colorLink: dark ? '#8ab4f8' : '#1a73e8',
      colorInfo: dark ? '#8ab4f8' : '#1a73e8',
      colorSuccess: '#10b981',
      colorWarning: '#f59e0b',
      colorError: '#f43f5e',
      borderRadius: 10,     // 控件
      borderRadiusLG: 16,   // 卡片/弹窗 (设计系统主圆角)
      borderRadiusSM: 8,    // 标签
      ...(dark
        ? {
            colorBgLayout: '#0d1320',
            colorBgContainer: '#161d2c',
            colorBgElevated: '#1d2738',
            colorText: '#e7ecf3',
            colorTextSecondary: '#9fabbf',
            colorTextTertiary: '#6b7689',
            colorBorder: '#283246',
            colorBorderSecondary: '#1e2738',
          }
        : {
            colorBgLayout: '#f8fafc',
            colorBgContainer: '#ffffff',
            colorText: '#1e293b',
            colorTextSecondary: '#64748b',
            colorTextTertiary: '#94a3b8',
            colorBorder: '#e8edf2',
            colorBorderSecondary: '#eef2f7',
          }),
    },
    components: {
      Layout: { headerBg: navBg, bodyBg: dark ? '#0d1320' : '#f8fafc' },
      Menu: {
        darkItemBg: 'transparent',
        darkSubMenuItemBg: navBg,
        darkItemColor: 'rgba(255,255,255,.80)',
        darkItemHoverColor: '#ffffff',
        darkItemHoverBg: 'rgba(255,255,255,.10)',
        darkItemSelectedColor: '#ffffff',
        darkItemSelectedBg: 'rgba(138,180,248,.28)',
      },
    },
  };
}

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [mode, setMode] = useState<Mode>(() => (localStorage.getItem('panse_theme') === 'dark' ? 'dark' : 'light'));
  useEffect(() => {
    document.documentElement.setAttribute('data-theme', mode);
    localStorage.setItem('panse_theme', mode);
  }, [mode]);
  const value = useMemo<ThemeCtxValue>(
    () => ({ mode, toggle: () => setMode((m) => (m === 'light' ? 'dark' : 'light')), setMode }),
    [mode],
  );
  return (
    <ThemeCtx.Provider value={value}>
      <ConfigProvider locale={zhCN} theme={buildTheme(mode)}>
        {children}
      </ConfigProvider>
    </ThemeCtx.Provider>
  );
}
