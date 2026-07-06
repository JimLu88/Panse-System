/**
 * 定价页「表格下载」Tab (2026-07-06) — 把各类批量下载表格做成说明卡片 + 导出按钮, 集中一处。
 * 每张表都是【点下载时实时生成】(后端现算, 成本/售价一改就跟着变), 不是缓存文件。
 * 后续新增批量下载: 往 CARDS 里加一项即可。
 */
import { useState } from 'react';
import { Button, Card, Col, Row, Space, Tag, Typography, message } from 'antd';
import { DownloadOutlined } from '@ant-design/icons';
import {
  downloadSingleItemDiscount, downloadSignupForm, downloadPricingCatalog,
} from '../api/catalog';

const XLSX_MIME = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet';

function triggerDownload(data: BlobPart, filename: string) {
  const url = URL.createObjectURL(new Blob([data], { type: XLSX_MIME }));
  const a = document.createElement('a');
  a.href = url; a.download = filename; a.click();
  URL.revokeObjectURL(url);
}

type DownloadCard = {
  key: string; title: string; tag: string; color: string;
  desc: string; filename: string; run: () => Promise<BlobPart>;
};

// 淘宝「单品立减」三档: SKU级别减钱(模板明确 sku级别不支持打折), 表头对齐淘宝批量模板可直接上传。
const CARDS: DownloadCard[] = [
  {
    key: 'si-mid', title: '淘宝单品立减 · 超级立减', tag: '10%', color: 'green',
    desc: '日常「超级立减」场（官方立减 10%，目标 = 中促价）。SKU 级别减钱：列 = 商品id / SKU_ID / 立减金额(元)，表头与淘宝批量模板逐字一致，可直接导入淘宝上传。缺淘宝 SKU_ID 的会跳过。',
    filename: '淘宝单品立减_超级立减10%.xlsx', run: () => downloadSingleItemDiscount('mid'),
  },
  {
    key: 'si-big', title: '淘宝单品立减 · 88VIP大促', tag: '12%', color: 'blue',
    desc: '88VIP 大促场（官方立减 12%，目标 = 大促价）。SKU 级别减钱，直接导入淘宝上传。',
    filename: '淘宝单品立减_88VIP大促12%.xlsx', run: () => downloadSingleItemDiscount('big'),
  },
  {
    key: 'si-618', title: '淘宝单品立减 · 大促(618/双11)', tag: '15%', color: 'purple',
    desc: '618 / 双11 大促场（官方立减 15%，目标 = 大促价·同价）。SKU 级别减钱，直接导入淘宝上传。官方减得更多，单品立减金额会比 88VIP 档更少。',
    filename: '淘宝单品立减_大促15%.xlsx', run: () => downloadSingleItemDiscount('big618'),
  },
  {
    key: 'signup', title: '活动报名表(带图)', tag: '总表', color: 'orange',
    desc: '给同事填活动价的精简总表：各档到手 + 报名价(88VIP大促 / 超大促618) + 单品立减(折 + 立减金额)，各档一起看一起填。',
    filename: '畔色活动报名表.xlsx', run: downloadSignupForm,
  },
  {
    key: 'catalog', title: '定价图册(带图全字段)', tag: '综合', color: 'default',
    desc: '一 SKU 一行 + 产品图 + 全字段（价格 / 成本 / 活动价 / 报名价），综合参考、存档用。',
    filename: '畔色定价图册.xlsx', run: downloadPricingCatalog,
  },
];

export default function PricingDownloadsTab() {
  const [busy, setBusy] = useState<string | null>(null);
  const go = async (c: DownloadCard) => {
    setBusy(c.key);
    message.loading({ content: `正在生成「${c.title}」…`, key: c.key, duration: 0 });
    try {
      const data = await c.run();
      triggerDownload(data, c.filename);
      message.success({ content: `已下载「${c.title}」`, key: c.key, duration: 1.6 });
    } catch {
      message.error({ content: `「${c.title}」生成失败`, key: c.key });
    } finally {
      setBusy(null);
    }
  };
  return (
    <Space direction="vertical" style={{ width: '100%' }} size="middle">
      <Typography.Text type="secondary">
        每张表都是<b>点下载时实时生成</b>（后端现算，成本 / 售价一改就跟着变）。
        「单品立减」三张按活动力度分开，表头与淘宝批量模板一致，可直接上传。
      </Typography.Text>
      <Row gutter={[16, 16]}>
        {CARDS.map((c) => (
          <Col key={c.key} xs={24} sm={12} lg={8}>
            <Card size="small" style={{ height: '100%' }}
              title={<Space size={6}><Tag color={c.color} style={{ marginInlineEnd: 0 }}>{c.tag}</Tag>{c.title}</Space>}>
              <Space direction="vertical" style={{ width: '100%' }} size="small">
                <Typography.Paragraph style={{ minHeight: 88, color: '#475569', margin: 0, fontSize: 13 }}>
                  {c.desc}
                </Typography.Paragraph>
                <Button type="primary" icon={<DownloadOutlined />} block
                  loading={busy === c.key} onClick={() => go(c)}>
                  导出下载
                </Button>
              </Space>
            </Card>
          </Col>
        ))}
      </Row>
    </Space>
  );
}
