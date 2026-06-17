import { useMemo, useState } from 'react';
import {
  Alert,
  AutoComplete,
  Button,
  Card,
  Col,
  DatePicker,
  Form,
  Input,
  InputNumber,
  Modal,
  Popconfirm,
  Row,
  Select,
  Space,
  Statistic,
  Table,
  Tag,
  Typography,
  Upload,
  message,
} from 'antd';
import { DeleteOutlined, EditOutlined, PlusOutlined, UploadOutlined } from '@ant-design/icons';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import dayjs from 'dayjs';
import {
  AccountBalanceRow,
  BalanceUpsertPayload,
  DeriveOpeningResult,
  deleteAccountByName,
  deleteBalance,
  deriveOpeningBalance,
  importAccountBalancesCsv,
  listBalances,
  upsertBalance,
} from '../api/finance';
import PresetTable from '../components/PresetTable';

// 常见账户名 (支付宝企业号自动取数写「支付宝-企业账号」, 以它为准); 可自由输入新名字
const ACCOUNT_SUGGESTIONS = [
  '支付宝-企业账号', '主力号', '佳宝号', '个体户私账',
  '聚合余额', '推广余额', '平台保证金', '银行卡',
];

// 余额是某天手填的快照 → 新鲜度按「统计日期」算, 而非入库时间
function freshness(asOf: string | null): { label: string; color: string } {
  if (!asOf) return { label: '无统计日期', color: 'default' };
  const days = dayjs().diff(dayjs(asOf), 'day');
  if (days <= 40) return { label: `${days} 天前`, color: 'green' };
  if (days <= 100) return { label: `${days} 天前 · 偏旧`, color: 'orange' };
  return { label: `${days} 天前 · 过期`, color: 'red' };
}

