import { Suspense, lazy } from 'react';
import { Avatar, Button, Dropdown, Layout, Menu, Space, Spin, Tag } from 'antd';
import { LogoutOutlined, UserOutlined } from '@ant-design/icons';
import { Link, Navigate, Route, Routes, useLocation } from 'react-router-dom';
import LoginPage from './pages/LoginPage';
import NotificationBell from './components/NotificationBell';
import CommandPalette from './components/CommandPalette';
import { useAuth } from './auth/AuthProvider';

// Phase 12: 全部页面 lazy load — 把 1.6MB bundle 拆成 ~30 个小 chunk, 首屏只装登录所需
const MaterialsPage = lazy(() => import('./pages/MaterialsPage'));
const PartInventoryPage = lazy(() => import('./pages/PartInventoryPage'));
const ExceptionsPage = lazy(() => import('./pages/ExceptionsPage'));
const ProductsPage = lazy(() => import('./pages/ProductsPage'));
const ProductInventoryPage = lazy(() => import('./pages/ProductInventoryPage'));
const BomViewerPage = lazy(() => import('./pages/BomViewerPage'));
const FeishuSettingsPage = lazy(() => import('./pages/FeishuSettingsPage'));
const QuotePage = lazy(() => import('./pages/QuotePage'));
const OrdersPage = lazy(() => import('./pages/OrdersPage'));
const ProducibilityPage = lazy(() => import('./pages/ProducibilityPage'));
const AlipayPage = lazy(() => import('./pages/AlipayPage'));
const ReconciliationPage = lazy(() => import('./pages/ReconciliationPage'));
const AiAssistantPage = lazy(() => import('./pages/AiAssistantPage'));
const MarketingPage = lazy(() => import('./pages/MarketingPage'));
const AdminPage = lazy(() => import('./pages/AdminPage'));
const ImporterPage = lazy(() => import('./pages/ImporterPage'));
const SuppliersPage = lazy(() => import('./pages/SuppliersPage'));
const ReportsPage = lazy(() => import('./pages/ReportsPage'));
const FactorySheetPage = lazy(() => import('./pages/FactorySheetPage'));
const CustomizationPage = lazy(() => import('./pages/CustomizationPage'));
const ScreenshotImportPage = lazy(() => import('./pages/ScreenshotImportPage'));
const AfterSalesPage = lazy(() => import('./pages/AfterSalesPage'));
const ForecastPage = lazy(() => import('./pages/ForecastPage'));
const AssetsPage = lazy(() => import('./pages/AssetsPage'));
const CustomersPage = lazy(() => import('./pages/CustomersPage'));
const OrdersKanbanPage = lazy(() => import('./pages/OrdersKanbanPage'));
const AccountingPeriodsPage = lazy(() => import('./pages/AccountingPeriodsPage'));
const SupplierScoresPage = lazy(() => import('./pages/SupplierScoresPage'));
const PricingPage = lazy(() => import('./pages/PricingPage'));

const { Header, Content } = Layout;

const ROLE_COLOR: Record<string, string> = {
  admin: 'red',
  operator: 'blue',
  viewer: 'default',
};

function PageFallback() {
  return (
    <div style={{ display: 'flex', justifyContent: 'center', padding: 64 }}>
      <Spin tip="加载中..."><div style={{ minHeight: 40 }} /></Spin>
    </div>
  );
}

