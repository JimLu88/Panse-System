// Isolated local test harness, not a production entry or authentication bypass.
import React from 'react';
import { createRoot } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ConfigProvider } from 'antd';
import OpsToolsPage from '../src/pages/OpsToolsPage';
createRoot(document.getElementById('root')!).render(<React.StrictMode><QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><ConfigProvider><main style={{ padding: 20, maxWidth: 1280, margin: 'auto' }}><OpsToolsPage /></main></ConfigProvider></QueryClientProvider></React.StrictMode>);
