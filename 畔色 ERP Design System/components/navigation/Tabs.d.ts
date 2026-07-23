import * as React from 'react';

export interface TabItem {
  key: string;
  label: React.ReactNode;
  /** 右上角计数徽标 */
  badge?: React.ReactNode;
}

export interface TabsProps extends Omit<React.HTMLAttributes<HTMLDivElement>, 'onChange'> {
  items: TabItem[];
  value?: string;
  defaultValue?: string;
  onChange?: (key: string) => void;
}

/** 下划线标签页。 */
export function Tabs(props: TabsProps): JSX.Element;
