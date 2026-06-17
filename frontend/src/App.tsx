import { Suspense, lazy } from 'react';
import { Avatar, Button, Dropdown, Layout, Menu, Space, Spin, Tag } from 'antd';
import { CameraOutlined, EditOutlined, LogoutOutlined, SearchOutlined, UserOutlined } from '@ant-design/icons';
import { Link, Navigate, Route, Routes, useLocation, useNavigate } from 'react-router-dom';
import LoginPage from './pages/LoginPage';
import ForcePasswordChange from './components/ForcePasswordChange';
import NotificationBell from './components/NotificationBell';
import CommandPalette from './components/CommandPalette';
import AiAssistantWidget from './components/AiAssistantWidget';
import VersionTag from './components/VersionTag';
import { useAuth } from './auth/AuthProvider';

// 全部页面 lazy load
const MaterialsPage = lazy(() => import('./pages/MaterialsPage'));
const WebAgentPage = lazy(() => import('./pages/WebAgentPage'));
const PartInventoryPage = lazy(() => import('./pages/PartInventoryPage'));
const ExceptionsPage = lazy(() => import('./pages/ExceptionsPage'));
const ProductsPage = lazy(() => import('./pages/ProductsPage'));
const ProductInventoryPage = lazy(() => import('./pages/ProductInventoryPage'));
const SampleInventoryPage = lazy(() => import('./pages/SampleInventoryPage'));
const BomViewerPage = lazy(() => import('./pages/BomViewerPage'));
const BomListPage = lazy(() => import('./pages/BomListPage'));
const FeishuSettingsPage = lazy(() => import('./pages/FeishuSettingsPage'));
const QuotePage = lazy(() => import('./pages/QuotePage'));
const OrdersPage = lazy(() => import('./pages/OrdersPage'));
const ProducibilityPage = lazy(() => import('./pages/ProducibilityPage'));
const AlipayPage = lazy(() => import('./pages/AlipayPage'));
const AccountBalancesPage = lazy(() => import('./pages/AccountBalancesPage'));
const ReconciliationPage = lazy(() => import('./pages/ReconciliationPage'));
const AiAssistantPage = lazy(() => import('./pages/AiAssistantPage'));
const MarketingPage = lazy(() => import('./pages/MarketingPage'));
const AdminPage = lazy(() => import('./pages/AdminPage'));
const OpsToolsPage = lazy(() => import('./pages/OpsToolsPage'));
const ImporterPage = lazy(() => import('./pages/ImporterPage'));
const SuppliersPage = lazy(() => import('./pages/SuppliersPage'));
const ReportsPage = lazy(() => import('./pages/ReportsPage'));
const FactorySheetPage = lazy(() => import('./pages/FactorySheetPage'));
const CustomizationPage = lazy(() => import('./pages/CustomizationPage'));
const ScreenshotImportPage = lazy(() => import('./pages/ScreenshotImportPage'));
const AfterSalesPage = lazy(() => import('./pages/AfterSalesPage'));
const ForecastPage = lazy(() => import('./pages/ForecastPage'));
const AssetsPage = lazy(() => import('./pages/AssetsPage'));
const AssetsCashflowPage = lazy(() => import('./pages/AssetsCashflowPage'));
const CustomersPage = lazy(() => import('./pages/CustomersPage'));
const OrdersKanbanPage = lazy(() => import('./pages/OrdersKanbanPage'));
const AccountingPeriodsPage = lazy(() => import('./pages/AccountingPeriodsPage'));
const SupplierScoresPage = lazy(() => import('./pages/SupplierScoresPage'));
const PricingPage = lazy(() => import('./pages/PricingPage'));
const PricingFormulaPage = lazy(() => import('./pages/PricingFormulaPage'));
const DashboardPage = lazy(() => import('./pages/DashboardPage'));
const OpsChecklistPage = lazy(() => import('./pages/OpsChecklistPage'));
const SalesRankingPage = lazy(() => import('./pages/SalesRankingPage'));
const PurchasesPage = lazy(() => import('./pages/PurchasesPage'));
const FactoryOrdersPage = lazy(() => import('./pages/FactoryOrdersPage'));
const FactoryStatementPage = lazy(() => import('./pages/FactoryStatementPage'));
const TaobaoListingsPage = lazy(() => import('./pages/TaobaoListingsPage'));
const NewProductComposerPage = lazy(() => import('./pages/NewProductComposerPage'));
const CustomQuoteV2Page = lazy(() => import('./pages/CustomQuoteV2Page'));
const WanshifuBillsPage = lazy(() => import('./pages/WanshifuBillsPage'));
const LogisticsBillsPage = lazy(() => import('./pages/LogisticsBillsPage'));
const RefillRecordsPage = lazy(() => import('./pages/RefillRecordsPage'));
const CashFlowPage = lazy(() => import('./pages/CashFlowPage'));
const DataExplorerPage = lazy(() => import('./pages/DataExplorerPage'));
const SettlementsPage = lazy(() => import('./pages/SettlementsPage'));
const ImportArchivePage = lazy(() => import('./pages/ImportArchivePage'));
const AuditTrailPage = lazy(() => import('./pages/AuditTrailPage'));
const DataExportPage = lazy(() => import('./pages/DataExportPage'));
const ReconDiagnosticsPage = lazy(() => import('./pages/ReconDiagnosticsPage'));
const PrepayLedgerPage = lazy(() => import('./pages/PrepayLedgerPage'));
const FactoryReconPage = lazy(() => import('./pages/FactoryReconPage'));
const ReconCenterPage = lazy(() => import('./pages/ReconCenterPage'));
const PromotionFlowsPage = lazy(() => import('./pages/PromotionFlowsPage'));

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

