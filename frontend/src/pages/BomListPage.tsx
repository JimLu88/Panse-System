/**
 * BOM 清单 (扁平/可管理) — 反馈整改。
 *
 * 按产品编码 / 物料编码筛 BOM 行; 显示产品名+图; 可编辑(改 SKU 归属/料号/单耗/单位)或删除。
 * 与 /bom/:productCode 的"按产品看 BOM"不同 —— 这里是全局可搜、可改、可删的管理列表。
 */
import { useState } from 'react';
import {
  Alert, Button, Card, Form, Image, Input, InputNumber, Modal, Popconfirm,
  Space, Table, Tooltip, Typography, message,
} from 'antd';
import { DeleteOutlined, EditOutlined } from '@ant-design/icons';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { deleteBomLine, listBomLines, updateBomLine } from '../api/client';
import type { BomLineRow } from '../api/client';

export default function BomListPage() {
  const qc = useQueryClient();
  const [productCode, setProductCode] = useState('');
  const [materialCode, setMaterialCode] = useState('');
  const [editRow, setEditRow] = useState<BomLineRow | null>(null);
  const [form] = Form.useForm();

  const { data = [], isLoading } = useQuery({
    queryKey: ['bom-list', productCode, materialCode],
    queryFn: () => listBomLines({
      product_code: productCode || undefined,
      material_code: materialCode || undefined,
    }),
  });

  const invalidate = () => qc.invalidateQueries({ queryKey: ['bom-list'] });
  const delMut = useMutation({
    mutationFn: (id: number) => deleteBomLine(id),
    onSuccess: () => { message.success('已删除该 BOM 行'); invalidate(); },
    onError: () => message.error('删除失败'),
  });
  const editMut = useMutation({
    mutationFn: ({ id, patch }: { id: number; patch: Record<string, unknown> }) => updateBomLine(id, patch),
    onSuccess: () => { message.success('已保存'); setEditRow(null); form.resetFields(); invalidate(); },
    onError: (e: any) => message.error(e?.response?.data?.detail ?? '保存失败'),
  });

  const openEdit = (r: BomLineRow) => {
    setEditRow(r);
    form.setFieldsValue({
      sku_code: r.sku_code, sku: r.sku, material_code: r.material_code,
      material_name: r.material_name, unit: r.unit, qty_per_product: Number(r.qty_per_product),
    });
  };

  const columns = [
    {
      title: '产品', width: 230,
      render: (_: unknown, r: BomLineRow) => (
        <Space>
          {r.product_image_url
            ? <Image src={r.product_image_url} width={36} height={36} style={{ objectFit: 'cover', borderRadius: 4 }} />
            : <span style={{ color: '#ddd', fontSize: 11 }}>无图</span>}
          <div style={{ lineHeight: 1.2 }}>
            <div style={{ fontSize: 12 }}>{r.product_name ?? '-'}</div>
            <code style={{ fontSize: 11, color: '#999' }}>{r.product_code}</code>
          </div>
        </Space>
      ),
    },
    { title: 'SKU 编码', dataIndex: 'sku_code', width: 150, render: (v: string | null) => v ? <code style={{ fontSize: 12 }}>{v}</code> : <span style={{ color: '#ccc' }}>—</span> },
    { title: '物料编码', dataIndex: 'material_code', width: 110, render: (v: string) => <code style={{ fontSize: 12 }}>{v}</code> },
    {
      title: '物料名称', dataIndex: 'material_name', ellipsis: true,
      render: (v: string | null, r: BomLineRow) => v ? <Tooltip title={v}><span>{v}</span></Tooltip> : <span style={{ color: '#ccc' }}>{r.material_code}</span>,
    },
    { title: '单位', dataIndex: 'unit', width: 60 },
    { title: '单耗', dataIndex: 'qty_per_product', width: 70, align: 'right' as const },
    {
      title: '操作', width: 140,
      render: (_: unknown, r: BomLineRow) => (
        <Space>
          <Button size="small" icon={<EditOutlined />} onClick={() => openEdit(r)}>编辑</Button>
          <Popconfirm title="删这条 BOM 行？" description="只删这一条料，不影响产品。" okText="删除" okButtonProps={{ danger: true }} cancelText="取消" onConfirm={() => delMut.mutate(r.id)}>
            <Button size="small" danger icon={<DeleteOutlined />} />
          </Popconfirm>
        </Space>
      ),
    },
  ];

  return (
    <Space direction="vertical" style={{ width: '100%' }} size="middle">
      <Typography.Title level={4} style={{ margin: 0 }}>BOM 清单</Typography.Title>
      <Alert
        type="info" showIcon
        message="按产品编码 / 物料编码筛 BOM 行。数据错了可直接「编辑」(改 SKU 归属/料号/单耗/单位)或「删除」。"
        description="异常中心报的「一码挂多产品」可在这里按产品编码找出来，把错挂的行改 SKU 编码或删掉；整产品删除去「产品总表」。"
      />
      <Space wrap>
        <Input.Search placeholder="产品编码（如 PPS24210070901）" allowClear style={{ width: 300 }} onSearch={setProductCode} />
        <Input.Search placeholder="物料编码（如 AC-0074）" allowClear style={{ width: 240 }} onSearch={setMaterialCode} />
      </Space>
      <Card size="small">
        <Image.PreviewGroup>
          <Table<BomLineRow>
            rowKey="id" loading={isLoading} dataSource={data} columns={columns as any}
            size="small" scroll={{ x: 'max-content' }}
            pagination={{ pageSize: 50, showSizeChanger: true, pageSizeOptions: [50, 100, 200] }}
            locale={{ emptyText: '没有匹配的 BOM 行（默认显示最近 500 条，可按编码筛选）' }}
          />
        </Image.PreviewGroup>
      </Card>

      <Modal
        title={`编辑 BOM 行${editRow ? ` — ${editRow.product_code}` : ''}`}
        open={!!editRow}
        onCancel={() => { setEditRow(null); form.resetFields(); }}
        onOk={() => form.submit()}
        confirmLoading={editMut.isPending}
        destroyOnClose
      >
        <Form
          form={form} layout="vertical"
          onFinish={(v) => editMut.mutate({ id: editRow!.id, patch: {
            sku_code: v.sku_code || null, sku: v.sku || null,
            material_code: v.material_code || undefined, material_name: v.material_name || null,
            unit: v.unit || null, qty_per_product: v.qty_per_product,
          } })}
        >
          <Form.Item name="sku_code" label="SKU 编码" tooltip="改这里可把错挂的行归到正确的 SKU（解决一码挂多产品）">
            <Input placeholder="如 PPS2421007090112" />
          </Form.Item>
          <Form.Item name="sku" label="SKU 描述"><Input /></Form.Item>
          <Form.Item name="material_code" label="物料编码" tooltip="改料号需该物料已在物料库存在"><Input /></Form.Item>
          <Form.Item name="material_name" label="物料名称(冗余备注)"><Input /></Form.Item>
          <Space size="large">
            <Form.Item name="unit" label="单位"><Input style={{ width: 120 }} /></Form.Item>
            <Form.Item name="qty_per_product" label="单耗(每件用量)"><InputNumber min={0} style={{ width: 160 }} /></Form.Item>
          </Space>
        </Form>
      </Modal>
    </Space>
  );
}
