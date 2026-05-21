import { useState } from 'react';
import { AutoComplete, Button, Card, Form, Input, Space, Tag, Typography } from 'antd';
import { useQuery } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import { fuzzyMatch } from '../api/client';
import { CustomizationDialog } from '../components/CustomizationDialog';
import { FirstVisitTip } from '../components/FirstVisitTip';

export default function CustomizationPage() {
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
      <Typography.Title level={4} style={{ margin: 0 }}>
        尺寸微定制 <Tag color="orange">业务需求 §2</Tag>
      </Typography.Title>

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
        onConfirmed={(customSku) => {
          setOpen(false);
          // 跳转到 BOM 查看页看新生成的 BOM (这里跳产品页因为 BOM 是按 product_code 看的)
          nav('/products');
        }}
      />
    </Space>
  );
}
