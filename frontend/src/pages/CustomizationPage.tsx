import { useState } from 'react';
import {
  Alert,
  AutoComplete,
  Button,
  Card,
  Descriptions,
  Form,
  Input,
  InputNumber,
  Select,
  Space,
  Spin,
  Table,
  Tabs,
  Tag,
  Typography,
  Upload,
  message,
} from 'antd';
import { InboxOutlined, RobotOutlined, SettingOutlined } from '@ant-design/icons';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import {
  AiQuoteResult,
  QuoteConfig,
  aiCustomizationQuote,
  fuzzyMatch,
  getQuoteConfig,
  updateQuoteConfig,
} from '../api/client';
import { CustomizationDialog } from '../components/CustomizationDialog';
import { FirstVisitTip } from '../components/FirstVisitTip';

const { Dragger } = Upload;

function AiQuoteTab() {
  const [result, setResult] = useState<AiQuoteResult | null>(null);

  const quoteMut = useMutation({
    mutationFn: (file: File) => aiCustomizationQuote(file),
    onSuccess: (res) => {
      setResult(res);
      if (res.error && !res.ai_used) {
        message.warning('AI 未配置，已返回基础估价');
      }
    },
    onError: (e: any) => message.error(e?.response?.data?.detail ?? '报价失败'),
  });

  return (
    <Space direction="vertical" style={{ width: '100%' }} size="middle">
      <Alert
        type="info"
        showIcon
        icon={<RobotOutlined />}
        message="AI 截图报价"
        description="上传客户发来的定制截图（含尺寸/材质要求）→ AI 自动识别要求并估算价格。AI 未配置时回退手动向导。"
      />
      <Card size="small">
        <Dragger
          accept="image/*"
          showUploadList={false}
          beforeUpload={(f) => { quoteMut.mutate(f); return false; }}
          disabled={quoteMut.isPending}
          multiple={false}
        >
          <p className="ant-upload-drag-icon"><InboxOutlined /></p>
          <p className="ant-upload-text">
            {quoteMut.isPending ? <Spin tip="AI 分析中..." /> : '点击或拖入定制截图'}
          </p>
          <p className="ant-upload-hint">支持 JPG / PNG / WEBP</p>
        </Dragger>
      </Card>

      {result && (
        <Card
          size="small"
          title={
            <Space>
              <span>报价结果</span>
              {result.ai_used && <Tag color="blue">AI 分析</Tag>}
              {result.model && <Tag color="default" style={{ fontSize: 11 }}>{result.model}</Tag>}
            </Space>
          }
        >
          {result.error && (
            <Alert type="warning" message="AI 提示" description={result.error} style={{ marginBottom: 8 }} />
          )}
          <Descriptions size="small" column={2} bordered style={{ marginBottom: 12 }}>
            <Descriptions.Item label="匹配产品">{result.base_product || '-'}</Descriptions.Item>
            <Descriptions.Item label="基础 SKU">{result.base_sku || '-'}</Descriptions.Item>
            <Descriptions.Item label="尺寸分类">{result.base_size || '-'}</Descriptions.Item>
            <Descriptions.Item label="估算总价">
              {result.est_price != null
                ? <Tag color="green" style={{ fontSize: 14, fontWeight: 600 }}>¥{result.est_price.toLocaleString()}</Tag>
                : <Tag color="default">暂无估价</Tag>}
            </Descriptions.Item>
          </Descriptions>

          {result.changes.length > 0 && (
            <div style={{ marginBottom: 12 }}>
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>识别到的变更：</Typography.Text>
              <div style={{ marginTop: 4 }}>
                {result.changes.map((c, i) => <Tag key={i} color="orange">{c}</Tag>)}
              </div>
            </div>
          )}

          {result.breakdown.length > 0 && (
            <Table
              size="small"
              pagination={false}
              rowKey="label"
              dataSource={result.breakdown}
              columns={[
                { title: '项目', dataIndex: 'label' },
                {
                  title: '金额',
                  dataIndex: 'amount',
                  align: 'right' as const,
                  render: (v: number) => (
                    <span style={{ color: v >= 0 ? '#3f8600' : '#cf1322', fontWeight: 500 }}>
                      {v >= 0 ? '+' : ''}¥{v.toLocaleString()}
                    </span>
                  ),
                },
                { title: '说明', dataIndex: 'note', ellipsis: true },
              ]}
            />
          )}
        </Card>
      )}
    </Space>
  );
}

