import * as React from 'react';

export interface StatCardProps extends React.HTMLAttributes<HTMLDivElement> {
  /** 指标名 */
  title: React.ReactNode;
  /** 主数值 (建议已格式化的字符串/数字) */
  value: React.ReactNode;
  /** 数值前缀，如 ¥ */
  prefix?: React.ReactNode;
  /** 标题前图标 */
  icon?: React.ReactNode;
  /** 涨跌幅文本，如 "12.4%" */
  delta?: React.ReactNode;
  /** 涨跌方向 (上绿/下红) */
  deltaDir?: 'up' | 'down';
  /** 脚注说明 */
  footer?: React.ReactNode;
  /** 覆盖数值颜色 (如负值转红) */
  valueColor?: string;
}

/** KPI 数字卡片 (配合 Card 使用)。 */
export function StatCard(props: StatCardProps): JSX.Element;
