/**
 * 配件采购视图 (按配件汇总) — Block E。
 *
 * 跨所有订单, 按配件(料号)汇总还缺多少: 待买(未采购) / 已买未到(已下单+运输中)。
 * 一次性采购方便。可标「已购买」(填采购单号) / 「已到货」/ 「自送已到」(玻璃这类工厂周边买+自送, 免物流号)。
 * 与「订单看板里按订单看配件」是两个视角 —— 这里是按配件横切。
 */
import { Alert, Button, Card, Input, Modal, Popconfirm, Space, Table, Tag, Typography, message } from 'antd';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { bulkUpdateAccessories, fetchAccessoriesByComponent } from '../api/client';
import type { ComponentGroup, ComponentItem } from '../api/client';

const STATUS_COLOR: Record<string, string> = {
  未采购: 'default', 已下单: 'blue', 运输中: 'gold', 已到货: 'green', 工厂提供: 'cyan',
};

export default function AccessoryPurchasePage() {
  const qc = useQueryClient();
  const { data: groups = [], isLoading } = useQuery({
    queryKey: ['acc-by-component'],
    queryFn: fetchAccessoriesByComponent,
    refetchInterval: 60000,
  });

  const bulkMut = useMutation({
    mutationFn: bulkUpdateAccessories,
    onSuccess: (r) => {
      message.success(`已更新 ${r.updated} 项`);
      qc.invalidateQueries({ queryKey: ['acc-by-component'] });
      qc.invalidateQueries({ queryKey: ['orders-kanban-acc'] });
    },
    onError: () => message.error('更新失败'),
  });

  const idsByStatus = (g: ComponentGroup, statuses: string[]) =>
    g.items.filter((i) => statuses.includes(i.status)).map((i) => i.id);
  const allIds = (g: ComponentGroup) => g.items.map((i) => i.id);  // 视图本就只含未到货项

  const markBought = (g: ComponentGroup) => {
    let po = '';
    Modal.confirm({
      title: `「${g.material_name ?? g.material_code}」标为已购买`,
      content: <Input placeholder="采购单号（选填）" onChange={(e) => { po = e.target.value; }} />,
      okText: '确认已购买', cancelText: '取消',
      onOk: () => bulkMut.mutate({ item_ids: idsByStatus(g, ['未采购']), status: '已下单', purchase_no: po || undefined }),
    });
  };

  const columns = [
    { title: '配件', dataIndex: 'material_name', render: (v: string, r: ComponentGroup) => v ?? r.material_code },
    { title: '编码', dataIndex: 'material_code', width: 110, render: (v: string) => <code style={{ fontSize: 12 }}>{v}</code> },
    {
      title: '待买', dataIndex: 'to_buy_qty', width: 90, align: 'right' as const,
      render: (v: string, r: ComponentGroup) =>
        Number(v) > 0 ? <Tag color="red">{v}{r.unit ?? ''}</Tag> : <span style={{ color: '#bbb' }}>0</span>,
    },
    {
      title: '已买未到', dataIndex: 'bought_pending_qty', width: 100, align: 'right' as const,
      render: (v: string, r: ComponentGroup) =>
        Number(v) > 0 ? <Tag color="gold">{v}{r.unit ?? ''}</Tag> : <span style={{ color: '#bbb' }}>0</span>,
    },
    { title: '涉及订单', dataIndex: 'order_count', width: 90, align: 'right' as const },
    {
      title: '操作', width: 300,
      render: (_: unknown, g: ComponentGroup) => (
        <Space wrap>
          <Button size="small" type="primary" onClick={() => markBought(g)}
                  disabled={idsByStatus(g, ['未采购']).length === 0}>标已购买</Button>
          <Popconfirm title="这种配件全部标为已到货？" okText="确认" cancelText="取消"
                      onConfirm={() => bulkMut.mutate({ item_ids: allIds(g), status: '已到货' })}>
            <Button size="small">已到货</Button>
          </Popconfirm>
          <Popconfirm title="标为自送(免物流号)且已到？" okText="确认" cancelText="取消"
                      onConfirm={() => bulkMut.mutate({ item_ids: allIds(g), status: '已到货', self_delivered: true })}>
            <Button size="small">自送已到</Button>
          </Popconfirm>
        </Space>
      ),
    },
  ];

  return (
    <Space direction="vertical" style={{ width: '100%' }} size="middle">
      <Typography.Title level={4} style={{ margin: 0 }}>配件采购（按配件汇总）</Typography.Title>
      <Alert
        type="info" showIcon
        message="按配件看全局还缺多少 —— 方便一次性采购。「待买」=未采购；「已买未到」=已下单/运输中。"
        description="「标已购买」可填采购单号；玻璃这类工厂周边买、自己送的，用「自送已到」(免物流号)。展开看每个配件分摊到哪些订单。与订单看板的「按订单看配件」是两个视角。"
      />
      <Card size="small">
        <Table<ComponentGroup>
          rowKey="material_code"
          loading={isLoading}
          dataSource={groups}
          columns={columns as any}
          size="small"
          pagination={false}
          locale={{ emptyText: '当前没有待采购的配件（都已到货，或还没生成配件清单）' }}
          expandable={{
            expandedRowRender: (g) => (
              <Table<ComponentItem>
                rowKey="id" dataSource={g.items} size="small" pagination={false}
                columns={[
                  { title: '订单号', dataIndex: 'order_no', render: (v: string) => <code style={{ fontSize: 12 }}>{v}</code> },
                  { title: '数量', dataIndex: 'qty_required', width: 80, align: 'right' as const, render: (v: string) => `${v}${g.unit ?? ''}` },
                  { title: '状态', dataIndex: 'status', width: 90, render: (v: string) => <Tag color={STATUS_COLOR[v] ?? 'default'}>{v}</Tag> },
                  { title: '采购单号', dataIndex: 'purchase_no', width: 140, render: (v: string | null) => v || <span style={{ color: '#ccc' }}>—</span> },
                  {
                    title: '物流号', dataIndex: 'tracking_no', width: 140,
                    render: (v: string | null, r: ComponentItem) =>
                      r.self_delivered ? <Tag color="purple">自送</Tag> : (v || <span style={{ color: '#ccc' }}>—</span>),
                  },
                ]}
              />
            ),
          }}
        />
      </Card>
    </Space>
  );
}
