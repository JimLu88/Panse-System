import { Suspense, lazy, useState } from 'react';
import { Avatar, Button, Drawer, Dropdown, Grid, Layout, Menu, Space, Spin, Tag } from 'antd';
import { BulbFilled, BulbOutlined, EditOutlined, LogoutOutlined, MenuOutlined, SearchOutlined, SettingOutlined, UserOutlined } from '@ant-design/icons';
import { Link, Navigate, Route, Routes, useLocation, useNavigate } from 'react-router-dom';
import LoginPage from './pages/LoginPage';
import ProgramErrorPage from './pages/ProgramErrorPage';
import { canAccessPerm, filterMenuByPerms, homePathFor, resolvePagePerm } from './auth/permissions';
import ForcePasswordChange from './components/ForcePasswordChange';
import NotificationBell from './components/NotificationBell';
import CommandPalette from './components/CommandPalette';
import VersionTag from './components/VersionTag';
import { useAuth } from './auth/AuthProvider';
import { useThemeMode } from './theme/ThemeProvider';

// ChatBI 问数抽屉 (Plan4 v2, 仅 admin)
const ChatBiDrawer = lazy(() => import('./components/chatbi/ChatBiDrawer'));

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
const StaffSalaryPage = lazy(() => import('./pages/StaffSalaryPage'));
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
const AfterSalesPage = lazy(() => import('./pages/AfterSalesPage'));
const ForecastPage = lazy(() => import('./pages/ForecastPage'));
const AssetsPage = lazy(() => import('./pages/AssetsPage'));
const AssetsCashflowPage = lazy(() => import('./pages/AssetsCashflowPage'));
const CustomersPage = lazy(() => import('./pages/CustomersPage'));
const OrdersKanbanPage = lazy(() => import('./pages/OrdersKanbanPage'));
const CustomReconcilePage = lazy(() => import('./pages/CustomReconcilePage'));
const PerOrderReconcilePage = lazy(() => import('./pages/PerOrderReconcilePage'));
const AccountingPeriodsPage = lazy(() => import('./pages/AccountingPeriodsPage'));
const PricingPage = lazy(() => import('./pages/PricingPage'));
const ShopPriceBoardPage = lazy(() => import('./pages/ShopPriceBoardPage'));
const PricingFormulaPage = lazy(() => import('./pages/PricingFormulaPage'));
const DashboardPage = lazy(() => import('./pages/DashboardPage'));
const OpsChecklistPage = lazy(() => import('./pages/OpsChecklistPage'));
const SalesRankingPage = lazy(() => import('./pages/SalesRankingPage'));
const PurchasesPage = lazy(() => import('./pages/PurchasesPage'));
const MonthlySettlementCenterPage = lazy(() => import('./pages/MonthlySettlementCenterPage'));
const FactoryOrdersPage = lazy(() => import('./pages/FactoryOrdersPage'));
const FactoryStatementPage = lazy(() => import('./pages/FactoryStatementPage'));
const FactorySettlementPage = lazy(() => import('./pages/FactorySettlementPage'));
const TaobaoListingsPage = lazy(() => import('./pages/TaobaoListingsPage'));
const NewProductComposerPage = lazy(() => import('./pages/NewProductComposerPage'));
const NpdPage = lazy(() => import('./pages/NpdPage'));
const NpdDetailPage = lazy(() => import('./pages/NpdDetailPage'));
const CustomQuoteV2Page = lazy(() => import('./pages/CustomQuoteV2Page'));
const WanshifuBillsPage = lazy(() => import('./pages/WanshifuBillsPage'));
const LogisticsBillsPage = lazy(() => import('./pages/LogisticsBillsPage'));
const PackingBillsPage = lazy(() => import('./pages/PackingBillsPage'));
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
  npd: 'g-product',
  pricing: 'g-price', 'pricing-formulas': 'g-price', quote: 'g-price', customization: 'g-price',
  inventory: 'g-stock', 'product-inventory': 'g-stock', producibility: 'g-stock', samples: 'g-stock',
  orders: 'g-order', customers: 'g-order', aftersales: 'g-order',
  'logistics-bills': 'g-logistics', 'wanshifu-bills': 'g-logistics',
  marketing: 'g-marketing',
  suppliers: 'g-supply', 'supplier-scores': 'g-supply', purchases: 'g-supply', materials: 'g-supply',
  'cash-flow': 'g-finance', alipay: 'g-finance', 'account-balances': 'g-finance',
  'staff-salary': 'g-finance',
  'per-order-reconcile': 'g-finance',
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
  const { mode, toggle: toggleTheme } = useThemeMode();
  const screens = Grid.useBreakpoint();
  const isMobile = screens.md === false;   // <768px: 收起顶栏巨菜单, 改抽屉导航 (=== false 防桌面首屏闪烁)
  const [navOpen, setNavOpen] = useState(false);
  const [chatbiOpen, setChatbiOpen] = useState(false);

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

  // 子账号页面权限: 当前页需要的 permKey + 能否访问 (admin/主账号恒可)
  const currentPerm = resolvePagePerm(loc.pathname, loc.search);
  const pageAllowed = canAccessPerm(user, currentPerm);
  // 登录后落地页: admin→数据大盘; 受限子账号→第一个有权页面(通常产品总表), 避免落到无权的大盘看「程序错误」
  const homePath = homePathFor(user);

  const menuItems = [
    {
      key: 'g-data',
      label: '数据分析',
      children: [
        { key: 'dashboard', label: <Link to="/dashboard">数据大盘</Link> },
        { key: 'reports', label: <Link to="/reports">报表</Link> },  // #分析合并: 报表并入数据分析 (销售排行榜/预测已嵌入数据大盘)
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
        { key: 'npd', label: <Link to="/npd">新品开发</Link> },
        { key: 'taobao-listings', label: <Link to="/taobao-listings">淘宝对应表</Link> },
      ],
    },
    {
      key: 'g-price',
      label: '价格',
      children: [
        { key: 'pricing', label: <Link to="/pricing">定价表</Link> },
        { key: 'customization', label: <Link to="/customization?tab=competitor">竞品价库</Link> },
      ],
    },
    {
      key: 'g-stock',
      label: '库存',
      children: [
        { key: 'inventory', label: <Link to="/inventory">配件库存</Link> },
        { key: 'product-inventory', label: <Link to="/product-inventory">成品库存</Link> },
        { key: 'samples', label: <Link to="/samples">样品库存</Link> },
        { key: 'marketing-wood', label: <Link to="/marketing?tab=wood_loss">木材损耗</Link> },
      ],
    },
    {
      key: 'g-order',
      label: '订单',
      children: [
        { key: 'orders', label: <Link to="/orders">订单</Link> },
        { key: 'orders-kanban', label: <Link to="/orders/kanban">看板（含配件备料）</Link> },
        { key: 'custom-reconcile', label: <Link to="/orders/custom-reconcile">定制单核对</Link> },
        { key: 'customers', label: <Link to="/customers">客户</Link> },
        { key: 'aftersales', label: <Link to="/aftersales">退货/售后</Link> },
      ],
    },
    {
      key: 'g-logistics',
      label: '物流',
      children: [
        { key: 'logistics-bills', label: <Link to="/logistics-bills">物流账单</Link> },
        { key: 'packing-bills', label: <Link to="/packing-bills">打包费账单</Link> },
        // 万师傅: 月结对账(充值制可不用) + 安装订单档案(配对淘宝订单, 需要); 恢复入口, 默认进安装订单档案
        { key: 'wanshifu-bills', label: <Link to="/wanshifu-bills">万师傅</Link> },
      ],
    },
    {
      key: 'g-marketing',
      label: '营销',
      children: [
        { key: 'marketing-promotion', label: <Link to="/marketing?tab=promotion">推广记录</Link> },
        { key: 'promotion-flows', label: <Link to="/promotion-flows">推广费流水</Link> },
        { key: 'marketing-brand', label: <Link to="/marketing?tab=brand">品牌营销</Link> },
        { key: 'refill-records', label: <Link to="/refill-records">补单记录</Link> },
        { key: 'marketing-daily', label: <Link to="/marketing?tab=daily">日常经营</Link> },
      ],
    },
    {
      key: 'g-supply',
      label: '供应链',
      children: [
        { key: 'suppliers', label: <Link to="/suppliers">供应商</Link> },
        { key: 'purchases', label: <Link to="/purchases">配件采购</Link> },
        { key: 'monthly-settlement', label: <Link to="/monthly-settlement">月结对账中心</Link> },
        { key: 'factory-orders', label: <Link to="/factory-orders">工厂下单表</Link> },
        { key: 'factory-statement', label: <Link to="/factory-statement">工厂对账单</Link> },
        { key: 'factory-settlement', label: <Link to="/factory-settlement">工厂月结销账</Link> },
      ],
    },
    {
      key: 'g-finance',
      label: '财务',
      children: [
        { key: 'assets-cashflow', label: <Link to="/assets-cashflow">资产 &amp; 流水</Link> },
        { key: 'alipay', label: <Link to="/alipay">支付宝流水</Link> },
        { key: 'account-balances', label: <Link to="/account-balances">账户余额</Link> },
        { key: 'staff-salary', label: <Link to="/staff-salary">人员工资</Link> },
        { key: 'per-order-reconcile', label: <Link to="/per-order-reconcile">逐单核对</Link> },
        { key: 'recon-center', label: <Link to="/recon-center">对账中心 (结算/诊断/工厂/代付)</Link> },
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
        // 评价程序 = 独立程序 (群晖 NAS 常驻; 公网反代 https://jimlu1029.synology.me:17902 → NAS:7902, 外网也可访问); 外链新窗口打开, 无内部路由。
        // Menu 无全局 onClick, 故用 <a> 直链; key 与 permissions.ts / page_permissions.py 三处一致。
        { key: 'review-program', label: <a href="https://jimlu1029.synology.me:17902/" target="_blank" rel="noopener noreferrer">评价程序</a> },
        // 「管理」已移到右上角 小人菜单 → 系统设置 (2026-06-22; 账户设置 Tab 于 2026-06-23 去掉, 用户管理在系统设置内); 路由 /admin 保留
      ],
    },
  ];

  // 子账号: 按开通权限过滤菜单 (无权的叶子隐藏, 空了的分组整块不显示); admin/主账号原样全看
  const visibleMenu = filterMenuByPerms(menuItems, user);

  const isCustomQuoteV2 = loc.pathname === '/custom-quote-v2';

  return (
    <Layout style={{ minHeight: '100vh' }}>
      <Header style={{ display: 'flex', alignItems: 'center', paddingRight: isMobile ? 8 : 16, gap: 0 }}>
        {isMobile ? (
          <>
            <Button
              type="text" aria-label="菜单"
              icon={<MenuOutlined style={{ fontSize: 18 }} />}
              onClick={() => setNavOpen(true)}
              style={{ color: '#fff', flexShrink: 0, marginRight: 4 }}
            />
            <div style={{ color: 'white', fontWeight: 600, whiteSpace: 'nowrap', flex: 1, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis' }}>
              畔色孚格 ERP
            </div>
            <Button
              type="text" aria-label="搜索"
              icon={<SearchOutlined style={{ fontSize: 16 }} />}
              onClick={() => window.dispatchEvent(new Event('panse:open-search'))}
              style={{ color: 'rgba(255,255,255,0.9)', flexShrink: 0 }}
            />
            <NotificationBell />
            <Dropdown
              menu={{
                items: [
                  ...(user.role === 'admin' ? [
                    { key: 'system', icon: <SettingOutlined />, label: <Link to="/admin">系统设置</Link> },
                    { type: 'divider' as const },
                  ] : []),
                  { key: 'logout', icon: <LogoutOutlined />, label: '退出登录', onClick: logout },
                ],
              }}
            >
              <Avatar size="small" icon={<UserOutlined />} style={{ marginLeft: 6, flexShrink: 0, cursor: 'pointer' }} />
            </Dropdown>
          </>
        ) : (
          <>
        <div style={{ color: 'white', fontWeight: 600, marginRight: 16, whiteSpace: 'nowrap' }}>
          畔色孚格 ERP
        </div>
        <Menu
          theme="dark"
          mode="horizontal"
          selectedKeys={selectedKeys}
          style={{ flex: 1, minWidth: 0 }}
          items={visibleMenu}
        />
        {/* 工具按钮：定制报价 (截图录单已停用, 改手动导订单 2026-07-01); 子账号无权则隐藏 */}
        {canAccessPerm(user, 'custom-quote-v2') && (
        <Space style={{ marginLeft: 8, marginRight: 8, flexShrink: 0 }}>
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
        )}
        {user.role === 'admin' && (
        <Button
          size="small"
          ghost
          onClick={() => setChatbiOpen(true)}
          style={{ marginRight: 8, flexShrink: 0, borderColor: 'rgba(255,255,255,0.45)', color: 'rgba(255,255,255,0.85)' }}
          title="自然语言问数 (ChatBI)：本月净利润 / 产品毛利率排行 / 退款率趋势…"
        >
          问数
        </Button>
        )}
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
        <Button
          icon={mode === 'dark' ? <BulbFilled /> : <BulbOutlined />}
          size="small"
          ghost
          onClick={toggleTheme}
          style={{ marginRight: 8, flexShrink: 0, borderColor: 'rgba(255,255,255,0.45)', color: 'rgba(255,255,255,0.85)' }}
          title={mode === 'dark' ? '切换到浅色模式' : '切换到深色模式'}
        />
        <VersionTag />
        <NotificationBell />
        <Dropdown
          menu={{
            items: [
              ...(user.role === 'admin' ? [
                { key: 'system', icon: <SettingOutlined />, label: <Link to="/admin">系统设置</Link> },
                { type: 'divider' as const },
              ] : []),
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
          </>
        )}
      </Header>
      {user.role === 'admin' && (
        <Suspense fallback={null}>
          <ChatBiDrawer open={chatbiOpen} onClose={() => setChatbiOpen(false)} />
        </Suspense>
      )}
      {isMobile && (
        <Drawer
          title={<span style={{ fontWeight: 700 }}>畔色孚格 ERP</span>}
          placement="left"
          open={navOpen}
          onClose={() => setNavOpen(false)}
          width={284}
          styles={{ body: { padding: 0 } }}
        >
          <div style={{ padding: '12px 12px 4px', display: 'flex', flexDirection: 'column', gap: 8 }}>
            {canAccessPerm(user, 'custom-quote-v2') && (
              <Button icon={<EditOutlined />} block onClick={() => { nav('/custom-quote-v2'); setNavOpen(false); }}>定制报价</Button>
            )}
            <Button icon={mode === 'dark' ? <BulbFilled /> : <BulbOutlined />} block onClick={toggleTheme}>
              {mode === 'dark' ? '浅色模式' : '深色模式'}
            </Button>
          </div>
          <Menu
            mode="inline"
            selectedKeys={selectedKeys}
            defaultOpenKeys={group ? [group] : []}
            items={visibleMenu}
            onClick={() => setNavOpen(false)}
            style={{ borderInlineEnd: 0 }}
          />
        </Drawer>
      )}
      <CommandPalette />
      <Content style={{ padding: 24 }}>
        <Suspense fallback={<PageFallback />}>
          {!pageAllowed ? <ProgramErrorPage /> : (
          <Routes>
            <Route path="/" element={<Navigate to={homePath} replace />} />
            <Route path="/login" element={<Navigate to={homePath} replace />} />
            <Route path="/dashboard" element={<DashboardPage />} />
            <Route path="/ops-checklist" element={<OpsChecklistPage />} />
            <Route path="/sales-ranking" element={<SalesRankingPage />} />
            <Route path="/products" element={<ProductsPage />} />
            <Route path="/bom/:productCode" element={<BomViewerPage />} />
            <Route path="/bom-list" element={<BomListPage />} />
            {/* BOM尺寸复核已并入 BOM 清单页做 Tab; 旧链接重定向过去 (2026-07-01) */}
            <Route path="/bom-size-review" element={<Navigate to="/bom-list?tab=size" replace />} />
            <Route path="/materials" element={<MaterialsPage />} />
            <Route path="/inventory" element={<PartInventoryPage />} />
            <Route path="/product-inventory" element={<ProductInventoryPage />} />
            <Route path="/samples" element={<SampleInventoryPage />} />
            <Route path="/orders" element={<OrdersPage />} />
            <Route path="/orders/kanban" element={<OrdersKanbanPage />} />
            <Route path="/orders/custom-reconcile" element={<CustomReconcilePage />} />
            <Route path="/orders/:orderId/factory-sheet" element={<FactorySheetPage />} />
            <Route path="/customers" element={<CustomersPage />} />
            <Route path="/customization" element={<CustomizationPage />} />
            <Route path="/custom-quote-v2" element={<CustomQuoteV2Page />} />
            <Route path="/aftersales" element={<AfterSalesPage />} />
            <Route path="/forecast" element={<ForecastPage />} />
            <Route path="/assets" element={<AssetsPage />} />
            <Route path="/assets-cashflow" element={<AssetsCashflowPage />} />
            <Route path="/accounting" element={<AccountingPeriodsPage />} />
            <Route path="/producibility" element={<ProducibilityPage />} />
            <Route path="/quote" element={<QuotePage />} />
            <Route path="/pricing" element={<PricingPage />} />
            <Route path="/shop-price-board" element={<ShopPriceBoardPage />} />
            <Route path="/pricing-formulas" element={<PricingFormulaPage />} />
            <Route path="/alipay" element={<AlipayPage />} />
            <Route path="/account-balances" element={<AccountBalancesPage />} />
            <Route path="/staff-salary" element={<StaffSalaryPage />} />
            <Route path="/reconciliation" element={<ReconciliationPage />} />
            <Route path="/per-order-reconcile" element={<PerOrderReconcilePage />} />
            <Route path="/suppliers" element={<SuppliersPage />} />
            <Route path="/purchases" element={<PurchasesPage />} />
            <Route path="/monthly-settlement" element={<MonthlySettlementCenterPage />} />
            <Route path="/factory-orders" element={<FactoryOrdersPage />} />
            <Route path="/factory-statement" element={<FactoryStatementPage />} />
            <Route path="/factory-settlement" element={<FactorySettlementPage />} />
            <Route path="/importer" element={<ImporterPage />} />
            <Route path="/web-agent" element={<WebAgentPage />} />
            <Route path="/taobao-listings" element={<TaobaoListingsPage />} />
            <Route path="/new-product" element={<NewProductComposerPage />} />
            <Route path="/npd" element={<NpdPage />} />
            <Route path="/npd/:id" element={<NpdDetailPage />} />
            <Route path="/ai" element={<AiAssistantPage />} />
            <Route path="/marketing" element={<MarketingPage />} />
            <Route path="/exceptions" element={<ExceptionsPage />} />
            <Route path="/reports" element={<ReportsPage />} />
            <Route path="/feishu" element={<FeishuSettingsPage />} />
            <Route path="/admin" element={<AdminPage />} />
            <Route path="/ops-tools" element={<OpsToolsPage />} />
            <Route path="/wanshifu-bills" element={<WanshifuBillsPage />} />
            <Route path="/logistics-bills" element={<LogisticsBillsPage />} />
            <Route path="/packing-bills" element={<PackingBillsPage />} />
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
          )}
        </Suspense>
      </Content>
    </Layout>
  );
}
