import { useState } from 'react';
import {
  Alert,
  Badge,
  Button,
  Card,
  Col,
  Descriptions,
  Divider,
  Form,
  Input,
  InputNumber,
  message,
  Modal,
  Popconfirm,
  Row,
  Space,
  Switch,
  Table,
  Tag,
  Tooltip,
  Typography,
} from 'antd';
import {
  CheckCircleOutlined,
  ExclamationCircleOutlined,
  FunctionOutlined,
  PlayCircleOutlined,
  ReloadOutlined,
  UndoOutlined,
} from '@ant-design/icons';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '../api/base';

const { Text, Title } = Typography;
const { TextArea } = Input;

interface FormulaRule {
  id: number;
  field_name: string;
  display_name: string | null;
  expression: string;
  description: string | null;
  enabled: boolean;
  sort_order: number;
  is_builtin: boolean;
}

interface ValidateResult {
  ok: boolean;
  error?: string;
  detected_inputs: string[];
  sample_result?: number;
}

const fetchRules = () =>
  api.get<FormulaRule[]>('/api/pricing/formula-rules').then((r: { data: FormulaRule[] }) => r.data);

const updateRule = (id: number, body: Partial<FormulaRule>) =>
  api.put<FormulaRule>(`/api/pricing/formula-rules/${id}`, body).then((r: { data: FormulaRule }) => r.data);

const validateExpr = (expression: string, sample_values?: Record<string, number>) =>
  api
    .post<ValidateResult>('/api/pricing/formula-rules/validate', { expression, sample_values })
    .then((r: { data: ValidateResult }) => r.data);

const seedRules = () =>
  api.post<{ message: string; inserted: number }>('/api/pricing/formula-rules/seed').then((r: { data: { message: string; inserted: number } }) => r.data);

const recomputeAll = (force: boolean) =>
  api.post<{ updated: number; message: string }>(`/api/pricing/recompute-all?force=${force}`).then((r: { data: { updated: number; message: string } }) => r.data);

// ---------------------------------------------------------------------------
// Inline expression editor modal
// ---------------------------------------------------------------------------
function EditFormulaModal({
  rule,
  onClose,
  onSaved,
}: {
  rule: FormulaRule;
  onClose: () => void;
  onSaved: (r: FormulaRule) => void;
}) {
  const [expr, setExpr] = useState(rule.expression);
  const [sampleStr, setSampleStr] = useState('');
  const [validation, setValidation] = useState<ValidateResult | null>(null);
  const [validating, setValidating] = useState(false);
  const [saving, setSaving] = useState(false);

  const parseSamples = (): Record<string, number> | undefined => {
    if (!sampleStr.trim()) return undefined;
    try {
      const obj: Record<string, number> = {};
      sampleStr.split(',').forEach((pair) => {
        const [k, v] = pair.split('=').map((s) => s.trim());
        if (k && v) obj[k] = parseFloat(v);
      });
      return Object.keys(obj).length ? obj : undefined;
    } catch {
      return undefined;
    }
  };

  const handleValidate = async () => {
    setValidating(true);
    try {
      const r = await validateExpr(expr, parseSamples());
      setValidation(r);
    } catch {
      message.error('验证请求失败');
    } finally {
      setValidating(false);
    }
  };

  const handleSave = async () => {
    if (!validation?.ok && validation !== null) {
      message.warning('请先通过验证再保存');
      return;
    }
    setSaving(true);
    try {
      const saved = await updateRule(rule.id, { expression: expr });
      onSaved(saved);
      message.success('公式已保存');
      onClose();
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '保存失败');
    } finally {
      setSaving(false);
    }
  };

  return (
    <Modal
      open
      title={
        <Space>
          <FunctionOutlined />
          编辑公式 — {rule.display_name || rule.field_name}
        </Space>
      }
      onCancel={onClose}
      width={700}
      footer={
        <Space>
          <Button onClick={onClose}>取消</Button>
          <Button
            icon={<PlayCircleOutlined />}
            onClick={handleValidate}
            loading={validating}
          >
            验证公式
          </Button>
          <Button type="primary" onClick={handleSave} loading={saving}>
            保存
          </Button>
        </Space>
      }
    >
      <Space direction="vertical" style={{ width: '100%' }} size="middle">
        <div>
          <Text type="secondary">
            支持运算符：+ - * / 括号 &nbsp;|&nbsp; 函数：IF(条件, 真, 假) SUM(字段1, 字段2, …) MIN MAX ABS ROUND
          </Text>
          <br />
          <Text type="secondary">字段名用中文，例：物理总成本 / 0.4</Text>
        </div>

        <TextArea
          value={expr}
          onChange={(e) => {
            setExpr(e.target.value);
            setValidation(null);
          }}
          rows={4}
          style={{ fontFamily: 'monospace', fontSize: 14 }}
          placeholder="输入公式，例：物理总成本 / 0.4"
        />

        <div>
          <Text type="secondary">
            试算样例值（可选）：格式 <code>字段名=数值, 字段名=数值</code>
          </Text>
          <Input
            value={sampleStr}
            onChange={(e) => setSampleStr(e.target.value)}
            placeholder="物理总成本=1000, 总出厂成本=800"
            style={{ marginTop: 4 }}
          />
        </div>

        {validation && (
          <Alert
            type={validation.ok ? 'success' : 'error'}
            icon={
              validation.ok ? (
                <CheckCircleOutlined />
              ) : (
                <ExclamationCircleOutlined />
              )
            }
            showIcon
            message={validation.ok ? '公式语法正确' : '公式有误'}
            description={
              validation.ok ? (
                <Space direction="vertical" size={2}>
                  <Text>依赖字段：{validation.detected_inputs.join('、') || '无'}</Text>
                  {validation.sample_result !== null && validation.sample_result !== undefined && (
                    <Text>
                      试算结果：<strong>{validation.sample_result.toFixed(4)}</strong>
                    </Text>
                  )}
                </Space>
              ) : (
                <Text type="danger">{validation.error}</Text>
              )
            }
          />
        )}

        {rule.is_builtin && (
          <Alert
            type="warning"
            showIcon
            message="这是内置公式"
            description={`原始公式：${rule.expression}`}
          />
        )}
      </Space>
    </Modal>
  );
}

