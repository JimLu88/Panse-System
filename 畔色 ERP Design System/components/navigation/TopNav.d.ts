import * as React from 'react';

export interface NavItem {
  key: string;
  label: React.ReactNode;
}

export interface TopNavProps extends React.HTMLAttributes<HTMLDivElement> {
  /** 品牌名 */
  brand?: React.ReactNode;
  /** Logo 字符 (默认「畔」) */
  logo?: React.ReactNode;
  /** 菜单项 */
  items?: NavItem[];
  /** 当前选中 key */
  activeKey?: string;
  /** 选中回调 */
  onSelect?: (key: string) => void;
  /** 右侧操作区 (搜索/通知/头像) */
  right?: React.ReactNode;
}

/**
 * 顶部主导航栏。
 * @startingPoint section="Navigation" subtitle="深青顶栏：品牌 + 横向菜单 + 操作区" viewport="1000x80"
 */
export function TopNav(props: TopNavProps): JSX.Element;
