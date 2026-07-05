import { useState } from 'react';
import {
  Alert, Button, Card, Col, DatePicker, Form, Input, InputNumber, Modal, Popconfirm,
  Row, Segmented, Select, Space, Statistic, Table, Tag, Typography, Upload, message,
} from 'antd';
import { DownloadOutlined, InboxOutlined, PlusOutlined, ReloadOutlined, SettingOutlined } from '@ant-design/icons';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import dayjs from 'dayjs';
import {
  listReviewAssets, reviewStats, reviewCoverage, createReviewAsset, patchReviewAsset,
  deleteReviewAsset, importReviewAssets, getReviewSettings, putReviewSettings, reviewTemplateUrl,
  type ReviewAssetRow, type ReviewCoverageRow,
} from '../api/client';

const STATUS_META: Record<string, { label: string; color: string }> = {
  pending_review: { label: '待评价', color: 'default' },
  reviewed: { label: '已评价', color: 'green' },
  folding_soon: { label: '临近折叠', color: 'gold' },
  folded: { label: '已折叠', color: 'red' },
  released: { label: '已释放', color: 'blue' },
  abandoned: { label: '放弃', color: 'default' },
};

const SOURCE_META: Record<string, { label: string; color: string }> = {
  refill: { label: '补单', color: 'purple' },
  natural: { label: '自然', color: 'default' },
};