// ---------------------------------------------------------------------------
// Recompute modal
// ---------------------------------------------------------------------------
function RecomputeModal({ onClose }: { onClose: () => void }) {
  const [force, setForce] = useState(false);
  const [result, setResult] = useState<{ updated: number; message: string } | null>(null);
  const [loading, setLoading] = useState(false);

  const handleRun = async () => {
    setLoading(true);
    try {
      const r = await recomputeAll(force);
      setResult(r);
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '重算失败');
    } finally {
      setLoading(false);
    }
  };

  return (
    <Modal
      open
      title={
        <Space>
          <ReloadOutlined />
          批量重算定价公式
        </Space>
      }
      onCancel={onClose}
      footer={
        <Space>
          <Button onClick={onClose}>关闭</Button>
          <Button type="primary" onClick={handleRun} loading={loading} icon={<ReloadOutlined />}>
            开始重算
          </Button>
        </Space>
      }
    >
      <Space direction="vertical" style={{ width: '100%' }} size="middle">
        <Alert
          type="info"
          showIcon
          message="系统将按公式依赖顺序对所有 SKU 重新计算公式列"
          description="默认只填补 NULL 字段；开启「强制覆盖」后将覆盖所有已有值。"
        />
        <Form layout="inline">
          <Form.Item label="强制覆盖已有值">
            <Switch checked={force} onChange={setForce} />
          </Form.Item>
        </Form>
        {result && (
          <Alert
            type="success"
            showIcon
            message={result.message}
          />
        )}
      </Space>
    </Modal>
  );
}

