import React from 'react';
import { Button, Result } from 'antd';

interface State {
  hasError: boolean;
  error?: Error;
}

/** 全局错误边界: 单个页面/组件渲染崩溃时, 显示可刷新的错误页, 而不是白屏整站。 */
export default class ErrorBoundary extends React.Component<{ children: React.ReactNode }, State> {
  state: State = { hasError: false };

  static getDerivedStateFromError(error: Error): State {
    return { hasError: true, error };
  }

  componentDidCatch(error: Error, info: React.ErrorInfo) {
    // eslint-disable-next-line no-console
    console.error('UI 渲染错误:', error, info);
  }

  handleReload = () => {
    window.location.reload();
  };

  render() {
    if (this.state.hasError) {
      return (
        <Result
          status="error"
          title="页面出错了"
          subTitle={this.state.error?.message ?? '发生了未预期的错误，请刷新重试。'}
          extra={
            <Button type="primary" onClick={this.handleReload}>
              刷新页面
            </Button>
          }
        />
      );
    }
    return this.props.children;
  }
}