// 路径 → 所属导航 group 映射
const PATH_TO_GROUP: Record<string, string> = {
  dashboard: 'g-data', forecast: 'g-data', 'ops-checklist': 'g-data', 'sales-ranking': 'g-data',
  products: 'g-product', 'new-product': 'g-product', 'taobao-listings': 'g-product', bom: 'g-product',
  pricing: 'g-price', 'pricing-formulas': 'g-price', quote: 'g-price', customization: 'g-price',
  inventory: 'g-stock', 'product-inventory': 'g-stock', producibility: 'g-stock', samples: 'g-stock',
  orders: 'g-order', customers: 'g-order', aftersales: 'g-order',
  'logistics-bills': 'g-logistics', 'wanshifu-bills': 'g-logistics',
  marketing: 'g-marketing',
  suppliers: 'g-supply', 'supplier-scores': 'g-supply', purchases: 'g-supply', materials: 'g-supply',
  'cash-flow': 'g-finance', alipay: 'g-finance', 'account-balances': 'g-finance',
  reconciliation: 'g-finance', 'refill-records': 'g-finance', 'recon-diagnostics': 'g-finance', 'prepay-ledger': 'g-finance',
  'factory-recon': 'g-finance', 'shop-deposits': 'g-finance', 'promotion-flows': 'g-finance',
  accounting: 'g-finance', assets: 'g-finance', 'assets-cashflow': 'g-finance',
  reports: 'g-analysis', exceptions: 'g-analysis',
  importer: 'g-tools', 'data-explorer': 'g-tools', 'import-archive': 'g-tools', feishu: 'g-tools', admin: 'g-tools',
  'web-agent': 'g-tools', 'audit-trail': 'g-tools', 'data-export': 'g-tools', 'ops-tools': 'g-tools',
};