// ---------------------------------------------------------------------------
// Main page
// ---------------------------------------------------------------------------
export default function PricingFormulaPage() {
  const qc = useQueryClient();
  const { data: rules = [], isLoading } = useQuery({
    queryKey: ['pricing-formula-rules'],
    queryFn: fetchRules,
  });

  const [editingRule, setEditingRule] = useState<FormulaRule | null>(null);
  const [showRecompute, setShowRecompute] = useState(false);

  const toggleMut = useMutation({
    mutationFn: ({ id, enabled }: { id: number; enabled: boolean }) =>
      updateRule(id, { enabled }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['pricing-formula-rules'] }),
  });

  const seedMut = useMutation({
    mutationFn: seedRules,
    onSuccess: (data) => {
      message.success(data.message || '内置规则已补充');
      qc.invalidateQueries({ queryKey: ['pricing-formula-rules'] });
    },
    onError: () => message.error('初始化失败'),
  });

  const columns = [
    {
      title: '排序',
      dataIndex: 'sort_order',
      width: 60,
      align: 'center' as const,
    },
    {
      title: '字段名',
      dataIndex: 'field_name',
      width: 160,
      render: (v: string, r: FormulaRule) => (
        <Space direction="vertical" size={0}>
          <Text strong>{r.display_name || v}</Text>
          <Text type="secondary" style={{ fontSize: 11 }}>
            {v}
          </Text>
        </Space>
      ),
    },
    {
      title: '公式表达式',
      dataIndex: 'expression',
      render: (v: string) => (
        <Text code style={{ fontSize: 12, wordBreak: 'break-all' }}>
          {v}
        </Text>
      ),
    },
    {
      title: '说明',
      dataIndex: 'description',
      render: (v: string | null) => (
        <Text type="secondary" style={{ fontSize: 12 }}>
          {v || '-'}
        </Text>
      ),
    },
    {
      title: '类型',
      dataIndex: 'is_builtin',
      width: 80,
      align: 'center' as const,
      render: (v: boolean) =>
        v ? <Tag color="blue">内置</Tag> : <Tag color="green">自定义</Tag>,
    },
    {
      title: '启用',
      dataIndex: 'enabled',
      width: 70,
      align: 'center' as const,
      render: (v: boolean, r: FormulaRule) => (
        <Switch
          size="small"
          checked={v}
          loading={toggleMut.isPending}
          onChange={(checked) => toggleMut.mutate({ id: r.id, enabled: checked })}
        />
      ),
    },
    {
      title: '操作',
      width: 80,
      align: 'center' as const,
      render: (_: any, r: FormulaRule) => (
        <Button size="small" icon={<FunctionOutlined />} onClick={() => setEditingRule(r)}>
          编辑
        </Button>
      ),
    },
  ];

  const enabledCount = rules.filter((r) => r.enabled).length;

  return (
    <div style={{ maxWidth: 1200, margin: '0 auto' }}>
      <Row align="middle" justify="space-between" style={{ marginBottom: 16 }}>
        <Col>
          <Title level={4} style={{ margin: 0 }}>
            <FunctionOutlined style={{ marginRight: 8 }} />
            定价公式规则
          </Title>
          <Text type="secondary">
            配置定价计算列的公式；修改后点「批量重算」立即生效
          </Text>
        </Col>
        <Col>
          <Space>
            <Popconfirm
              title="补充内置公式规则"
              description="将把缺失的内置公式规则插入到数据库（已有的不覆盖）"
              onConfirm={() => seedMut.mutate()}
            >
              <Button icon={<UndoOutlined />} loading={seedMut.isPending}>
                初始化内置公式
              </Button>
            </Popconfirm>
            <Button
              type="primary"
              icon={<ReloadOutlined />}
              onClick={() => setShowRecompute(true)}
            >
              批量重算
            </Button>
          </Space>
        </Col>
      </Row>

      <Row gutter={16} style={{ marginBottom: 16 }}>
        <Col span={6}>
          <Card size="small">
            <Descriptions column={1} size="small">
              <Descriptions.Item label="公式总数">{rules.length}</Descriptions.Item>
              <Descriptions.Item label="已启用">
                <Badge status="success" text={`${enabledCount} 条`} />
              </Descriptions.Item>
            </Descriptions>
          </Card>
        </Col>
        <Col span={18}>
          <Card size="small">
            <Text type="secondary">
              <strong>使用说明：</strong>
              公式用中文字段名书写（如 <code>物理总成本 / 0.4</code>）。
              支持 <code>+ - * /</code> 运算，以及{' '}
              <code>IF(条件, 真值, 假值)</code>、<code>SUM(字段1, 字段2, …)</code>、
              <code>MIN</code>、<code>MAX</code>、<code>ABS</code>、<code>ROUND</code> 函数。
              系统会按依赖关系自动排序计算（例如：先算「会计总成本」再算「毛利率」）。
              修改公式后点「验证」确认无误，再点「保存」；最后点「批量重算」让改动作用到所有 SKU。
            </Text>
          </Card>
        </Col>
      </Row>

      <Table
        dataSource={rules}
        columns={columns}
        rowKey="id"
        loading={isLoading}
        size="small"
        pagination={false}
        scroll={{ x: 900 }}
      />

      {editingRule && (
        <EditFormulaModal
          rule={editingRule}
          onClose={() => setEditingRule(null)}
          onSaved={() => {
            qc.invalidateQueries({ queryKey: ['pricing-formula-rules'] });
            setEditingRule(null);
          }}
        />
      )}

      {showRecompute && <RecomputeModal onClose={() => setShowRecompute(false)} />}
    </div>
  );
}
