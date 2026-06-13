/**
 * 尺寸微定制对话框 (业务需求 §2).
 *
 * 用法:
 *   <CustomizationDialog open={open} baseSkuCode="PPS001..." onCancel={...} onConfirmed={(custom_sku_code) => ...} />
 *
 * 流程:
 *   1. 用户输入维度变更 (长/宽/高 mm)
 *   2. 点「预览」→ 显示 BOM diff (哪些料要随尺寸变, 哪些不变)
 *   3. 用户确认 → 调用 confirm, 后端生成 改01 SKU + 克隆 BOM
 */
import { useState } from 'react';
import {
  Alert,
  Button,
  Form,
  Input,
  InputNumber,
  Modal,
  Space,
  Table,
  Tag,
  Typography,
  message,
} from 'antd';
import { useMutation } from '@tanstack/react-query';
import {
  CustomizationDiffLine,
  CustomizationPreview,
  confirmCustomization,
  previewCustomization,
} from '../api/client';

const DIMS = [
  { key: '长', label: '长 (mm)' },
  { key: '宽', label: '宽 (mm)' },
  { key: '高', label: '高 (mm)' },
];

export function CustomizationDialog({
  open,
  baseSkuCode,
  orderNo,
  onCancel,
  onConfirmed,
}: {
  open: boolean;
  baseSkuCode: string;
  orderNo?: string;
  onCancel: () => void;
  onConfirmed: (customSkuCode: string) => void;
}) {
  const [form] = Form.useForm();
  const [preview, setPreview] = useState<CustomizationPreview | null>(null);

  const previewMut = useMutation({
    mutationFn: () => {
      const v = form.getFieldsValue();
      const changes: Record<string, number> = {};
      for (const d of DIMS) {
        if (v[d.key] != null) changes[d.key] = v[d.key];
      }
      if (Object.keys(changes).length === 0) {
        return Promise.reject({ response: { data: { detail: '至少改一个维度' } } });
      }
      return previewCustomization({
        base_sku_code: baseSkuCode,
        dimension_changes: changes,
      });
    },
    onSuccess: setPreview,
    onError: (e: any) => message.error(e?.response?.data?.detail ?? '预览失败'),
  });

  const confirmMut = useMutation({
    mutationFn: (ack: boolean) => {
      if (!preview) throw new Error('需要先 preview');
      return confirmCustomization({
        base_sku_code: baseSkuCode,
        dimension_changes: preview.dimension_changes as Record<string, unknown>,
        order_no: orderNo,
        note: form.getFieldValue('note'),
        acknowledge_shortage: ack,
      });
    },
    onSuccess: (r) => {
      message.success(`已生成定制 SKU ${r.custom_sku_code}, 克隆 ${r.cloned_bom_lines} 行 BOM`);
      onConfirmed(r.custom_sku_code);
      setPreview(null);
      form.resetFields();
    },
    onError: (e: any) => {
      const detail = e?.response?.data?.detail;
      // Plan F5: 缺料 → 分组弹窗确认后重发
      if (e?.response?.status === 409 && detail?.shortage_unacknowledged) {
        const sc = detail.stock_check || {};
        Modal.confirm({
          title: '该定制单存在缺料 — 请确认',
          width: 580,
          okText: '已知缺料，继续生成',
          cancelText: '先去备料',
          onOk: () => confirmMut.mutate(true),
          content: (
            <div>
              {(sc.need_purchase || []).length > 0 && (
                <p><b>需采购：</b>{sc.need_purchase.map((i: any) =>
                  `${i.material_name || i.material_code}(缺${i.shortage})`).join('、')}</p>
              )}
              {(sc.need_new_material || []).length > 0 && (
                <p><b>需新开料：</b>{sc.need_new_material.map((i: any) =>
                  i.material_name || i.material_code).join('、')}</p>
              )}
              {(sc.in_stock || []).length > 0 && (
                <p style={{ color: '#888' }}>现货够：{sc.in_stock.length} 项</p>
              )}
            </div>
          ),
        });
        return;
      }
      message.error(typeof detail === 'string' ? detail : '确认失败');
    },
  });

  return (
    <Modal
      open={open}
      title={
        <Space>
          <span>尺寸微定制</span>
          <Tag color="orange">业务需求 §2</Tag>
        </Space>
      }
      onCancel={() => {
        setPreview(null);
        onCancel();
      }}
      width={720}
      footer={[
        <Button key="cancel" onClick={onCancel}>取消</Button>,
        !preview ? (
          <Button key="preview" type="primary" loading={previewMut.isPending} onClick={() => previewMut.mutate()}>
            预览 BOM 变更
          </Button>
        ) : (
          <Space key="confirm">
            <Button onClick={() => setPreview(null)}>重填尺寸</Button>
            <Button type="primary" danger loading={confirmMut.isPending} onClick={() => confirmMut.mutate(false)}>
              确认 — 生成 {preview.proposed_custom_sku_code}
            </Button>
          </Space>
        ),
      ]}
    >
      <Alert
        type="info"
        showIcon
        style={{ marginBottom: 16 }}
        message={`基础 SKU: ${baseSkuCode}`}
        description="只填要变的维度, 不填的保持原样。确认后系统生成 改NN 后缀的新 SKU 并克隆 BOM。"
      />
      <Form form={form} layout="vertical">
        <Space style={{ width: '100%' }}>
          {DIMS.map((d) => (
            <Form.Item key={d.key} name={d.key} label={d.label}>
              <InputNumber min={100} max={5000} style={{ width: 120 }} placeholder="不变请留空" />
            </Form.Item>
          ))}
        </Space>
        <Form.Item name="note" label="备注 (可选)">
          <Input placeholder="例如: 客户要求 2100×1900 加长加宽" />
        </Form.Item>
      </Form>

      {preview && (
        <>
          <Typography.Title level={5} style={{ marginTop: 16 }}>
            BOM 变更预览
          </Typography.Title>
          <Table<CustomizationDiffLine>
            rowKey="material_code"
            dataSource={preview.diff_lines}
            pagination={false}
            size="small"
            columns={[
              { title: '物料', dataIndex: 'material_code', width: 110, render: (v: string) => <code>{v}</code> },
              { title: '名称', dataIndex: 'material_name', ellipsis: true },
              { title: '原数量', dataIndex: 'original_qty', width: 80, align: 'right' },
              { title: '新数量', dataIndex: 'new_qty', width: 80, align: 'right' },
              {
                title: '说明',
                dataIndex: 'note',
                render: (v: string | null) =>
                  v ? <Tag color="orange">{v}</Tag> : <Tag>不随尺寸变</Tag>,
              },
            ]}
          />
        </>
      )}
    </Modal>
  );
}