function ManualQuoteTab() {
  const nav = useNavigate();
  const [skuCode, setSkuCode] = useState('');
  const [orderNo, setOrderNo] = useState('');
  const [search, setSearch] = useState('');
  const [open, setOpen] = useState(false);

  const { data: candidates } = useQuery({
    queryKey: ['match', 'sku', search],
    queryFn: () => fuzzyMatch(search, 'sku', 10),
    enabled: search.length > 0,
  });

  return (
    <Space direction="vertical" style={{ width: '100%' }} size="middle">
      <FirstVisitTip
        storageKey="customization"
        title="如何用"
        description={
          <ol style={{ marginBottom: 0 }}>
            <li>选一个已有 SKU 作为「基础」(普通链接的标准型号)</li>
            <li>填客户要求的目标尺寸 (长/宽/高 mm) — 只填要变的</li>
            <li>预览 BOM 哪些料会随尺寸变, 用户确认</li>
            <li>系统生成 改NN 后缀的新 SKU + 克隆 BOM, 自动入库</li>
          </ol>
        }
      />

      <Card>
        <Form layout="vertical">
          <Form.Item label="基础 SKU 编码 (搜索)" required>
            <AutoComplete
              value={skuCode}
              onChange={setSkuCode}
              onSearch={setSearch}
              options={(candidates ?? []).map((c) => ({
                value: c.code,
                label: `${c.code}  ${c.name}`,
              }))}
              placeholder="按编码或名称搜索, 如 榉木无边床"
              style={{ width: 480 }}
            />
          </Form.Item>
          <Form.Item label="关联订单号 (可选)">
            <Input
              value={orderNo}
              onChange={(e) => setOrderNo(e.target.value)}
              placeholder="如有客户订单, 把订单号填进来留痕"
              style={{ width: 320 }}
            />
          </Form.Item>
          <Button
            type="primary"
            disabled={!skuCode}
            onClick={() => setOpen(true)}
          >
            开始定制
          </Button>
        </Form>
      </Card>

      <CustomizationDialog
        open={open}
        baseSkuCode={skuCode}
        orderNo={orderNo || undefined}
        onCancel={() => setOpen(false)}
        onConfirmed={() => {
          setOpen(false);
          nav('/products');
        }}
      />
    </Space>
  );
}

