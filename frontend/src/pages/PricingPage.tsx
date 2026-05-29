import { useState } from 'react';
import {
  Button,
  Card,
  Dropdown,
  Form,
  Input,
  InputNumber,
  Modal,
  Select,
  Space,
  Table,
  Tag,
  Tooltip,
  Typography,
  message,
} from 'antd';
import { DownloadOutlined, EditOutlined, ExportOutlined, PlusOutlined } from '@ant-design/icons';
import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  PricingSku,
  createPricingSku,
  downloadPricingTemplate,
  listPricingSkus,
  listPricingTemplates,
  updatePricingSku,
  listTaobaoExportTypes,
  downloadTaobaoExport,
} from '../api/client';

const PAGE_SIZE = 50;

function money(v: number | null) {
  return v === null || v === undefined ? '-' : `¥${Number(v).toLocaleString()}`;
}

function SkuFormFields() {
  return (
    <>
      <Space wrap style={{ width: '100%' }}>
        <Form.Item name="product_code" label="产品编码" rules={[{ required: true }]} style={{ minWidth: 180 }}>
          <Input placeholder="如 PS-24-21-001-0814" />
        </Form.Item>
        <Form.Item name="sku" label="SKU 描述">
          <Input placeholder="如 榉木餐桌-1.4米" style={{ minWidth: 200 }} />
        </Form.Item>
        <Form.Item name="sku_code" label="SKU 编码">
          <Input placeholder="系统自动生成可留空" style={{ minWidth: 160 }} />
        </Form.Item>
        <Form.Item name="size_category" label="尺寸分类">
          <Select style={{ width: 100 }} options={[
            { value: '小型', label: '小型' },
            { value: '中型', label: '中型' },
            { value: '大型', label: '大型' },
          ]} allowClear />
        </Form.Item>
      </Space>
      <Space wrap style={{ width: '100%' }}>
        <Form.Item name="list_price" label="标价">
          <InputNumber min={0} step={0.01} prefix="¥" style={{ width: 120 }} />
        </Form.Item>
        <Form.Item name="daily_price" label="日常价">
          <InputNumber min={0} step={0.01} prefix="¥" style={{ width: 120 }} />
        </Form.Item>
        <Form.Item name="small_promo" label="小促价">
          <InputNumber min={0} step={0.01} prefix="¥" style={{ width: 120 }} />
        </Form.Item>
        <Form.Item name="mid_promo" label="中促价">
          <InputNumber min={0} step={0.01} prefix="¥" style={{ width: 120 }} />
        </Form.Item>
        <Form.Item name="big_promo" label="大促价">
          <InputNumber min={0} step={0.01} prefix="¥" style={{ width: 120 }} />
        </Form.Item>
      </Space>
      <Space wrap style={{ width: '100%' }}>
        <Form.Item name="accounting_cost" label="会计成本">
          <InputNumber min={0} step={0.01} prefix="¥" style={{ width: 120 }} />
        </Form.Item>
        <Form.Item name="physical_cost" label="物理成本">
          <InputNumber min={0} step={0.01} prefix="¥" style={{ width: 120 }} />
        </Form.Item>
        <Form.Item name="platform_fee_rate" label="平台佣金率">
          <InputNumber min={0} max={1} step={0.01} style={{ width: 100 }} placeholder="如 0.05" />
        </Form.Item>
        <Form.Item name="tax" label="税率">
          <InputNumber min={0} max={1} step={0.01} style={{ width: 100 }} placeholder="如 0.03" />
        </Form.Item>
      </Space>
      <Form.Item name="image_url" label="图片 URL（选填）">
        <Input placeholder="https://..." />
      </Form.Item>
    </>
  );
}

