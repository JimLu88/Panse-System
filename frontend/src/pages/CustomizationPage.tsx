import { useState } from 'react';
import {
  Alert,
  AutoComplete,
  Button,
  Card,
  Descriptions,
  Form,
  Input,
  Space,
  Spin,
  Table,
  Tabs,
  Tag,
  Typography,
  Upload,
  message,
} from 'antd';
import { InboxOutlined, RobotOutlined } from '@ant-design/icons';
import { useMutation, useQuery } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import { AiQuoteResult, aiCustomizationQuote, fuzzyMatch } from '../api/client';
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
        ]}
      />
    </Space>
  );
}
