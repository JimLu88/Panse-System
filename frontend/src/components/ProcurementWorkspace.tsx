import { useEffect, useMemo, useState } from 'react';
import {
  Alert,
  Button,
  Card,
  Checkbox,
  Col,
  Descriptions,
  Drawer,
  Form,
  Input,
  InputNumber,
  Modal,
  Popconfirm,
  Progress,
  Radio,
  Row,
  Segmented,
  Slider,
  Space,
  Statistic,
  Switch,
  Table,
  Tag,
  Tooltip,
  Typography,
  message,
} from 'antd';
import {
  CopyOutlined,
  ExperimentOutlined,
  PlusOutlined,
  RobotOutlined,
  SendOutlined,
} from '@ant-design/icons';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  applyProcurementWinner,
  createProcurementTask,
  generateProcurementScripts,
  getProcurementAgentStatus,
  getProcurementExperiment,
  listProcurementInquiries,
  listProcurementTasks,
  listProcurementDueActions,
  markProcurementSent,
  patchProcurementInquiry,
  prepareProcurementQueue,
  recordProcurementReply,
  reviewProcurementMessage,
  reviewProcurementScripts,
  type ProcurementChannel,
  type ProcurementInquiry,
  type ProcurementReplyInput,
  type ProcurementTask,
  type ProcurementTaskInput,
} from '../api/client';

const { Paragraph, Text, Title } = Typography;
const { TextArea } = Input;

const categoryLabel: Record<string, string> = {
  daily: '日常配件',
  photo: '拍摄搭配',
  production: '生产材料',
};
const channelLabel: Record<string, string> = {
  taobao: '淘宝',
  '1688': '1688',
  xiaohongshu: '小红书',
};
const statusMeta: Record<string, { label: string; color: string }> = {
  draft: { label: '草稿', color: 'default' },
  ready: { label: '待执行', color: 'blue' },
  running: { label: '询价中', color: 'processing' },
  needs_review: { label: '需人工处理', color: 'orange' },
  completed: { label: '已完成', color: 'green' },
  cancelled: { label: '已取消', color: 'default' },
  waiting_winner: { label: '等待优胜话术', color: 'purple' },
  waiting_reply: { label: '等待回复', color: 'processing' },
  followup_ready: { label: '待追问', color: 'cyan' },
  replied: { label: '已回复', color: 'blue' },
  needs_manual: { label: '转人工', color: 'orange' },
  no_reply: { label: '未回复', color: 'default' },
  failed: { label: '执行失败', color: 'red' },
};

function StatusTag({ status }: { status: string }) {
  const meta = statusMeta[status] || { label: status, color: 'default' };
  return <Tag color={meta.color}>{meta.label}</Tag>;
}

function errorText(error: any) {
  return error?.response?.data?.detail || error?.message || String(error);
}

const initialTaskValues: ProcurementTaskInput & {
  channel_daily_limits: Record<string, number>;
  followup_intervals_hours: Record<string, number>;
} = {
  title: '',
  category: 'production',
  item_name: '',
  specification: '',
  quantity: 1,
  unit: '件',
  requirements: '',
  execution_mode: 'assisted',
  taobao_client_mode: 'desktop',
  channels: ['taobao', '1688'],
  planned_merchant_count: 10,
  max_followup_rounds: 3,
  ab_test_enabled: true,
  ab_test_sample_size: 6,
  channel_daily_limits: { taobao: 10, '1688': 5, xiaohongshu: 3 },
  followup_intervals_hours: { taobao: 12, '1688': 12, xiaohongshu: 24 },
  generate_scripts: true,
};

