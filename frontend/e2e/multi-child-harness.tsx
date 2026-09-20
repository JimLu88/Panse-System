// Isolated local test only; no production entry and no real account connection.
import React from 'react';
import { createRoot } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ConfigProvider } from 'antd';
import { SalesBreakdownTab } from '../src/pages/ReportsPage';
import SalesRankingPage from '../src/pages/SalesRankingPage';
createRoot(document.getElementById('root')!).render(<QueryClientProvider client={new QueryClient({defaultOptions:{queries:{retry:false}}})}><ConfigProvider><main style={{padding:20,maxWidth:1280,margin:'auto'}}><SalesBreakdownTab /><SalesRankingPage /></main></ConfigProvider></QueryClientProvider>);
