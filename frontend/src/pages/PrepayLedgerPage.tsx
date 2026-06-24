/**
 * 代付台账 — 补单佣金 / 补单快递 / 售后 的"实际打款"明细 (这三类对账的进项来源)。
 * 导入后, 对账页/大盘的 refill_commission_payout / refill_express_payout / aftersales_payout
 * 三条规则即可用台账实付 ↔ 订单应摊 逐月对账。
 */
import { useState } from 'react';
import {
  Alert, Card, Col, Row, Segmented, Space, Statistic, Table, Tag, Typography, Upload, message,
} from 'antd';
import { UploadOutlined } from '@ant-design/icons';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { PrepayRow, fetchPrepaySummary, importPrepay, listPrepay } from '../api/settlements';
import PresetTable from '../components/PresetTable';

const CATS = [
  { value: 'refill_commission', label: '补单佣金' },
  { value: 'refill_express', label: '补单快递' },
  { value: 'aftersales', label: '售后' },
];
const CAT_LABEL: Record<string, string> = Object.fromEntries(CATS.map((c) => [c.value, c.label]));

export default function PrepayLedgerPage() {
  const qc = useQueryClient();
  const [category, setCategory] = useState<string>('refill_commission');

  const { data: sum } = useQuery({ queryKey: ['prepay-summary'], queryFn: fetchPrepaySummary });
  const { data: rows = [], isLoading } = useQuery({
    queryKey: ['prepay-list', category],
    queryFn: () => listPrepay(category),
  });

  const importMut = useMutation({
    mutationFn: (file: File) => importPrepay(file, category),
    onSuccess: (r) => {
      if (r.errors?.length) { message.error(r.errors[0]); return; }
      message.success(`导入完成: 新增 ${r.inserted} / 重复 ${r.skipped_duplicate} / 无效 ${r.skipped_invalid}`
        + (r.unmapped_columns?.length ? ` · 未识别列: ${r.unmapped_columns.join(',')}` : '')
        + (r.duplicate_upload ? ' · 该文件曾上传过' : ''));
      qc.invalidateQueries({ queryKey: ['prepay-list'] });
      qc.invalidateQueries({ queryKey: ['prepay-summary'] });
    },
    onError: (e: any) => message.error(e?.response?.data?.detail ?? '导入失败'),
  });

  return (
    <Space direction="vertical" style={{ width: '100%' }} size="middle">
      <Typography.Title level={4} style={{ margin: 0 }}>代付台账</Typography.Title>
      <Alert
        type="info" showIcon
        message="补单佣金 / 补单快递 / 售后 的实际打款台账 (对账进项来源)"
        description="这三类此前没有「实际打款」数据, 只有订单侧「应摊」, 故对不上。在此导入实际代付/打款明细(列: 打款日期/打款流水号/订单号/金额/收款方/备注, 按流水号去重), 对账页与大盘的「补单佣金/补单快递/售后」三条规则即可逐月对账(实付↔应摊)。"
      />

      {sum && (
        <Row gutter={12}>
          <Col xs={12} sm={8} md={6}><Card size="small"><Statistic title="台账总笔数" value={sum.total} /></Card></Col>
          {CATS.map((c) => (
            <Col xs={12} sm={8} md={6} key={c.value}>
              <Card size="small">
                <Statistic title={c.label} value={sum.by_category[c.value]?.amount ?? 0} precision={0} prefix="¥" />
                <div style={{ color: '#999', fontSize: 12 }}>{sum.by_category[c.value]?.count ?? 0} 笔</div>
              </Card>
            </Col>
          ))}
        </Row>
      )}

      <Card size="small" title="导入代付台账">
        <Space wrap>
          <span>类型:</span>
          <Segmented value={category} onChange={(v) => setCategory(v as string)} options={CATS} />
          <Upload accept=".csv" showUploadList={false} beforeUpload={(file) => { importMut.mutate(file as File); return false; }}>
            <Tag color="blue" style={{ cursor: 'pointer', padding: '4px 10px' }}>
              <UploadOutlined /> 上传 {CAT_LABEL[category]} 打款 CSV
            </Tag>
          </Upload>
          {importMut.isPending && <span>导入中…</span>}
        </Space>
      </Card>

      <Card size="small" title={`${CAT_LABEL[category]} 明细`}>
        <PresetTable<PrepayRow>
          tableKey="prepay_ledger"
          rowKey="id" size="small" loading={isLoading} dataSource={rows}
          pagination={{ defaultPageSize: 100, showSizeChanger: true, pageSizeOptions: [20, 50, 100, 200], showTotal: (t) => `共 ${t} 笔` }}
          columns={[
            { title: '打款日期', dataIndex: 'pay_date', width: 110, render: (v) => v || '-' },
            { title: '打款流水号', dataIndex: 'pay_no', width: 200, render: (v) => v || '-' },
            { title: '订单号', dataIndex: 'order_no', width: 180, render: (v) => v || '-' },
            { title: '金额', dataIndex: 'amount', width: 110, align: 'right' as const, render: (v: number) => `¥${Number(v).toFixed(2)}` },
            { title: '收款方', dataIndex: 'payee', width: 120, render: (v) => v || '-' },
            { title: '备注', dataIndex: 'remark', ellipsis: true, render: (v) => v || '-' },
          ]}
        />
      </Card>
    </Space>
  );
}
