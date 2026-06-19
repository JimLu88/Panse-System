/**
 * 资产 & 流水 —— 把「资产」和「剩余流水」合并到一个页面(两个 tab), 按用户要求统一查看。
 * 复用既有的 AssetsPage / CashFlowPage 组件, 数据/逻辑不变。
 */
import { Tabs } from 'antd';
import AssetsPage from './AssetsPage';
import CashFlowPage from './CashFlowPage';
import ShopDepositsPage from './ShopDepositsPage';
import RefillCallout from '../components/RefillCallout';

export default function AssetsCashflowPage() {
  return (
    <Tabs
      defaultActiveKey="assets"
      items={[
        // 资产 tab 内嵌的 AssetsPage 自身已渲染 RefillCallout, 此处不再重复
        { key: 'assets', label: '资产', children: <AssetsPage /> },
        { key: 'cashflow', label: '剩余流水', children: <><RefillCallout /><CashFlowPage /></> },
        // 平台保证金并入此页 (原独立财务页移除); 其合计已自动并入剩余流水的「平台保证金」加项
        { key: 'shop-deposits', label: '平台保证金', children: <><RefillCallout /><ShopDepositsPage /></> },
      ]}
    />
  );
}
