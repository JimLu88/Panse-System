import { Layout, Menu } from 'antd';
import { Link, Navigate, Route, Routes, useLocation } from 'react-router-dom';
import MaterialsPage from './pages/MaterialsPage';
import PartInventoryPage from './pages/PartInventoryPage';
import ExceptionsPage from './pages/ExceptionsPage';

const { Header, Content } = Layout;

export default function App() {
  const loc = useLocation();
  const key = loc.pathname.split('/')[1] || 'inventory';
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
            { key: 'inventory', label: <Link to="/inventory">配件库存</Link> },
            { key: 'materials', label: <Link to="/materials">物料单价库</Link> },
            { key: 'exceptions', label: <Link to="/exceptions">异常处理</Link> },
          ]}
        />
      </Header>
      <Content style={{ padding: 24 }}>
        <Routes>
          <Route path="/" element={<Navigate to="/inventory" replace />} />
          <Route path="/inventory" element={<PartInventoryPage />} />
          <Route path="/materials" element={<MaterialsPage />} />
          <Route path="/exceptions" element={<ExceptionsPage />} />
        </Routes>
      </Content>
    </Layout>
  );
}
