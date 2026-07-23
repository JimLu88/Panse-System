import * as React from 'react';

export interface SegmentedOption {
  label: React.ReactNode;
  value: string | number;
}

export interface SegmentedProps extends Omit<React.HTMLAttributes<HTMLDivElement>, 'onChange'> {
  /** 选项，可为字符串数组或 {label,value} 数组 */
  options: (string | SegmentedOption)[];
  value?: string | number;
  defaultValue?: string | number;
  onChange?: (value: string | number) => void;
  size?: 'sm' | 'md';
}

/** 分段控制器 (时间段/视图切换)。 */
export function Segmented(props: SegmentedProps): JSX.Element;
