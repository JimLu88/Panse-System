/**
 * Plan F2: 取消带在制工厂单的订单 → 强制二选一弹窗 (不可跳过)。
 *
 * 后端 POST /api/orders/{id}/status 返回 422 {need_disposition:true, factory_orders:[...]}
 * 时由页面捕获并打开本弹窗; 确认后带 disposition(+planned_ship_date) 重发。
 */
import { useState } from 'react';
import { Alert, DatePicker, Modal, Radio, Space, Typography } from 'antd';
import dayjs, { Dayjs } from 'dayjs';

export interface DispositionRequest {
  orderId: number;
  status: string;   // 目标状态 (cancelled)
  factoryOrders: { id: number; factory_order_no: string; qty?: number }[];
}

export default function DispositionModal({
  req, onCancel, onSubmit, loading,
}: {
  req: DispositionRequest | null;
  onCancel: () => void;
  onSubmit: (d: { disposition: 'future' | 'release'; plannedShipDate?: string }) => void;
  loading?: boolean;
}) {
  const [disp, setDisp] = useState<'future' | 'release'>('release');
  const [shipDate, setShipDate] = useState<Dayjs | null>(null);
  return (
    <Modal
      open={!!req}
      title="该订单有在制工厂单 — 必须选择去向"
      okText="确认"
      cancelText="返回"
      confirmLoading={loading}
      maskClosable={false}
      keyboard={false}
      onCancel={onCancel}
      okButtonProps={{ disabled: disp === 'future' && !shipDate }}
      onOk={() => onSubmit({
        disposition: disp,
        plannedShipDate: disp === 'future' && shipDate ? shipDate.format('YYYY-MM-DD') : undefined,
      })}
    >
      <Alert
        type="warning" showIcon style={{ marginBottom: 12 }}
        message={`在制工厂单: ${(req?.factoryOrders || []).map((f) => f.factory_order_no).join('、') || '—'}`}
        description="直接取消会让锁定的物料悬空。请明确这批料的去向。"
      />
      <Radio.Group value={disp} onChange={(e) => setDisp(e.target.value)}>
        <Space direction="vertical">
          <Radio value="release">纯释放库存 — 工厂单作废，锁定物料全部释放回可用</Radio>
          <Radio value="future">转远期单 — 释放库存，同时派生远期订单，发货日前 10 天自动激活重锁</Radio>
        </Space>
      </Radio.Group>
      {disp === 'future' && (
        <div style={{ marginTop: 12 }}>
          <Typography.Text>预计发货日：</Typography.Text>
          <DatePicker
            value={shipDate} onChange={setShipDate}
            disabledDate={(d) => !!d && d < dayjs().startOf('day')}
          />
        </div>
      )}
    </Modal>
  );
}
