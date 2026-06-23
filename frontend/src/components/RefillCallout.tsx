/**
 * 刷单(补单)单列提示 — 放在各算账页面, 统一展示
 * 「刷单(补单) X笔 · 流水¥Y · 成本¥Z —— 已单列, 不计入上方经营数据」。
 *
 * 主数据(营收/利润/成交/资产/现金流/排行/预测/报表)早已剔除刷单(P1), 本组件只把被剔除的
 * 那部分单独亮出来, 让"补单/没补单"一眼看清、数字分开。(2026-06-19 用户拍板: 补单=刷单/假单)
 * 无刷单时不渲染。periodStart/periodEnd 缺省 = 本年至今。
 */
import { Alert, Typography } from 'antd';
import { useQuery } from '@tanstack/react-query';
import { getRefillSummary } from '../api/finance';

const yuan = (n: number) =>
  '¥' + (n || 0).toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });

export default function RefillCallout({
  periodStart,
  periodEnd,
  compact,
}: {
  periodStart?: string;
  periodEnd?: string;
  compact?: boolean;
}) {
  const { data } = useQuery({
    queryKey: ['refill-summary', periodStart ?? '', periodEnd ?? ''],
    queryFn: () => getRefillSummary({ period_start: periodStart, period_end: periodEnd }),
    staleTime: 60_000,
  });
  if (!data || data.count === 0) return null; // 无刷单 → 不显示

  return (
    <Alert
      type="warning"
      showIcon
      banner={compact}
      style={{ marginBottom: compact ? 8 : 12 }}
      message={
        <Typography.Text style={{ fontSize: 13 }}>
          刷单(补单) <b>{data.count}</b> 笔 · 流水 <b>{yuan(data.gmv)}</b> · 成本{' '}
          <b>{yuan(data.cost)}</b>
          <Typography.Text type="secondary" style={{ marginLeft: 8, fontSize: 12 }}>
            —— 已单列, 不计入上方经营数据
          </Typography.Text>
        </Typography.Text>
      }
    />
  );
}
