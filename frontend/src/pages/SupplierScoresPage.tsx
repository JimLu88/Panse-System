/**
 * 供应商月度评分 (Phase 8 Tier 1 #5, 借鉴 Tesla).
 */
import { useState } from 'react';
import {
  Alert, Button, Card, DatePicker, Space, Table, Tag, Typography, message,
} from 'antd';
import dayjs from 'dayjs';
import PresetTable from '../components/PresetTable';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  SupplierScore,
  computeSupplierScores,
  fetchSupplierScores,
} from '../api/client';


export default function SupplierScoresPage() {
  const qc = useQueryClient();
  const [period, setPeriod] = useState(dayjs().subtract(1, 'month'));

  const { data: scores = [] } = useQuery({
    queryKey: ['supplier-scores', period.year(), period.month() + 1],
    queryFn: () => fetchSupplierScores(period.year(), period.month() + 1),
  });

  const computeMut = useMutation({
    mutationFn: ({ y, m }: { y: number; m: number }) => computeSupplierScores(y, m),
    onSuccess: () => {
      message.success('评分已重算');
      qc.invalidateQueries({ queryKey: ['supplier-scores'] });
    },
  });

  return (
    <Space direction="vertical" style={{ width: '100%' }} size="middle">
      <Alert
        type="info" showIcon
        message="供应商评分卡 (借鉴 Tesla)"
        description="按 按时率 / 退货率 / 价格波动 综合分. 选供应商按数据说话."
      />
      <Card size="small"
            extra={
              <Space>
                <DatePicker.MonthPicker value={period}
                                         onChange={(v) => v && setPeriod(v)}
                                         allowClear={false} />
                <Button onClick={() => computeMut.mutate({
                  y: period.year(), m: period.month() + 1,
                })} loading={computeMut.isPending}>
                  重算
                </Button>
              </Space>
            }>
        <PresetTable<SupplierScore>
          tableKey="supplier_score"
          size="small" rowKey="supplier_id"
          dataSource={scores}
          pagination={false}
          columns={[
            { title: '排名', dataIndex: 'rank', width: 70,
              render: (v: number) => v && v <= 3 ?
                <Tag color={['gold', 'default', 'volcano'][v - 1]}>#{v}</Tag> :
                <Tag>#{v ?? '-'}</Tag>,
            },
            { title: '供应商 ID', dataIndex: 'supplier_id', width: 110 },
            { title: '综合评分', dataIndex: 'score', width: 110,
              sorter: (a: any, b: any) => (a.score ?? 0) - (b.score ?? 0),
              render: (v: number | null) => v != null ? (
                <Tag color={v >= 90 ? 'green' : v >= 70 ? 'orange' : 'red'}>
                  {v.toFixed(1)}
                </Tag>
              ) : '-',
            },
            { title: '订单数', dataIndex: 'total_orders', width: 90, align: 'right' },
            { title: '总金额', dataIndex: 'total_amount', width: 120, align: 'right',
              render: (v: number | null) => v != null ? `¥${v.toFixed(2)}` : '-' },
            { title: '按时率', dataIndex: 'on_time_rate', width: 90,
              render: (v: number | null) => v != null ? `${(v * 100).toFixed(1)}%` : '-' },
            { title: '退货率', dataIndex: 'return_rate', width: 90,
              render: (v: number | null) => v != null ? `${(v * 100).toFixed(1)}%` : '-' },
            { title: '价格波动', dataIndex: 'price_variance_pct', width: 110,
              render: (v: number | null) => v != null ? `${v >= 0 ? '+' : ''}${v.toFixed(1)}%` : '-' },
          ]}
        />
      </Card>
    </Space>
  );
}