function QuoteSettingsTab() {
  const qc = useQueryClient();
  const { data: cfg, isLoading } = useQuery({ queryKey: ['quote-config'], queryFn: getQuoteConfig });
  const [draft, setDraft] = useState<QuoteConfig | null>(null);
  const c = draft ?? cfg ?? null;

  const saveMut = useMutation({
    mutationFn: (patch: Partial<QuoteConfig>) => updateQuoteConfig(patch),
    onSuccess: (res) => {
      message.success('参数已保存');
      qc.setQueryData(['quote-config'], res);
      setDraft(null);
    },
    onError: (e: any) => message.error(e?.response?.data?.detail ?? '保存失败'),
  });

  if (isLoading || !c) return <Spin />;

  const upd = (patch: Partial<QuoteConfig>) => setDraft({ ...c, ...patch });
  const updLabor = (type: string, idx: number, v: number) => {
    const labor = { ...c.labor, [type]: [...c.labor[type]] };
    labor[type][idx] = v;
    upd({ labor });
  };
  const updRule = (type: string, idx: number, v: number) => {
    const size_rules = { ...c.size_rules, [type]: [...c.size_rules[type]] };
    size_rules[type][idx] = v;
    upd({ size_rules });
  };

  const laborRows = Object.keys(c.labor).map((t) => ({
    key: t, type: t,
    small: c.labor[t][0], mid: c.labor[t][1], big: c.labor[t][2],
    ruleBig: c.size_rules[t]?.[0] ?? 0, ruleMid: c.size_rules[t]?.[1] ?? 0,
  }));

  const numCell = (val: number, onCh: (v: number) => void) => (
    <InputNumber size="small" value={val} min={0} style={{ width: 80 }}
      onChange={(v) => onCh(Number(v ?? 0))} />
  );

  return (
    <Space direction="vertical" style={{ width: '100%' }} size="middle">
      <Alert type="info" showIcon message="这里改的是全定制报价用的参数, 保存后立即生效。人工费按「品类 × 小/中/大」, 大小由长度阈值判定。" />
      <Card size="small" title="利润系数 / 投影对照">
        <Space wrap size="large">
          <span>工厂利润系数 {numCell(c.factory_profit_rate, (v) => upd({ factory_profit_rate: v }))}</span>
          <span>畔色利润系数 {numCell(c.panse_profit_rate, (v) => upd({ panse_profit_rate: v }))}</span>
          <span>投影口径
            <Select size="small" style={{ width: 110, marginLeft: 6 }} value={c.projection_type}
              onChange={(v) => upd({ projection_type: v })}
              options={[{ value: 'front', label: '正面(宽×高)' }, { value: 'top', label: '俯视(宽×深)' }]} />
          </span>
          <span>投影系数(元/㎡) {numCell(c.projection_rate, (v) => upd({ projection_rate: v }))}</span>
        </Space>
        <div style={{ marginTop: 12 }}>
          打包费 小 {numCell(c.packing[0], (v) => upd({ packing: [v, c.packing[1], c.packing[2]] }))}
          {' '}中 {numCell(c.packing[1], (v) => upd({ packing: [c.packing[0], v, c.packing[2]] }))}
          {' '}大 {numCell(c.packing[2], (v) => upd({ packing: [c.packing[0], c.packing[1], v] }))}
        </div>
      </Card>

      <Card size="small" title="人工费表 + 大小判定 (按品类)">
        <Table
          size="small" pagination={false} dataSource={laborRows} scroll={{ y: 360 }}
          columns={[
            { title: '品类', dataIndex: 'type', width: 90, fixed: 'left' as const },
            { title: '小型', width: 95, render: (_: any, r: any) => numCell(r.small, (v) => updLabor(r.type, 0, v)) },
            { title: '中型', width: 95, render: (_: any, r: any) => numCell(r.mid, (v) => updLabor(r.type, 1, v)) },
            { title: '大型', width: 95, render: (_: any, r: any) => numCell(r.big, (v) => updLabor(r.type, 2, v)) },
            { title: '大型阈值(m)', width: 110, render: (_: any, r: any) => numCell(r.ruleBig, (v) => updRule(r.type, 0, v)) },
            { title: '中型阈值(m)', width: 110, render: (_: any, r: any) => numCell(r.ruleMid, (v) => updRule(r.type, 1, v)) },
          ] as any}
        />
        <div style={{ marginTop: 6, color: '#999', fontSize: 12 }}>
          长度 ≥ 大型阈值 → 大型; ≥ 中型阈值 → 中型; 否则小型。
        </div>
      </Card>

      <Space>
        <Button type="primary" disabled={!draft} loading={saveMut.isPending}
          onClick={() => draft && saveMut.mutate(draft)}>保存</Button>
        <Button disabled={!draft} onClick={() => setDraft(null)}>撤销改动</Button>
      </Space>
    </Space>
  );
}

export default function CustomizationPage() {
  return (
    <Space direction="vertical" style={{ width: '100%' }} size="middle">
      <Typography.Title level={4} style={{ margin: 0 }}>
        尺寸微定制 <Tag color="orange">业务需求 §2</Tag>
      </Typography.Title>
      <Tabs
        items={[
          { key: 'ai', label: <><RobotOutlined /> AI 截图报价</>, children: <AiQuoteTab /> },
          { key: 'manual', label: '手动定制向导', children: <ManualQuoteTab /> },
          { key: 'settings', label: <><SettingOutlined /> 报价参数设置</>, children: <QuoteSettingsTab /> },
        ]}
      />
    </Space>
  );
}
