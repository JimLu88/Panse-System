import { Avatar, Button, Dropdown, Layout, Menu, Space, Spin, Tag } from 'antd';
import { LogoutOutlined, UserOutlined } from '@ant-design/icons';
import { Link, Navigate, Route, Routes, useLocation } from 'react-router-dom';
import MaterialsPage from './pages/MaterialsPage';
import PartInventoryPage from './pages/PartInventoryPage';
import ExceptionsPage from './pages/ExceptionsPage';
import ProductsPage from './pages/ProductsPage';
import ProductInventoryPage from './pages/ProductInventoryPage';
import BomViewerPage from './pages/BomViewerPage';
import FeishuSettingsPage from './pages/FeishuSettingsPage';
import QuotePage from './pages/QuotePage';
import OrdersPage from './pages/OrdersPage';
import ProducibilityPage from './pages/ProducibilityPage';
import AlipayPage from './pages/AlipayPage';
import ReconciliationPage from './pages/ReconciliationPage';
import AiAssistantPage from './pages/AiAssistantPage';
import MarketingPage from './pages/MarketingPage';
import LoginPage from './pages/LoginPage';
import AdminPage from './pages/AdminPage';
import { useAuth } from './auth/AuthProvider';

const { Header, Content } = Layout;

const ROLE_COLOR: Record<string, string> = {
  admin: 'red',
  operator: 'blue',
  viewer: 'default',
};

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

  const menuItems = [
    { key: 'products', label: <Link to="/products">产品总表</Link> },
    { key: 'materials', label: <Link to="/materials">物料单价库</Link> },
    { key: 'inventory', label: <Link to="/inventory">配件库存</Link> },
    { key: 'product-inventory', label: <Link to="/product-inventory">成品库存</Link> },
    { key: 'orders', label: <Link to="/orders">订单</Link> },
    { key: 'producibility', label: <Link to="/producibility">可生产数</Link> },
    { key: 'quote', label: <Link to="/quote">报价</Link> },
    { key: 'alipay', label: <Link to="/alipay">支付宝</Link> },
    { key: 'reconciliation', label: <Link to="/reconciliation">对账</Link> },
    { key: 'marketing', label: <Link to="/marketing">营销/售后</Link> },
    { key: 'exceptions', label: <Link to="/exceptions">异常</Link> },
    { key: 'ai', label: <Link to="/ai">AI 助手</Link> },
    { key: 'feishu', label: <Link to="/feishu">飞书</Link> },
    ...(user.role === 'admin' ? [{ key: 'admin', label: <Link to="/admin">管理</Link> }] : []),
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
      <Content style={{ padding: 24 }}>
        <Routes>
          <Route path="/" element={<Navigate to="/products" replace />} />
          <Route path="/login" element={<Navigate to="/products" replace />} />
          <Route path="/products" element={<ProductsPage />} />
          <Route path="/bom/:productCode" element={<BomViewerPage />} />
          <Route path="/materials" element={<MaterialsPage />} />
          <Route path="/inventory" element={<PartInventoryPage />} />
          <Route path="/product-inventory" element={<ProductInventoryPage />} />
          <Route path="/orders" element={<OrdersPage />} />
          <Route path="/producibility" element={<ProducibilityPage />} />
          <Route path="/quote" element={<QuotePage />} />
          <Route path="/alipay" element={<AlipayPage />} />
          <Route path="/reconciliation" element={<ReconciliationPage />} />
          <Route path="/ai" element={<AiAssistantPage />} />
          <Route path="/marketing" element={<MarketingPage />} />
          <Route path="/exceptions" element={<ExceptionsPage />} />
          <Route path="/feishu" element={<FeishuSettingsPage />} />
          <Route path="/admin" element={<AdminPage />} />
        </Routes>
      </Content>
    </Layout>
  );
}
