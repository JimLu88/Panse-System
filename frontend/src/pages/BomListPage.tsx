/**
 * BOM 清单 (扁平/可管理) — 反馈整改新增。
 *
 * 按产品编码 / 物料编码筛 BOM 行, 可删单条(清理串料 / 错挂到别的 SKU 的料)。
 * 与 /bom/:productCode 的"按产品看 BOM"不同 —— 这里是全局可搜、可删的管理列表。
 */
import { useState } from 'react';
import { Alert, Button, Card, Input, Popconfirm, Space, Table, Tooltip, Typography, message } from 'antd';
import { DeleteOutlined } from '@ant-design/icons';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { deleteBomLine, listBomLines } from '../api/client';
import type { BomLineRow } from '../api/client';

export default function BomListPage() {
  const qc = useQueryClient();
  const [productCode, setProductCode] = useState('');
  const [materialCode, setMaterialCode] = useState('');

  const { data = [], isLoading } = useQuery({
    queryKey: ['bom-list', productCode, materialCode],
    queryFn: () => listBomLines({
      product_code: productCode || undefined,
      material_code: materialCode || undefined,
    }),
  });

  const delMut = useMutation({
    mutationFn: (id: number) => deleteBomLine(id),
    onSuccess: () => { message.success('已删除该 BOM 行'); qc.invalidateQueries({ queryKey: ['bom-list'] }); },
    onError: () => message.error('删除失败'),
  });

  const columns = [
    { title: '产品编码', dataIndex: 'product_code', width: 150, render: (v: string) => <code style={{ fontSize: 12 }}>{v}</code> },
    {
      title: 'SKU 编码', dataIndex: 'sku_code', width: 150,
      render: (v: string | null) => v ? <code style={{ fontSize: 12 }}>{v}</code> : <span style={{ color: '#ccc' }}>—</span>,
    },
    { title: '物料编码', dataIndex: 'material_code', width: 110, render: (v: string) => <code style={{ fontSize: 12 }}>{v}</code> },
    {
      title: '物料名称', dataIndex: 'material_name', ellipsis: true,
      render: (v: string | null, r: BomLineRow) =>
        v ? <Tooltip title={v}><span>{v}</span></Tooltip> : <span style={{ color: '#ccc' }}>{r.material_code}</span>,
    },
    { title: '单位', dataIndex: 'unit', width: 70 },
    { title: '单耗', dataIndex: 'qty_per_product', width: 80, align: 'right' as const },
    {
      title: '操作', width: 90,
      render: (_: unknown, r: BomLineRow) => (
        <Popconfirm
          title="删这条 BOM 行？" description="只删这一条料，不影响产品和其它行。"
          okText="删除" okButtonProps={{ danger: true }} cancelText="取消"
          onConfirm={() => delMut.mutate(r.id)}
        >
          <Button size="small" danger icon={<DeleteOutlined />}>删除</Button>
        </Popconfirm>
      ),
    },
  ];

  return (
    <Space direction="vertical" style={{ width: '100%' }} size="middle">
      <Typography.Title level={4} style={{ margin: 0 }}>BOM 清单</Typography.Title>
      <Alert
        type="info" showIcon
        message="按产品编码 / 物料编码筛 BOM 行。用于清理串料/错挂的料（如一个 SKU 编码下混了两个产品的料）。"
        description="删行只删这一条料，不动产品本身；要整产品删除请去「产品总表」。异常中心报的「一码挂多产品」可在这里按产品编码找出来逐条删。"
      />
      <Space wrap>
        <Input.Search placeholder="产品编码（如 PPS24210070901）" allowClear style={{ width: 300 }} onSearch={setProductCode} />
        <Input.Search placeholder="物料编码（如 AC-0074）" allowClear style={{ width: 240 }} onSearch={setMaterialCode} />
      </Space>
      <Card size="small">
        <Table<BomLineRow>
          rowKey="id" loading={isLoading} dataSource={data} columns={columns as any}
          size="small" scroll={{ x: 'max-content' }}
          pagination={{ pageSize: 50, showSizeChanger: true, pageSizeOptions: [50, 100, 200] }}
          locale={{ emptyText: '没有匹配的 BOM 行（默认显示最近 500 条，可按编码筛选）' }}
        />
      </Card>
    </Space>
  );
}
