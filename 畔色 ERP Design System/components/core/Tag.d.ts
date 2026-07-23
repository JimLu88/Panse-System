import * as React from 'react';

export interface TagProps extends React.HTMLAttributes<HTMLSpanElement> {
  /** 语义色调 */
  tone?: 'default' | 'brand' | 'success' | 'warning' | 'danger' | 'info';
  /** 实色填充 (默认软底) */
  solid?: boolean;
  /** 前置状态圆点 */
  dot?: boolean;
  /** 可关闭 (显示 ×) */
  closable?: boolean;
  onClose?: (e: React.MouseEvent) => void;
  children?: React.ReactNode;
}

/** 状态标签 / 类目标签。 */
export function Tag(props: TagProps): JSX.Element;
