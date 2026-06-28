/**
 * BOM 尺寸复核页 (配件 epic 阶段1d)。
 * AI 按 SKU 推演的面积料(岩板/玻璃/洞石)尺寸 → 人工核对、编辑、二次确认。
 * 口径: est_size 仅预估(inferred), 不动原 remark; 编辑保存后默认仍 inferred,
 *       点「确认」经二次确认弹窗 → confirmed(认定该尺寸可信)。计算面积时 remark 优先、缺则 est_size。
 */
import { useState } from 'react';
import {
  Alert, Button, Input, Popconfirm, Segmented, Select, Space, Table, Tag, Tooltip, Typography, message,
} from 'antd';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  SizeReviewRow, listSizeReview, patchSizeReview, runSizeInference,
} from '../api/bomSizeReview';

const CATEGORIES = ['岩板', '玻璃', '洞石饰面板'];
type StatusFilter = 'inferred' | 'confirmed' | 'all';

export default function BomSizeReviewPage() {
  const qc = useQueryClient();
  const [status, setStatus] = useState<StatusFilter>('inferred');
  const [category, setCategory] = useState<string | undefined>(undefined);
  const [edits, setEdits] = useState<Record<number, string>>({});

  const { data, isLoading, isError } = useQuery({
    queryKey: ['bom-size-review', status, category],
    queryFn: () => listSizeReview(status, category),
  });

  const invalidate = () => qc.invalidateQueries({ queryKey: ['bom-size-review'] });

  const saveMut = useMutation({
    mutationFn: ({ id, est, confirm }: { id: number; est: string; confirm: boolean }) =>
      patchSizeReview(id, est, confirm),
    onSuccess: (_r, v) => {
      message.success(v.confirm ? '已确认' : '已保存');
      setEdits((e) => {
        const n = { ...e };
        delete n[v.id];
        return n;
      });
      invalidate();
    },
    onError: (e: any) => message.error(e?.response?.data?.detail ?? '保存失败'),
  });

  const runMut = useMutation({
    mutationFn: () => runSizeInference(category ? [category] : CATEGORIES, true, false),
    onSuccess: (r) =>
      message.success(`推演完成: 缺尺寸 ${r.missing}, 落库 ${r.applied} 行`),
    onError: (e: any) => message.error(e?.response?.data?.detail ?? '推演失败'),
  });

  const rows = data ?? [];

  const columns = [
    { title: '产品', dataIndex: 'product_name', ellipsis: true, width: 200,
      render: (v: string, r: SizeReviewRow) => v || r.product_code },
    { title: 'SKU', dataIndex: 'sku', ellipsis: true, width: 200 },
    { title: '分类', dataIndex: 'category', width: 90,
      render: (v: string) => (v ? <Tag color="blue">{v}</Tag> : '—') },
    { title: '物料', dataIndex: 'material_name', ellipsis: true, width: 140 },
    {
      title: '原备注(remark)', dataIndex: 'remark', ellipsis: true, width: 160,
      render: (v: string) => (v ? <Tooltip title={v}><span style={{ color: '#999' }}>{v}</span></Tooltip> : <span style={{ color: '#ccc' }}>无</span>),
    },
    {
      title: '推演尺寸(长*深 mm)', dataIndex: 'est_size', width: 170,
      render: (v: string, r: SizeReviewRow) => (
        <Input
          size="small"
          value={edits[r.id] ?? v ?? ''}
          placeholder="如 1800*800"
          onChange={(e) => setEdits((s) => ({ ...s, [r.id]: e.target.value }))}
          style={{ width: 130 }}
        />
      ),
    },
    {
      title: '面积(㎡)', dataIndex: 'area', width: 90, align: 'right' as const,
      render: (v: number) => (v ? (v / 1_000_000).toFixed(3) : '—'),
    },
    {
      title: '状态', dataIndex: 'size_status', width: 90,
      render: (v: string) =>
        v === 'confirmed' ? <Tag color="green">已确认</Tag>
          : v === 'inferred' ? <Tag color="orange">AI预估</Tag>
            : <Tag>—</Tag>,
    },
    {
      title: '操作', width: 160, fixed: 'right' as const,
      render: (_: unknown, r: SizeReviewRow) => {
        const cur = edits[r.id] ?? r.est_size ?? '';
        const changed = (edits[r.id] ?? r.est_size ?? '') !== (r.est_size ?? '');
        return (
          <Space size={4}>
            <Button
              size="small"
              disabled={!changed || !cur.trim()}
              loading={saveMut.isPending}
              onClick={() => saveMut.mutate({ id: r.id, est: cur.trim(), confirm: false })}
            >保存</Button>
            <Popconfirm
              title="确认这个尺寸?"
              description="确认后标为「已确认」，参与配件成本分摊计算。"
              okText="确认无误" cancelText="再看看"
              onConfirm={() => saveMut.mutate({ id: r.id, est: cur.trim(), confirm: true })}
            >
              <Button size="small" type="primary" ghost disabled={!cur.trim() || r.size_status === 'confirmed'}>
                确认
              </Button>
            </Popconfirm>
          </Space>
        );
      },
    },
  ];

  return (
    <Space direction="vertical" style={{ width: '100%' }} size="middle">
      <Typography.Title level={4} style={{ margin: 0 }}>BOM 尺寸复核</Typography.Title>
      <Alert
        type="info" showIcon
        message="AI 按 SKU 尺寸推演面积料(岩板/玻璃/洞石)的下料尺寸，供人工核对。"
        description="橙色「AI预估」是推演值、可改；改完点「保存」仍是预估，点「确认」二次确认后转「已确认」。原始备注 remark 不会被改动；计算面积时 remark 优先、缺则用此推演尺寸。圆桌按外接方形 d×d(整块岩板下料含四角废料)。"
      />
      <Space wrap>
        <Segmented
          value={status}
          onChange={(v) => setStatus(v as StatusFilter)}
          options={[
            { label: 'AI预估待核', value: 'inferred' },
            { label: '已确认', value: 'confirmed' },
            { label: '全部', value: 'all' },
          ]}
        />
        <Select
          allowClear placeholder="全部分类" style={{ width: 160 }}
          value={category} onChange={setCategory}
          options={CATEGORIES.map((c) => ({ label: c, value: c }))}
        />
        <Popconfirm
          title="重新推演并落库?"
          description={`对 ${category || '岩板/玻璃/洞石'} 缺尺寸行重新按 SKU 推演，写入预估值(不覆盖已确认行)。`}
          okText="推演" cancelText="取消"
          onConfirm={() => runMut.mutate()}
        >
          <Button loading={runMut.isPending}>重新推演(落库)</Button>
        </Popconfirm>
        <Typography.Text type="secondary">共 {rows.length} 行</Typography.Text>
      </Space>
      {isError ? (
        <Alert type="error" message="加载失败" />
      ) : (
        <Table
          rowKey="id"
          size="small"
          loading={isLoading}
          columns={columns}
          dataSource={rows}
          scroll={{ x: 1200 }}
          pagination={{ pageSize: 50, showSizeChanger: true }}
        />
      )}
    </Space>
  );
}
