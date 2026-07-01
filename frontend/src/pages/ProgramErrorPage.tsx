/**
 * 「程序错误」页 — 子账号访问未授权页面时渲染 (用户 2026-07-01)。
 *
 * 需求原话: 未开通的页面「哪怕他点进去了, 也显示: 程序错误」。
 * 刻意做成通用报错样式 (不点破是权限问题), 只给一个「返回首页」出口。
 */
import { Button, Result } from 'antd';
import { useNavigate } from 'react-router-dom';

export default function ProgramErrorPage() {
  const nav = useNavigate();
  return (
    <Result
      status="error"
      title="程序错误"
      subTitle="抱歉，该页面无法访问，请返回首页后重试。"
      extra={
        <Button type="primary" onClick={() => nav('/')}>
          返回首页
        </Button>
      }
    />
  );
}
