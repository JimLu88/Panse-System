import * as React from 'react';

export interface InputProps extends Omit<React.InputHTMLAttributes<HTMLInputElement>, 'size' | 'prefix'> {
  /** 字段标签 (省略则只渲染输入框) */
  label?: React.ReactNode;
  /** 必填星标 */
  required?: boolean;
  /** 尺寸 */
  size?: 'sm' | 'md' | 'lg';
  /** 前置图标/文本 */
  prefix?: React.ReactNode;
  /** 后置图标/文本 (如单位) */
  suffix?: React.ReactNode;
  /** 错误信息 (转红 + 显示) */
  error?: React.ReactNode;
  disabled?: boolean;
}

/** 文本输入框。 */
export function Input(props: InputProps): JSX.Element;