const fmt = (v: string | null | undefined) =>
  v == null || v === '' ? '-' : `¥${Number(v).toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

// Plan F10: 期初余额倒推 Modal — 最近快照 − 区间Σ流水 → 目标日期初, 可一键写入
function DeriveOpeningModal({ open, onClose, onWritten }: {
  open: boolean; onClose: () => void; onWritten: () => void;
}) {
  const [account, setAccount] = useState('支付宝-企业账号');
  const [target, setTarget] = useState(() => dayjs().startOf('month'));
  const [result, setResult] = useState<DeriveOpeningResult | null>(null);
  const deriveMut = useMutation({
    mutationFn: () => deriveOpeningBalance(account, target.format('YYYY-MM-DD')),
    onSuccess: setResult,
    onError: (e: any) => message.error(e?.response?.data?.detail ?? '倒推失败'),
  });
  const writeMut = useMutation({
    mutationFn: () => upsertBalance({
      account_name: account,
      period_year: target.year(),
      period_month: target.month() + 1,
      as_of_date: target.format('YYYY-MM-DD'),
      opening_balance: String(result!.derived_balance),
      closing_balance: String(result!.derived_balance),
      remark: `期初倒推: 按 ${result!.snapshot_date} 快照 − 区间流水推得`,
    }),
    onSuccess: () => { message.success('已写入余额快照'); onWritten(); onClose(); },
    onError: (e: any) => message.error(e?.response?.data?.detail ?? '写入失败'),
  });
  return (
    <Modal open={open} onCancel={onClose} title="期初余额倒推工具" footer={null} width={520}>
      <Space direction="vertical" style={{ width: '100%' }} size="middle">
        <Space>
          <AutoComplete
            value={account} onChange={setAccount} style={{ width: 140 }}
            options={ACCOUNT_SUGGESTIONS.map((a) => ({ value: a }))}
          />
          <DatePicker value={target} onChange={(d) => d && setTarget(d)} />
          <Button type="primary" loading={deriveMut.isPending} onClick={() => deriveMut.mutate()}>倒推</Button>
        </Space>
        {result && !result.ok && <Alert type="warning" showIcon message={result.message} />}
        {result?.ok && (
          <Card size="small">
            <p>快照: {result.snapshot_date} 余额 ¥{Math.round(result.snapshot_balance!).toLocaleString()}</p>
            <p>区间净流水: ¥{Math.round(result.interval_net_flow!).toLocaleString()}（{result.days_with_flows}/{result.span_days} 天有流水）</p>
            <p><b>{result.target_date} 推得期初: ¥{Number(result.derived_balance).toLocaleString()}</b></p>
            {(result.gap_days ?? 0) > 0 && <Alert type="info" showIcon message={result.hint} style={{ marginBottom: 8 }} />}
            <Button type="primary" loading={writeMut.isPending} onClick={() => writeMut.mutate()}>
              一键写入余额快照
            </Button>
          </Card>
        )}
      </Space>
    </Modal>
  );
}

export default function AccountBalancesPage() {
  const qc = useQueryClient();
  const [year, setYear] = useState<number | undefined>(undefined);
  const [editing, setEditing] = useState<AccountBalanceRow | null>(null);
  const [modalOpen, setModalOpen] = useState(false);
  const [deriveOpen, setDeriveOpen] = useState(false);
  const [form] = Form.useForm();

  const { data, isLoading } = useQuery({
    queryKey: ['account-balances', year],
    queryFn: () => listBalances(year ? { year } : {}),
  });

  const rows = data ?? [];

  // 年份下拉选项
  const years = useMemo(() => {
    const s = new Set<number>();
    rows.forEach((r) => s.add(r.period_year));
    return Array.from(s).sort((a, b) => b - a);
  }, [rows]);

  // 每个账户取最新一期 → 账上现金合计
  const latestPerAccount = useMemo(() => {
    const m = new Map<string, AccountBalanceRow>();
    rows.forEach((r) => {
      const prev = m.get(r.account_name);
      const key = (x: AccountBalanceRow) => x.period_year * 100 + x.period_month;
      if (!prev || key(r) > key(prev)) m.set(r.account_name, r);
    });
    return Array.from(m.values()).sort((a, b) => a.account_name.localeCompare(b.account_name, 'zh-CN'));
  }, [rows]);

  const totalCash = useMemo(
    () => latestPerAccount.reduce((s, r) => s + Number(r.closing_balance || 0), 0),
    [latestPerAccount],
  );

  const saveMut = useMutation({
    mutationFn: (payload: BalanceUpsertPayload) => upsertBalance(payload),
    onSuccess: () => {
      message.success('已保存');
      setModalOpen(false);
      setEditing(null);
      qc.invalidateQueries({ queryKey: ['account-balances'] });
      qc.invalidateQueries({ queryKey: ['cash-flow'] }); // 余额变了, 剩余流水跟着刷新
    },
    onError: (e: any) => message.error(e?.response?.data?.detail ?? '保存失败'),
  });

  const delMut = useMutation({
    mutationFn: (id: number) => deleteBalance(id),
    onSuccess: () => {
      message.success('已删除');
      qc.invalidateQueries({ queryKey: ['account-balances'] });
      qc.invalidateQueries({ queryKey: ['cash-flow'] });
    },
    onError: (e: any) => message.error(e?.response?.data?.detail ?? '删除失败'),
  });

  // 整账删除 (高危): 需二次确认 — 输入账户名 + 登录密码
  const [delAccount, setDelAccount] = useState<string | null>(null);
  const [delConfirmName, setDelConfirmName] = useState('');
  const [delPassword, setDelPassword] = useState('');
  const delAccountMut = useMutation({
    mutationFn: ({ accountName, password }: { accountName: string; password: string }) =>
      deleteAccountByName(accountName, password),
    onSuccess: (r) => {
      message.success(`已删除账户『${r.deleted_account}』共 ${r.deleted_rows} 条记录`);
      setDelAccount(null); setDelConfirmName(''); setDelPassword('');
      qc.invalidateQueries({ queryKey: ['account-balances'] });
      qc.invalidateQueries({ queryKey: ['cash-flow'] });
    },
    onError: (e: any) => message.error(e?.response?.data?.detail ?? '整账删除失败'),
  });

  const importMut = useMutation({
    mutationFn: (file: File) => importAccountBalancesCsv(file),
    onSuccess: (r) => {
      Modal.info({
        title: '账户余额导入结果',
        content: (
          <Space direction="vertical">
            <div>新增/更新：<Tag color="green">{r.inserted}</Tag></div>
            <div>无效行：<Tag color="red">{r.skipped_invalid}</Tag></div>
            {r.errors?.length > 0 && <Alert type="error" message={r.errors.join('\n')} />}
          </Space>
        ),
      });
      qc.invalidateQueries({ queryKey: ['account-balances'] });
      qc.invalidateQueries({ queryKey: ['cash-flow'] });
    },
    onError: (e: any) => message.error(e?.response?.data?.detail ?? '导入失败'),
  });

  const openCreate = () => {
    setEditing(null);
    form.resetFields();
    form.setFieldsValue({ period: dayjs(), as_of_date: dayjs() });
    setModalOpen(true);
  };

  const openEdit = (row: AccountBalanceRow) => {
    setEditing(row);
    form.setFieldsValue({
      account_name: row.account_name,
      account_no: row.account_no ?? undefined,
      period: dayjs(`${row.period_year}-${String(row.period_month).padStart(2, '0')}-01`),
      as_of_date: row.as_of_date ? dayjs(row.as_of_date) : undefined,
      opening_balance: row.opening_balance ? Number(row.opening_balance) : undefined,
      income: row.income ? Number(row.income) : undefined,
      expense: row.expense ? Number(row.expense) : undefined,
      closing_balance: Number(row.closing_balance),
      remark: row.remark ?? undefined,
    });
    setModalOpen(true);
  };

  const submit = async () => {
    const v = await form.validateFields();
    const period = v.period as dayjs.Dayjs;
    const payload: BalanceUpsertPayload = {
      account_name: v.account_name.trim(),
      account_no: v.account_no?.trim() || undefined,
      period_year: period.year(),
      period_month: period.month() + 1,
      as_of_date: v.as_of_date ? (v.as_of_date as dayjs.Dayjs).format('YYYY-MM-DD') : undefined,
      opening_balance: v.opening_balance != null ? String(v.opening_balance) : undefined,
      income: v.income != null ? String(v.income) : undefined,
      expense: v.expense != null ? String(v.expense) : undefined,
      closing_balance: String(v.closing_balance ?? 0),
      remark: v.remark?.trim() || undefined,
    };
    saveMut.mutate(payload);
  };

  const columns = [
    { title: '账户', dataIndex: 'account_name', width: 120, fixed: 'left' as const },
    { title: '账号', dataIndex: 'account_no', width: 150, ellipsis: true, render: (v: string | null) => v || '-' },
    {
      title: '期间', key: 'period', width: 100,
      render: (_: unknown, r: AccountBalanceRow) => `${r.period_year}-${String(r.period_month).padStart(2, '0')}`,
    },
    {
      title: '统计日期', dataIndex: 'as_of_date', width: 160,
      render: (v: string | null) => {
        const f = freshness(v);
        return (
          <Space size={4}>
            <span>{v || '-'}</span>
            <Tag color={f.color}>{f.label}</Tag>
          </Space>
        );
      },
    },
    { title: '期初', dataIndex: 'opening_balance', width: 120, align: 'right' as const, render: fmt },
    { title: '收入', dataIndex: 'income', width: 120, align: 'right' as const, render: fmt },
    { title: '支出', dataIndex: 'expense', width: 120, align: 'right' as const, render: fmt },
    {
      title: '期末余额', dataIndex: 'closing_balance', width: 130, align: 'right' as const,
      render: (v: string) => <span style={{ fontWeight: 600 }}>{fmt(v)}</span>,
    },
    { title: '备注', dataIndex: 'remark', ellipsis: true, render: (v: string | null) => v || '-' },
    {
      title: '操作', key: 'op', width: 110, fixed: 'right' as const,
      render: (_: unknown, r: AccountBalanceRow) => (
        <Space size={4}>
          <Button size="small" icon={<EditOutlined />} onClick={() => openEdit(r)} />
          <Popconfirm title="删除这条余额记录?" onConfirm={() => delMut.mutate(r.id)}>
            <Button size="small" danger icon={<DeleteOutlined />} />
          </Popconfirm>
        </Space>
      ),
    },
  ];

  return (
    <Space direction="vertical" style={{ width: '100%' }} size="middle">
      <Space style={{ justifyContent: 'space-between', width: '100%' }} align="start">
        <div>
          <Typography.Title level={4} style={{ margin: 0 }}>账户余额</Typography.Title>
          <Typography.Text type="secondary">
            各账户期末余额快照 (支付宝企业/爱群/聚合/推广/银行卡…)。余额是某天手填的, 新鲜度按「统计日期」算。
          </Typography.Text>
        </div>
        <Space>
          <Button type="primary" icon={<PlusOutlined />} onClick={openCreate}>录入余额</Button>
          <Upload
            accept=".csv,.xlsx,.xls"
            showUploadList={false}
            beforeUpload={(file) => { importMut.mutate(file as File); return false; }}
          >
            <Button icon={<UploadOutlined />} loading={importMut.isPending}>CSV 导入</Button>
          </Upload>
          <Button onClick={() => setDeriveOpen(true)}>期初倒推</Button>
        </Space>
      </Space>

      <DeriveOpeningModal
        open={deriveOpen}
        onClose={() => setDeriveOpen(false)}
        onWritten={() => qc.invalidateQueries({ queryKey: ['account-balances'] })}
      />

      {/* 账上现金合计 (每账户取最新一期) */}
      <Card size="small">
        <Row gutter={[16, 16]} align="middle">
          <Col>
            <Statistic
              title="账上现金合计 (各账户最新期末)"
              value={totalCash}
              precision={2}
              prefix="¥"
              valueStyle={{ color: totalCash >= 0 ? '#3f8600' : '#cf1322' }}
            />
          </Col>
          {latestPerAccount.map((r) => (
            <Col key={r.account_name}>
              <Statistic
                title={(
                  <Space size={4}>
                    {r.account_name}
                    <Tag color={freshness(r.as_of_date).color}>{r.period_year}-{String(r.period_month).padStart(2, '0')}</Tag>
                    <Button
                      size="small"
                      type="text"
                      danger
                      icon={<DeleteOutlined />}
                      title="删除整个账户 (需密码二次确认)"
                      onClick={() => { setDelAccount(r.account_name); setDelConfirmName(''); setDelPassword(''); }}
                    />
                  </Space>
                )}
                value={Number(r.closing_balance)}
                precision={2}
                prefix="¥"
                valueStyle={{ fontSize: 18 }}
              />
            </Col>
          ))}
        </Row>
      </Card>

      <Space>
        <span>年份</span>
        <Select
          style={{ width: 140 }}
          allowClear
          placeholder="全部年份"
          value={year}
          onChange={(v) => setYear(v)}
          options={years.map((y) => ({ label: `${y} 年`, value: y }))}
        />
      </Space>

      <PresetTable<AccountBalanceRow>
        tableKey="account_balance"
        rowKey="id"
        loading={isLoading}
        dataSource={rows}
        columns={columns as any}
        pagination={{ defaultPageSize: 100, showSizeChanger: true, pageSizeOptions: [20, 50, 100, 200] }}
        size="middle"
        scroll={{ x: 1200 }}
      />

      <Modal
        open={modalOpen}
        title={editing ? `编辑余额 — ${editing.account_name}` : '录入账户余额'}
        onCancel={() => { setModalOpen(false); setEditing(null); }}
        onOk={submit}
        confirmLoading={saveMut.isPending}
        okText="保存"
        destroyOnClose
      >
        <Form form={form} layout="vertical" requiredMark="optional">
          <Form.Item name="account_name" label="账户名" rules={[{ required: true, message: '请填账户名' }]}>
            <AutoComplete
              options={ACCOUNT_SUGGESTIONS.map((a) => ({ value: a }))}
              placeholder="如 企业号 / 爱群号 / 聚合余额 / 银行卡"
              filterOption={(input, opt) => (opt?.value ?? '').includes(input)}
            />
          </Form.Item>
          <Form.Item name="account_no" label="账号 (可选)">
            <Input placeholder="手机号/卡号尾号等, 仅备注用" />
          </Form.Item>
          <Row gutter={12}>
            <Col span={12}>
              <Form.Item name="period" label="所属月份" rules={[{ required: true, message: '请选月份' }]}>
                <DatePicker.MonthPicker style={{ width: '100%' }} placeholder="YYYY-MM" />
              </Form.Item>
            </Col>
            <Col span={12}>
              <Form.Item name="as_of_date" label="统计日期 (这条余额是哪天的)">
                <DatePicker style={{ width: '100%' }} placeholder="如 2026-05-20" />
              </Form.Item>
            </Col>
          </Row>
          <Form.Item
            name="closing_balance"
            label="期末余额"
            rules={[{ required: true, message: '请填期末余额' }]}
          >
            <InputNumber style={{ width: '100%' }} addonBefore="¥" step={100} placeholder="账户当前余额数字" />
          </Form.Item>
          <Row gutter={12}>
            <Col span={8}>
              <Form.Item name="opening_balance" label="期初 (可选)">
                <InputNumber style={{ width: '100%' }} addonBefore="¥" />
              </Form.Item>
            </Col>
            <Col span={8}>
              <Form.Item name="income" label="收入 (可选)">
                <InputNumber style={{ width: '100%' }} addonBefore="¥" />
              </Form.Item>
            </Col>
            <Col span={8}>
              <Form.Item name="expense" label="支出 (可选)">
                <InputNumber style={{ width: '100%' }} addonBefore="¥" />
              </Form.Item>
            </Col>
          </Row>
          <Form.Item name="remark" label="备注 (可选)">
            <Input.TextArea rows={2} />
          </Form.Item>
        </Form>
      </Modal>

      {/* 整账删除二次确认: 输入账户名 + 登录密码 (高危, 用户拍板 2026-06-17) */}
      <Modal
        open={!!delAccount}
        title={<span style={{ color: '#cf1322' }}>⚠️ 整账删除 —「{delAccount}」</span>}
        onCancel={() => setDelAccount(null)}
        okText="确认删除"
        okButtonProps={{
          danger: true,
          disabled: delConfirmName.trim() !== (delAccount ?? '') || !delPassword,
          loading: delAccountMut.isPending,
        }}
        onOk={() => delAccount && delAccountMut.mutate({ accountName: delAccount, password: delPassword })}
        destroyOnClose
      >
        <Alert type="error" showIcon style={{ marginBottom: 12 }}
          message="将永久删除该账户的全部余额记录, 不可恢复"
          description="此操作仅用于清理重复/废弃账户。请再次输入账户名并填登录密码以确认。" />
        <Form layout="vertical">
          <Form.Item label={`再次输入账户名「${delAccount}」`}>
            <Input value={delConfirmName} onChange={(e) => setDelConfirmName(e.target.value)}
                   placeholder="逐字输入账户名" autoComplete="off" />
          </Form.Item>
          <Form.Item label="你的登录密码">
            <Input.Password value={delPassword} onChange={(e) => setDelPassword(e.target.value)}
                            placeholder="输入登录密码确认" autoComplete="new-password"
                            onPressEnter={() => {
                              if (delAccount && delConfirmName.trim() === delAccount && delPassword) {
                                delAccountMut.mutate({ accountName: delAccount, password: delPassword });
                              }
                            }} />
          </Form.Item>
        </Form>
      </Modal>
    </Space>
  );
}