export default function ReviewAssetsPage() {
  const qc = useQueryClient();
  const [view, setView] = useState<'list' | 'coverage'>('list');
  const [filters, setFilters] = useState<{ status?: string; source?: string; due_in_days?: number; keyword?: string }>({});
  const [addOpen, setAddOpen] = useState(false);
  const [editRow, setEditRow] = useState<ReviewAssetRow | null>(null);
  const [form] = Form.useForm();
  const [editForm] = Form.useForm();

  const { data: rows = [], isLoading } = useQuery({
    queryKey: ['review-assets', filters],
    queryFn: () => listReviewAssets(filters),
  });
  const { data: stats } = useQuery({ queryKey: ['review-assets-stats'], queryFn: reviewStats });
  const { data: coverage = [] } = useQuery({
    queryKey: ['review-assets-coverage'], queryFn: reviewCoverage, enabled: view === 'coverage',
  });

  const refresh = () => {
    qc.invalidateQueries({ queryKey: ['review-assets'] });
    qc.invalidateQueries({ queryKey: ['review-assets-stats'] });
    qc.invalidateQueries({ queryKey: ['review-assets-coverage'] });
  };

  const doImport = async (file: File) => {
    try {
      const r = await importReviewAssets(file);
      message.success(
        `导入成功: 新增 ${r.inserted}, 重复跳过 ${r.skipped_duplicate}, ` +
        `无效 ${r.skipped_invalid}, 未关联订单 ${r.unlinked}`,
      );
      refresh();
    } catch (e: any) {
      message.error(e?.response?.data?.detail ?? '导入失败');
    }
    return false;
  };

  const markReleased = async (r: ReviewAssetRow) => {
    try {
      await patchReviewAsset(r.id, { status: 'released' });
      message.success('已标记为已释放');
      refresh();
    } catch (e: any) {
      message.error(e?.response?.data?.detail ?? '操作失败');
    }
  };

  const doDelete = async (r: ReviewAssetRow) => {
    try {
      await deleteReviewAsset(r.id);
      message.success('已删除');
      refresh();
    } catch (e: any) {
      message.error(e?.response?.data?.detail ?? '删除失败');
    }
  };

  const foldTag = (r: ReviewAssetRow) => {
    if (r.status === 'folded') return <Tag color="red">已折叠</Tag>;
    if (r.days_to_fold == null) return <Tag>—</Tag>;
    const d = r.days_to_fold;
    const color = d <= 7 ? 'red' : d <= 14 ? 'volcano' : d <= 30 ? 'gold' : 'default';
    const txt = d < 0 ? `逾期${-d}天` : `剩${d}天`;
    return <Tag color={color}>{r.fold_due_date} ({txt})</Tag>;
  };

  const openEdit = (r: ReviewAssetRow) => {
    setEditRow(r);
    editForm.setFieldsValue({
      review_date: r.review_date ? dayjs(r.review_date) : null,
      image_count: r.image_count,
      rating: r.rating ?? undefined,
      status: r.status,
      remark: r.remark ?? undefined,
    });
  };

  const submitAdd = async () => {
    const v = await form.validateFields();
    try {
      await createReviewAsset({
        order_no: v.order_no,
        review_date: v.review_date ? v.review_date.format('YYYY-MM-DD') : null,
        image_count: v.image_count ?? 0,
        rating: v.rating ?? null,
        product_code: v.product_code ?? null,
        sku_name: v.sku_name ?? null,
        shop: v.shop ?? null,
        remark: v.remark ?? null,
      });
      message.success('已新增');
      setAddOpen(false);
      form.resetFields();
      refresh();
    } catch (e: any) {
      message.error(e?.response?.data?.detail ?? '新增失败');
    }
  };

  const submitEdit = async () => {
    if (!editRow) return;
    const v = await editForm.validateFields();
    try {
      await patchReviewAsset(editRow.id, {
        review_date: v.review_date ? v.review_date.format('YYYY-MM-DD') : null,
        image_count: v.image_count,
        rating: v.rating ?? null,
        status: v.status,
        remark: v.remark ?? null,
      });
      message.success('已保存');
      setEditRow(null);
      refresh();
    } catch (e: any) {
      message.error(e?.response?.data?.detail ?? '保存失败');
    }
  };

  const openSettings = async () => {
    try {
      const s = await getReviewSettings();
      let fold = s.fold_days;
      let pend = s.pending_timeout_days;
      let cov = s.coverage_min;
      Modal.confirm({
        title: '评价资产设置',
        icon: <SettingOutlined />,
        width: 460,
        content: (
          <div style={{ marginTop: 12, display: 'flex', flexDirection: 'column', gap: 10 }}>
            <div>折叠周期(天): <InputNumber defaultValue={fold} min={1} max={730} onChange={(v) => { fold = Number(v ?? 180); }} /></div>
            <div>待评价超时(天): <InputNumber defaultValue={pend} min={0} max={60} onChange={(v) => { pend = Number(v ?? 10); }} /></div>
            <div>产品覆盖预警阈值(条): <InputNumber defaultValue={cov} min={0} max={100} onChange={(v) => { cov = Number(v ?? 2); }} /></div>
            <div style={{ color: '#999', fontSize: 12 }}>改折叠周期会对所有未结束记录重算折叠日。</div>
          </div>
        ),
        okText: '保存',
        onOk: async () => {
          await putReviewSettings({ fold_days: fold, pending_timeout_days: pend, coverage_min: cov });
          message.success('设置已保存');
          refresh();
        },
      });
    } catch (e: any) {
      message.error(e?.response?.data?.detail ?? '读取设置失败');
    }
  };

  const columns = [
    { title: '订单', dataIndex: 'order_no', width: 130, ellipsis: true,
      render: (v: string) => <span title={v}>…{v?.slice(-6)}</span> },
    { title: '店铺', dataIndex: 'shop', width: 90, ellipsis: true },
    { title: '产品', dataIndex: 'product_code', width: 100, ellipsis: true },
    { title: 'SKU', dataIndex: 'sku_name', width: 130, ellipsis: true },
    { title: '评价日', dataIndex: 'review_date', width: 105 },
    { title: '图', dataIndex: 'image_count', width: 50, align: 'right' as const },
    { title: '星', dataIndex: 'rating', width: 50, align: 'right' as const, render: (v: number | null) => v ?? '-' },
    { title: '折叠倒计时', width: 150, render: (_: unknown, r: ReviewAssetRow) => foldTag(r) },
    { title: '状态', dataIndex: 'status', width: 90,
      render: (v: string) => { const m = STATUS_META[v] ?? { label: v, color: 'default' }; return <Tag color={m.color}>{m.label}</Tag>; } },
    { title: '来源', dataIndex: 'source', width: 70,
      render: (v: string) => { const m = SOURCE_META[v] ?? { label: v, color: 'default' }; return <Tag color={m.color}>{m.label}</Tag>; } },
    { title: '操作', width: 200, fixed: 'right' as const, render: (_: unknown, r: ReviewAssetRow) => (
      <Space size={4} wrap>
        {r.status !== 'released' && r.status !== 'abandoned' && (
          <Popconfirm title="确认已安排新刷单节点释放评价图?" onConfirm={() => markReleased(r)}>
            <Button size="small" type="link">标记已释放</Button>
          </Popconfirm>
        )}
        <Button size="small" type="link" onClick={() => openEdit(r)}>编辑</Button>
        <Popconfirm title="删除该评价资产?" onConfirm={() => doDelete(r)}>
          <Button size="small" type="link" danger>删除</Button>
        </Popconfirm>
      </Space>
    ) },
  ];

  const coverageColumns = [
    { title: '产品', dataIndex: 'product_code', width: 120 },
    { title: '活跃带图评价', dataIndex: 'active_image_reviews', width: 130, align: 'right' as const,
      render: (v: number, r: ReviewCoverageRow) => <Tag color={r.below_min ? 'red' : 'green'}>{v} 条</Tag> },
    { title: '最近评价日', dataIndex: 'last_review_date', width: 120 },
    { title: '最近折叠日', dataIndex: 'next_fold_date', width: 120 },
    { title: '覆盖', width: 120,
      render: (_: unknown, r: ReviewCoverageRow) => (r.below_min
        ? <Tag color="red">偏低 (&lt;{r.coverage_min})</Tag>
        : <Tag color="green">达标</Tag>) },
  ];

  return (
    <Space direction="vertical" style={{ width: '100%' }} size="middle">
      <Space align="center">
        <Typography.Title level={4} style={{ margin: 0 }}>评价资产台账</Typography.Title>
        <Tag color="purple">营销</Tag>
      </Space>

      <Alert type="info" showIcon
        message={'本台账含补单(刷单)评价, 仅用于评价资产管理; 补单不进任何经营/财务数字。'
          + '淘宝评价约 180 天后退出商品页首屏默认排序(折叠), 请在折叠前安排新刷单节点释放评价图。'} />

      <Row gutter={12}>
        <Col span={6}><Card size="small"><Statistic title="临近折叠(30天内)" value={stats?.near_fold ?? 0} valueStyle={{ color: '#cf1322' }} /></Card></Col>
        <Col span={6}><Card size="small"><Statistic title="待评价超时" value={stats?.pending_overdue ?? 0} valueStyle={{ color: '#d46b08' }} /></Card></Col>
        <Col span={6}><Card size="small"><Statistic title="本月新增评价" value={stats?.new_this_month ?? 0} /></Card></Col>
        <Col span={6}><Card size="small"><Statistic title="低覆盖产品" value={stats?.low_coverage_products ?? 0} valueStyle={{ color: '#cf1322' }} /></Card></Col>
      </Row>

      <Space wrap>
        <Upload accept=".xlsx" showUploadList={false} beforeUpload={doImport}>
          <Button type="primary" icon={<InboxOutlined />}>导入 xlsx</Button>
        </Upload>
        <Button icon={<DownloadOutlined />} onClick={() => window.open(reviewTemplateUrl)}>下载模板</Button>
        <Button icon={<PlusOutlined />} onClick={() => setAddOpen(true)}>手工新增</Button>
        <Button icon={<SettingOutlined />} onClick={openSettings}>设置</Button>
        <Button icon={<ReloadOutlined />} onClick={refresh}>刷新</Button>
      </Space>

      <Space wrap>
        <Segmented value={view} onChange={(v) => setView(v as 'list' | 'coverage')}
          options={[{ label: '平铺列表', value: 'list' }, { label: '产品覆盖视图', value: 'coverage' }]} />
        {view === 'list' && (
          <>
            <Select allowClear placeholder="状态" style={{ width: 120 }} value={filters.status}
              onChange={(v) => setFilters((f) => ({ ...f, status: v }))}
              options={Object.entries(STATUS_META).map(([k, m]) => ({ value: k, label: m.label }))} />
            <Select allowClear placeholder="来源" style={{ width: 100 }} value={filters.source}
              onChange={(v) => setFilters((f) => ({ ...f, source: v }))}
              options={[{ value: 'refill', label: '补单' }, { value: 'natural', label: '自然' }]} />
            <Select allowClear placeholder="临近折叠" style={{ width: 130 }} value={filters.due_in_days}
              onChange={(v) => setFilters((f) => ({ ...f, due_in_days: v }))}
              options={[{ value: 7, label: '7天内' }, { value: 14, label: '14天内' }, { value: 30, label: '30天内' }]} />
            <Input.Search allowClear placeholder="订单/产品/SKU" style={{ width: 180 }}
              onSearch={(v) => setFilters((f) => ({ ...f, keyword: v || undefined }))} />
          </>
        )}
      </Space>

      {view === 'list' ? (
        <Table<ReviewAssetRow> size="small" loading={isLoading} rowKey="id" dataSource={rows} columns={columns}
          pagination={{ defaultPageSize: 50, showSizeChanger: true }} scroll={{ x: 1160 }}
          onRow={(r) => ({
            style: (r.status !== 'released' && r.status !== 'abandoned' && r.status !== 'folded' && r.days_to_fold != null)
              ? (r.days_to_fold <= 7 ? { background: '#fff1f0' } : r.days_to_fold <= 14 ? { background: '#fffbe6' } : {})
              : {},
          })} />
      ) : (
        <Table<ReviewCoverageRow> size="small" rowKey="product_code" dataSource={coverage} columns={coverageColumns}
          pagination={{ defaultPageSize: 50 }} scroll={{ x: 620 }} />
      )}

      <Modal title="手工新增评价资产" open={addOpen} onCancel={() => setAddOpen(false)} onOk={submitAdd} okText="新增" destroyOnClose>
        <Form form={form} layout="vertical" preserve={false}>
          <Form.Item name="order_no" label="订单号" rules={[{ required: true, message: '必填' }]}>
            <Input placeholder="平台订单号" />
          </Form.Item>
          <Form.Item name="review_date" label="评价日期 (留空 = 待评价)"><DatePicker style={{ width: '100%' }} /></Form.Item>
          <Space>
            <Form.Item name="image_count" label="图张数" initialValue={0}><InputNumber min={0} /></Form.Item>
            <Form.Item name="rating" label="星级"><InputNumber min={1} max={5} /></Form.Item>
          </Space>
          <Form.Item name="product_code" label="产品编码 (订单不在库时填)"><Input /></Form.Item>
          <Form.Item name="sku_name" label="SKU"><Input /></Form.Item>
          <Form.Item name="shop" label="店铺"><Input /></Form.Item>
          <Form.Item name="remark" label="备注"><Input.TextArea rows={2} /></Form.Item>
        </Form>
      </Modal>

      <Modal title="编辑评价资产" open={!!editRow} onCancel={() => setEditRow(null)} onOk={submitEdit} okText="保存" destroyOnClose>
        <Form form={editForm} layout="vertical" preserve={false}>
          <Form.Item name="review_date" label="评价日期 (补录后自动转已评价)"><DatePicker style={{ width: '100%' }} /></Form.Item>
          <Space>
            <Form.Item name="image_count" label="图张数"><InputNumber min={0} /></Form.Item>
            <Form.Item name="rating" label="星级"><InputNumber min={1} max={5} /></Form.Item>
          </Space>
          <Form.Item name="status" label="状态">
            <Select options={Object.entries(STATUS_META).map(([k, m]) => ({ value: k, label: m.label }))} />
          </Form.Item>
          <Form.Item name="remark" label="备注"><Input.TextArea rows={2} /></Form.Item>
        </Form>
      </Modal>
    </Space>
  );
}