export default function App() {
  const { user, loading, logout } = useAuth();
  const loc = useLocation();

  if (loading) {
    return (
      <div style={{ display: 'flex', minHeight: '100vh', alignItems: 'center', justifyContent: 'center' }}>
        <Spin tip="加载中..."><div style={{ minHeight: 40 }} /></Spin>
      </div>
    );
  }

  // 未登录 → 只能进 /login
  if (!user) {
    return (
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route path="*" element={<Navigate to="/login" state={{ from: loc.pathname }} replace />} />
      </Routes>
    );
  }

  const seg = loc.pathname.split('/')[1] || 'products';
  const key = seg === 'bom' ? 'products' : seg;

  // 分组下拉, 避免 26 个入口挤成一长条 (顶栏只剩 5 个分组)
  const menuItems = [
    {
      key: 'g-product',
      label: '商品',
      children: [
        { key: 'products', label: <Link to="/products">产品总表</Link> },
        { key: 'pricing', label: <Link to="/pricing">定价表</Link> },
        { key: 'materials', label: <Link to="/materials">物料单价库</Link> },
        { key: 'inventory', label: <Link to="/inventory">配件库存</Link> },
        { key: 'product-inventory', label: <Link to="/product-inventory">成品库存</Link> },
        { key: 'producibility', label: <Link to="/producibility">可生产数</Link> },
        { key: 'customization', label: <Link to="/customization">微定制</Link> },
      ],
    },
    {
      key: 'g-order',
      label: '订单',
      children: [
        { key: 'orders', label: <Link to="/orders">订单</Link> },
        { key: 'orders-kanban', label: <Link to="/orders/kanban">看板</Link> },
        { key: 'customers', label: <Link to="/customers">客户</Link> },
        { key: 'screenshots', label: <Link to="/screenshots">截图录单</Link> },
        { key: 'aftersales', label: <Link to="/aftersales">退货/售后</Link> },
      ],
    },
    {
      key: 'g-finance',
      label: '财务',
      children: [
        { key: 'alipay', label: <Link to="/alipay">支付宝</Link> },
        { key: 'reconciliation', label: <Link to="/reconciliation">对账</Link> },
        { key: 'suppliers', label: <Link to="/suppliers">供应商对账</Link> },
        { key: 'supplier-scores', label: <Link to="/supplier-scores">供应商评分</Link> },
        { key: 'assets', label: <Link to="/assets">资产</Link> },
        { key: 'accounting', label: <Link to="/accounting">会计期间</Link> },
      ],
    },
    {
      key: 'g-analysis',
      label: '分析',
      children: [
        { key: 'reports', label: <Link to="/reports">报表</Link> },
        { key: 'forecast', label: <Link to="/forecast">销售预测</Link> },
        { key: 'quote', label: <Link to="/quote">报价</Link> },
        { key: 'marketing', label: <Link to="/marketing">营销</Link> },
        { key: 'exceptions', label: <Link to="/exceptions">异常</Link> },
      ],
    },
    {
      key: 'g-tools',
      label: '工具',
      children: [
        { key: 'importer', label: <Link to="/importer">Excel 导入</Link> },
        { key: 'ai', label: <Link to="/ai">AI 助手</Link> },
        { key: 'feishu', label: <Link to="/feishu">飞书</Link> },
        ...(user.role === 'admin'
          ? [{ key: 'admin', label: <Link to="/admin">管理</Link> }]
          : []),
      ],
    },
  ];

  return (
    <Layout style={{ minHeight: '100vh' }}>
      <Header style={{ display: 'flex', alignItems: 'center', paddingRight: 24 }}>
        <div style={{ color: 'white', fontWeight: 600, marginRight: 24 }}>
          畔色孚格 ERP
        </div>
        <Menu
          theme="dark"
          mode="horizontal"
          selectedKeys={[key]}
          style={{ flex: 1, minWidth: 0 }}
          items={menuItems}
        />
        <NotificationBell />
        <Dropdown
          menu={{
            items: [
              { key: 'logout', icon: <LogoutOutlined />, label: '退出登录', onClick: logout },
            ],
          }}
        >
          <Space style={{ color: 'white', cursor: 'pointer' }}>
            <Avatar size="small" icon={<UserOutlined />} />
            <span>{user.display_name || user.username}</span>
            <Tag color={ROLE_COLOR[user.role]}>{user.role}</Tag>
          </Space>
        </Dropdown>
      </Header>
      <CommandPalette />
      <Content style={{ padding: 24 }}>
        <Suspense fallback={<PageFallback />}>
          <Routes>
            <Route path="/" element={<Navigate to="/products" replace />} />
            <Route path="/login" element={<Navigate to="/products" replace />} />
            <Route path="/products" element={<ProductsPage />} />
            <Route path="/bom/:productCode" element={<BomViewerPage />} />
            <Route path="/materials" element={<MaterialsPage />} />
            <Route path="/inventory" element={<PartInventoryPage />} />
            <Route path="/product-inventory" element={<ProductInventoryPage />} />
            <Route path="/orders" element={<OrdersPage />} />
            <Route path="/orders/kanban" element={<OrdersKanbanPage />} />
            <Route path="/orders/:orderId/factory-sheet" element={<FactorySheetPage />} />
            <Route path="/customers" element={<CustomersPage />} />
            <Route path="/customization" element={<CustomizationPage />} />
            <Route path="/screenshots" element={<ScreenshotImportPage />} />
            <Route path="/aftersales" element={<AfterSalesPage />} />
            <Route path="/forecast" element={<ForecastPage />} />
            <Route path="/assets" element={<AssetsPage />} />
            <Route path="/supplier-scores" element={<SupplierScoresPage />} />
            <Route path="/accounting" element={<AccountingPeriodsPage />} />
            <Route path="/producibility" element={<ProducibilityPage />} />
            <Route path="/quote" element={<QuotePage />} />
            <Route path="/pricing" element={<PricingPage />} />
            <Route path="/alipay" element={<AlipayPage />} />
            <Route path="/reconciliation" element={<ReconciliationPage />} />
            <Route path="/suppliers" element={<SuppliersPage />} />
            <Route path="/importer" element={<ImporterPage />} />
            <Route path="/ai" element={<AiAssistantPage />} />
            <Route path="/marketing" element={<MarketingPage />} />
            <Route path="/exceptions" element={<ExceptionsPage />} />
            <Route path="/reports" element={<ReportsPage />} />
            <Route path="/feishu" element={<FeishuSettingsPage />} />
            <Route path="/admin" element={<AdminPage />} />
          </Routes>
        </Suspense>
      </Content>
    </Layout>
  );
}
