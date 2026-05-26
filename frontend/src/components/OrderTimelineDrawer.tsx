/**
 * 订单事件时间轴抽屉 (Phase 8 Tier 1 #2).
 *
 * 调用方在订单列表点 "时间线" 按钮打开. 展示 OrderEvent 流 + 评论输入框.
 */
import { useState } from 'react';
import { Button, Drawer, Form, Input, Space, Tag, Timeline, Typography, message } from 'antd';
import { CommentOutlined } from '@ant-design/icons';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { fetchOrderTimeline, postOrderComment } from '../api/client';

const KIND_COLOR: Record<string, string> = {
  status_change: 'blue',
  factory_order_generated: 'green',
  factory_order_voided: 'orange',
  inventory_locked: 'cyan',
  inventory_released: 'purple',
  inventory_shortage: 'red',
  shipped: 'blue',
  shipping_label_generated: 'gold',
  comment: 'default',
  system_note: 'default',
};

const KIND_LABEL: Record<string, string> = {
  status_change: '状态变化',
  factory_order_generated: '生成工厂单',
  factory_order_voided: '作废工厂单',
  inventory_locked: '锁定库存',
  inventory_released: '释放库存',
  inventory_shortage: '⚠️ 物料缺货',
  shipping_label_generated: '打印面单',
  comment: '评论',
  system_note: '系统',
};


export default function OrderTimelineDrawer({
  orderId, open, onClose,
}: { orderId: number | null; open: boolean; onClose: () => void }) {
  const qc = useQueryClient();
  const [form] = Form.useForm();

  const { data: events = [], isLoading } = useQuery({
    queryKey: ['order-timeline', orderId],
    queryFn: () => fetchOrderTimeline(orderId!),
    enabled: orderId !== null && open,
    refetchInterval: 30000,
  });

  const commentMut = useMutation({
    mutationFn: (text: string) => postOrderComment(orderId!, text),
    onSuccess: () => {
      message.success('评论已添加');
      form.resetFields();
      qc.invalidateQueries({ queryKey: ['order-timeline', orderId] });
    },
  });

  return (
    <Drawer
      title={`订单 #${orderId} · 时间轴`}
      open={open}
      onClose={onClose}
      width={560}
      destroyOnClose
    >
      <Timeline
        items={events.map((e) => ({
          color: KIND_COLOR[e.kind] ?? 'gray',
          dot: e.kind === 'comment' ? <CommentOutlined /> : undefined,
          children: (
            <Space direction="vertical" size={0} style={{ width: '100%' }}>
              <Space>
                <Tag color={KIND_COLOR[e.kind] ?? 'default'}>
                  {KIND_LABEL[e.kind] ?? e.kind}
                </Tag>
                <strong>{e.summary}</strong>
              </Space>
              {e.detail && (
                <Typography.Text style={{ fontSize: 12 }}>{e.detail}</Typography.Text>
              )}
              <Typography.Text type="secondary" style={{ fontSize: 11 }}>
                {new Date(e.created_at).toLocaleString('zh-CN')}
                {e.actor && ` · ${e.actor}`}
              </Typography.Text>
            </Space>
          ),
        }))}
      />

      <Form
        form={form}
        layout="inline"
        style={{ marginTop: 24 }}
        onFinish={(v) => commentMut.mutate(v.text)}
      >
        <Form.Item name="text" style={{ flex: 1 }} rules={[{ required: true, min: 1 }]}>
          <Input placeholder="加评论 (将进入时间轴)" />
        </Form.Item>
        <Form.Item>
          <Button type="primary" htmlType="submit" loading={commentMut.isPending}>
            发送
          </Button>
        </Form.Item>
      </Form>
    </Drawer>
  );
}
