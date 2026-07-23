import * as React from 'react';

export interface CardProps extends React.HTMLAttributes<HTMLDivElement> {
  /** 标题 (省略则不显示头部) */
  title?: React.ReactNode;
  /** 头部右侧操作/说明 */
  extra?: React.ReactNode;
  /** 可点击悬浮态 */
  hoverable?: boolean;
  /** 紧凑内边距 */
  tight?: boolean;
  children?: React.ReactNode;
}

/**
 * 内容卡片 / 面板。
 * @startingPoint section="Core" subtitle="圆角 16 柔和阴影卡片，含标题与悬浮态" viewport="700x220"
 */
export function Card(props: CardProps): JSX.Element;
