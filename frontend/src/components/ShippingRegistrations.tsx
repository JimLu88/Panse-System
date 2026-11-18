import { Alert, Table } from 'antd';
import { useQuery } from '@tanstack/react-query';
import { api } from '../api/client';

type Registration = { message_id: string; created_at: string; order_no: string | null;
  sub_order_no: string | null; carrier: string | null; tracking_no: string | null; detail: string };

export default function ShippingRegistrations() {
  const { data, isLoading, isError } = useQuery({
    queryKey: ['shipping-registrations'],
    queryFn: async () => (await api.get<Registration[]>('/api/shipments/registrations')).data,
    refetchInterval: 60000,
  });
  if (isError) return <Alert type="error" message="无法读取发货登记，请核对订单操作权限" />;
  return <Table<Registration> rowKey="message_id" loading={isLoading} dataSource={data}
    locale={{ emptyText: '尚无发货登记' }} scroll={{ x: 820 }}
    columns={[
      { title: '登记时间', dataIndex: 'created_at', render: (value: string) => new Date(value).toLocaleString('zh-CN') },
      { title: '主订单号', dataIndex: 'order_no' }, { title: '子订单号', dataIndex: 'sub_order_no' },
      { title: '承运商', dataIndex: 'carrier' }, { title: '运单号', dataIndex: 'tracking_no' },
      { title: '核对结果', dataIndex: 'detail' },
    ]} />;
}
