/**
 * 定价页「表格下载」Tab — 各类批量下载表格做成说明卡片 + 导出按钮, 按「千牛后台分类」分组。
 * 每张表都是【点下载时实时生成】(后端现算, 成本/售价一改就跟着变), 不是缓存文件。
 * 后续新增: 往对应分类的 cards 里加一项即可。
 */
import { useState } from 'react';
import { Alert, Button, Card, Col, Divider, Row, Space, Tag, Typography, message } from 'antd';
import { DownloadOutlined } from '@ant-design/icons';
import {
  downloadSingleItemDiscount, downloadPromoSignup, downloadSignupForm, downloadPricingCatalog,
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
type DownloadGroup = { title: string; note: string; cards: DownloadCard[] };

// 按千牛后台的功能分类分组
const GROUPS: DownloadGroup[] = [
  {
    title: '① 单品立减 的导入表格',
    note: '千牛后台 →「单品立减」(SKU级·减钱)。列 = 商品id / SKU_ID / 优惠值(立减金额)，表头对齐淘宝模板，可直接导入上传。缺淘宝 SKU_ID 的行自动跳过。',
    cards: [
      {
        key: 'si-mid', title: '超级立减', tag: '10%', color: 'green',
        desc: '日常「超级立减」场，官方立减 10%，目标 = 中促到手。表里填的是【立减金额】。',
        filename: '淘宝单品立减_超级立减10%.xlsx', run: () => downloadSingleItemDiscount('mid'),
      },
      {
        key: 'si-big', title: '88VIP大促', tag: '12%', color: 'blue',
        desc: '88VIP 大促场，官方立减 12%，目标 = 大促到手。表里填的是【立减金额】。',
        filename: '淘宝单品立减_88VIP大促12%.xlsx', run: () => downloadSingleItemDiscount('big'),
      },
      {
        key: 'si-618', title: '超级大促(双11)', tag: '15%', color: 'purple',
        desc: '618 / 双11 场，官方立减 15%，目标 = 大促到手。官方减得多，立减金额比 88VIP 档更少。',
        filename: '淘宝单品立减_大促15%.xlsx', run: () => downloadSingleItemDiscount('big618'),
      },
    ],
  },
  {
    title: '② 大促活动 的导入表格（活动报名）',
    note: '千牛后台 →「大促活动报名」(SKU级)。每行只填 商品ID / SKUID / 活动价，其余列留空。真 SKU 三个档位的活动价都等于 ERP 日常价；只有定制占位 SKU 按不同官方力度套保护上限。',
    cards: [
      {
        key: 'ps-mid', title: '超级立减', tag: '10%', color: 'green',
        desc: '真 SKU 活动价 = ERP 日常价；平台官方10%再叠加本档单品立减，对齐中促到手。',
        filename: '大促活动报名_超级立减10%.xlsx', run: () => downloadPromoSignup('mid'),
      },
      {
        key: 'ps-big', title: '88VIP大促', tag: '12%', color: 'blue',
        desc: '真 SKU 活动价 = ERP 日常价；平台官方12%再叠加本档单品立减，对齐大促到手。',
        filename: '大促活动报名_88VIP大促12%.xlsx', run: () => downloadPromoSignup('big'),
      },
      {
        key: 'ps-618', title: '超级大促(双11)', tag: '15%', color: 'purple',
        desc: '真 SKU 活动价 = ERP 日常价；平台官方15%再叠加本档单品立减，对齐大促到手。',
        filename: '大促活动报名_超级大促双11 15%.xlsx', run: () => downloadPromoSignup('big618'),
      },
    ],
  },
  {
    title: '③ 参考 / 汇总表（系统内部看，不上传千牛）',
    note: '给运营 / 同事对照用的汇总表，不是上传模板。',
    cards: [
      {
        key: 'signup', title: '活动报名表(带图)', tag: '汇总', color: 'orange',
        desc: '三档活动报名价、目标到手和单品立减金额逐列展示，与自动报名同一计算源。',
        filename: '畔色活动报名表.xlsx', run: downloadSignupForm,
      },
      {
        key: 'catalog', title: '定价图册(带图全字段)', tag: '综合', color: 'default',
        desc: '一 SKU 一行 + 产品图 + 全字段（价格 / 成本 / 活动价 / 报名价 / 降价金额），存档用。',
        filename: '畔色定价图册.xlsx', run: downloadPricingCatalog,
      },
    ],
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
      <Alert
        type="info" showIcon
        message="每张表点下载时实时生成（成本 / 售价一改就跟着变）。上传类表格表头对齐淘宝 / 千牛模板，可直接导入。"
        description="唯一口径：真 SKU 活动报名价 = ERP 日常价；官方立减与单品立减必须叠加，到手 = 活动价 − 官方立减 − 单品立减。占位 SKU 只用保护报名价，不生成单品立减。"
      />
      {GROUPS.map((g) => (
        <div key={g.title}>
          <Divider orientation="left" style={{ margin: '4px 0 12px' }}>
            <Typography.Text strong>{g.title}</Typography.Text>
          </Divider>
          <Typography.Paragraph type="secondary" style={{ fontSize: 12, margin: '0 0 10px' }}>
            {g.note}
          </Typography.Paragraph>
          <Row gutter={[16, 16]}>
            {g.cards.map((c) => (
              <Col key={c.key} xs={24} sm={12} lg={8}>
                <Card size="small" style={{ height: '100%' }}
                  title={<Space size={6}><Tag color={c.color} style={{ marginInlineEnd: 0 }}>{c.tag}</Tag>{c.title}</Space>}>
                  <Space direction="vertical" style={{ width: '100%' }} size="small">
                    <Typography.Paragraph style={{ minHeight: 66, color: '#475569', margin: 0, fontSize: 13 }}>
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
        </div>
      ))}
    </Space>
  );
}
