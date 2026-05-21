import { Layout, Menu } from 'antd';
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

const { Header, Content } = Layout;

export default function App() {
  const loc = useLocation();
  const seg = loc.pathname.split('/')[1] || 'products';
  // /bom/:code 也归到 "products" 菜单高亮
  const key = seg === 'bom' ? 'products' : seg;
  return (
    <Layout style={{ minHeight: '100vh' }}>
      <Header style={{ display: 'flex', alignItems: 'center' }}>
        <div style={{ color: 'white', fontWeight: 600, marginRight: 32 }}>
          畔色孚格 ERP
        </div>
        <Menu
          theme="dark"
          mode="horizontal"
          selectedKeys={[key]}
          style={{ flex: 1, minWidth: 0 }}
          items={[
            { key: 'products', label: <Link to="/products">产品总表</Link> },
            { key: 'materials', label: <Link to="/materials">物料单价库</Link> },
            { key: 'inventory', label: <Link to="/inventory">配件库存</Link> },
            { key: 'product-inventory', label: <Link to="/product-inventory">成品库存</Link> },
            { key: 'orders', label: <Link to="/orders">订单</Link> },
            { key: 'producibility', label: <Link to="/producibility">可生产数</Link> },
            { key: 'quote', label: <Link to="/quote">报价计算</Link> },
            { key: 'alipay', label: <Link to="/alipay">支付宝流水</Link> },
            { key: 'reconciliation', label: <Link to="/reconciliation">对账</Link> },
            { key: 'exceptions', label: <Link to="/exceptions">异常处理</Link> },
            { key: 'ai', label: <Link to="/ai">AI 助手</Link> },
            { key: 'feishu', label: <Link to="/feishu">飞书同步</Link> },
          ]}
        />
      </Header>
      <Content style={{ padding: 24 }}>
        <Routes>
          <Route path="/" element={<Navigate to="/products" replace />} />
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
          <Route path="/exceptions" element={<ExceptionsPage />} />
          <Route path="/feishu" element={<FeishuSettingsPage />} />
        </Routes>
      </Content>
    </Layout>
  );
}
