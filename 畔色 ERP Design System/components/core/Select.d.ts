import * as React from 'react';

export interface SelectOption {
  label: React.ReactNode;
  value: string | number;
}

export interface SelectProps {
  /** 选项列表 */
  options: SelectOption[];
  /** 受控值 */
  value?: string | number;
  /** 非受控初值 */
  defaultValue?: string | number;
  /** 选中回调 (value, option) */
  onChange?: (value: string | number, option: SelectOption) => void;
  /** 占位文本 */
  placeholder?: string;
  style?: React.CSSProperties;
  className?: string;
}

/** 下拉选择器。 */
export function Select(props: SelectProps): JSX.Element;
