/**
 * 结算对账 — 导入 微信/聚合 (billDetail) 与 支付宝 结算账单, 看每笔订单的实际到账与扣费。
 * 逐笔对账视图(订单↔实收)后续接入。
 */
import { useState } from 'react';
import {
  Alert, Card, Col, Row, Segmented, Space, Statistic, Table, Tag, Typography, Upload, message,
} from 'antd';
import { UploadOutlined } from '@ant-design/icons';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  SettlementRow, fetchSettlementSummary, importSettlementBill, listSettlements,
} from '../api/settlements';

const SOURCE_LABEL: Record<string, string> = { wechat: '微信/聚合', alipay: '支付宝' };

export default function SettlementsPage() {
  const qc = useQueryClient();
  const [source, setSource] = useState<'wechat' | 'alipay'>('wechat');

  const { data: sum } = useQuery({ queryKey: ['settlement-summary'], queryFn: fetchSettlementSummary });
  const { data: rows = [], isLoading } = useQuery({ queryKey: ['settlements'], queryFn: () => listSettlements(300) });

  const importMut = useMutation({
    mutationFn: (file: File) => importSettlementBill(file, source),
    onSuccess: (r) => {
      if (r.error) { message.error(r.error); return; }
      message.success(`导入完成:新增 ${r.inserted ?? 0} 笔 / 更新 ${r.updated ?? 0} 笔`);
      qc.invalidateQueries({ queryKey: ['settlements'] });
      qc.invalidateQueries({ queryKey: ['settlement-summary'] });
    },
    onError: (e: any) => message.error(e?.response?.data?.detail ?? '导入失败'),
  });

  return (
    <Space direction="vertical" style={{ width: '100%' }} size="middle">
      <Typography.Title level={4} style={{ margin: 0 }}>结算对账</Typography.Title>
      <Alert
        type="info" showIcon
        message="导入淘宝结算账单(billDetail)"
        description="微信支付的订单货款走「聚合账户」,导出的 billDetail 即聚合收支明细;支付宝支付的订单在支付宝企业号流水。两者都有 订单号/入账时间/收款/扣款,导入后即可逐笔对账(订单实付 ↔ 实际到账,差额=平台软件费/消费券代付/2%补贴税)。"
      />

      <Card size="small" title="导入账单">
        <Space wrap>
          <span>来源:</span>
          <Segmented
            value={source}
            onChange={(v) => setSource(v as 'wechat' | 'alipay')}
            options={[{ label: '微信 / 聚合 (billDetail)', value: 'wechat' }, { label: '支付宝 结算', value: 'alipay' }]}
          />
          <Upload
            accept=".xlsx,.xls"
            showUploadList={false}
            beforeUpload={(file) => { importMut.mutate(file as File); return false; }}
          >
            <Tag color="blue" style={{ cursor: 'pointer', padding: '4px 10px' }}>
              <UploadOutlined /> 选择 billDetail 文件上传
            </Tag>
          </Upload>
          {importMut.isPending && <span>导入中…</span>}
        </Space>
      </Card>

      {sum && (
        <Row gutter={12}>
          <Col span={5}><Card size="small"><Statistic title="结算笔数" value={sum.count} /></Card></Col>
          <Col span={5}><Card size="small"><Statistic title="覆盖订单数" value={sum.orders} /></Card></Col>
          <Col span={5}><Card size="small"><Statistic title="收款合计" value={sum.income} precision={2} prefix="¥" /></Card></Col>
          <Col span={5}><Card size="small"><Statistic title="扣款合计" value={sum.expense} precision={2} prefix="¥" valueStyle={{ color: '#cf1322' }} /></Card></Col>
          <Col span={4}><Card size="small"><Statistic title="净到账" value={sum.net} precision={2} prefix="¥" valueStyle={{ color: '#389e0d' }} /></Card></Col>
        </Row>
      )}

      <Card size="small" title="结算明细(近 300 笔)">
        <Table<SettlementRow>
          rowKey="id" size="small" loading={isLoading} dataSource={rows}
          pagination={{ pageSize: 50 }}
          columns={[
            { title: '来源', dataIndex: 'source', width: 90, render: (v) => <Tag>{SOURCE_LABEL[v] ?? v}</Tag> },
            { title: '入账时间', dataIndex: 'settle_time', width: 160, render: (v) => v ? new Date(v).toLocaleString('zh-CN') : <Tag color="warning">无日期</Tag> },
            { title: '淘宝订单编号', dataIndex: 'order_no', width: 180, render: (v) => v || '-' },
            { title: '入账类型', dataIndex: 'entry_type', width: 100 },
            { title: '收款', dataIndex: 'income', width: 100, align: 'right' as const, render: (v) => v > 0 ? `¥${v.toFixed(2)}` : '-' },
            { title: '扣款', dataIndex: 'expense', width: 100, align: 'right' as const, render: (v) => v > 0 ? <span style={{ color: '#cf1322' }}>¥{v.toFixed(2)}</span> : '-' },
            { title: '业务描述', dataIndex: 'description', ellipsis: true },
          ]}
        />
      </Card>
    </Space>
  );
}
