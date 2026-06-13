/**
 * 会计期间管理 (Phase 8 Tier 1 #3).
 *
 * 列出最近 24 个月, admin 可 关闭 / 重开 / 锁死.
 */
import { useState } from 'react';
import PresetTable from '../components/PresetTable';
import {
  Alert,
  Button,
  Card,
  DatePicker,
  Modal,
  Space,
  Table,
  Tag,
  Typography,
  message,
} from 'antd';
import dayjs from 'dayjs';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  AccountingPeriod,
  closeAccountingPeriod,
  fetchAccountingPeriods,
  lockAccountingPeriod,
  reopenAccountingPeriod,
} from '../api/client';


export default function AccountingPeriodsPage() {
  const qc = useQueryClient();
  const [target, setTarget] = useState(dayjs());

  const { data: periods = [] } = useQuery({
    queryKey: ['accounting-periods'], queryFn: fetchAccountingPeriods,
  });

  const closeMut = useMutation({
    mutationFn: ({ y, m }: { y: number; m: number }) => closeAccountingPeriod(y, m),
    onSuccess: () => {
      message.success('已关闭, 该月数据不可改');
      qc.invalidateQueries({ queryKey: ['accounting-periods'] });
    },
    onError: (e: any) => message.error(e?.response?.data?.detail ?? '失败'),
  });
  const reopenMut = useMutation({
    mutationFn: ({ y, m }: { y: number; m: number }) => reopenAccountingPeriod(y, m),
    onSuccess: () => {
      message.success('已重开');
      qc.invalidateQueries({ queryKey: ['accounting-periods'] });
    },
    onError: (e: any) => message.error(e?.response?.data?.detail ?? '失败'),
  });
  const lockMut = useMutation({
    mutationFn: ({ y, m }: { y: number; m: number }) => lockAccountingPeriod(y, m),
    onSuccess: () => {
      message.success('已锁死. 锁后不能重开!');
      qc.invalidateQueries({ queryKey: ['accounting-periods'] });
    },
  });

  return (
    <Space direction="vertical" style={{ width: '100%' }} size="middle">
      <Alert
        type="warning" showIcon
        message="关账 = 该月所有订单 / 财务 不可改"
        description="关闭后 admin 还可以重开. 年审锁死后, 任何人不能重开 (除非走 SQL)."
      />
      <Card size="small" title="选择月份"
            extra={
              <Space>
                <DatePicker.MonthPicker value={target} onChange={(v) => v && setTarget(v)} />
                <Button danger
                        onClick={() => Modal.confirm({
                          title: `关闭 ${target.format('YYYY-MM')} ?`,
                          content: '关闭后该月所有 Order / 财务记录不可改',
                          onOk: () => closeMut.mutate({ y: target.year(), m: target.month() + 1 }),
                        })}>
                  关闭此月
                </Button>
              </Space>
            }>
        <PresetTable<AccountingPeriod>
          tableKey="accounting_period"
          size="small" rowKey="id"
          dataSource={periods}
          pagination={{ defaultPageSize: 24, showSizeChanger: true, pageSizeOptions: [20, 50, 100, 200] }}
          columns={[
            { title: '年月', width: 110,
              render: (_: any, r: AccountingPeriod) => `${r.year}-${String(r.month).padStart(2, '0')}` },
            { title: '状态', dataIndex: 'status', width: 100,
              render: (v: string) => (
                <Tag color={{ open: 'green', closed: 'orange', locked: 'red' }[v] ?? 'default'}>
                  {{ open: '开放', closed: '已关账', locked: '已锁死' }[v] ?? v}
                </Tag>
              ),
            },
            { title: '关账时间', dataIndex: 'closed_at', width: 170,
              render: (v: string | null) => v ? new Date(v).toLocaleString('zh-CN') : '-' },
            { title: '操作人', dataIndex: 'closed_by', width: 100 },
            { title: '操作', fixed: 'right', width: 200,
              render: (_: any, r: AccountingPeriod) => (
                <Space>
                  {r.status === 'closed' && (
                    <Button size="small"
                            onClick={() => reopenMut.mutate({ y: r.year, m: r.month })}>
                      重开
                    </Button>
                  )}
                  {(r.status === 'open' || r.status === 'closed') && (
                    <Button size="small" danger
                            onClick={() => Modal.confirm({
                              title: '锁死该月份?',
                              content: '锁死后任何人都不能再修改 (包括 admin). 通常在年审后执行.',
                              okType: 'danger',
                              onOk: () => lockMut.mutate({ y: r.year, m: r.month }),
                            })}>
                      锁死
                    </Button>
                  )}
                </Space>
              ),
            },
          ]}
        />
      </Card>
    </Space>
  );
}
