/**
 * 对账中心 (用户拍板 2026-06-11): 对账 / 结算对账 / 对账诊断 / 工厂逐单对账 / 代付台账 / 财务系数设置
 * 多个对账页面合并为一页多 Tab, ?tab= 驱动可直链。
 *
 * 各 Tab 含义:
 *  - 对账: 订单 ↔ 收款逐笔核对
 *  - 结算对账: 淘宝平台结算单 ↔ 系统订单逐月核对 (平台到底打了多少钱)
 *  - 对账诊断: 对不上的账自动找原因 (缺流水/缺订单/金额错位)
 *  - 工厂逐单对账: 工厂账单逐单 ↔ 系统工厂下单核对
 *  - 代付台账: 别人代付的钱 (补单佣金/快递/售后) 的进出台账
 *  - 财务系数设置: 全系统成本/利润口径系数 (手续费率/活动抽成/税率/外包) — 决定怎么算成本和利润 (用户需求 2026-06-23, 从工具→管理控制台迁入)
 */
import { Tabs } from 'antd';
import { useSearchParams } from 'react-router-dom';
import RefillCallout from '../components/RefillCallout';
import SettlementsPage from './SettlementsPage';
import ReconDiagnosticsPage from './ReconDiagnosticsPage';
import FactoryReconPage from './FactoryReconPage';
import PrepayLedgerPage from './PrepayLedgerPage';
import ReconciliationPage from './ReconciliationPage';
import FinancialCoefficientsCard from '../components/FinancialCoefficientsCard';

export default function ReconCenterPage() {
  const [params, setParams] = useSearchParams();
  const tab = params.get('tab') || 'settlements';
  return (
    <>
      {/* 刷单(补单)单列提示 — 对账中心顶部, 无账期 */}
      <RefillCallout />
      <Tabs
        activeKey={tab}
        onChange={(k) => setParams(k === 'settlements' ? {} : { tab: k }, { replace: true })}
        destroyInactiveTabPane
        items={[
        { key: 'reconciliation', label: '对账 (订单↔收款)', children: <ReconciliationPage /> },
        { key: 'settlements', label: '结算对账 (平台打款)', children: <SettlementsPage /> },
        { key: 'diagnostics', label: '对账诊断 (找原因)', children: <ReconDiagnosticsPage /> },
        { key: 'factory', label: '工厂逐单对账', children: <FactoryReconPage /> },
        { key: 'prepay', label: '代付台账', children: <PrepayLedgerPage /> },
        { key: 'fin-coef', label: '💰 财务系数设置', children: <FinancialCoefficientsCard /> },
        ]}
      />
    </>
  );
}
