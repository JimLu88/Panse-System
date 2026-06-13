/**
 * 资产 & 流水 —— 把「资产」和「剩余流水」合并到一个页面(两个 tab), 按用户要求统一查看。
 * 复用既有的 AssetsPage / CashFlowPage 组件, 数据/逻辑不变。
 */
import { Tabs } from 'antd';
import AssetsPage from './AssetsPage';
import CashFlowPage from './CashFlowPage';

export default function AssetsCashflowPage() {
  return (
    <Tabs
      defaultActiveKey="assets"
      items={[
        { key: 'assets', label: '资产', children: <AssetsPage /> },
        { key: 'cashflow', label: '剩余流水', children: <CashFlowPage /> },
      ]}
    />
  );
}
