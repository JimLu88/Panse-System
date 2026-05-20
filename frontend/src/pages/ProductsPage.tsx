import { useState } from 'react';
import {
  Alert,
  Button,
  Form,
  Input,
  Modal,
  Select,
  Space,
  Table,
  Typography,
  message,
} from 'antd';
import { PlusOutlined } from '@ant-design/icons';
import { Link } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Product, createProduct, listProducts } from '../api/client';

const CATEGORY_OPTIONS = [
  { value: '14', label: '14 客厅-茶几' },
  { value: '15', label: '15 客厅-柜' },
  { value: '16', label: '16 客厅-沙发' },
  { value: '21', label: '21 餐厅-餐桌' },
  { value: '22', label: '22 餐厅-椅凳' },
  { value: '25', label: '25 餐厅-餐边柜' },
  { value: '33', label: '33 卧室-床' },
  { value: '35', label: '35 卧室-柜' },
  { value: '38', label: '38 卧室-床头柜' },
  { value: '41', label: '41 书房-书桌' },
  { value: '45', label: '45 书房-书柜' },
  { value: '55', label: '55 玄关-柜' },
  { value: '78', label: '78 餐厅-岛台' },
  { value: '99', label: '99 其它' },
];

export default function ProductsPage() {
  const qc = useQueryClient();
  const [q, setQ] = useState('');
  const [open, setOpen] = useState(false);
  const [form] = Form.useForm();

  const { data, isLoading } = useQuery({
    queryKey: ['products', q],
    queryFn: () => listProducts(q || undefined),
  });

  const createMut = useMutation({
    mutationFn: createProduct,
    onSuccess: (p) => {
      Modal.success({
        title: '产品创建成功',
        content: (
          <div>
            <p>
              已分配产品编码：<b>{p.code}</b>
            </p>
            <p style={{ color: '#888' }}>下一步可以去「BOM」补登物料清单。</p>
          </div>
        ),
      });
      setOpen(false);
      form.resetFields();
      qc.invalidateQueries({ queryKey: ['products'] });
    },
    onError: (e: any) => message.error(e?.response?.data?.detail ?? '创建失败'),
  });

  const columns = [
    { title: '编码', dataIndex: 'code', width: 160 },
    { title: '名称', dataIndex: 'name', ellipsis: true },
    { title: '品牌', dataIndex: 'brand', width: 80 },
    { title: '类目', dataIndex: 'category', width: 140 },
    {
      title: '操作',
      width: 100,
      render: (_: unknown, row: Product) => <Link to={`/bom/${row.code}`}>查看 BOM</Link>,
    },
  ];

  return (
    <Space direction="vertical" style={{ width: '100%' }} size="middle">
      <Space style={{ justifyContent: 'space-between', width: '100%' }}>
        <Typography.Title level={4} style={{ margin: 0 }}>
          产品总表 (1)
        </Typography.Title>
        <Space>
          <Input.Search placeholder="按编码或名称" allowClear style={{ width: 280 }} onSearch={setQ} />
          <Button type="primary" icon={<PlusOutlined />} onClick={() => setOpen(true)}>
            新建产品
          </Button>
        </Space>
      </Space>

      <Alert
        type="info"
        showIcon
        message="产品编码由系统按 P + 品牌 + 年份 + 类目 + 计数 + 月日 自动生成，不需要手填。"
      />

      <Table<Product>
        rowKey="id"
        loading={isLoading}
        dataSource={data}
        columns={columns as any}
        pagination={{ pageSize: 20 }}
      />

      <Modal
        title="新建产品"
        open={open}
        onCancel={() => setOpen(false)}
        onOk={() => form.submit()}
        confirmLoading={createMut.isPending}
        destroyOnClose
      >
        <Form
          form={form}
          layout="vertical"
          onFinish={(v) => {
            const cat = CATEGORY_OPTIONS.find((c) => c.value === v.category);
            createMut.mutate({
              ...v,
              category_label: cat?.label.replace(/^\d+\s+/, ''),
            });
          }}
          initialValues={{ brand: 'PS' }}
        >
          <Form.Item name="name" label="产品名称" rules={[{ required: true }]}>
            <Input placeholder="如：畔色榉木无边床 |榉木金属腿床" />
          </Form.Item>
          <Space size="middle" style={{ width: '100%' }}>
            <Form.Item name="brand" label="品牌" rules={[{ required: true }]}>
              <Select
                style={{ width: 140 }}
                options={[
                  { value: 'PS', label: 'PS 畔色' },
                  { value: 'FG', label: 'FG 孚格' },
                ]}
              />
            </Form.Item>
            <Form.Item name="category" label="类目" rules={[{ required: true }]} style={{ flex: 1 }}>
              <Select style={{ width: 260 }} options={CATEGORY_OPTIONS} showSearch />
            </Form.Item>
          </Space>
          <Form.Item name="remark" label="备注">
            <Input />
          </Form.Item>
        </Form>
      </Modal>
    </Space>
  );
}
