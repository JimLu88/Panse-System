import { Button, Space, Tag, Typography, Modal, message } from 'antd';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { updateForward, retryForwardSync, type ForwardItem } from '../api/orders';

const labels: Record<string, string> = {pending: '待转发', forwarded: '已转发', deferred: '暂缓转发'};
export default function FactoryForwardItems({items}: {items: ForwardItem[]}) {
  const qc = useQueryClient();
  const retry = useMutation({mutationFn: retryForwardSync,
    onSuccess: () => { message.success('已安排重新同步'); qc.invalidateQueries({queryKey: ['factory-production']}); },
    onError: () => message.error('重试未启动，请稍后再试')});
  const mutation = useMutation({
    mutationFn: ({item, state}: {item: ForwardItem; state: string}) => updateForward(item.key, state, item.revision),
    onSuccess: () => { message.success('转发登记已保存'); qc.invalidateQueries({queryKey: ['factory-production']}); },
    onError: (e: any) => { message.error(e?.response?.data?.detail || '保存失败'); qc.invalidateQueries({queryKey: ['factory-production']}); },
  });
  return <div className="no-print" style={{marginTop: 12, borderTop: '1px solid #eee', paddingTop: 8}}>
    {items.map(item => <div key={item.key} style={{marginBottom: 8}}>
      <Space wrap><Typography.Text strong>畔色{item.factory_no || '待编号'}单</Typography.Text>
        <Tag color={item.state === 'forwarded' ? 'green' : item.state === 'deferred' ? 'orange' : 'default'}>{item.label}</Tag>
        {item.sync_pending && <Tag color="orange">{item.sync_error || '正在同步飞书'}</Tag>}
        {item.sync_error && <Button size="small" loading={retry.isPending} onClick={() => retry.mutate(item.key)}>重试同步</Button>}
      </Space>
      <div><Typography.Text type="secondary">{item.product_name} · 子单{item.sub_order_no || '旧母单'}</Typography.Text></div>
      <Space wrap>
        {Object.entries(labels).map(([state, label]) => <Button key={state} size="small" loading={mutation.isPending}
          disabled={state === item.state} onClick={() => mutation.mutate({item, state})}>
          {state === 'forwarded' ? '✅ ' : state === 'deferred' ? '❌ ' : ''}{label}
        </Button>)}
        <Button size="small" type="link" onClick={() => Modal.info({title: '转发登记历史', width: 640,
          content: <div>{item.history.length ? item.history.map(h => <p key={h.revision}>
            {new Date(h.at).toLocaleString()} · {h.actor} · {labels[h.from]} → {labels[h.to]}</p>) : '尚未登记'}</div>})}>记录</Button>
      </Space>
      {item.actor && <div><Typography.Text type="secondary">{item.actor} · {item.changed_at ? new Date(item.changed_at).toLocaleString() : ''}</Typography.Text></div>}
    </div>)}
    {!!items.length && <Typography.Text type="secondary">仅登记人工转发微信，不代表工厂接单。</Typography.Text>}
  </div>;
}
