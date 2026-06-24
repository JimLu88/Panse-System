import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  Alert,
  Button,
  Dropdown,
  Form,
  Input,
  InputNumber,
  Modal,
  Segmented,
  Select,
  Space,
  Table,
  Tag,
  Typography,
  Upload,
  message,
} from 'antd';
import { UploadOutlined } from '@ant-design/icons';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  AlipayFlow,
  CsvImportReport,
  importAlipayCsv,
  listAlipayFlows,
} from '../api/client';
import { api } from '../api/client';
import FullColumnView from '../components/FullColumnView';
import PresetTable from '../components/PresetTable';

// 用户拍板 (2026-06-11): 常用只有 企业号/主力号; 爱群号/佳宝号/个体户私账 折叠收纳
const MAIN_ACCOUNTS = ['企业号', '主力号'];
const FOLDED_ACCOUNTS = ['爱群号', '佳宝号', '个体户私账'];
const ACCOUNTS = [...MAIN_ACCOUNTS, ...FOLDED_ACCOUNTS];

// 核销类型中文 (用户要求: 界面不出现英文)
const RECON_LABELS: Record<string, string> = {
  customer_payment: '客户回款', factory_payment: '工厂付款', promotion: '推广',
  logistics: '物流', salary: '工资外包', refund_in: '退款回流', refund_out: '退款支出',
  internal_transfer: '内部转移', platform_fee: '平台费', platform_deposit: '平台保证金',
  opening: '期初余额', aftersales: '售后',
};
const RECON_COLOR: Record<string, string> = {
  customer_payment: 'green', factory_payment: 'volcano', promotion: 'purple',
  logistics: 'cyan', refund_out: 'red', refund_in: 'lime',
  internal_transfer: 'default', platform_fee: 'orange', platform_deposit: 'gold',
};

// 关联订单号净化: 取去空格后最长的数字串 (≥12 位), 如 T200P27018466…001 070 → 2701846…070
const extractOrderNo = (raw: string | null): string | null => {
  if (!raw) return null;
  const runs = raw.replace(/\s+/g, '').match(/\d{12,}/g);
  return runs?.length ? runs.reduce((a, b) => (b.length > a.length ? b : a)) : null;
};

