import * as React from 'react';

export interface PageHeaderProps extends React.HTMLAttributes<HTMLDivElement> {
  /** 页面标题 */
  title: React.ReactNode;
  /** 副标题/说明 */
  subtitle?: React.ReactNode;
  /** 面包屑节点数组 */
  breadcrumb?: React.ReactNode[];
  /** 右侧操作区 */
  extra?: React.ReactNode;
}

/** 页面标题区 (标题 + 副标题 + 面包屑 + 操作)。 */
export function PageHeader(props: PageHeaderProps): JSX.Element;
