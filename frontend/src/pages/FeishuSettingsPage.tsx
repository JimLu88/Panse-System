import {
  Alert,
  Button,
  Card,
  Form,
  Input,
  Modal,
  Popconfirm,
  Radio,
  Select,
  Space,
  Switch,
  Table,
  Tag,
  Typography,
  message,
} from 'antd';
import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  FeishuBinding,
  FeishuConflict,
  createFeishuBinding,
  deleteFeishuBinding,
  feishuStatus,
  feishuSupportedTables,
  getFeishuCredentials,
  listFeishuBindings,
  listFeishuConflicts,
  putFeishuCredentials,
  resolveFeishuConflict,
  resolveFeishuConflictFields,
  testFeishuConnection,
  triggerFeishuSync,
  updateFeishuBinding,
} from '../api/client';

export default function FeishuSettingsPage() {
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState<FeishuBinding | null>(null);
  const [form] = Form.useForm();
  const [credForm] = Form.useForm();

  const { data: cred } = useQuery({ queryKey: ['feishu-cred'], queryFn: getFeishuCredentials });
  const { data: tables } = useQuery({ queryKey: ['feishu-tables'], queryFn: feishuSupportedTables });
  const { data: bindings, isLoading } = useQuery({
    queryKey: ['feishu-bindings'],
    queryFn: listFeishuBindings,
  });
  const { data: status } = useQuery({ queryKey: ['feishu-status'], queryFn: feishuStatus });
  const { data: conflicts } = useQuery({
    queryKey: ['feishu-conflicts'],
    queryFn: listFeishuConflicts,
    refetchInterval: 30000,
  });

  const invalidateAll = () => {
    qc.invalidateQueries({ queryKey: ['feishu-bindings'] });
    qc.invalidateQueries({ queryKey: ['feishu-status'] });
    qc.invalidateQueries({ queryKey: ['feishu-conflicts'] });
  };

  const credMut = useMutation({
    mutationFn: putFeishuCredentials,
    onSuccess: () => {
      message.success('飞书凭证已保存');
      qc.invalidateQueries({ queryKey: ['feishu-cred'] });
    },
    onError: (e: any) => message.error(e?.response?.data?.detail ?? '保存失败'),
  });

  const testMut = useMutation({
    mutationFn: testFeishuConnection,
    onSuccess: (r) =>
      r.ok ? message.success('飞书连接正常') : message.error(`连接失败: ${r.error}`),
    onError: (e: any) => message.error(e?.response?.data?.detail ?? '测试失败'),
  });

  const createMut = useMutation({
    mutationFn: createFeishuBinding,
    onSuccess: () => {
      message.success('绑定已创建');
      setOpen(false);
      form.resetFields();
      invalidateAll();
    },
    onError: (e: any) => message.error(e?.response?.data?.detail ?? '创建失败'),
  });

  const updateMut = useMutation({
    mutationFn: (v: { id: number; payload: any }) => updateFeishuBinding(v.id, v.payload),
    onSuccess: () => {
      message.success('已保存');
      setEditing(null);
      invalidateAll();
    },
    onError: (e: any) => message.error(e?.response?.data?.detail ?? '保存失败'),
  });

  const deleteMut = useMutation({
    mutationFn: deleteFeishuBinding,
    onSuccess: () => {
      message.success('已删除');
      invalidateAll();
    },
  });

  const syncMut = useMutation({
    mutationFn: () => triggerFeishuSync(),
    onSuccess: (r) => {
      const total = r.results.reduce(
        (acc, x) => ({
          pushed: acc.pushed + x.pushed,
          pulled: acc.pulled + x.pulled,
          conflicts: acc.conflicts + x.conflicts,
        }),
        { pushed: 0, pulled: 0, conflicts: 0 },
      );
      message.success(`同步完成: 推 ${total.pushed} / 拉 ${total.pulled} / 冲突 ${total.conflicts}`);
      invalidateAll();
    },
    onError: (e: any) => message.error(e?.response?.data?.detail ?? '同步失败'),
  });

  const resolveMut = useMutation({
    mutationFn: (v: { id: number; keep: 'system' | 'feishu' }) =>
      resolveFeishuConflict(v.id, v.keep),
    onSuccess: () => {
      message.success('已裁决');
      invalidateAll();
    },
    onError: (e: any) => message.error(e?.response?.data?.detail ?? '裁决失败'),
  });

  // 字段级合并裁决
  const [merging, setMerging] = useState<FeishuConflict | null>(null);
  const [choices, setChoices] = useState<Record<string, 'system' | 'feishu'>>({});
  const mergeMut = useMutation({
    mutationFn: (v: { id: number; field_choices: Record<string, 'system' | 'feishu'> }) =>
      resolveFeishuConflictFields(v.id, v.field_choices),
    onSuccess: () => {
      message.success('已按字段合并裁决');
      setMerging(null);
      invalidateAll();
    },
    onError: (e: any) => message.error(e?.response?.data?.detail ?? '合并失败'),
  });

  function openMerge(c: FeishuConflict) {
    const init: Record<string, 'system' | 'feishu'> = {};
    (c.context?.diffs ?? []).forEach((d) => { init[d.field] = 'system'; });
    setChoices(init);
    setMerging(c);
  }

  const webhookUrl = `${window.location.origin}/api/feishu/webhook`;

  const tableOptions = (tables?.tables ?? []).map((t) => ({ value: t, label: t }));

  function openEdit(b: FeishuBinding) {
    setEditing(b);
    form.setFieldsValue({
      feishu_app_token: b.feishu_app_token,
      feishu_table_id: b.feishu_table_id,
      direction: b.direction,
      field_mapping: b.field_mapping,
      enabled: b.enabled,
    });
  }

  return (
    <Space direction="vertical" style={{ width: '100%' }} size="middle">
      <Typography.Title level={4} style={{ margin: 0 }}>
        飞书双向同步
      </Typography.Title>

      <Alert
        type="info"
        showIcon
        message="双向同步已启用"
        description="填好飞书应用凭证 + 表绑定 (含 field_mapping) 后, 系统每 30 分钟自动双向同步, 也可手动「立即同步」。两边都改同一条记录会产生冲突, 在下方按更新时间裁决保留哪端。"
      />

      {/* 凭证 */}
      <Card size="small" title="飞书应用凭证"
            extra={<Tag color={cred?.configured ? 'green' : 'red'}>
              {cred?.configured ? '已配置' : '未配置'}</Tag>}>
        <Form form={credForm} layout="inline"
              onFinish={(v) => credMut.mutate(v)}
              initialValues={{ app_id: cred?.app_id }}>
          <Form.Item name="app_id" label="App ID">
            <Input placeholder="cli_xxx" style={{ width: 200 }} defaultValue={cred?.app_id} />
          </Form.Item>
          <Form.Item name="app_secret" label="App Secret">
            <Input.Password placeholder={cred?.app_secret_masked || '输入以更新'} style={{ width: 200 }} />
          </Form.Item>
          <Form.Item name="verification_token" label="Verification Token"
                     tooltip="事件回调来源校验, 飞书「事件订阅」页提供">
            <Input.Password placeholder={cred?.verification_token_set ? '已设置, 输入以更新' : '可选'} style={{ width: 200 }} />
          </Form.Item>
          <Form.Item name="encrypt_key" label="Encrypt Key"
                     tooltip="若飞书开启了加密推送则需填; 留空表示明文推送">
            <Input.Password placeholder={cred?.encrypt_key_set ? '已设置, 输入以更新' : '可选'} style={{ width: 200 }} />
          </Form.Item>
          <Form.Item>
            <Space>
              <Button type="primary" htmlType="submit" loading={credMut.isPending}>保存</Button>
              <Button onClick={() => testMut.mutate()} loading={testMut.isPending}>测试连接</Button>
            </Space>
          </Form.Item>
        </Form>
        <Alert type="info" showIcon style={{ marginTop: 12 }}
          message="近实时同步 (事件回调)"
          description={
            <span>
              在飞书开放平台「事件订阅」填回调地址 <Typography.Text code copyable>{webhookUrl}</Typography.Text>,
              订阅「多维表格记录变更」事件, 飞书改完即触发同步 (无需等 30 分钟轮询)。需本服务有公网地址 + 网络放行。
            </span>
          } />
      </Card>

      {/* 冲突裁决 */}
      {(conflicts?.length ?? 0) > 0 && (
        <Card size="small" title={<Space>冲突待裁决<Tag color="volcano">{conflicts!.length}</Tag></Space>}>
          <Alert type="warning" showIcon style={{ marginBottom: 8 }}
                 message="网页端和飞书端都改了同一条记录, 请选择保留哪一端 (可参考更新时间)。" />
          <Table<FeishuConflict>
            rowKey="id" size="small" pagination={false}
            dataSource={conflicts}
            columns={[
              { title: '系统表', dataIndex: 'system_table', width: 120 },
              { title: '记录', dataIndex: 'source_pk', width: 140 },
              {
                title: '差异', render: (_: any, c: FeishuConflict) => (
                  <Space direction="vertical" size={0}>
                    {(c.context?.diffs ?? []).slice(0, 6).map((d, i) => (
                      <span key={i} style={{ fontSize: 12 }}>
                        <b>{d.field}</b>: <span style={{ color: '#999' }}>{String(d.system)}</span>
                        {' → '}<span>{String(d.feishu)}</span>
                      </span>
                    ))}
                  </Space>
                ),
              },
              {
                title: '更新时间', width: 200, render: (_: any, c: FeishuConflict) => (
                  <Space direction="vertical" size={0} style={{ fontSize: 11 }}>
                    <span>系统: {c.context?.system_updated_at || '-'}</span>
                    <span>飞书: {c.context?.feishu_updated_at ? String(c.context.feishu_updated_at) : '-'}</span>
                  </Space>
                ),
              },
              {
                title: '裁决', width: 280, render: (_: any, c: FeishuConflict) => (
                  <Space wrap>
                    <Button size="small"
                            onClick={() => resolveMut.mutate({ id: c.id, keep: 'system' })}>
                      保留网页端
                    </Button>
                    <Button size="small" type="primary" ghost
                            onClick={() => resolveMut.mutate({ id: c.id, keep: 'feishu' })}>
                      保留飞书端
                    </Button>
                    <Button size="small" type="link" onClick={() => openMerge(c)}>逐字段</Button>
                  </Space>
                ),
              },
            ]}
          />
        </Card>
      )}

      {/* 字段级合并裁决 */}
      <Modal
        title={`逐字段裁决 — ${merging?.system_table} / ${merging?.source_pk}`}
        open={!!merging}
        onCancel={() => setMerging(null)}
        onOk={() => merging && mergeMut.mutate({ id: merging.id, field_choices: choices })}
        confirmLoading={mergeMut.isPending}
        okText="按所选合并"
        destroyOnClose
        width={560}
      >
        <Alert type="info" showIcon style={{ marginBottom: 12 }}
          message="逐个字段选择保留哪一端, 合并后两端写成一致 (主键不可改)。" />
        <Space direction="vertical" style={{ width: '100%' }} size="middle">
          {(merging?.context?.diffs ?? []).map((d) => (
            <div key={d.field}>
              <Typography.Text strong>{d.field}</Typography.Text>
              <Radio.Group
                style={{ display: 'block', marginTop: 4 }}
                value={choices[d.field] ?? 'system'}
                onChange={(e) => setChoices((p) => ({ ...p, [d.field]: e.target.value }))}
              >
                <Radio value="system">网页端: <span style={{ color: '#999' }}>{String(d.system)}</span></Radio>
                <Radio value="feishu">飞书端: <span>{String(d.feishu)}</span></Radio>
              </Radio.Group>
            </div>
          ))}
        </Space>
      </Modal>

      {/* 绑定 */}
      <Card size="small" title="表绑定"
            extra={
              <Space>
                <Button onClick={() => syncMut.mutate()} loading={syncMut.isPending}>立即同步</Button>
                <Button type="primary" onClick={() => { setEditing(null); form.resetFields(); setOpen(true); }}>
                  新增绑定
                </Button>
              </Space>
            }>
        <Table<FeishuBinding>
          rowKey="id"
          loading={isLoading}
          dataSource={bindings}
          size="middle"
          pagination={false}
          columns={[
            { title: '系统表', dataIndex: 'system_table', width: 120 },
            { title: '飞书 App Token', dataIndex: 'feishu_app_token', ellipsis: true },
            { title: '飞书 Table ID', dataIndex: 'feishu_table_id', width: 180 },
            {
              title: '方向', dataIndex: 'direction', width: 130,
              render: (v: string) => ({
                in: <Tag color="green">仅入</Tag>,
                out: <Tag color="blue">仅出</Tag>,
                bidirectional: <Tag color="purple">双向</Tag>,
              }[v] || v),
            },
            {
              title: '启用', dataIndex: 'enabled', width: 80,
              render: (v: boolean, b: FeishuBinding) => (
                <Switch checked={v}
                        onChange={(checked) => updateMut.mutate({ id: b.id, payload: { enabled: checked } })} />
              ),
            },
            {
              title: '操作', width: 140, render: (_: any, b: FeishuBinding) => (
                <Space>
                  <Button size="small" onClick={() => openEdit(b)}>编辑</Button>
                  <Popconfirm title="删除该绑定?" onConfirm={() => deleteMut.mutate(b.id)}>
                    <Button size="small" danger>删除</Button>
                  </Popconfirm>
                </Space>
              ),
            },
          ]}
        />
      </Card>

      {/* 同步状态 */}
      <Card size="small" title="同步状态">
        <Table
          rowKey={(r) => `${r.system_table}-${r.feishu_table_id}`}
          dataSource={status}
          size="small"
          pagination={false}
          columns={[
            { title: '系统表', dataIndex: 'system_table' },
            { title: '飞书 Table ID', dataIndex: 'feishu_table_id' },
            { title: '方向', dataIndex: 'direction' },
            { title: '已映射行数', dataIndex: 'mapped_rows' },
            {
              title: '启用', dataIndex: 'enabled',
              render: (v: boolean) => (v ? <Tag color="green">on</Tag> : <Tag>off</Tag>),
            },
          ]}
        />
      </Card>

      <Modal
        title={editing ? `编辑绑定 — ${editing.system_table}` : '新增飞书绑定'}
        open={open || !!editing}
        onCancel={() => { setOpen(false); setEditing(null); }}
        onOk={() => form.submit()}
        confirmLoading={createMut.isPending || updateMut.isPending}
        destroyOnClose
        width={560}
      >
        <Form
          form={form}
          layout="vertical"
          onFinish={(v) =>
            editing
              ? updateMut.mutate({ id: editing.id, payload: v })
              : createMut.mutate(v)}
          initialValues={{ direction: 'bidirectional', enabled: false }}
        >
          {!editing && (
            <Form.Item name="system_table" label="系统表" rules={[{ required: true }]}>
              <Select options={tableOptions} showSearch />
            </Form.Item>
          )}
          <Form.Item name="feishu_app_token" label="飞书 App Token (多维表 base token)"
                     rules={[{ required: true }]}>
            <Input placeholder="bascnxxxx" />
          </Form.Item>
          <Form.Item name="feishu_table_id" label="飞书 Table ID" rules={[{ required: true }]}>
            <Input placeholder="tblxxxx" />
          </Form.Item>
          <Form.Item name="direction" label="同步方向">
            <Select
              options={[
                { value: 'in', label: '仅入 (飞书 → 系统)' },
                { value: 'out', label: '仅出 (系统 → 飞书)' },
                { value: 'bidirectional', label: '双向' },
              ]}
            />
          </Form.Item>
          <Form.Item name="field_mapping"
                     label="字段映射 (JSON: 系统字段 → 飞书列名, 必须含主键字段)"
                     rules={[{ required: true }]}>
            <Input.TextArea rows={5}
                            placeholder={'{"code": "编码", "name": "名称"}'} />
          </Form.Item>
          <Form.Item name="enabled" label="启用" valuePropName="checked">
            <Switch />
          </Form.Item>
        </Form>
      </Modal>
    </Space>
  );
}