export default function ProcurementWorkspace() {
  const qc = useQueryClient();
  const [createOpen, setCreateOpen] = useState(false);
  const [selectedTask, setSelectedTask] = useState<ProcurementTask | null>(null);
  const [editingInquiry, setEditingInquiry] = useState<ProcurementInquiry | null>(null);
  const [replyInquiry, setReplyInquiry] = useState<ProcurementInquiry | null>(null);
  const [messageReviewInquiry, setMessageReviewInquiry] = useState<ProcurementInquiry | null>(null);
  const [messageDraft, setMessageDraft] = useState('');
  const [messageReviewConfirmed, setMessageReviewConfirmed] = useState(false);
  const [scriptA, setScriptA] = useState('');
  const [scriptB, setScriptB] = useState('');
  const [createForm] = Form.useForm();
  const [merchantForm] = Form.useForm();
  const [replyForm] = Form.useForm();

  const plannedCount = Form.useWatch('planned_merchant_count', createForm) || 10;
  const abEnabled = Form.useWatch('ab_test_enabled', createForm) ?? true;
  const selectedChannels = (Form.useWatch('channels', createForm) || []) as ProcurementChannel[];

  const { data: tasks = [], isLoading } = useQuery({
    queryKey: ['procurement-tasks'],
    queryFn: listProcurementTasks,
  });
  const { data: agentRuntime } = useQuery({
    queryKey: ['procurement-agent-status'],
    queryFn: getProcurementAgentStatus,
    refetchInterval: 30_000,
  });
  const taskId = selectedTask?.id;
  const { data: inquiries = [], isLoading: inquiriesLoading } = useQuery({
    queryKey: ['procurement-inquiries', taskId],
    queryFn: () => listProcurementInquiries(taskId!),
    enabled: Boolean(taskId),
  });
  const { data: experiment } = useQuery({
    queryKey: ['procurement-experiment', taskId],
    queryFn: () => getProcurementExperiment(taskId!),
    enabled: Boolean(taskId && selectedTask?.ab_test_enabled),
  });
  const { data: dueActions = [] } = useQuery({
    queryKey: ['procurement-due-actions', taskId],
    queryFn: () => listProcurementDueActions(taskId!),
    enabled: Boolean(taskId),
    refetchInterval: 60_000,
  });

  useEffect(() => {
    setScriptA(selectedTask?.script_a || '');
    setScriptB(selectedTask?.script_b || '');
  }, [selectedTask?.id, selectedTask?.script_a, selectedTask?.script_b]);

  const refreshTask = async (task: ProcurementTask) => {
    await qc.invalidateQueries({ queryKey: ['procurement-tasks'] });
    setSelectedTask(task);
  };

  const createMut = useMutation({
    mutationFn: createProcurementTask,
    onSuccess: async (task) => {
      message.success('采购询价任务已建立，话术建议已生成');
      setCreateOpen(false);
      createForm.resetFields();
      await refreshTask(task);
    },
    onError: (error: any) => message.error(`建立失败：${errorText(error)}`),
  });
  const scriptMut = useMutation({
    mutationFn: () => generateProcurementScripts(taskId!),
    onSuccess: async (result) => {
      setScriptA(result.script_a);
      setScriptB(result.script_b);
      message[result.ai_used ? 'success' : 'warning'](result.note);
      await qc.invalidateQueries({ queryKey: ['procurement-tasks'] });
    },
    onError: (error: any) => message.error(`生成话术失败：${errorText(error)}`),
  });
  const saveScriptsMut = useMutation({
    mutationFn: () => reviewProcurementScripts(taskId!, { script_a: scriptA, script_b: scriptB }),
    onSuccess: async (task) => {
      message.success('人工改稿已保存并确认，可以生成询价队列');
      await refreshTask(task);
    },
    onError: (error: any) => message.error(`保存失败：${errorText(error)}`),
  });
  const queueMut = useMutation({
    mutationFn: () => prepareProcurementQueue(taskId!),
    onSuccess: async (rows) => {
      message.success(`已准备 ${rows.length} 个商家询价位`);
      await Promise.all([
        qc.invalidateQueries({ queryKey: ['procurement-inquiries', taskId] }),
        qc.invalidateQueries({ queryKey: ['procurement-tasks'] }),
        qc.invalidateQueries({ queryKey: ['procurement-experiment', taskId] }),
        qc.invalidateQueries({ queryKey: ['procurement-due-actions', taskId] }),
      ]);
    },
    onError: (error: any) => message.error(`生成队列失败：${errorText(error)}`),
  });
  const winnerMut = useMutation({
    mutationFn: (variant?: 'A' | 'B') => applyProcurementWinner(taskId!, variant),
    onSuccess: async (result: any) => {
      message.success(`已采用 ${result.winner} 组话术，释放 ${result.activated} 家后续询价`);
      await Promise.all([
        qc.invalidateQueries({ queryKey: ['procurement-inquiries', taskId] }),
        qc.invalidateQueries({ queryKey: ['procurement-tasks'] }),
        qc.invalidateQueries({ queryKey: ['procurement-experiment', taskId] }),
        qc.invalidateQueries({ queryKey: ['procurement-due-actions', taskId] }),
      ]);
    },
    onError: (error: any) => message.warning(errorText(error)),
  });
  const patchInquiryMut = useMutation({
    mutationFn: (values: any) => patchProcurementInquiry(editingInquiry!.id, values),
    onSuccess: async () => {
      message.success('商家信息已保存');
      setEditingInquiry(null);
      merchantForm.resetFields();
      await qc.invalidateQueries({ queryKey: ['procurement-inquiries', taskId] });
    },
    onError: (error: any) => message.error(`保存失败：${errorText(error)}`),
  });
  const sentMut = useMutation({
    mutationFn: ({ row, content }: { row: ProcurementInquiry; content: string }) =>
      markProcurementSent(row.id, content),
    onSuccess: async () => {
      message.success('已记录为平台发送成功');
      await Promise.all([
        qc.invalidateQueries({ queryKey: ['procurement-inquiries', taskId] }),
        qc.invalidateQueries({ queryKey: ['procurement-tasks'] }),
        qc.invalidateQueries({ queryKey: ['procurement-experiment', taskId] }),
        qc.invalidateQueries({ queryKey: ['procurement-due-actions', taskId] }),
      ]);
    },
    onError: (error: any) => message.error(`记录失败：${errorText(error)}`),
  });
  const reviewMessageMut = useMutation({
    mutationFn: ({ row, content }: { row: ProcurementInquiry; content: string }) =>
      reviewProcurementMessage(row.id, content),
    onSuccess: async (result) => {
      try {
        await navigator.clipboard.writeText(result.approved_message);
        message.success('人工确认稿已保存并复制');
      } catch {
        message.warning('人工确认稿已保存；浏览器未允许自动复制，请从编辑框手动复制');
      }
      setMessageReviewInquiry(null);
      setMessageDraft('');
      setMessageReviewConfirmed(false);
      await Promise.all([
        qc.invalidateQueries({ queryKey: ['procurement-inquiries', taskId] }),
        qc.invalidateQueries({ queryKey: ['procurement-due-actions', taskId] }),
      ]);
    },
    onError: (error: any) => message.error(`文案确认失败：${errorText(error)}`),
  });
  const replyMut = useMutation({
    mutationFn: (values: ProcurementReplyInput) =>
      recordProcurementReply(replyInquiry!.id, values),
    onSuccess: async () => {
      message.success('商家反馈已归档');
      setReplyInquiry(null);
      replyForm.resetFields();
      await Promise.all([
        qc.invalidateQueries({ queryKey: ['procurement-inquiries', taskId] }),
        qc.invalidateQueries({ queryKey: ['procurement-tasks'] }),
        qc.invalidateQueries({ queryKey: ['procurement-experiment', taskId] }),
      ]);
    },
    onError: (error: any) => message.error(`归档失败：${errorText(error)}`),
  });

  const currentTask = useMemo(
    () => tasks.find((task) => task.id === taskId) || selectedTask,
    [tasks, taskId, selectedTask],
  );

  const openMessageEditor = (row: ProcurementInquiry) => {
    const due = dueActions.find((action) => action.inquiry_id === row.id);
    const content = due?.approved_message || due?.suggested_message || '';
    if (!content) {
      message.warning('该商家还没有可用话术');
      return;
    }
    setMessageReviewInquiry(row);
    setMessageDraft(content);
    setMessageReviewConfirmed(false);
  };

  const normalizeMessage = (value?: string | null) =>
    (value || '').trim().replace(/\s+/g, ' ');

  const scriptsReadyForReview = Boolean(
    currentTask
    && normalizeMessage(scriptA)
    && (!currentTask.ab_test_enabled || normalizeMessage(scriptB))
    && (
      !currentTask.script_a_ai_draft
      || normalizeMessage(scriptA) !== normalizeMessage(currentTask.script_a_ai_draft)
    )
    && (
      !currentTask.ab_test_enabled
      || !currentTask.script_b_ai_draft
      || normalizeMessage(scriptB) !== normalizeMessage(currentTask.script_b_ai_draft)
    ),
  );
  const messageReviewAction = messageReviewInquiry
    ? dueActions.find((action) => action.inquiry_id === messageReviewInquiry.id)
    : undefined;
  const messageDraftChanged = Boolean(
    messageReviewAction
    && normalizeMessage(messageDraft) !== normalizeMessage(messageReviewAction.suggested_message),
  );
  const markReviewedMessageSent = (row: ProcurementInquiry) => {
    const due = dueActions.find((action) => action.inquiry_id === row.id);
    if (!due?.approved_message || due.review_required) {
      message.warning('请先打开文案、人工检查并确认');
      return;
    }
    sentMut.mutate({ row, content: due.approved_message });
  };

  const taskColumns = [
    {
      title: '任务',
      key: 'task',
      render: (_: unknown, row: ProcurementTask) => (
        <Space direction="vertical" size={0}>
          <Text strong>{row.title}</Text>
          <Text type="secondary">{row.task_no}</Text>
        </Space>
      ),
    },
    {
      title: '采购内容',
      key: 'item',
      render: (_: unknown, row: ProcurementTask) => (
        <Space direction="vertical" size={0}>
          <Text>{row.item_name}</Text>
          <Text type="secondary">{categoryLabel[row.category]} · {Number(row.quantity)}{row.unit}</Text>
        </Space>
      ),
    },
    {
      title: '工作方式',
      key: 'mode',
      render: (_: unknown, row: ProcurementTask) => (
        <Space direction="vertical" size={2}>
          <Tag color={row.execution_mode === 'agent' ? 'purple' : 'blue'}>
            {row.execution_mode === 'agent' ? '代理队列' : '人工辅助'}
          </Tag>
          <Space size={4}>
            {row.channels.map((channel) => <Tag key={channel}>{channelLabel[channel]}</Tag>)}
          </Space>
        </Space>
      ),
    },
    {
      title: '进度',
      key: 'progress',
      width: 170,
      render: (_: unknown, row: ProcurementTask) => {
        const total = row.counts.total || row.planned_merchant_count;
        const percent = total ? Math.round((row.counts.completed / total) * 100) : 0;
        return (
          <Space direction="vertical" size={0} style={{ width: '100%' }}>
            <Progress percent={percent} size="small" />
            <Text type="secondary">
              已发 {row.counts.sent} · 已回 {row.counts.replied} · 转人工 {row.counts.needs_manual}
            </Text>
          </Space>
        );
      },
    },
    {
      title: '测试设置',
      key: 'test',
      render: (_: unknown, row: ProcurementTask) => (
        <Text>
          {row.ab_test_enabled ? `A/B 首测 ${row.ab_test_sample_size} 家` : '不做 A/B'}
          <br />
          <Text type="secondary">最多追问 {row.max_followup_rounds} 轮</Text>
        </Text>
      ),
    },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      render: (status: string) => <StatusTag status={status} />,
    },
    {
      title: '操作',
      key: 'action',
      render: (_: unknown, row: ProcurementTask) => (
        <Button type="link" onClick={() => setSelectedTask(row)}>打开工作台</Button>
      ),
    },
  ];

  const inquiryColumns = [
    { title: '#', dataIndex: 'slot_no', key: 'slot_no', width: 48 },
    {
      title: '渠道',
      dataIndex: 'channel',
      key: 'channel',
      width: 86,
      render: (channel: string) => <Tag>{channelLabel[channel]}</Tag>,
    },
    {
      title: '商家',
      key: 'merchant',
      width: 155,
      render: (_: unknown, row: ProcurementInquiry) => (
        <Space direction="vertical" size={0}>
          <Text>{row.merchant_name || '待填写'}</Text>
          {row.merchant_url && (
            <a href={row.merchant_url} target="_blank" rel="noreferrer">打开店铺</a>
          )}
        </Space>
      ),
    },
    {
      title: '话术',
      dataIndex: 'message_variant',
      key: 'variant',
      width: 100,
      render: (variant: string) => (
        variant === 'winner_pending'
          ? <Tag color="purple">等待胜出</Tag>
          : <Tag color={variant === 'A' ? 'blue' : 'magenta'}>{variant} 组</Tag>
      ),
    },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      width: 110,
      render: (status: string) => <StatusTag status={status} />,
    },
    {
      title: '反馈/报价',
      key: 'feedback',
      render: (_: unknown, row: ProcurementInquiry) => (
        <Space direction="vertical" size={0}>
          {row.leased_by && row.lease_until && new Date(row.lease_until).getTime() > Date.now() && (
            <Tag color="processing">执行器处理中</Tag>
          )}
          {row.requires_wechat && <Tag color="orange">要求加微信：{row.wechat_contact || '未识别账号'}</Tag>}
          {row.normalized_unit_price != null && (
            <Text>标准单价：¥{Number(row.normalized_unit_price).toLocaleString()}</Text>
          )}
          {row.last_inbound_message && (
            <Tooltip title={row.last_inbound_message}>
              <Text ellipsis style={{ maxWidth: 230 }}>{row.last_inbound_message}</Text>
            </Tooltip>
          )}
          {!row.last_inbound_message && <Text type="secondary">暂无回复</Text>}
          {row.last_execution_error && (
            <Tooltip title={row.last_execution_error}>
              <Text type="danger" ellipsis style={{ maxWidth: 230 }}>
                执行异常（第 {row.execution_attempts} 次）
              </Text>
            </Tooltip>
          )}
        </Space>
      ),
    },
    {
      title: '轮次',
      key: 'round',
      width: 80,
      render: (_: unknown, row: ProcurementInquiry) => (
        <Text>{row.followup_round}/{currentTask?.max_followup_rounds ?? 0}</Text>
      ),
    },
    {
      title: '操作',
      key: 'action',
      width: 280,
      render: (_: unknown, row: ProcurementInquiry) => (
        <Space size={4} wrap>
          <Button size="small" onClick={() => {
            setEditingInquiry(row);
            merchantForm.setFieldsValue({
              channel: row.channel,
              merchant_name: row.merchant_name,
              merchant_url: row.merchant_url,
              product_url: row.product_url,
            });
          }}>
            填商家
          </Button>
          {row.status === 'ready' && (
            <>
              <Button size="small" icon={<CopyOutlined />} onClick={() => openMessageEditor(row)}>
                修改并复制
              </Button>
              <Popconfirm
                title="只记录发送结果"
                description="请确认平台实际发送内容与 ERP 最后确认稿完全一致；此按钮本身不会给商家发消息。"
                onConfirm={() => markReviewedMessageSent(row)}
                okText="确认已发送"
                cancelText="取消"
              >
                <Button size="small" type="primary" icon={<SendOutlined />}>标记已发</Button>
              </Popconfirm>
            </>
          )}
          {['waiting_reply', 'followup_ready', 'replied', 'needs_manual'].includes(row.status) && (
            <Button size="small" onClick={() => {
              setReplyInquiry(row);
              replyForm.setFieldsValue({
                quote_complete: false,
                response_quality: 60,
              });
            }}>
              录入回复
            </Button>
          )}
          {row.status === 'followup_ready' && (
            <>
              <Button size="small" icon={<CopyOutlined />} onClick={() => openMessageEditor(row)}>
                修改并确认追问
              </Button>
              <Popconfirm
                title="确认追问已在平台发送成功？"
                description="只有人工修改并确认过的本轮追问才能登记；此按钮不会替你点击平台发送。"
                onConfirm={() => markReviewedMessageSent(row)}
                okText="已发送"
                cancelText="取消"
              >
                <Button size="small">标记追问已发</Button>
              </Popconfirm>
            </>
          )}
        </Space>
      ),
    },
  ];

  return (
    <>
      <Alert
        type="info"
        showIcon
        message="两种工作模式"
        description={
          <span>
            <b>人工辅助：</b>ERP 生成并复制话术，你在淘宝/1688/小红书发送后回填结果；
            <b style={{ marginLeft: 12 }}>代理队列：</b>ERP 输出限速、待发送和待追问队列，供独立桌面执行器领取。
            执行器默认只预览，只有采购电脑本机明确开启后才可发送。
          </span>
        }
        style={{ marginBottom: 16 }}
      />
      <Card size="small" style={{ marginBottom: 16 }}>
        <Row gutter={16} align="middle">
          <Col flex="auto">
            <Space direction="vertical" size={2}>
              <Text strong>桌面执行器</Text>
              {!agentRuntime?.token_configured ? (
                <Text type="warning">尚未启用：当前保持人工辅助，不会操作真实账号。</Text>
              ) : agentRuntime.agents.length === 0 ? (
                <Text type="secondary">令牌已配置，但暂未发现运行中的采购电脑。</Text>
              ) : (
                <Space wrap>
                  {agentRuntime.agents.map((agent) => (
                    <Tag
                      key={agent.agent_id}
                      color={agent.online ? (agent.mode === 'live' ? 'green' : 'blue') : 'default'}
                    >
                      {agent.display_name || agent.agent_id} · {
                        agent.online
                          ? agent.mode === 'live'
                            ? '自动执行'
                            : agent.mode === 'review'
                              ? '人工确认'
                              : '仅预览'
                          : '离线'
                      }
                    </Tag>
                  ))}
                </Space>
              )}
            </Space>
          </Col>
          <Col>
            <Statistic title="正在执行" value={agentRuntime?.active_leases || 0} suffix="项" />
          </Col>
        </Row>
      </Card>
      <Card
        title={<Space><RobotOutlined />智能询价任务</Space>}
        extra={<Button type="primary" icon={<PlusOutlined />} onClick={() => setCreateOpen(true)}>新建采购询价</Button>}
      >
        <Table
          rowKey="id"
          loading={isLoading}
          dataSource={tasks}
          columns={taskColumns}
          scroll={{ x: 1050 }}
          pagination={{ defaultPageSize: 20 }}
        />
      </Card>

      <Modal
        title="新建采购询价计划"
        open={createOpen}
        width={820}
        okText="建立任务并生成话术"
        cancelText="取消"
        confirmLoading={createMut.isPending}
        onCancel={() => setCreateOpen(false)}
        onOk={() => createForm.submit()}
        destroyOnHidden
      >
        <Form
          form={createForm}
          layout="vertical"
          initialValues={initialTaskValues}
          onFinish={(values) => createMut.mutate(values as ProcurementTaskInput)}
        >
          <Row gutter={16}>
            <Col span={12}>
              <Form.Item name="title" label="本次计划名称" rules={[{ required: true }]}>
                <Input placeholder="例如：岩板供应商第一轮比价" />
              </Form.Item>
            </Col>
            <Col span={6}>
              <Form.Item name="category" label="采购类型">
                <Segmented block options={[
                  { label: '日常', value: 'daily' },
                  { label: '拍摄', value: 'photo' },
                  { label: '生产', value: 'production' },
                ]} />
              </Form.Item>
            </Col>
            <Col span={6}>
              <Form.Item name="execution_mode" label="工作模式">
                <Radio.Group optionType="button" buttonStyle="solid">
                  <Radio.Button value="assisted">人工辅助</Radio.Button>
                  <Radio.Button value="agent">代理队列</Radio.Button>
                </Radio.Group>
              </Form.Item>
            </Col>
          </Row>
          <Row gutter={16}>
            <Col span={9}>
              <Form.Item name="item_name" label="采购品名" rules={[{ required: true }]}>
                <Input placeholder="岩板 / 电力轨道 / 螺丝…" />
              </Form.Item>
            </Col>
            <Col span={9}>
              <Form.Item name="specification" label="规格">
                <Input placeholder="尺寸、材质、颜色、工艺" />
              </Form.Item>
            </Col>
            <Col span={3}>
              <Form.Item name="quantity" label="数量" rules={[{ required: true }]}>
                <InputNumber min={0.0001} style={{ width: '100%' }} />
              </Form.Item>
            </Col>
            <Col span={3}>
              <Form.Item name="unit" label="单位">
                <Input />
              </Form.Item>
            </Col>
          </Row>
          <Row gutter={16}>
            <Col span={8}>
              <Form.Item name="target_unit_price" label="目标单价（可不填）">
                <InputNumber min={0} prefix="¥" style={{ width: '100%' }} />
              </Form.Item>
            </Col>
            <Col span={8}>
              <Form.Item name="channels" label="询价渠道" rules={[{ required: true }]}>
                <Checkbox.Group options={[
                  { label: '淘宝', value: 'taobao' },
                  { label: '1688', value: '1688' },
                  { label: '小红书', value: 'xiaohongshu' },
                ]} />
              </Form.Item>
            </Col>
            <Col span={8}>
              <Form.Item name="taobao_client_mode" label="淘宝执行端">
                <Radio.Group>
                  <Radio value="desktop">淘宝桌面版</Radio>
                  <Radio value="chrome">Chrome 采购号</Radio>
                </Radio.Group>
              </Form.Item>
            </Col>
          </Row>
          <Form.Item name="requirements" label="本次特别要求">
            <TextArea
              rows={3}
              placeholder="例如：必须报含运/含税价；说明岩板切割损耗、木架费、破损补发；支持先寄样"
            />
          </Form.Item>
          <Row gutter={24}>
            <Col span={8}>
              <Form.Item name="planned_merchant_count" label={`计划询问商家：${plannedCount} 家`}>
                <Slider
                  min={1}
                  max={30}
                  marks={{ 1: '1', 10: '10', 20: '20', 30: '30' }}
                  onChange={(value) => {
                    const sample = createForm.getFieldValue('ab_test_sample_size') || 2;
                    if (sample > value) {
                      createForm.setFieldValue('ab_test_sample_size', Math.max(2, value));
                    }
                  }}
                />
              </Form.Item>
            </Col>
            <Col span={8}>
              <Form.Item name="max_followup_rounds" label="最多自动追问轮数">
                <Slider min={0} max={5} marks={{ 0: '不追', 2: '2轮', 3: '3轮', 5: '5轮' }} />
              </Form.Item>
            </Col>
            <Col span={8}>
              <Form.Item name="ab_test_enabled" label="话术 A/B 测试" valuePropName="checked">
                <Switch checkedChildren="开启" unCheckedChildren="关闭" />
              </Form.Item>
              {abEnabled && (
                <Form.Item name="ab_test_sample_size" label="首批测试商家数">
                  <Slider
                    min={2}
                    max={Math.max(2, plannedCount)}
                    marks={{ 2: '2', [Math.max(2, plannedCount)]: String(plannedCount) }}
                  />
                </Form.Item>
              )}
            </Col>
          </Row>
          <Card size="small" title="账号节奏保护（每天每渠道上限）">
            <Row gutter={16}>
              {selectedChannels.map((channel) => (
                <Col span={8} key={channel}>
                  <Form.Item
                    name={['channel_daily_limits', channel]}
                    label={`${channelLabel[channel]} 每日最多`}
                  >
                    <InputNumber min={1} max={30} addonAfter="家" />
                  </Form.Item>
                </Col>
              ))}
            </Row>
            {selectedChannels.includes('xiaohongshu') && (
              <Alert
                type="warning"
                showIcon
                message="小红书按长期跟进处理：先私信、持续检查后台回复，收到回复后才进入下一轮。"
              />
            )}
          </Card>
        </Form>
      </Modal>

      <Drawer
        title={currentTask ? `${currentTask.title} · ${currentTask.task_no}` : '采购询价工作台'}
        open={Boolean(selectedTask)}
        width="min(1180px, 96vw)"
        onClose={() => setSelectedTask(null)}
        destroyOnHidden={false}
      >
        {currentTask && (
          <Space direction="vertical" size={16} style={{ width: '100%' }}>
            <Descriptions bordered size="small" column={4}>
              <Descriptions.Item label="品名">{currentTask.item_name}</Descriptions.Item>
              <Descriptions.Item label="规格">{currentTask.specification || '-'}</Descriptions.Item>
              <Descriptions.Item label="计划">{currentTask.planned_merchant_count} 家</Descriptions.Item>
              <Descriptions.Item label="模式">
                {currentTask.execution_mode === 'agent' ? '代理队列' : '人工辅助'}
              </Descriptions.Item>
              <Descriptions.Item label="数量">{Number(currentTask.quantity)} {currentTask.unit}</Descriptions.Item>
              <Descriptions.Item label="目标单价">
                {currentTask.target_unit_price == null ? '-' : `¥${Number(currentTask.target_unit_price)}`}
              </Descriptions.Item>
              <Descriptions.Item label="追问上限">{currentTask.max_followup_rounds} 轮</Descriptions.Item>
              <Descriptions.Item label="淘宝端">
                {currentTask.taobao_client_mode === 'desktop' ? '桌面版' : 'Chrome 采购号'}
              </Descriptions.Item>
            </Descriptions>

            <Card
              size="small"
              title={<Space><ExperimentOutlined />话术建议与 A/B 测试</Space>}
              extra={
                <Space>
                  <Button loading={scriptMut.isPending} onClick={() => scriptMut.mutate()}>
                    AI 重新建议
                  </Button>
                  <Button
                    type="primary"
                    disabled={!scriptsReadyForReview}
                    loading={saveScriptsMut.isPending}
                    onClick={() => saveScriptsMut.mutate()}
                  >
                    保存修改并确认
                  </Button>
                </Space>
              }
            >
              <Alert
                type={currentTask.scripts_reviewed_at ? 'success' : 'warning'}
                showIcon
                message={
                  currentTask.scripts_reviewed_at
                    ? `已由 ${currentTask.scripts_reviewed_by || '采购人员'} 人工改稿确认`
                    : 'AI 内容只是原稿：A/B 两组都要人工修改后，才能生成队列或交给代理'
                }
                style={{ marginBottom: 12 }}
              />
              {currentTask.ai_suggestion_note && (
                <Alert
                  type={currentTask.ai_model ? 'success' : 'warning'}
                  showIcon
                  message={currentTask.ai_suggestion_note}
                  style={{ marginBottom: 12 }}
                />
              )}
              <Row gutter={16}>
                <Col span={12}>
                  <Text strong>A 组 · 直接报价型</Text>
                  <TextArea value={scriptA} onChange={(event) => setScriptA(event.target.value)} rows={6} />
                </Col>
                <Col span={12}>
                  <Text strong>B 组 · 合作澄清型</Text>
                  <TextArea value={scriptB} onChange={(event) => setScriptB(event.target.value)} rows={6} />
                </Col>
              </Row>
              {currentTask.ab_test_enabled && experiment && (
                <Row gutter={16} style={{ marginTop: 16 }}>
                  {(['A', 'B'] as const).map((variant) => (
                    <Col span={9} key={variant}>
                      <Card size="small">
                        <Space size="large">
                          <Statistic title={`${variant} 组回复率`} value={experiment[variant].reply_rate * 100} precision={0} suffix="%" />
                          <Statistic title="完整报价" value={experiment[variant].quote_complete} suffix={`/ ${experiment[variant].sent}`} />
                          <Statistic title="综合分" value={experiment[variant].score * 100} precision={0} />
                        </Space>
                      </Card>
                    </Col>
                  ))}
                  <Col span={6}>
                    <Card size="small">
                      <Text strong>当前判断</Text>
                      <Paragraph style={{ margin: '6px 0' }}>{experiment.reason}</Paragraph>
                      <Space>
                        <Button
                          type={experiment.winner ? 'primary' : 'default'}
                          disabled={!experiment.winner}
                          loading={winnerMut.isPending}
                          onClick={() => winnerMut.mutate(undefined)}
                        >
                          应用优胜组
                        </Button>
                        <Tooltip title="数据不足时也可以由采购负责人手动指定">
                          <Button onClick={() => Modal.confirm({
                            title: '手动选择后续话术',
                            content: (
                              <Space>
                                <Button onClick={() => { winnerMut.mutate('A'); Modal.destroyAll(); }}>采用 A</Button>
                                <Button onClick={() => { winnerMut.mutate('B'); Modal.destroyAll(); }}>采用 B</Button>
                              </Space>
                            ),
                            footer: null,
                          })}>
                            手动选
                          </Button>
                        </Tooltip>
                      </Space>
                    </Card>
                  </Col>
                </Row>
              )}
            </Card>

            <Card
              size="small"
              title="商家询价队列"
              extra={
                inquiries.length === 0
                  ? (
                    <Tooltip title={currentTask.scripts_reviewed_at ? '' : '先修改并确认上方 A/B 话术'}>
                      <Button
                        type="primary"
                        disabled={!currentTask.scripts_reviewed_at}
                        loading={queueMut.isPending}
                        onClick={() => queueMut.mutate()}
                      >
                        生成 {currentTask.planned_merchant_count} 家队列
                      </Button>
                    </Tooltip>
                  )
                  : <Text type="secondary">已有 {inquiries.length} 个询价位</Text>
              }
            >
              {inquiries.length === 0 && (
                <Alert
                  type="info"
                  message={`将先分配 ${currentTask.ab_test_sample_size} 家做 A/B 测试，其余商家等待优胜话术。`}
                  style={{ marginBottom: 12 }}
                />
              )}
              <Table
                rowKey="id"
                loading={inquiriesLoading}
                dataSource={inquiries}
                columns={inquiryColumns}
                size="small"
                scroll={{ x: 1120 }}
                pagination={{ defaultPageSize: 20 }}
              />
            </Card>
          </Space>
        )}
      </Drawer>

      <Modal
        title={`发送前人工改稿 · ${messageReviewInquiry?.merchant_name || `#${messageReviewInquiry?.slot_no || ''}`}`}
        open={Boolean(messageReviewInquiry)}
        okText={currentTask?.execution_mode === 'agent' ? '保存并批准代理使用' : '保存确认稿并复制'}
        cancelText="先不处理"
        confirmLoading={reviewMessageMut.isPending}
        okButtonProps={{
          disabled: (
            !messageReviewConfirmed
            || !normalizeMessage(messageDraft)
            || (
              messageReviewAction?.action !== 'initial_message'
              && !messageDraftChanged
            )
          ),
        }}
        onCancel={() => {
          setMessageReviewInquiry(null);
          setMessageDraft('');
          setMessageReviewConfirmed(false);
        }}
        onOk={() => {
          if (messageReviewInquiry) {
            reviewMessageMut.mutate({
              row: messageReviewInquiry,
              content: messageDraft,
            });
          }
        }}
        destroyOnHidden
      >
        <Space direction="vertical" size={12} style={{ width: '100%' }}>
          <Alert
            type="warning"
            showIcon
            message="系统不会直接使用 AI 原稿"
            description={
              messageReviewAction?.action === 'initial_message'
                ? '这份首轮话术已经过任务级人工改稿；你仍可按商家情况继续调整，确认后才复制或释放给代理。'
                : '追问必须在这里人工修改，未修改、未勾选确认时，后端和桌面代理都会拒绝发送。'
            }
          />
          {messageReviewAction && (
            <div>
              <Text type="secondary">系统原稿</Text>
              <Paragraph
                copyable
                style={{ padding: 10, background: '#f5f5f5', marginTop: 4 }}
              >
                {messageReviewAction.suggested_message}
              </Paragraph>
            </div>
          )}
          <div>
            <Text strong>你的确认稿</Text>
            <TextArea
              value={messageDraft}
              onChange={(event) => {
                setMessageDraft(event.target.value);
                setMessageReviewConfirmed(false);
              }}
              rows={7}
              showCount
              maxLength={1000}
              style={{ marginTop: 4 }}
            />
          </div>
          <Checkbox
            checked={messageReviewConfirmed}
            onChange={(event) => setMessageReviewConfirmed(event.target.checked)}
          >
            我已逐句检查，确认这就是本轮允许对外发送的内容
          </Checkbox>
        </Space>
      </Modal>

      <Modal
        title={`填写商家信息 · #${editingInquiry?.slot_no || ''}`}
        open={Boolean(editingInquiry)}
        okText="保存"
        cancelText="取消"
        confirmLoading={patchInquiryMut.isPending}
        onCancel={() => setEditingInquiry(null)}
        onOk={() => merchantForm.submit()}
        destroyOnHidden
      >
        <Form form={merchantForm} layout="vertical" onFinish={(values) => patchInquiryMut.mutate(values)}>
          <Form.Item name="channel" label="渠道">
            <Radio.Group options={(currentTask?.channels || []).map((channel) => ({
              label: channelLabel[channel], value: channel,
            }))} />
          </Form.Item>
          <Form.Item name="merchant_name" label="商家名称"><Input /></Form.Item>
          <Form.Item name="merchant_url" label="店铺链接"><Input /></Form.Item>
          <Form.Item name="product_url" label="商品链接"><Input /></Form.Item>
        </Form>
      </Modal>

      <Modal
        title={`录入商家回复 · ${replyInquiry?.merchant_name || `#${replyInquiry?.slot_no || ''}`}`}
        open={Boolean(replyInquiry)}
        okText="归档反馈"
        cancelText="取消"
        confirmLoading={replyMut.isPending}
        onCancel={() => setReplyInquiry(null)}
        onOk={() => replyForm.submit()}
        destroyOnHidden
      >
        <Form
          form={replyForm}
          layout="vertical"
          onFinish={(values) => replyMut.mutate(values as ProcurementReplyInput)}
        >
          <Form.Item name="content" label="商家原回复" rules={[{ required: true }]}>
            <TextArea rows={5} placeholder="粘贴商家回复；出现“加微信”时系统会自动转入人工处理" />
          </Form.Item>
          <Row gutter={16}>
            <Col span={8}>
              <Form.Item name="quote_complete" label="报价信息完整" valuePropName="checked">
                <Switch />
              </Form.Item>
            </Col>
            <Col span={8}>
              <Form.Item name="quote_amount" label="本次总报价"><InputNumber min={0} prefix="¥" /></Form.Item>
            </Col>
            <Col span={8}>
              <Form.Item name="normalized_unit_price" label="换算后单位价"><InputNumber min={0} prefix="¥" /></Form.Item>
            </Col>
          </Row>
          <Row gutter={16}>
            <Col span={12}>
              <Form.Item name="response_quality" label="回复质量（0-100）">
                <Slider min={0} max={100} />
              </Form.Item>
            </Col>
            <Col span={12}>
              <Form.Item name="wechat_contact" label="微信号（商家主动提供时填写）">
                <Input />
              </Form.Item>
            </Col>
          </Row>
        </Form>
      </Modal>
    </>
  );
}