export default function PricingPage() {
  const qc = useQueryClient();
  const [q, setQ] = useState('');
  const [sizeCategory, setSizeCategory] = useState<string | undefined>(undefined);
  const [page, setPage] = useState(1);
  const [createOpen, setCreateOpen] = useState(false);
  const [editRow, setEditRow] = useState<PricingSku | null>(null);
  const [form] = Form.useForm();

  const { data, isFetching } = useQuery({
    queryKey: ['pricing-skus', q, sizeCategory, page],
    queryFn: () =>
      listPricingSkus({ q: q || undefined, size_category: sizeCategory, limit: PAGE_SIZE, offset: (page - 1) * PAGE_SIZE }),
    placeholderData: keepPreviousData,
  });

  const { data: templates } = useQuery({
    queryKey: ['pricing-templates'],
    queryFn: listPricingTemplates,
    staleTime: 60 * 60 * 1000,
  });

  async function handleDownloadTemplate(key: string, label: string) {
    try {
      const blob = await downloadPricingTemplate(key);
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `${label}.xlsx`;
      a.click();
      URL.revokeObjectURL(url);
    } catch {
      message.error('模板下载失败');
    }
  }

  const { data: exportTypes } = useQuery({
    queryKey: ['taobao-export-types'],
    queryFn: listTaobaoExportTypes,
    staleTime: 60 * 60 * 1000,
  });

  async function handleExport(exportType: string, label: string) {
    try {
      const blob = await downloadTaobaoExport(exportType);
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `淘宝-${label}.xlsx`;
      a.click();
      URL.revokeObjectURL(url);
    } catch {
      message.error('导出失败');
    }
  }

  const createMut = useMutation({
    mutationFn: createPricingSku,
    onSuccess: () => {
      message.success('定价 SKU 已创建');
      setCreateOpen(false);
      form.resetFields();
      qc.invalidateQueries({ queryKey: ['pricing-skus'] });
    },
    onError: (e: any) => message.error(e?.response?.data?.detail ?? '创建失败'),
  });

  const updateMut = useMutation({
    mutationFn: ({ id, patch }: { id: number; patch: Record<string, unknown> }) =>
      updatePricingSku(id, patch),
    onSuccess: () => {
      message.success('已更新');
      setEditRow(null);
      form.resetFields();
      qc.invalidateQueries({ queryKey: ['pricing-skus'] });
    },
    onError: (e: any) => message.error(e?.response?.data?.detail ?? '更新失败'),
  });

  function openEdit(row: PricingSku) {
    setEditRow(row);
    form.setFieldsValue(row);
  }

  return (
    <Space direction="vertical" style={{ width: '100%' }} size="middle">
      <Space style={{ justifyContent: 'space-between', width: '100%' }}>
        <Typography.Title level={4} style={{ margin: 0 }}>定价总表</Typography.Title>
        <Space>
          <Dropdown
            disabled={!templates || templates.length === 0}
            menu={{
              items: (templates ?? []).map((t) => ({
                key: t.key,
                label: (
                  <Space direction="vertical" size={0}>
                    <span>{t.label}</span>
                    <Typography.Text type="secondary" style={{ fontSize: 11 }}>{t.desc}</Typography.Text>
                  </Space>
                ),
                onClick: () => handleDownloadTemplate(t.key, t.label),
              })),
            }}
          >
            <Button icon={<DownloadOutlined />}>一键模板下载</Button>
          </Dropdown>
          <Tooltip title="把当前系统定价数据填入淘宝后台批量格式, 下载后可直接上传淘宝后台">
            <Dropdown
              disabled={!exportTypes || exportTypes.length === 0}
              menu={{
                items: (exportTypes ?? []).map((t) => ({
                  key: t.key,
                  label: t.label,
                  onClick: () => handleExport(t.key, t.label),
                })),
              }}
            >
              <Button icon={<ExportOutlined />}>批量导出（填好数据）</Button>
            </Dropdown>
          </Tooltip>
          <Button type="primary" icon={<PlusOutlined />} onClick={() => { setCreateOpen(true); form.resetFields(); }}>
            新增定价
          </Button>
        </Space>
      </Space>
      <Card size="small">
        <Space wrap>
          <Input.Search
            allowClear
            placeholder="搜产品编码 / SKU 编码 / 描述"
            style={{ width: 280 }}
            onSearch={(v) => { setQ(v); setPage(1); }}
          />
          <Select
            allowClear
            placeholder="大小分类"
            style={{ width: 140 }}
            value={sizeCategory}
            onChange={(v) => { setSizeCategory(v); setPage(1); }}
            options={[
              { value: '小型', label: '小型' },
              { value: '中型', label: '中型' },
              { value: '大型', label: '大型' },
            ]}
          />
        </Space>
      </Card>
      <Table<PricingSku>
        size="small"
        rowKey="id"
        loading={isFetching}
        dataSource={data?.items ?? []}
        scroll={{ x: 1200 }}
        pagination={{
          current: page,
          pageSize: PAGE_SIZE,
          total: data?.total ?? 0,
          showTotal: (t) => `共 ${t} 条`,
          onChange: setPage,
          showSizeChanger: false,
        }}
        columns={[
          { title: '产品编码', dataIndex: 'product_code', fixed: 'left', width: 110 },
          { title: 'SKU 编码', dataIndex: 'sku_code', fixed: 'left', width: 120 },
          { title: '描述', dataIndex: 'sku', width: 160, ellipsis: true },
          { title: '分类', dataIndex: 'size_category', width: 70 },
          { title: '标价', dataIndex: 'list_price', width: 90, render: money },
          { title: '日常价', dataIndex: 'daily_price', width: 90, render: money },
          { title: '小促', dataIndex: 'small_promo', width: 90, render: money },
          { title: '中促', dataIndex: 'mid_promo', width: 90, render: money },
          { title: '大促', dataIndex: 'big_promo', width: 90, render: money },
          {
            title: '毛利率',
            dataIndex: 'gross_margin_rate',
            width: 90,
            render: (v: number | null) =>
              v === null || v === undefined ? '-'
                : <Tag color={v >= 0.3 ? 'green' : v >= 0.15 ? 'orange' : 'red'}>{(Number(v) * 100).toFixed(1)}%</Tag>,
          },
          { title: '会计成本', dataIndex: 'accounting_cost', width: 100, render: money },
          { title: '物理成本', dataIndex: 'physical_cost', width: 100, render: money },
          {
            title: '操作', width: 70, fixed: 'right',
            render: (_: unknown, row: PricingSku) => (
              <Button size="small" icon={<EditOutlined />} onClick={() => openEdit(row)}>编辑</Button>
            ),
          },
        ]}
      />

      {/* 新增弹窗 */}
      <Modal
        title="新增定价 SKU"
        open={createOpen}
        onCancel={() => { setCreateOpen(false); form.resetFields(); }}
        onOk={() => form.submit()}
        confirmLoading={createMut.isPending}
        width={720}
        destroyOnClose
      >
        <Form form={form} layout="vertical" onFinish={(v) => createMut.mutate(v)}>
          <SkuFormFields />
        </Form>
      </Modal>

      {/* 编辑弹窗 */}
      <Modal
        title={`编辑定价 — ${editRow?.sku_code}`}
        open={!!editRow}
        onCancel={() => { setEditRow(null); form.resetFields(); }}
        onOk={() => form.submit()}
        confirmLoading={updateMut.isPending}
        width={720}
        destroyOnClose
      >
        <Form
          form={form}
          layout="vertical"
          onFinish={(v) => editRow && updateMut.mutate({ id: editRow.id, patch: v })}
        >
          <SkuFormFields />
        </Form>
      </Modal>
    </Space>
  );
}