export default function App() {
  const { user, loading, logout } = useAuth();
  const loc = useLocation();
  const nav = useNavigate();

  if (loading) {
    return (
      <div style={{ display: 'flex', minHeight: '100vh', alignItems: 'center', justifyContent: 'center' }}>
        <Spin tip="加载中..."><div style={{ minHeight: 40 }} /></Spin>
      </div>
    );
  }

  if (!user) {
    return (
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route path="*" element={<Navigate to="/login" state={{ from: loc.pathname }} replace />} />
      </Routes>
    );
  }

  // 强制改密: 仍在用默认/弱密码的账号, 改密前全屏阻断
  if (user.must_change_password) {
    return <ForcePasswordChange />;
  }

  const seg = loc.pathname.split('/')[1] || 'dashboard';
  const group = PATH_TO_GROUP[seg] || '';
  const selectedKeys = group ? [seg, group] : [seg];

  const menuItems = [
    {
      key: 'g-data',
      label: '数据分析',
      children: [
        { key: 'dashboard', label: <Link to="/dashboard">数据大盘</Link> },
        { key: 'sales-ranking', label: <Link to="/sales-ranking">销售排行榜</Link> },
        { key: 'forecast', label: <Link to="/forecast">销售预测</Link> },
        { key: 'reports', label: <Link to="/reports">报表</Link> },  // #分析合并: 报表并入数据分析
      ],
    },
    {
      key: 'g-product',
      label: '产品',
      children: [
        { key: 'products', label: <Link to="/products">产品总表</Link> },
        { key: 'bom-list', label: <Link to="/bom-list">BOM 清单</Link> },
        { key: 'materials', label: <Link to="/materials">物料单价库</Link> },
        { key: 'new-product', label: <Link to="/new-product">新产品录入</Link> },
        { key: 'taobao-listings', label: <Link to="/taobao-listings">淘宝对应表</Link> },
      ],
    },
    {
      key: 'g-price',
      label: '价格',
      children: [
        { key: 'pricing', label: <Link to="/pricing">定价表</Link> },
        { key: 'customization', label: <Link to="/customization?tab=competitor">竞品价库</Link> },
        { key: 'custom-settings', label: <Link to="/customization?tab=settings">报价参数</Link> },
      ],
    },
    {
      key: 'g-stock',
      label: '库存',
      children: [
        { key: 'inventory', label: <Link to="/inventory">配件库存</Link> },
        { key: 'product-inventory', label: <Link to="/product-inventory">成品库存</Link> },
        { key: 'samples', label: <Link to="/samples">样品库存</Link> },
        { key: 'producibility', label: <Link to="/producibility">可生产数</Link> },
        { key: 'marketing-wood', label: <Link to="/marketing?tab=wood_loss">木材损耗</Link> },
      ],
    },
    {
      key: 'g-order',
      label: '订单',
      children: [
        { key: 'orders', label: <Link to="/orders">订单</Link> },
        { key: 'orders-kanban', label: <Link to="/orders/kanban">看板（含配件备料）</Link> },
        { key: 'customers', label: <Link to="/customers">客户</Link> },
        { key: 'aftersales', label: <Link to="/aftersales">退货/售后</Link> },
      ],
    },
    {
      key: 'g-logistics',
      label: '物流',
      children: [
        { key: 'logistics-bills', label: <Link to="/logistics-bills">物流账单</Link> },
        // 万师傅: 月结对账(充值制可不用) + 安装订单档案(配对淘宝订单, 需要); 恢复入口, 默认进安装订单档案
        { key: 'wanshifu-bills', label: <Link to="/wanshifu-bills">万师傅</Link> },
      ],
    },
    {
      key: 'g-marketing',
      label: '营销',
      children: [
        { key: 'marketing-promotion', label: <Link to="/marketing?tab=promotion">推广记录</Link> },
        { key: 'marketing-brand', label: <Link to="/marketing?tab=brand">品牌营销</Link> },
        { key: 'marketing-daily', label: <Link to="/marketing?tab=daily">日常经营</Link> },
        { key: 'marketing-outsourcing', label: <Link to="/marketing?tab=outsourcing">人员外包</Link> },
      ],
    },
    {
      key: 'g-supply',
      label: '供应链',
      children: [
        { key: 'suppliers', label: <Link to="/suppliers">供应商</Link> },
        { key: 'supplier-scores', label: <Link to="/supplier-scores">供应商评分</Link> },
        { key: 'purchases', label: <Link to="/purchases">配件采购</Link> },
        { key: 'factory-orders', label: <Link to="/factory-orders">工厂下单表</Link> },
        { key: 'factory-statement', label: <Link to="/factory-statement">工厂对账单</Link> },
      ],
    },
    {
      key: 'g-finance',
      label: '财务',
      children: [
        { key: 'assets-cashflow', label: <Link to="/assets-cashflow">资产 &amp; 流水</Link> },
        { key: 'alipay', label: <Link to="/alipay">支付宝流水</Link> },
        { key: 'account-balances', label: <Link to="/account-balances">账户余额</Link> },
        { key: 'reconciliation', label: <Link to="/reconciliation">对账</Link> },
        { key: 'recon-center', label: <Link to="/recon-center">对账中心 (结算/诊断/工厂/代付)</Link> },
        { key: 'promotion-flows', label: <Link to="/promotion-flows">推广费流水</Link> },
        { key: 'refill-records', label: <Link to="/refill-records">补单记录</Link> },
        // 会计期间(关账)暂时隐藏 (用户 2026-06-12: 没有专业财务先不用) —
        // 路由保留, 需要时直接访问 /accounting 或恢复此行
        // { key: 'accounting', label: <Link to="/accounting">会计期间</Link> },
      ],
    },
    // 「分析」组已撤 (2026-06-17): 报表并入「数据分析」; 异常右上角已有入口, 不再重复
    // #24: 「待办事项」提到顶层, 原在「数据分析」组内名「待办台账」
    { key: 'ops-checklist', label: <Link to="/ops-checklist">待办事项</Link> },
    {
      key: 'g-tools',
      label: '工具',
      children: [
        { key: 'web-agent', label: <Link to="/web-agent">自动取数</Link> },
        { key: 'importer', label: <Link to="/importer">Excel 导入</Link> },
        { key: 'data-export', label: <Link to="/data-export">Excel 导出</Link> },
        { key: 'import-archive', label: <Link to="/import-archive">资料存档库</Link> },
        { key: 'audit-trail', label: <Link to="/audit-trail">修改历史</Link> },
        // 全列数据浏览已裁撤 (各页自带"全部列"视图, 重复) — 路由保留, 直链仍可用
        { key: 'feishu', label: <Link to="/feishu">飞书</Link> },
        // 运维工具 (2026-06-12) 并入「管理」页内, 不再单列菜单 (用户从不用、避免菜单膨胀); 路由保留
        ...(user.role === 'admin'
          ? [{ key: 'admin', label: <Link to="/admin">管理</Link> }]
          : []),
      ],
    },
  ];

  const isScreenshots = loc.pathname === '/screenshots';
  const isCustomQuoteV2 = loc.pathname === '/custom-quote-v2';

  return (
    <Layout style={{ minHeight: '100vh' }}>
      <Header style={{ display: 'flex', alignItems: 'center', paddingRight: 16, gap: 0 }}>
        <div style={{ color: 'white', fontWeight: 600, marginRight: 16, whiteSpace: 'nowrap' }}>
          畔色孚格 ERP
        </div>
        <Menu
          theme="dark"
          mode="horizontal"
          selectedKeys={selectedKeys}
          style={{ flex: 1, minWidth: 0 }}
          items={menuItems}
        />
        {/* 工具按钮：截图录单 + 定制报价 */}
        <Space style={{ marginLeft: 8, marginRight: 8, flexShrink: 0 }}>
          <Button
            icon={<CameraOutlined />}
            size="small"
            type={isScreenshots ? 'primary' : 'default'}
            ghost={!isScreenshots}
            onClick={() => nav('/screenshots')}
            style={{ borderColor: isScreenshots ? undefined : 'rgba(255,255,255,0.45)', color: isScreenshots ? undefined : 'rgba(255,255,255,0.85)' }}
          >
            截图录单
          </Button>
          <Button
            icon={<EditOutlined />}
            size="small"
            type={isCustomQuoteV2 ? 'primary' : 'default'}
            ghost={!isCustomQuoteV2}
            onClick={() => nav('/custom-quote-v2')}
            style={{ borderColor: isCustomQuoteV2 ? undefined : 'rgba(255,255,255,0.45)', color: isCustomQuoteV2 ? undefined : 'rgba(255,255,255,0.85)' }}
          >
            定制报价
          </Button>
        </Space>
        <Button
          icon={<SearchOutlined />}
          size="small"
          ghost
          onClick={() => window.dispatchEvent(new Event('panse:open-search'))}
          style={{ marginRight: 8, flexShrink: 0, borderColor: 'rgba(255,255,255,0.45)', color: 'rgba(255,255,255,0.85)' }}
          title="全局搜索 (Ctrl+K): 订单号 / 客户 / 产品 / 流水"
        >
          搜索
        </Button>
        <VersionTag />
        <NotificationBell />
        <Dropdown
          menu={{
            items: [
              { key: 'logout', icon: <LogoutOutlined />, label: '退出登录', onClick: logout },
            ],
          }}
        >
          <Space style={{ color: 'white', cursor: 'pointer', marginLeft: 8 }}>
            <Avatar size="small" icon={<UserOutlined />} />
            <span>{user.display_name || user.username}</span>
            <Tag color={ROLE_COLOR[user.role]}>{user.role}</Tag>
          </Space>
        </Dropdown>
      </Header>
      <CommandPalette />
      <AiAssistantWidget />
      <Content style={{ padding: 24 }}>
        <Suspense fallback={<PageFallback />}>
          <Routes>
            <Route path="/" element={<Navigate to="/dashboard" replace />} />
            <Route path="/login" element={<Navigate to="/dashboard" replace />} />
            <Route path="/dashboard" element={<DashboardPage />} />
            <Route path="/ops-checklist" element={<OpsChecklistPage />} />
            <Route path="/sales-ranking" element={<SalesRankingPage />} />
            <Route path="/products" element={<ProductsPage />} />
            <Route path="/bom/:productCode" element={<BomViewerPage />} />
            <Route path="/bom-list" element={<BomListPage />} />
            <Route path="/materials" element={<MaterialsPage />} />
            <Route path="/inventory" element={<PartInventoryPage />} />
            <Route path="/product-inventory" element={<ProductInventoryPage />} />
            <Route path="/samples" element={<SampleInventoryPage />} />
            <Route path="/orders" element={<OrdersPage />} />
            <Route path="/orders/kanban" element={<OrdersKanbanPage />} />
            <Route path="/orders/:orderId/factory-sheet" element={<FactorySheetPage />} />
            <Route path="/customers" element={<CustomersPage />} />
            <Route path="/customization" element={<CustomizationPage />} />
            <Route path="/custom-quote-v2" element={<CustomQuoteV2Page />} />
            <Route path="/screenshots" element={<ScreenshotImportPage />} />
            <Route path="/aftersales" element={<AfterSalesPage />} />
            <Route path="/forecast" element={<ForecastPage />} />
            <Route path="/assets" element={<AssetsPage />} />
            <Route path="/assets-cashflow" element={<AssetsCashflowPage />} />
            <Route path="/supplier-scores" element={<SupplierScoresPage />} />
            <Route path="/accounting" element={<AccountingPeriodsPage />} />
            <Route path="/producibility" element={<ProducibilityPage />} />
            <Route path="/quote" element={<QuotePage />} />
            <Route path="/pricing" element={<PricingPage />} />
            <Route path="/pricing-formulas" element={<PricingFormulaPage />} />
            <Route path="/alipay" element={<AlipayPage />} />
            <Route path="/account-balances" element={<AccountBalancesPage />} />
            <Route path="/reconciliation" element={<ReconciliationPage />} />
            <Route path="/suppliers" element={<SuppliersPage />} />
            <Route path="/purchases" element={<PurchasesPage />} />
            <Route path="/factory-orders" element={<FactoryOrdersPage />} />
            <Route path="/factory-statement" element={<FactoryStatementPage />} />
            <Route path="/importer" element={<ImporterPage />} />
            <Route path="/web-agent" element={<WebAgentPage />} />
            <Route path="/taobao-listings" element={<TaobaoListingsPage />} />
            <Route path="/new-product" element={<NewProductComposerPage />} />
            <Route path="/ai" element={<AiAssistantPage />} />
            <Route path="/marketing" element={<MarketingPage />} />
            <Route path="/exceptions" element={<ExceptionsPage />} />
            <Route path="/reports" element={<ReportsPage />} />
            <Route path="/feishu" element={<FeishuSettingsPage />} />
            <Route path="/admin" element={<AdminPage />} />
            <Route path="/ops-tools" element={<OpsToolsPage />} />
            <Route path="/wanshifu-bills" element={<WanshifuBillsPage />} />
            <Route path="/logistics-bills" element={<LogisticsBillsPage />} />
            <Route path="/refill-records" element={<RefillRecordsPage />} />
            <Route path="/cash-flow" element={<CashFlowPage />} />
            <Route path="/recon-center" element={<ReconCenterPage />} />
            {/* 旧链接重定向到对账中心对应 Tab (页面已合并) */}
            <Route path="/settlements" element={<Navigate to="/recon-center" replace />} />
            <Route path="/recon-diagnostics" element={<Navigate to="/recon-center?tab=diagnostics" replace />} />
            <Route path="/factory-recon" element={<Navigate to="/recon-center?tab=factory" replace />} />
            <Route path="/prepay-ledger" element={<Navigate to="/recon-center?tab=prepay" replace />} />
            {/* 平台保证金已并入「资产 & 流水」页的标签页; 旧链接重定向过去 */}
            <Route path="/shop-deposits" element={<Navigate to="/assets-cashflow" replace />} />
            <Route path="/promotion-flows" element={<PromotionFlowsPage />} />
            <Route path="/data-explorer" element={<DataExplorerPage />} />
            <Route path="/import-archive" element={<ImportArchivePage />} />
            <Route path="/audit-trail" element={<AuditTrailPage />} />
            <Route path="/data-export" element={<DataExportPage />} />
          </Routes>
        </Suspense>
      </Content>
    </Layout>
  );
}
