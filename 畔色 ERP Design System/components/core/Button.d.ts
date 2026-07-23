import * as React from 'react';

export interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  /** 视觉变体 */
  variant?: 'primary' | 'secondary' | 'ghost' | 'text';
  /** 尺寸 (sm 28 / md 36 / lg 44) */
  size?: 'sm' | 'md' | 'lg';
  /** 前置图标 (传入 React 元素，如 @ant-design/icons) */
  icon?: React.ReactNode;
  /** 加载态 — 显示转圈并禁用 */
  loading?: boolean;
  /** 危险操作 (红色) */
  danger?: boolean;
  /** 占满整行宽度 */
  block?: boolean;
  disabled?: boolean;
  children?: React.ReactNode;
}

/**
 * 畔色 ERP 主按钮。
 * @startingPoint section="Core" subtitle="主/次/幽灵/文本按钮，含危险态" viewport="700x150"
 */
export function Button(props: ButtonProps): JSX.Element;
