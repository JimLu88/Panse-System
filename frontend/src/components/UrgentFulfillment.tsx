import { Alert, Table } from 'antd';
import { useQuery } from '@tanstack/react-query';
import { api } from '../api/client';

type Deadline = { order_no: string; label: string; due: string | null; days: number | null;
  original_due: string | null; reason: string; action: string };

export default function UrgentFulfillment() {
  const { data, isLoading, isError } = useQuery({
    queryKey: ['urgent-fulfillment'],
    queryFn: async () => (await api.get<Deadline[]>('/api/orders/urgent-fulfillment')).data,
    refetchInterval: 60000,
  });
  if (isError) return <Alert type="error" message="紧急订单读取失败" />;
  return <Table<Deadline> rowKey="order_no" loading={isLoading} dataSource={data}
    locale={{ emptyText: '当前没有需要跟进的订单' }} scroll={{ x: 760 }}
    columns={[
      { title: '工厂单号', dataIndex: 'label' }, { title: '淘宝或平台订单号', dataIndex: 'order_no' },
      { title: '原交期', dataIndex: 'original_due' }, { title: '跟进日期', dataIndex: 'due' },
      { title: '剩余时间', dataIndex: 'days', render: (d: number | null) =>
        d === null ? '待核' : d < 0 ? `已超期 ${-d} 天` : d === 0 ? '今天到期' : `剩 ${d} 天` },
      { title: '事项', dataIndex: 'action' }, { title: '原因', dataIndex: 'reason' },
    ]} />;
}
