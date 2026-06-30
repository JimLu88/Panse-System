import { useEffect, useMemo, useState } from 'react';
import {
  Segmented, Button, Card, Tag, Progress, Dropdown, Modal, Form, Input, Select,
  DatePicker, InputNumber, message, Space, Typography, Grid, Empty, Spin,
} from 'antd';
import { PlusOutlined, DownOutlined } from '@ant-design/icons';
import type { MenuProps } from 'antd';
import { useNavigate } from 'react-router-dom';
import dayjs from 'dayjs';
import PresetTable from '../components/PresetTable';
import {
  listNpdStages, listNpdProjects, createNpdProject, moveNpdProject,
  type NpdStage, type NpdProject,
} from '../api/client';

const GROUP_LABEL: Record<string, string> = {
  plan: '立项', design: '设计', sourcing: '寻源', prototype: '打样',
  production: '量产', launch: '上架', review: '复盘',
};
const PRIORITY = [
  { value: 'high', label: '高' }, { value: 'mid', label: '中' }, { value: 'low', label: '低' },
];

function priorityLabel(p: string) { return p === 'high' ? '高' : p === 'low' ? '低' : '中'; }

export default function NpdPage() {
  const [view, setView] = useState<'board' | 'list'>('board');
  const [stages, setStages] = useState<NpdStage[]>([]);
  const [projects, setProjects] = useState<NpdProject[]>([]);
  const [loading, setLoading] = useState(false);
  const [modalOpen, setModalOpen] = useState(false);
  const [saving, setSaving] = useState(false);
  const [form] = Form.useForm();
  const nav = useNavigate();
  const screens = Grid.useBreakpoint();
  const isMobile = screens.md === false;

  const load = async () => {
    setLoading(true);
    try {
      const [st, pr] = await Promise.all([listNpdStages(), listNpdProjects()]);
      setStages(st);
      setProjects(pr);
    } catch (e: any) {
      message.error(e?.response?.data?.detail ?? '加载失败');
    } finally {
      setLoading(false);
    }
  };
  useEffect(() => { load(); }, []);

  // 有序分组(按阶段 sequence 出现顺序)
  const groups = useMemo(() => {
    const seen: string[] = [];
    for (const s of stages) if (!seen.includes(s.group)) seen.push(s.group);
    return seen;
  }, [stages]);

  const moveMenu = (p: NpdProject): MenuProps => ({
    items: groups.map((g) => ({
      key: g,
      type: 'group',
      label: GROUP_LABEL[g] || g,
      children: stages.filter((s) => s.group === g).map((s) => ({
        key: String(s.id),
        label: `${s.code} ${s.name}`,
        disabled: s.id === p.current_stage_id,
      })),
    })),
    onClick: async ({ key }) => {
      try {
        const np = await moveNpdProject(p.id, Number(key));
        setProjects((prev) => prev.map((x) => (x.id === np.id ? np : x)));
        message.success('已移动');
      } catch (e: any) {
        message.error(e?.response?.data?.detail ?? '移动失败');
      }
    },
  });

  const deadlineTag = (p: NpdProject) => {
    if (!p.deadline) return null;
    const d = dayjs(p.deadline);
    const days = d.diff(dayjs(), 'day');
    const color = days < 0 ? 'red' : days <= 2 ? 'volcano' : days <= 5 ? 'gold' : 'default';
    const suffix = days < 0 ? `(逾期${-days}天)` : days <= 5 ? `(剩${days}天)` : '';
    return <Tag color={color}>截止 {d.format('MM-DD')}{suffix}</Tag>;
  };

  const card = (p: NpdProject) => (
    <Card key={p.id} size="small" style={{ marginBottom: 8 }}>
      <Space direction="vertical" size={4} style={{ width: '100%' }}>
        <Space style={{ justifyContent: 'space-between', width: '100%' }} wrap>
          <Typography.Link strong onClick={() => nav(`/npd/${p.id}`)}>{p.name}</Typography.Link>
          <Tag>{p.code}</Tag>
        </Space>
        <Space wrap size={4}>
          {p.current_stage_name && <Tag color="blue">{p.current_stage_name}</Tag>}
          <Tag color={p.priority === 'high' ? 'red' : p.priority === 'low' ? 'default' : 'geekblue'}>
            {priorityLabel(p.priority)}优先
          </Tag>
          {deadlineTag(p)}
          {p.state === 'done' && <Tag color="green">已完成</Tag>}
        </Space>
        <Progress percent={p.percent_done} size="small" />
        <Space style={{ justifyContent: 'space-between', width: '100%' }}>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            {(p.category || '-') + ' · ' + (p.owner || '-')}
          </Typography.Text>
          <Space size={4}>
            <Button size="small" onClick={() => nav(`/npd/${p.id}`)}>详情</Button>
            <Dropdown menu={moveMenu(p)} trigger={['click']}>
              <Button size="small">移到 <DownOutlined /></Button>
            </Dropdown>
          </Space>
        </Space>
      </Space>
    </Card>
  );

  const board = (
    <div style={{ display: 'flex', gap: 12, overflowX: 'auto', paddingBottom: 8, alignItems: 'flex-start' }}>
      {groups.map((g) => {
        const items = projects.filter((p) => (p.current_stage_group || '') === g);
        return (
          <div key={g} style={{ minWidth: 260, flex: '0 0 260px', background: 'rgba(127,127,127,0.06)', borderRadius: 8, padding: 8 }}>
            <Typography.Text strong>{GROUP_LABEL[g] || g} <Tag>{items.length}</Tag></Typography.Text>
            <div style={{ marginTop: 8 }}>
              {items.length ? items.map(card) : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="无" />}
            </div>
          </div>
        );
      })}
    </div>
  );

  const columns = [
    { title: '编号', dataIndex: 'code', width: 100 },
    { title: '名称', dataIndex: 'name' },
    { title: '品类', dataIndex: 'category', width: 90 },
    {
      title: '当前阶段', dataIndex: 'current_stage_name', width: 150,
      render: (v: string) => (v ? <Tag color="blue">{v}</Tag> : '-'),
    },
    {
      title: '进度', dataIndex: 'percent_done', width: 120,
      render: (v: number) => <Progress percent={v} size="small" />,
    },
    { title: '负责人', dataIndex: 'owner', width: 90 },
    { title: '优先级', dataIndex: 'priority', width: 80, render: (v: string) => priorityLabel(v) },
    { title: '目标价', dataIndex: 'target_price', width: 90 },
    {
      title: '目标毛利', dataIndex: 'target_margin_rate', width: 90,
      render: (v: string | null) => (v != null ? `${(Number(v) * 100).toFixed(0)}%` : '-'),
    },
    { title: '截止', dataIndex: 'deadline', width: 130, render: (_: unknown, r: NpdProject) => deadlineTag(r) },
    {
      title: '状态', dataIndex: 'state', width: 80,
      render: (v: string) => (v === 'done' ? <Tag color="green">完成</Tag>
        : v === 'rework' ? <Tag color="orange">返工</Tag> : <Tag>进行</Tag>),
    },
    {
      title: '操作', key: 'act', width: 70,
      render: (_: unknown, r: NpdProject) => (
        <Button type="link" size="small" onClick={() => nav(`/npd/${r.id}`)}>详情</Button>
      ),
    },
  ];

  const submit = async () => {
    let v: any;
    try {
      v = await form.validateFields();
    } catch {
      return; // 校验未过
    }
    setSaving(true);
    try {
      const payload = {
        ...v,
        target_launch_date: v.target_launch_date ? dayjs(v.target_launch_date).format('YYYY-MM-DD') : null,
        target_margin_rate: v.target_margin_rate != null ? Number(v.target_margin_rate) / 100 : null,
      };
      await createNpdProject(payload);
      message.success('已立项');
      setModalOpen(false);
      form.resetFields();
      load();
    } catch (e: any) {
      message.error(e?.response?.data?.detail ?? '立项失败');
    } finally {
      setSaving(false);
    }
  };

  return (
    <div style={{ padding: isMobile ? 12 : 16 }}>
      <Space style={{ justifyContent: 'space-between', width: '100%', marginBottom: 12 }} wrap>
        <Space wrap>
          <Typography.Title level={4} style={{ margin: 0 }}>新品开发</Typography.Title>
          <Segmented
            value={view}
            onChange={(val) => setView(val as 'board' | 'list')}
            options={[{ label: '看板', value: 'board' }, { label: '清单', value: 'list' }]}
          />
        </Space>
        <Button type="primary" icon={<PlusOutlined />} onClick={() => setModalOpen(true)}>立项</Button>
      </Space>

      {loading ? <Spin /> : view === 'board' ? board : (
        <PresetTable
          tableKey="npd"
          rowKey="id"
          size="small"
          columns={columns as any}
          dataSource={projects}
          pagination={{ pageSize: 50 }}
          scroll={{ x: 1100 }}
        />
      )}

      <Modal
        title="新品立项"
        open={modalOpen}
        onOk={submit}
        confirmLoading={saving}
        onCancel={() => setModalOpen(false)}
        destroyOnClose
      >
        <Form form={form} layout="vertical" initialValues={{ priority: 'mid' }}>
          <Form.Item name="name" label="产品名称" rules={[{ required: true, message: '必填' }]}>
            <Input placeholder="如 岩板餐桌-樱桃木" />
          </Form.Item>
          <Space style={{ width: '100%' }} size={12} wrap>
            <Form.Item name="category" label="品类"><Input placeholder="餐桌/餐边柜…" /></Form.Item>
            <Form.Item name="product_line" label="产品线(泳道)"><Input /></Form.Item>
            <Form.Item name="brand" label="品牌"><Input /></Form.Item>
          </Space>
          <Space style={{ width: '100%' }} size={12} wrap>
            <Form.Item name="priority" label="优先级"><Select style={{ width: 120 }} options={PRIORITY} /></Form.Item>
            <Form.Item name="owner" label="负责人"><Input /></Form.Item>
            <Form.Item name="target_launch_date" label="目标上市日"><DatePicker /></Form.Item>
          </Space>
          <Space style={{ width: '100%' }} size={12} wrap>
            <Form.Item name="target_price" label="目标售价(成本门基线)">
              <InputNumber min={0} style={{ width: 170 }} addonAfter="元" />
            </Form.Item>
            <Form.Item name="target_margin_rate" label="目标毛利率">
              <InputNumber min={0} max={100} style={{ width: 140 }} addonAfter="%" />
            </Form.Item>
          </Space>
          <Form.Item name="remark" label="备注 / 机会陈述"><Input.TextArea rows={2} /></Form.Item>
        </Form>
      </Modal>
    </div>
  );
}
