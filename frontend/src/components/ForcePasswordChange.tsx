import { useState } from 'react';
import { Modal, Form, Input, Button, message, Typography } from 'antd';
import { changeMyPassword } from '../api/auth';
import { useAuth } from '../auth/AuthProvider';

/** 强制改密弹窗: 账号仍在用默认/弱密码 (must_change_password) 时全屏阻断, 改密后放行。 */
export default function ForcePasswordChange() {
  const { refreshUser } = useAuth();
  const [form] = Form.useForm();
  const [submitting, setSubmitting] = useState(false);

  const onFinish = async (v: { old_password: string; new_password: string; confirm: string }) => {
    if (v.new_password !== v.confirm) {
      message.error('两次输入的新密码不一致');
      return;
    }
    if (v.new_password === 'admin' || v.new_password.length < 6) {
      message.error('新密码至少 6 位, 且不能仍是 admin');
      return;
    }
    setSubmitting(true);
    try {
      await changeMyPassword(v.old_password, v.new_password);
      message.success('密码已修改');
      await refreshUser();
    } catch (e: any) {
      message.error(`修改失败: ${e?.response?.data?.detail ?? e?.message ?? '未知错误'}`);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Modal open title="请先修改默认密码" closable={false} maskClosable={false} keyboard={false} footer={null}>
      <Typography.Paragraph type="secondary">
        当前账号仍在使用默认/弱密码，为账户安全，请先设置新密码后再使用系统。
      </Typography.Paragraph>
      <Form form={form} layout="vertical" onFinish={onFinish}>
        <Form.Item name="old_password" label="当前密码" rules={[{ required: true, message: '请输入当前密码' }]}>
          <Input.Password autoFocus />
        </Form.Item>
        <Form.Item name="new_password" label="新密码" rules={[{ required: true, min: 6, message: '至少 6 位' }]}>
          <Input.Password />
        </Form.Item>
        <Form.Item name="confirm" label="确认新密码" rules={[{ required: true, message: '请再次输入新密码' }]}>
          <Input.Password />
        </Form.Item>
        <Button type="primary" htmlType="submit" block loading={submitting}>
          修改并继续
        </Button>
      </Form>
    </Modal>
  );
}
