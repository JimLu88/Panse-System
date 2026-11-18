import { useState } from 'react';
import { Alert, Button, Checkbox, Form, Input, Modal, Space, Table, Tag, Typography, message } from 'antd';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '../api/client';

type Registration = { message_id: string; created_at: string; order_no: string | null;
  sub_order_no: string | null; carrier: string | null; tracking_no: string | null;
  detail: string; state: string; batch_id: string | null };
type Batch = { id: string; created_at: string; state: string; count: number; shop: string };
type InputValues = { order_no: string; carrier: string; tracking_no: string };
type Preview = InputValues & { snapshot_sha256: string; has_notes: boolean; notes: string; shop: string;
  lines: { id: number; sub_order_no: string; product_name: string; sku: string; qty: string; remark: string }[] };
const names: Record<string, string> = { ready: '待出表', review: '待核对', batch_prepared: '已出表，待淘宝结果',
  unknown: '淘宝结果待核', submitting: '正在提交', confirmed: '淘宝已确认' };
const errorText = (err: unknown) => (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail || '操作未完成，请核对原记录后再处理';
// getRandomValues also works on the existing LAN HTTP origin.
const requestId = () => {
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  bytes[6] = (bytes[6] & 15) | 64; bytes[8] = (bytes[8] & 63) | 128;
  const hex = [...bytes].map(b => b.toString(16).padStart(2, '0')).join('');
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
};

export default function ShippingRegistrations() {
  const cache = useQueryClient();
  const [form] = Form.useForm<InputValues>();
  const [selected, setSelected] = useState<React.Key[]>([]);
  const [open, setOpen] = useState(false);
  const [preview, setPreview] = useState<Preview | null>(null);
  const [handed, setHanded] = useState(false);
  const [notes, setNotes] = useState(false);
  const [busy, setBusy] = useState(false);
  const [manualId, setManualId] = useState('');
  const [batchRequest, setBatchRequest] = useState<{ id: string; ids: string[] } | null>(null);
  const registrations = useQuery({ queryKey: ['shipping-registrations'],
    queryFn: async () => (await api.get<Registration[]>('/api/shipments/registrations')).data, refetchInterval: 60000 });
  const batches = useQuery({ queryKey: ['shipping-batches'],
    queryFn: async () => (await api.get<Batch[]>('/api/shipments/batches')).data });
  const refresh = async () => {
    await Promise.all([cache.invalidateQueries({ queryKey: ['shipping-registrations'] }),
      cache.invalidateQueries({ queryKey: ['shipping-batches'] })]);
  };
  const download = async (id: string) => {
    try {
      const result = await api.get(`/api/shipments/batches/${id}/file`, { responseType: 'blob' });
      const url = URL.createObjectURL(result.data);
      const a = document.createElement('a');
      a.href = url; a.download = `批量自己联系发货-${id.slice(0, 8)}.xlsx`; a.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch { message.error('文件下载未完成，可从原批次再次下载'); }
  };
  const inspect = async () => {
    try {
      const values = await form.validateFields();
      setBusy(true);
      const result = await api.post<Preview>('/api/shipments/batch-preview', values);
      setPreview(result.data); setHanded(false); setNotes(false);
    } catch (err) { if (!(err as { errorFields?: unknown })?.errorFields) message.error(errorText(err)); }
    finally { setBusy(false); }
  };
  const register = async () => {
    if (!preview) return;
    setBusy(true);
    try {
      await api.post('/api/shipments/registrations/manual', { order_no: preview.order_no,
        carrier: preview.carrier, tracking_no: preview.tracking_no, snapshot_sha256: preview.snapshot_sha256,
        request_id: manualId, all_goods_handed_over: handed, notes_fulfilled: notes });
      setOpen(false); setPreview(null); form.resetFields(); await refresh();
      message.success('已登记，勾选后可生成官方批量表');
    } catch (err) { message.error(errorText(err)); }
    finally { setBusy(false); }
  };
  const prepare = async () => {
    setBusy(true);
    const request = batchRequest || { id: requestId(), ids: selected.map(String).sort() };
    setBatchRequest(request);
    try {
      const result = await api.post<Batch>('/api/shipments/batches', { request_id: request.id, message_ids: request.ids });
      setSelected([]); setBatchRequest(null); await refresh(); await download(result.data.id);
      message.success('官方批量表已生成；请在淘宝核对导入结果');
    } catch (err) {
      const status = (err as { response?: { status?: number } })?.response?.status;
      if (status === 409 || status === 422) setBatchRequest(null);
      message.error(errorText(err));
    }
    finally { setBusy(false); }
  };
  if (registrations.isError || batches.isError) return <Alert type="error" message="无法读取发货登记，请核对订单操作权限" />;
  return <Space direction="vertical" size="middle" style={{ width: '100%' }}>
    <Alert type="info" showIcon message="使用淘宝官方“批量自己联系发货”模板"
      description="填写主交易单号、完整物流公司名称和运单号。只登记整单全部商品已交运的订单，部分发货、多运单请单独核对。文件生成不代表淘宝已发货，自动上传尚未启用。" />
    <Space wrap>
      <Button onClick={() => { setManualId(requestId()); setPreview(null); setOpen(true); }}>新增发货登记</Button>
      <Button type="primary" loading={busy} disabled={!selected.length && !batchRequest} onClick={prepare}>
        {batchRequest ? '继续生成原批次' : `生成官方批量表（${selected.length}）`}</Button>
      <Button href="https://qn.taobao.com/home.htm/batch-consign" target="_blank" rel="noopener noreferrer">打开淘宝发货页面</Button>
    </Space>
    <Table<Registration> rowKey="message_id" loading={registrations.isLoading} dataSource={registrations.data}
      locale={{ emptyText: '尚无发货登记，可从上方新增或在登记群转发工厂原图和运单' }} scroll={{ x: 950 }}
      rowSelection={{ selectedRowKeys: selected, onChange: keys => { setSelected(keys); setBatchRequest(null); },
        getCheckboxProps: row => ({ disabled: row.state !== 'ready' || busy || Boolean(batchRequest) }) }}
      columns={[
        { title: '登记时间', dataIndex: 'created_at', render: value => new Date(value).toLocaleString('zh-CN') },
        { title: '主订单号', dataIndex: 'order_no' }, { title: '承运商', dataIndex: 'carrier' },
        { title: '运单号', dataIndex: 'tracking_no' },
        { title: '状态', dataIndex: 'state', render: value => <Tag>{names[value] || '已接收'}</Tag> },
        { title: '核对结果', dataIndex: 'detail' },
      ]} />
    <Typography.Title level={5}>已生成的批次</Typography.Title>
    <Typography.Text type="secondary">重复下载取得的是同一文件；上传结果不明时，请先查看淘宝“导入/导出记录”，不要再次导入。</Typography.Text>
    <Table<Batch> rowKey="id" dataSource={batches.data} loading={batches.isLoading} size="small"
      columns={[{ title: '生成时间', dataIndex: 'created_at', render: value => new Date(value).toLocaleString('zh-CN') },
        { title: '店铺', dataIndex: 'shop' }, { title: '订单数', dataIndex: 'count' }, { title: '状态', render: () => '已出表，待淘宝核对' },
        { title: '文件', render: (_, row) => <Button type="link" onClick={() => download(row.id)}>下载原批次</Button> }]} />
    <Modal title="登记整单发货" open={open} width={800} onCancel={() => { if (!busy) setOpen(false); }}
      footer={preview ? <Space><Button disabled={busy} onClick={() => setPreview(null)}>修改单号</Button>
        <Button type="primary" loading={busy} disabled={!handed || (preview.has_notes && !notes)} onClick={register}>确认登记</Button></Space>
        : <Button type="primary" loading={busy} onClick={inspect}>核对订单</Button>}>
      {!preview ? <Form form={form} layout="vertical">
        <Form.Item name="order_no" label="淘宝主交易单号" rules={[{ required: true }]}><Input autoComplete="off" /></Form.Item>
        <Form.Item name="carrier" label="淘宝物流公司名称或编码" rules={[{ required: true }]}>
          <Input placeholder="填写完整名称，如中通快运；不要混用中通快递" /></Form.Item>
        <Form.Item name="tracking_no" label="物流运单号" rules={[{ required: true }]}><Input autoComplete="off" /></Form.Item>
      </Form> : <Space direction="vertical" style={{ width: '100%' }}>
        <Typography.Text>{preview.shop} · {preview.order_no} · {preview.carrier} · {preview.tracking_no}</Typography.Text>
        <Table rowKey="id" size="small" pagination={false} dataSource={preview.lines} columns={[
          { title: '子订单', dataIndex: 'sub_order_no' }, { title: '商品', dataIndex: 'product_name' },
          { title: '规格', dataIndex: 'sku' }, { title: '数量', dataIndex: 'qty' }, { title: '备注', dataIndex: 'remark' }]} />
        {preview.notes && <Alert type="warning" message="主订单备注" description={preview.notes} />}
        <Checkbox checked={handed} onChange={e => setHanded(e.target.checked)}>以上全部商品已实际交运，由这一运单覆盖整单</Checkbox>
        {preview.has_notes && <Checkbox checked={notes} onChange={e => setNotes(e.target.checked)}>已核实并落实备注中的拍照等发货要求</Checkbox>}
      </Space>}
    </Modal>
  </Space>;
}
