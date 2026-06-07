/**
 * 通用「查快递」组件 — 给任意带快递单号的实体显示实时物流状态 + 一键刷新 + 轨迹。
 *
 * 用法 (各页表格里): <ShipmentTracker entityType="order" entityId={record.id} />
 * 数据来自中央 shipments 表; 点刷新即时调 provider(快递100/快递鸟) 并回写派生状态。
 */
import { ReloadOutlined } from '@ant-design/icons';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Button, Popover, Space, Spin, Tag, Timeline, Tooltip, message } from 'antd';

import { listShipments, refreshEntityShipments, type Shipment, type ShipmentEntityType } from '../api/shipments';

interface Props {
  entityType: ShipmentEntityType;
  entityId: number;
}

const STATUS_COLOR: Record<string, string> = {
  已到货: 'green',
  运输中: 'blue',
};

function statusTag(s: Shipment) {
  const color = s.is_signed ? 'green' : STATUS_COLOR[s.mapped_status ?? ''] ?? 'default';
  const text = s.mapped_status ?? (s.last_error ? '查询失败' : '未查询');
  return <Tag color={color}>{text}</Tag>;
}

function trackTimeline(s: Shipment) {
  if (s.events && s.events.length) {
    return (
      <div style={{ maxWidth: 380, maxHeight: 320, overflow: 'auto' }}>
        <Timeline
          items={s.events.slice(0, 25).map((ev) => ({
            children: (
              <span>
                <span style={{ color: '#999', marginRight: 6 }}>{ev.time ?? ''}</span>
                {ev.context}
              </span>
            ),
          }))}
        />
      </div>
    );
  }
  return <span style={{ color: '#999' }}>暂无轨迹{s.last_error ? `: ${s.last_error}` : ''}</span>;
}

export default function ShipmentTracker({ entityType, entityId }: Props) {
  const qc = useQueryClient();
  const queryKey = ['shipments', entityType, entityId];

  const { data, isLoading } = useQuery({
    queryKey,
    queryFn: () => listShipments(entityType, entityId),
    staleTime: 60_000,
    enabled: !!entityId,
  });

  const refreshMut = useMutation({
    mutationFn: () => refreshEntityShipments(entityType, entityId),
    onSuccess: (rows) => {
      qc.setQueryData(queryKey, rows);
      const first = rows[0];
      if (first?.last_error) message.warning(first.last_error);
      else if (!first) message.info('该记录未填快递单号');
      else message.success('物流已刷新');
    },
    onError: (e: any) => message.error(e?.response?.data?.detail ?? '刷新失败'),
  });

  if (isLoading) return <Spin size="small" />;

  const shipments = data ?? [];

  if (!shipments.length) {
    return (
      <Button
        size="small"
        type="link"
        icon={<ReloadOutlined />}
        loading={refreshMut.isPending}
        onClick={() => refreshMut.mutate()}
      >
        查快递
      </Button>
    );
  }

  return (
    <Space size={4} wrap>
      {shipments.map((s) => (
        <Popover
          key={s.id}
          title={`${s.carrier_name ?? ''} ${s.tracking_no}${s.provider ? ` (${s.provider})` : ''}`}
          content={trackTimeline(s)}
          trigger="hover"
        >
          {statusTag(s)}
        </Popover>
      ))}
      <Tooltip title="实时刷新物流">
        <Button
          size="small"
          type="text"
          icon={<ReloadOutlined />}
          loading={refreshMut.isPending}
          onClick={() => refreshMut.mutate()}
        />
      </Tooltip>
    </Space>
  );
}
