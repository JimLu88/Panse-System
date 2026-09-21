import { useMemo, useState } from 'react';
import { Alert, Button, Card, Input, Space, Table, Tag, Typography } from 'antd';
import { useQuery } from '@tanstack/react-query';
import { api } from '../api/base';

type SourceFile = { path: string; status: string; origin: string; semantic_reviewed: boolean; declarations: { name: string; kind: string; line: number }[] };
type Index = { files: SourceFile[]; dependencies: { from: string; to: string; line: number }[]; errors: unknown[]; missing_scopes: string[]; packaged_scopes: string[]; notice: string };
type Review = { requested_paths: string[]; notice: string; version: string; scan_errors: unknown[]; missing_scopes: string[]; retired_sources: string[];
  business_review: { unmapped_changes: string[] }; code_candidates: { unknown_paths: string[]; truncated: boolean; files: (SourceFile & { distance: number })[] };
  checklist: { id: string; action: string; condition: string; check: string; kind: string; evidence: string; source_current: boolean }[] };

export function downloadReview(value: unknown, filename: string) {
  const url = URL.createObjectURL(new Blob([JSON.stringify(value, null, 2)], { type: 'application/json;charset=utf-8' }));
  const link = document.createElement('a'); link.href = url; link.download = filename; link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export default function BusinessRelationshipReview() {
  const [search, setSearch] = useState(''), [paths, setPaths] = useState(''), [submitted, setSubmitted] = useState('');
  const index = useQuery({ queryKey: ['relationship-source-index'], queryFn: () => api.get<Index>('/api/admin/business-relationships/source-index').then(r => r.data), retry: false, staleTime: 60000 });
  const review = useQuery({ queryKey: ['relationship-change-review', submitted], enabled: !!submitted, retry: false,
    queryFn: () => api.get<Review>('/api/admin/business-relationships/changes', { params: { paths: submitted } }).then(r => r.data) });
  const files = useMemo(() => index.data?.files.filter(f => `${f.path} ${f.declarations.map(d => d.name).join(' ')}`.toLowerCase().includes(search.toLowerCase())) || [], [index.data, search]);
  return <Space direction="vertical" size="middle" style={{ width: '100%' }}>
    <Alert type="info" showIcon message="按本次改动集中核查，不触发任何业务操作" description="先输入修改文件，再查看受影响程序、已登记业务检查清单和剩余盲区。没有结果不代表安全。" />
    <Card size="small" title="本次改动影响检查">
      <Input.TextArea aria-label="本次修改文件" value={paths} onChange={e => setPaths(e.target.value)} rows={4} placeholder={'每行一个项目相对路径，例如：\nbackend/app/services/order_cost_service.py\nscripts/campaign_prior_failure_import.py'} />
      <Space wrap style={{ marginTop: 12 }}><Button type="primary" disabled={!paths.trim()} loading={review.isFetching} onClick={() => submitted === paths.trim() ? review.refetch() : setSubmitted(paths.trim())}>生成影响检查清单</Button>
        {review.data && <Button onClick={() => downloadReview(review.data, '业务影响检查清单.json')}>导出本次检查清单</Button>}</Space>
      {review.isError && <Alert type="error" message="核查失败，请检查路径格式、权限或连接；不能判定没有影响。" />}
      {review.data && <Space direction="vertical" style={{ width: '100%', marginTop: 12 }}>
        <Alert type="warning" showIcon message={`待核代码 ${review.data.code_candidates.files.length} 个；业务检查 ${review.data.checklist.length} 项；未详细建模 ${review.data.business_review.unmapped_changes.length} 个`} description={review.data.notice} />
        {(review.data.code_candidates.truncated || review.data.scan_errors.length > 0 || review.data.missing_scopes.length > 0) && <Alert type="warning" message="核查范围未完全展开或存在源码缺口" description={`深度截断：${review.data.code_candidates.truncated ? '是' : '否'}；扫描异常：${review.data.scan_errors.length}；未提供源码：${review.data.missing_scopes.join('、') || '无'}`} />}
        <Typography.Paragraph>未识别路径：{review.data.code_candidates.unknown_paths.join('、') || '无'}。未识别不等于无需检查。</Typography.Paragraph>
        <Table size="small" rowKey="id" dataSource={review.data.checklist} pagination={{ pageSize: 8 }} scroll={{ x: 760 }} columns={[
          { title: '影响 / 条件', render: (_, r) => <>{r.action}<div>{r.condition}</div></> },
          { title: '必须核查', dataIndex: 'check' },
          { title: '保护与证据', render: (_, r) => <><Tag>{r.kind === 'protect' ? '禁止自动覆盖' : '条件关联'}</Tag><Tag>{r.evidence === 'code' ? '已核代码' : '仍需复核'}</Tag>{!r.source_current && <Tag color="orange">来源已变化</Tag>}</> },
        ]} />
        <Typography.Paragraph className="br-wrap">未详细建模文件：{review.data.business_review.unmapped_changes.join('；') || '本次路径均有部分登记，仍非全部语义已审核'}</Typography.Paragraph>
        <Table size="small" rowKey="path" dataSource={review.data.code_candidates.files} pagination={{ pageSize: 8 }} columns={[{ title: '需要核查的程序', dataIndex: 'path' }, { title: '依赖层数', dataIndex: 'distance' }]} />
      </Space>}
    </Card>
    <Card size="small" title="全程序代码索引">
      <Input.Search aria-label="搜索程序或函数" value={search} onChange={e => setSearch(e.target.value)} placeholder="搜索程序路径或函数名" allowClear />
      {index.isError && <Alert type="error" message="代码索引读取失败" action={<Button onClick={() => index.refetch()}>重新读取索引</Button>} />}
      {index.data && <><Alert type="info" style={{ marginTop: 12 }} message={`${index.data.files.length} 个程序文件，${index.data.dependencies.length} 条静态依赖候选`} description={index.data.notice} />
        {!!index.data.packaged_scopes?.length && <Typography.Paragraph>同版发布源码索引：{index.data.packaged_scopes.join('、')}。这些文件是构建时的脱敏快照，不代表本机此刻运行的报名脚本已同步更新。</Typography.Paragraph>}
        {(index.data.errors.length > 0 || index.data.missing_scopes.length > 0) && <Alert type="warning" message={`扫描异常 ${index.data.errors.length}；缺源码范围：${index.data.missing_scopes.join('、') || '无'}`} />}
        <Table size="small" loading={index.isFetching} rowKey="path" dataSource={files} pagination={{ pageSize: 10 }} scroll={{ x: 680 }} expandable={{ expandedRowRender: r => <Space direction="vertical" style={{ width: '100%' }}><Typography.Text>这些是声明位置，不是业务完成证据。</Typography.Text><div className="br-wrap">{r.declarations.map(d => `${d.name}:${d.line}`).join(' · ') || '没有静态声明'}</div><Typography.Paragraph className="br-wrap">被引用于：{index.data!.dependencies.filter(e => e.to === r.path).map(e => `${e.from}:${e.line}`).join('；') || '未找到静态引用，动态调用仍可能存在'}</Typography.Paragraph></Space> }} columns={[
          { title: '程序路径', dataIndex: 'path' }, { title: '来源 / 状态', render: (_, r) => <><Tag>{r.origin === 'published_source_snapshot' ? '同版发布快照' : '当前源码'}</Tag>{r.status !== 'indexed' && <Tag color="red">解析失败</Tag>}</> }, { title: '声明数', render: (_, r) => r.declarations.length },
          { title: '加入本次检查', render: (_, r) => <Button size="small" onClick={() => setPaths(old => [...new Set([...old.split('\n').filter(Boolean), r.path])].join('\n'))}>加入检查</Button> },
        ]} /></>}
    </Card>
  </Space>;
}