export default function AlipayPage() {
  const qc = useQueryClient();
  const nav = useNavigate();
  const [account, setAccount] = useState<string>(ACCOUNTS[0]);
  const [importResult, setImportResult] = useState<CsvImportReport | null>(null);
  const [viewMode, setViewMode] = useState<'curated' | 'full'>('curated');
  const [q, setQ] = useState('');

  const { data, isLoading } = useQuery({
    queryKey: ['alipay', account, q],
    queryFn: () => listAlipayFlows({ account, q: q || undefined, limit: 500 }),
  });

  const importMut = useMutation({
    mutationFn: (file: File) => importAlipayCsv(file, account),
    onSuccess: (r) => {
      setImportResult(r);
      qc.invalidateQueries({ queryKey: ['alipay'] });
    },
    onError: (e: any) => message.error(e?.response?.data?.detail ?? '导入失败'),
  });

  // 手动修正流水 (系统入口, 替代手填库): 爱群号等脏流水改 类型/金额/时间
  const [editFlow, setEditFlow] = useState<AlipayFlow | null>(null);
  const [form] = Form.useForm();
  const editMut = useMutation({
    mutationFn: (vals: any) => api.patch(`/api/finance/alipay-flows/${editFlow!.id}`, vals),
    onSuccess: () => {
      message.success('已修正，对账已重算');
      setEditFlow(null);
      qc.invalidateQueries({ queryKey: ['alipay'] });
    },
    onError: (e: any) => message.error(e?.response?.data?.detail ?? '修正失败'),
  });
  const openEdit = (f: AlipayFlow) => {
    setEditFlow(f);
    form.setFieldsValue({
      reconciliation_type: f.reconciliation_type ?? '',
      amount: f.amount,
      transaction_time: f.transaction_time ? f.transaction_time.slice(0, 16).replace('T', ' ') : '',
    });
  };
  const submitEdit = () => {
    form.validateFields().then((vals) => {
      const payload: any = { reconciliation_type: vals.reconciliation_type ?? '', amount: vals.amount };
      const t = (vals.transaction_time || '').trim();
      if (t) payload.transaction_time = t.replace(' ', 'T');
      editMut.mutate(payload);
    });
  };

  const columns = [
    { title: '时间', dataIndex: 'transaction_time', width: 140,
      // 去掉 ISO 串里的 T / Z (T00:00:00 是日期时间分隔符, 用户看着别扭)
      render: (v: string | null) => v ? v.slice(0, 16).replace('T', ' ') : '-' },
    { title: '流水号', dataIndex: 'transaction_no', width: 230, ellipsis: true,
      render: (v: string) => <code style={{ fontSize: 11 }}>{v}</code> },
    { title: '类型', dataIndex: 'transaction_type', width: 90 },
    { title: '对象', dataIndex: 'counterparty', ellipsis: true },
    {
      title: '金额',
      dataIndex: 'amount',
      width: 110,
      align: 'right' as const,
      render: (v: string) => (
        <span style={{ color: Number(v) >= 0 ? '#3f8600' : '#cf1322', fontWeight: 600 }}>
          ¥{v}
        </span>
      ),
    },
    { title: '余额', dataIndex: 'balance', width: 110, align: 'right' as const },
    {
      title: '核销',
      dataIndex: 'reconciliation_type',
      width: 110,
      render: (v: string | null) => (v
        ? <Tag color={RECON_COLOR[v] ?? 'blue'}>{RECON_LABELS[v] ?? v}</Tag>
        : <Tag>未分类</Tag>),
    },
    { title: '关联订单', dataIndex: 'related_order_no', width: 190,
      // 提取净订单号 (去 T200P 前缀/空格), 点击跳订单总表搜索该单
      render: (v: string | null) => {
        const no = extractOrderNo(v);
        if (!no) return '-';
        return (
          <Tag color="geekblue" style={{ cursor: 'pointer' }}
               onClick={() => nav(`/orders?q=${no}`)} title={`原始: ${v}`}>
            {no}
          </Tag>
        );
      } },
    { title: '操作', dataIndex: 'id', width: 70, fixed: 'right' as const,
      render: (_: number, row: AlipayFlow) => (
        <Button size="small" type="link" style={{ padding: 0 }} onClick={() => openEdit(row)}>修正</Button>
      ) },
  ];

  return (
    <Space direction="vertical" style={{ width: '100%' }} size="middle">
      <Space style={{ justifyContent: 'space-between', width: '100%' }}>
        <Typography.Title level={4} style={{ margin: 0 }}>
          支付宝流水 (9a-9e)
        </Typography.Title>
        <Upload
          accept=".csv"
          showUploadList={false}
          beforeUpload={(file) => {
            importMut.mutate(file as File);
            return false;
          }}
        >
          <Button icon={<UploadOutlined />} loading={importMut.isPending}>
            CSV 导入到「{account}」
          </Button>
        </Upload>
      </Space>

      <Space wrap>
        <Segmented
          value={viewMode}
          onChange={(v) => setViewMode(v as 'curated' | 'full')}
          options={[
            { label: '精选视图（可编辑）', value: 'curated' },
            { label: '全部列', value: 'full' },
          ]}
        />
        {viewMode === 'curated' && (
          <>
            <Segmented
              value={account}
              onChange={(v) => setAccount(v as string)}
              options={[
                ...MAIN_ACCOUNTS,
                // 选中了折叠账号时临时显示, 否则不占位
                ...(FOLDED_ACCOUNTS.includes(account) ? [account] : []),
              ].map((a) => ({ label: a, value: a }))}
            />
            <Dropdown
              menu={{
                items: FOLDED_ACCOUNTS.map((a) => ({ key: a, label: a })),
                onClick: ({ key }) => setAccount(key),
              }}
            >
              <Button size="small">未来停用账号 ▾</Button>
            </Dropdown>
            <Input.Search
              allowClear
              placeholder="搜 备注/对方/流水号（如 壹米运费）"
              defaultValue={q}
              onSearch={(v) => setQ(v.trim())}
              style={{ width: 280 }}
            />
            {q && <Tag color="blue">搜索: {q}（{data?.length ?? 0} 条）</Tag>}
          </>
        )}
      </Space>

      {viewMode === 'full' && <FullColumnView entity="alipay_flow" defaultShowAll />}

      {viewMode === 'curated' && (
      <PresetTable<AlipayFlow>
        tableKey="alipay"
        rowKey="id"
        loading={isLoading}
        dataSource={data}
        columns={columns as any}
        pagination={{ defaultPageSize: 30, showSizeChanger: true, pageSizeOptions: [20, 50, 100, 200] }}
        size="middle"
      />
      )}

      <Modal
        open={!!importResult}
        title="CSV 导入结果"
        onCancel={() => setImportResult(null)}
        footer={[<Button key="ok" type="primary" onClick={() => setImportResult(null)}>知道了</Button>]}
      >
        {importResult && (
          <Space direction="vertical">
            <div>新增：<Tag color="green">{importResult.inserted}</Tag></div>
            <div>重复：<Tag>{importResult.skipped_duplicate}</Tag></div>
            <div>无效：<Tag color="red">{importResult.skipped_invalid}</Tag></div>
            {importResult.errors.length > 0 && (
              <Alert type="error" showIcon message={importResult.errors.join('\n')} />
            )}
          </Space>
        )}
      </Modal>

      <Modal
        open={!!editFlow}
        title="修正流水"
        onCancel={() => setEditFlow(null)}
        onOk={submitEdit}
        confirmLoading={editMut.isPending}
        okText="保存"
        cancelText="取消"
        destroyOnClose
      >
        {editFlow && (
          <Space direction="vertical" style={{ width: '100%' }} size="small">
            <Alert type="info" showIcon style={{ marginBottom: 8 }}
              message={`${editFlow.account} · ${editFlow.counterparty ?? ''} · 备注: ${editFlow.remark ?? '-'}`}
              description="用于纠正爱群号等脏流水：金额符号（支出应为负）、补交易时间、改核销类型。改后自动重算对账，并留修改档案可回溯。" />
            <Form form={form} layout="vertical">
              <Form.Item label="核销类型" name="reconciliation_type">
                <Select
                  allowClear
                  options={[{ value: '', label: '未分类（清空）' },
                    ...Object.entries(RECON_LABELS).map(([v, l]) => ({ value: v, label: l }))]} />
              </Form.Item>
              <Form.Item label="金额（支出为负，如 -14540）" name="amount">
                <InputNumber stringMode step={0.01} style={{ width: '100%' }} />
              </Form.Item>
              <Form.Item label="交易时间（无日期可补，如 2026-01-31）" name="transaction_time">
                <Input placeholder="YYYY-MM-DD 或 YYYY-MM-DD HH:mm" />
              </Form.Item>
            </Form>
          </Space>
        )}
      </Modal>
    </Space>
  );
}
