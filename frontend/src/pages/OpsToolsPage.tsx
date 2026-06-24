import { useState } from 'react';
import {
  Alert, Button, Card, Col, InputNumber, Row, Space, Statistic, Table, Tag, Typography, message,
} from 'antd';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  monthlyFinancial, monthlyFinancialXlsxUrl, ownerHealth,
  recycleBinList, recycleBinRestore, type RecycleBinItem,
} from '../api/system';
import PresetTable from '../components/PresetTable';

export default function OpsToolsPage() {
  const qc = useQueryClient();
  const health = useQuery({ queryKey: ['owner-health'], queryFn: ownerHealth });
  const bins = useQuery({ queryKey: ['recycle-bin'], queryFn: recycleBinList });

  const restore = useMutation({
    mutationFn: (f: string) => recycleBinRestore(f),
    onSuccess: (r: any) => {
      message.success(`已还原 ${r?.total ?? 0} 条数据`);
      qc.invalidateQueries();
    },
    onError: (e: any) => message.error(`还原失败: ${e?.response?.data?.detail ?? e?.message ?? '未知错误'}`),
  });

  const now = new Date();
  const [year, setYear] = useState(now.getMonth() === 0 ? now.getFullYear() - 1 : now.getFullYear());
  const [month, setMonth] = useState(now.getMonth() === 0 ? 12 : now.getMonth());
  const fin = useMutation({
    mutationFn: () => monthlyFinancial(year, month),
    onError: (e: any) => message.error(`查询失败: ${e?.response?.data?.detail ?? e?.message ?? '未知错误'}`),
  });

  const h = health.data;

  return (
    <Space direction="vertical" style={{ width: '100%' }} size="large">
      {/* 一页体检 (#10 + #4) */}
      <Card title="系统体检" loading={health.isLoading} extra={
        <Button size="small" onClick={() => health.refetch()}>刷新</Button>
      }>
        {h && (
          <>
            <Tag color={h.healthy ? 'green' : 'red'} style={{ marginBottom: 12 }}>
              {h.healthy ? '一切正常' : '有事项需处理'}
            </Tag>
            <Row gutter={16}>
              <Col xs={12} sm={8} md={6}><Statistic title="待处理异常" value={h.open_exceptions}
                valueStyle={{ color: h.open_exceptions ? '#cf1322' : undefined }} /></Col>
              <Col xs={12} sm={8} md={6}><Statistic title="失败的定时任务" value={h.failing_jobs.length}
                valueStyle={{ color: h.failing_jobs.length ? '#cf1322' : undefined }} /></Col>
              <Col xs={12} sm={8} md={6}><Statistic title="最新备份距今(小时)"
                value={h.latest_backup_age_h ?? '—'}
                valueStyle={{ color: h.backup_stale ? '#cf1322' : undefined }} /></Col>
              <Col xs={12} sm={8} md={6}><Statistic title="最新备份大小(MB)" value={h.latest_backup_size_mb ?? '—'} /></Col>
            </Row>
            {h.backup_stale && <Alert style={{ marginTop: 12 }} type="warning" showIcon
              message="最新备份过旧或缺失,请检查备份是否正常运行。" />}
            {h.failing_jobs.length > 0 && <Alert style={{ marginTop: 12 }} type="error" showIcon
              message={`最近失败的定时任务: ${h.failing_jobs.join(', ')}`} />}
          </>
        )}
      </Card>

      {/* 月度财务报表 (#3) */}
      <Card title="月度财务报表">
        <Space wrap>
          <InputNumber addonBefore="年" min={2020} max={2100} value={year}
            onChange={(v) => setYear(Number(v) || year)} />
          <InputNumber addonBefore="月" min={1} max={12} value={month}
            onChange={(v) => setMonth(Number(v) || month)} />
          <Button type="primary" loading={fin.isPending} onClick={() => fin.mutate()}>查询</Button>
          <Button href={monthlyFinancialXlsxUrl(year, month)} target="_blank">下载 Excel</Button>
        </Space>
        {fin.data && (
          <Row gutter={16} style={{ marginTop: 16 }}>
            <Col xs={12} sm={8} md={4}><Statistic title="订单数" value={fin.data.order_count} /></Col>
            <Col xs={12} sm={8} md={5}><Statistic title="营收" prefix="¥" value={fin.data.revenue} precision={2} /></Col>
            <Col xs={12} sm={8} md={5}><Statistic title="成本" prefix="¥" value={fin.data.cost} precision={2} /></Col>
            <Col xs={12} sm={8} md={5}><Statistic title="毛利" prefix="¥" value={fin.data.gross_profit} precision={2} /></Col>
            <Col xs={12} sm={8} md={5}><Statistic title="净利" prefix="¥" value={fin.data.net_profit} precision={2} /></Col>
          </Row>
        )}
      </Card>

      {/* 回收站 (#2 + #4) */}
      <Card title="回收站 (回滚删除前的数据快照)" loading={bins.isLoading} extra={
        <Button size="small" onClick={() => bins.refetch()}>刷新</Button>
      }>
        <Typography.Paragraph type="secondary" style={{ fontSize: 12 }}>
          导入回滚会先把数据存成快照。如误删,点"还原"把数据放回(保留原编号,重复点不会重复插)。
        </Typography.Paragraph>
        <PresetTable<RecycleBinItem>
          tableKey="recycle_bin"
          rowKey="file"
          size="small"
          pagination={false}
          dataSource={bins.data ?? []}
          columns={[
            { title: '快照文件', dataIndex: 'file' },
            { title: '大小(KB)', dataIndex: 'size_bytes', render: (b: number) => (b / 1024).toFixed(1) },
            {
              title: '操作', render: (_: unknown, r: RecycleBinItem) => (
                <Button size="small" loading={restore.isPending}
                  onClick={() => restore.mutate(r.file)}>还原</Button>
              ),
            },
          ]}
        />
      </Card>
    </Space>
  );
}
