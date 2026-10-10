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
  activateProcurementBatch,
  applyProcurementWinner,
  createProcurementTask,
  decideProcurementInquiry,
  generateProcurementScripts,
  getProcurementAgentStatus,
  getProcurementDailySummary,
  getProcurementExperiment,
  listProcurementInquiries,
  listProcurementTasks,
  listProcurementDueActions,
  listProcurementQuotes,
  markProcurementSent,
  patchProcurementInquiry,
  patchProcurementTask,
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
  pinduoduo: '拼多多',
  xiaohongshu: '小红书',
};
const statusMeta: Record<string, { label: string; color: string }> = {
  draft: { label: '草稿', color: 'default' },
  ready: { label: '待执行', color: 'blue' },
  discovery_ready: { label: '待搜索候选', color: 'gold' },
  running: { label: '询价中', color: 'processing' },
  needs_review: { label: '需人工处理', color: 'orange' },
  completed: { label: '已完成', color: 'green' },
  cancelled: { label: '已取消', color: 'default' },
  expired: { label: '已到期', color: 'default' },
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
  ab_test_enabled: false,
  ab_test_sample_size: 6,
  channel_daily_limits: { taobao: 10, '1688': 5, pinduoduo: 5, xiaohongshu: 3 },
  followup_intervals_hours: { taobao: 12, '1688': 12, pinduoduo: 12, xiaohongshu: 24 },
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
  const [searchQueriesText, setSearchQueriesText] = useState('');
  const [createForm] = Form.useForm();
  const [merchantForm] = Form.useForm();
  const [replyForm] = Form.useForm();

  const plannedCount = Form.useWatch('planned_merchant_count', createForm) || 10;
  const abEnabled = Form.useWatch('ab_test_enabled', createForm) ?? false;
  const selectedChannels = (Form.useWatch('channels', createForm) || []) as ProcurementChannel[];

  const { data: tasks = [], isLoading } = useQuery({
    queryKey: ['procurement-tasks'],
    queryFn: listProcurementTasks,
    refetchInterval: 60_000,
  });
  const { data: agentRuntime } = useQuery({
    queryKey: ['procurement-agent-status'],
    queryFn: getProcurementAgentStatus,
    refetchInterval: 30_000,
  });
  const { data: dailySummary } = useQuery({
    queryKey: ['procurement-daily-summary'],
    queryFn: getProcurementDailySummary,
    refetchInterval: 60_000,
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
  const { data: quoteComparison = [] } = useQuery({
    queryKey: ['procurement-quotes', taskId],
    queryFn: () => listProcurementQuotes(taskId!),
    enabled: Boolean(taskId),
  });

  useEffect(() => {
    setScriptA(selectedTask?.script_a || '');
    setScriptB(selectedTask?.script_b || '');
    setSearchQueriesText((selectedTask?.search_queries || []).join('\n'));
  }, [
    selectedTask?.id,
    selectedTask?.script_a,
    selectedTask?.script_b,
    selectedTask?.search_queries,
  ]);

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
  const activateMut = useMutation({
    mutationFn: () => activateProcurementBatch(taskId!),
    onSuccess: async (task) => {
      message.success('48 小时计时已启动；不代表自动收发已启用');
      await refreshTask(task);
      await qc.invalidateQueries({ queryKey: ['procurement-due-actions', taskId] });
    },
    onError: (error: any) => message.error(`启动失败：${errorText(error)}`),
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
      message.success('话术已由人工明确确认，可以生成询价队列');
      await refreshTask(task);
    },
    onError: (error: any) => message.error(`保存失败：${errorText(error)}`),
  });
  const searchQueriesMut = useMutation({
    mutationFn: () => patchProcurementTask(taskId!, {
      search_queries: searchQueriesText
        .split('\n')
        .map((value) => value.trim())
        .filter(Boolean),
    }),
    onSuccess: async (task) => {
      message.success('搜索词已保存，执行器会按顺序轮换尝试');
      await refreshTask(task);
    },
    onError: (error: any) => message.error(`保存搜索词失败：${errorText(error)}`),
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
        qc.invalidateQueries({ queryKey: ['procurement-quotes', taskId] }),
        qc.invalidateQueries({ queryKey: ['procurement-daily-summary'] }),
      ]);
    },
    onError: (error: any) => message.error(`归档失败：${errorText(error)}`),
  });
  const decisionMut = useMutation({
    mutationFn: ({ row, status }: {
      row: ProcurementInquiry;
      status: ProcurementInquiry['decision_status'];
    }) => decideProcurementInquiry(row.id, { status }),
    onSuccess: async () => {
      message.success('采购选择已记录；不会自动下单或付款');
      await Promise.all([
        qc.invalidateQueries({ queryKey: ['procurement-inquiries', taskId] }),
        qc.invalidateQueries({ queryKey: ['procurement-quotes', taskId] }),
        qc.invalidateQueries({ queryKey: ['procurement-daily-summary'] }),
      ]);
    },
    onError: (error: any) => message.error(`记录选择失败：${errorText(error)}`),
  });

  const currentTask = useMemo(
    () => tasks.find((task) => task.id === taskId) || selectedTask,
    [tasks, taskId, selectedTask],
  );
  const planFrozen = Boolean(currentTask?.batch_policy_version && (
    currentTask.started_at || ['cancelled', 'expired', 'completed'].includes(currentTask.status)
  ));

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
  );
  const messageReviewAction = messageReviewInquiry
    ? dueActions.find((action) => action.inquiry_id === messageReviewInquiry.id)
    : undefined;
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
          <Text>{row.merchant_name || (row.status === 'discovery_ready' ? '等待自动搜索' : '待填写')}</Text>
          {row.candidate_score != null && (
            <Text type="secondary">候选评分 {Number(row.candidate_score).toFixed(0)}</Text>
          )}
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
          {row.last_discovery_error && (
            <Tooltip title={row.last_discovery_error}>
              <Text type="warning" ellipsis style={{ maxWidth: 230 }}>
                搜索异常（第 {row.discovery_attempts} 次）
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
      width: 340,
      render: (_: unknown, row: ProcurementInquiry) => {
        const due = dueActions.find((action) => action.inquiry_id === row.id);
        const canReviewMessage = Boolean(
          due && ['ready', 'followup_ready', 'waiting_reply'].includes(row.status),
        );
        return (
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
          {canReviewMessage && (
            <>
              <Button size="small" icon={<CopyOutlined />} onClick={() => openMessageEditor(row)}>
                {due?.action === 'initial_message' ? '检查并确认首轮' : '检查并确认追问'}
              </Button>
              <Popconfirm
                title={due?.action === 'initial_message' ? '只记录首轮发送结果' : '只记录追问发送结果'}
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
          {row.decision_status !== 'selected' && row.merchant_name && (
            <Button
              size="small"
              onClick={() => decisionMut.mutate({ row, status: 'shortlisted' })}
            >
              入围
            </Button>
          )}
          {row.decision_status !== 'selected' && row.merchant_name && (
            <Popconfirm
              title="设为首选供应商？"
              description="这里只记录人工选择，不会下单或付款。"
              onConfirm={() => decisionMut.mutate({ row, status: 'selected' })}
            >
              <Button size="small" type="dashed">选定</Button>
            </Popconfirm>
          )}
          {row.decision_status === 'selected' && <Tag color="green">已选定</Tag>}
        </Space>
        );
      },
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
            <b>人工辅助：</b>ERP 生成并复制话术，你在淘宝/1688/拼多多/小红书发送后回填结果；
            <b style={{ marginLeft: 12 }}>代理队列：</b>ERP 输出限速、待发送和待追问队列，供独立桌面执行器领取。
            执行器默认只预览。新建 48 小时批次的自动收发尚未开放，即使本机开启代理也不会领取。
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
      <Card size="small" title={`采购日报 · ${dailySummary?.date || '今日'}`} style={{ marginBottom: 16 }}>
        <Row gutter={16}>
          <Col span={3}><Statistic title="新候选" value={dailySummary?.discovered || 0} /></Col>
          <Col span={3}><Statistic title="待审核" value={dailySummary?.review_pending || 0} /></Col>
          <Col span={3}><Statistic title="已询价" value={dailySummary?.sent || 0} /></Col>
          <Col span={3}><Statistic title="有回复" value={dailySummary?.replied || 0} /></Col>
          <Col span={3}><Statistic title="转人工" value={dailySummary?.manual || 0} /></Col>
          <Col span={3}><Statistic title="已选定" value={dailySummary?.selected || 0} /></Col>
          <Col span={3}>
            <Tooltip title="只有关联到 ERP 既有采购记录后才计入，不以询价或选定代替采购">
              <Statistic title="已采购" value={dailySummary?.purchased || 0} />
            </Tooltip>
          </Col>
          <Col span={3}><Statistic title="全部待办" value={dailySummary?.pending_total || 0} /></Col>
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
                  { label: '拼多多', value: 'pinduoduo' },
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
              <Form.Item name="planned_merchant_count" label="计划询问商家数量" rules={[{ required: true, type: 'integer', min: 1, max: 50 }]}>
                <InputNumber
                  min={1}
                  max={50}
                  precision={0}
                  addonAfter="家（1–50）"
                  onChange={(value) => {
                    const sample = createForm.getFieldValue('ab_test_sample_size') || 2;
                    if (value != null && sample > value) {
                      createForm.setFieldValue('ab_test_sample_size', Math.max(2, value));
                    }
                    if (value === 1) createForm.setFieldValue('ab_test_enabled', false);
                  }}
                />
              </Form.Item>
              <Space size={4} wrap>
                {[10, 20, 30, 50].map((count) => (
                  <Button key={count} size="small" onClick={() => createForm.setFieldValue('planned_merchant_count', count)}>{count} 家</Button>
                ))}
              </Space>
              <Paragraph type="secondary">这是本批联系上限，不是保证回复数；所有平台统一 48 小时。</Paragraph>
            </Col>
            <Col span={8}>
              <Form.Item name="max_followup_rounds" label="最多自动追问轮数">
                <Slider min={0} max={5} marks={{ 0: '不追', 2: '2轮', 3: '3轮', 5: '5轮' }} />
              </Form.Item>
            </Col>
            <Col span={8}>
              <Form.Item name="ab_test_enabled" label="话术 A/B 测试" valuePropName="checked">
                <Switch checkedChildren="开启" unCheckedChildren="关闭" disabled={plannedCount < 2} />
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
          <Alert type="info" showIcon message="保存需求不开始计时。准备资料和队列后，另行启动 48 小时批次；当前新批次的自动收发仍未开放。" style={{ marginBottom: 12 }} />
          <Card size="small" title="当前任务渠道上限（不是账号全局额度）">
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
                message="小红书也统一 48 小时截止；回复可能较慢，到期无回复会明确标注，迟到资料另存补充，不延长本批。"
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

            {currentTask.batch_policy_version && (
              <Card size="small" title="48 小时批次">
                <Alert type="warning" showIcon message="当前仅支持固定计时和截止证据快照；自动收发、全局配额与前五推荐尚未验收。新批次不会交给旧代理执行。" />
                <Paragraph style={{ marginTop: 12 }}>
                  {currentTask.deadline_at
                    ? `截止：${new Date(currentTask.deadline_at).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai', hour12: false })}（北京时间，不因暂停或重启顺延）`
                    : '尚未启动，不计时。请先确认规格、数量、需求说明及话术，并生成商家队列。'}
                </Paragraph>
                {!currentTask.started_at && currentTask.status === 'ready' && (
                  <Popconfirm title="启动后冻结当前需求，统一计时 48 小时；不包含下单和付款。" onConfirm={() => activateMut.mutate()}>
                    <Button type="primary" loading={activateMut.isPending}>启动 48 小时计时</Button>
                  </Popconfirm>
                )}
                {currentTask.deadline_report && (
                  <Alert type="info" showIcon message="截止证据快照已生成（不是最终推荐）"
                    description={`已确认发送 ${currentTask.deadline_report.confirmed_sent_merchants} 家，已回复 ${currentTask.deadline_report.replied_merchants} 家，未确认发送 ${currentTask.deadline_report.not_confirmed_sent_merchants} 家。未确认不等于未发送，不可据此补发。`} />
                )}
              </Card>
            )}

            <Card
              size="small"
              title="候选供应商搜索词"
              extra={(
                <Button
                  disabled={planFrozen}
                  loading={searchQueriesMut.isPending}
                  onClick={() => searchQueriesMut.mutate()}
                >
                  保存搜索词
                </Button>
              )}
            >
              <Alert
                type="info"
                showIcon
                message="每行一个搜索词，Windows 采购机会按顺序轮换；搜索候选不会向商家发送消息。"
                style={{ marginBottom: 10 }}
              />
              <TextArea
                disabled={planFrozen}
                rows={5}
                value={searchQueriesText}
                onChange={(event) => setSearchQueriesText(event.target.value)}
                placeholder="例如：岩板 1600×3200 12mm 厂家批发"
              />
            </Card>

            <Card
              size="small"
              title={<Space><ExperimentOutlined />话术建议与 A/B 测试</Space>}
              extra={
                <Space>
                  <Button disabled={planFrozen} loading={scriptMut.isPending} onClick={() => scriptMut.mutate()}>
                    AI 重新建议
                  </Button>
                  <Button
                    type="primary"
                    disabled={planFrozen || !scriptsReadyForReview}
                    loading={saveScriptsMut.isPending}
                    onClick={() => saveScriptsMut.mutate()}
                  >
                    明确确认话术
                  </Button>
                </Space>
              }
            >
              <Alert
                type={currentTask.scripts_reviewed_at ? 'success' : 'warning'}
                showIcon
                message={
                  currentTask.scripts_reviewed_at
                    ? `已由 ${currentTask.scripts_reviewed_by || '采购人员'} 人工确认`
                    : 'AI 内容只是建议：A/B 两组都要人工逐条检查并明确确认，原文准确时可以不改字'
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
                  <TextArea disabled={planFrozen} value={scriptA} onChange={(event) => setScriptA(event.target.value)} rows={6} />
                </Col>
                <Col span={12}>
                  <Text strong>B 组 · 合作澄清型</Text>
                  <TextArea disabled={planFrozen} value={scriptB} onChange={(event) => setScriptB(event.target.value)} rows={6} />
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
                    <Tooltip title={currentTask.scripts_reviewed_at ? '' : '先检查并确认上方 A/B 话术'}>
                      <Button
                        type="primary"
                        disabled={planFrozen || !currentTask.scripts_reviewed_at}
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
                  message={currentTask.ab_test_enabled
                    ? `将先分配 ${currentTask.ab_test_sample_size} 家做 A/B 测试，其余商家等待优胜话术。`
                    : `不做 A/B 测试，本批最多建立 ${currentTask.planned_merchant_count} 个询价位。建队列不代表已经联系商家。`}
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

            <Card size="small" title="供应商报价比较">
              <Alert
                type="info"
                showIcon
                message="按完整报价、标准单价和候选评分排序；选定只记录采购决策，不会自动下单或付款。"
                style={{ marginBottom: 12 }}
              />
              <Table
                rowKey="inquiry_id"
                size="small"
                dataSource={quoteComparison}
                pagination={false}
                scroll={{ x: 1000 }}
                columns={[
                  {
                    title: '商家',
                    dataIndex: 'merchant_name',
                    render: (value: string | null, row: any) => row.product_url
                      ? <a href={row.product_url} target="_blank" rel="noreferrer">{value || '查看商品'}</a>
                      : value || '-',
                  },
                  { title: '渠道', dataIndex: 'channel', render: (value: string) => channelLabel[value] || value },
                  {
                    title: '标准单价',
                    dataIndex: 'normalized_unit_price',
                    render: (value: number | string | null) => value == null ? '-' : `¥${Number(value).toLocaleString()}`,
                  },
                  { title: '运费', dataIndex: 'freight', render: (value: unknown) => value == null ? '-' : String(value) },
                  { title: '交期', dataIndex: 'lead_time', render: (value: unknown) => value == null ? '-' : String(value) },
                  { title: '规格', dataIndex: 'specification', render: (value: unknown) => value == null ? '-' : String(value) },
                  {
                    title: '完整度',
                    dataIndex: 'quote_complete',
                    render: (value: boolean) => <Tag color={value ? 'green' : 'orange'}>{value ? '完整' : '待追问'}</Tag>,
                  },
                  {
                    title: '采购选择',
                    dataIndex: 'decision_status',
                    render: (value: string) => value === 'selected'
                      ? <Tag color="green">已选定</Tag>
                      : value === 'shortlisted'
                        ? <Tag color="blue">已入围</Tag>
                        : value === 'rejected'
                          ? <Tag>已淘汰</Tag>
                          : <Tag>待评估</Tag>,
                  },
                ]}
              />
            </Card>
          </Space>
        )}
      </Drawer>

      <Modal
        title={`发送前人工审核 · ${messageReviewInquiry?.merchant_name || `#${messageReviewInquiry?.slot_no || ''}`}`}
        open={Boolean(messageReviewInquiry)}
        okText={currentTask?.execution_mode === 'agent' ? '保存并批准代理使用' : '保存确认稿并复制'}
        cancelText="先不处理"
        confirmLoading={reviewMessageMut.isPending}
        okButtonProps={{
          disabled: !messageReviewConfirmed || !normalizeMessage(messageDraft),
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
                : '追问必须在这里人工逐句检查并勾选确认；原稿准确时可以不改字。未确认时，后端和桌面代理都会拒绝发送。'
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
