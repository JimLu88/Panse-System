import React from 'react';
import ReactDOM from 'react-dom/client';
import { BrowserRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ConfigProvider } from 'antd';
import zhCN from 'antd/locale/zh_CN';
import App from './App';
import { AuthProvider } from './auth/AuthProvider';
import ErrorBoundary from './components/ErrorBoundary';
import './styles/global.css';

const qc = new QueryClient({
  defaultOptions: { queries: { staleTime: 5_000, refetchOnWindowFocus: false } },
});

// 苹果排版规范 token: 字号/行距/文字色/主色 (字体栈+字距见 global.css)
const APPLE_THEME = {
  token: {
    fontFamily:
      '-apple-system, BlinkMacSystemFont, "SF Pro Text", "SF Pro Display", "Helvetica Neue", "PingFang SC", "Microsoft YaHei", system-ui, sans-serif',
    fontSize: 15, // 苹果内容字号 (介于 macOS 13 与 iOS 17 之间, 兼顾桌面表格密度)
    lineHeight: 1.47, // 苹果正文行距
    colorPrimary: '#0071e3', // 苹果蓝 (按钮/链接/选中)
    colorLink: '#0071e3',
    colorText: '#1d1d1f', // 苹果主文字色
    colorTextSecondary: '#6e6e73', // 次要文字
    colorTextTertiary: '#86868b', // 三级/占位文字
  },
};

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <ConfigProvider locale={zhCN} theme={APPLE_THEME}>
      <QueryClientProvider client={qc}>
        <BrowserRouter>
          <AuthProvider>
            <ErrorBoundary>
              <App />
            </ErrorBoundary>
          </AuthProvider>
        </BrowserRouter>
      </QueryClientProvider>
    </ConfigProvider>
  </React.StrictMode>,
);
