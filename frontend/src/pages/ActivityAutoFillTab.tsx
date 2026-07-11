/**
 * 定价页「🚀 活动自动填写」Tab (2026-07-11) — 三步生成淘宝活动上传表 + 虚拟推送(预检)。
 *   Step1 批量改商品价格(SKU级)  正常不动价, 只有 618/双11 换SKU 时用 → 走改价台;
 *   Step2 批量单品立减           各档立减金额表 (与现有下载同源);
 *   Step3 批量报名活动价         大促报名表 (照千牛模板)。
 * 顶部「虚拟推送」= 生成前预检: 坏价产品 / 缺映射 / 15天最低价冲突 / 各步就绪计数, 不产文件不上传。
 * 最终上传淘宝的动作由运营手工做 (用户 2026-07-11: 不要真推送)。
 */
import { useState } from 'react';
import { Link } from 'react-router-dom';
import {
  Alert, Button, Card, Col, Divider, Modal, Row, Space, Statistic, Table, Tag, Typography, message,
} from 'antd';
import {
  DownloadOutlined, ExperimentOutlined, TableOutlined, WarningOutlined, CheckCircleOutlined,
  CloudUploadOutlined,
} from '@ant-design/icons';
import {
  downloadSingleItemDiscount, downloadPromoSignup, downloadSuperReduceSignup,
  fetchActivityPreflight, type ActivityPreflight,
  activityUploadStage, activityUploadCommit, type UploadStageResult,
} from '../api/catalog';

const XLSX_MIME = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet';
function triggerDownload(data: BlobPart, filename: string) {
  const url = URL.createObjectURL(new Blob([data], { type: XLSX_MIME }));
  const a = document.createElement('a');
  a.href = url; a.download = filename; a.click();
  URL.revokeObjectURL(url);
}

function StepCard({ n, title, tip, children }:
  { n: number; title: string; tip: string; children: React.ReactNode }) {
  return (
    <Card size="small" style={{ height: '100%' }}
      title={<Space><Tag color="blue">第 {n} 步</Tag><span>{title}</span></Space>}>
      <Typography.Paragraph type="secondary" style={{ fontSize: 12, minHeight: 40 }}>{tip}</Typography.Paragraph>
      <Space direction="vertical" style={{ width: '100%' }} size={8}>{children}</Space>
    </Card>
  );
}

export default function ActivityAutoFillTab() {
  const [busy, setBusy] = useState<string | null>(null);
  const [pre, setPre] = useState<ActivityPreflight | null>(null);
  const [preLoading, setPreLoading] = useState(false);

  const dl = async (key: string, title: string, filename: string, run: () => Promise<BlobPart>) => {
    setBusy(key);
    message.loading({ content: `正在生成「${title}」…`, key, duration: 0 });
    try {
      triggerDownload(await run(), filename);
      message.success({ content: `已下载「${title}」`, key, duration: 1.6 });
    } catch {
      message.error({ content: `「${title}」生成失败`, key });
    } finally { setBusy(null); }
  };

  const runPreflight = async () => {
    setPreLoading(true);
    message.loading({ content: '虚拟推送预检中（不产文件、不改数据）…', key: 'pre', duration: 0 });
    try {
      setPre(await fetchActivityPreflight(15));
      message.success({ content: '预检完成', key: 'pre', duration: 1.4 });
    } catch {
      message.error({ content: '预检失败', key: 'pre' });
    } finally { setPreLoading(false); }
  };

  // 缺映射(无淘宝SKUID)= 未上架, 按设计本就不推送, 不算问题 → 不纳入"全绿"判定 (用户 2026-07-11)
  const clean = pre && pre.bad_product_count === 0 && pre.conflict_count === 0;

  // ── 千牛上传 (stage → 比对表 → 确认 → commit) ──
  const [upChannel, setUpChannel] = useState<string | null>(null);
  const [upTier, setUpTier] = useState('big');
  const [staging, setStaging] = useState(false);
  const [committing, setCommitting] = useState(false);
  const [stageRes, setStageRes] = useState<UploadStageResult | null>(null);

  const doStage = async (channel: string, tier: string) => {
    setUpChannel(channel); setUpTier(tier); setStageRes(null); setStaging(true);
    message.loading({ content: '正在挂到千牛并预校验（不提交）…约30秒', key: 'up', duration: 0 });
    try {
      const r = await activityUploadStage(channel, tier);
      message.destroy('up');
      if (!r.ok) {
        message.error(r.need_scan ? '淘宝登录态过期，请先扫码' : (r.error || r.message || '挂载失败'));
        setUpChannel(null); return;
      }
      setStageRes(r);
    } catch {
      message.destroy('up'); message.error('上传服务未响应（确认 PC 上 Web-Agent 在线）'); setUpChannel(null);
    } finally { setStaging(false); }
  };

  const doCommit = async () => {
    if (!upChannel) return;
    setCommitting(true);
    message.loading({ content: '正在提交到千牛…', key: 'commit', duration: 0 });
    try {
      const r = await activityUploadCommit(upChannel, upTier);
      message.destroy('commit');
      if (r.ok && r.submitted) message.success(`已提交「${r.channel_name}」到千牛`);
      else message.error(r.error || '提交未成功，请到千牛核对');
      setUpChannel(null); setStageRes(null);
    } catch {
      message.destroy('commit'); message.error('提交失败');
    } finally { setCommitting(false); }
  };

  return (
    <Space direction="vertical" style={{ width: '100%' }} size="middle">
      <Alert
        type="info" showIcon
        message="活动自动填写 = 三步生成淘宝活动上传表；最终上传淘宝的动作由你们手工做（本页只生成 + 预检，不直接推送）。"
        description="生成表只推送有淘宝 SKUID 的 SKU（没上架的自动跳过）。点下方「虚拟推送(预检)」先体检：坏价产品自动排除、15 天最低价冲突会被淘宝判涨价。全绿了再逐步生成。"
      />

      {/* ── 虚拟推送(预检) ── */}
      <Card size="small" title={<Space><ExperimentOutlined /><b>虚拟推送（预检）</b>
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>不产文件 · 不改数据 · 不上传</Typography.Text></Space>}
        extra={<Button type="primary" icon={<ExperimentOutlined />} loading={preLoading} onClick={runPreflight}>
          {pre ? '重新预检' : '开始虚拟推送'}</Button>}>
        {!pre && <Typography.Text type="secondary">点右上角「开始虚拟推送」跑一次生成前体检。</Typography.Text>}
        {pre && (
          <Space direction="vertical" style={{ width: '100%' }} size="middle">
            <Row gutter={16}>
              <Col span={6}><Statistic title="坏价产品(排除)" value={pre.bad_product_count}
                valueStyle={{ color: pre.bad_product_count ? '#cf1322' : '#3f8600' }} suffix={`/${pre.bad_sku_count} SKU`} /></Col>
              <Col span={6}><Statistic title="未上架(不推送)" value={pre.unmapped_total}
                valueStyle={{ color: '#8c8c8c' }} suffix="SKU" /></Col>
              <Col span={6}><Statistic title="15天最低价冲突" value={pre.conflict_count}
                valueStyle={{ color: pre.conflict_count ? '#d46b08' : '#3f8600' }} suffix="SKU" /></Col>
              <Col span={6}><Statistic title="报名表可生成" value={pre.signup_big.rows}
                valueStyle={{ color: '#3f8600' }} suffix="行" /></Col>
            </Row>

            {clean && <Alert type="success" showIcon icon={<CheckCircleOutlined />}
              message="预检全绿：无坏价 / 无缺映射 / 无 15 天冲突，可放心逐步生成上传表。" />}

            {pre.bad_product_count > 0 && (
              <div>
                <Divider orientation="left" style={{ margin: '4px 0' }}>
                  <Typography.Text strong type="danger"><WarningOutlined /> 坏价产品（已自动从报名/立减表排除，改成真实价后自动纳入）</Typography.Text>
                </Divider>
                <Table size="small" pagination={false} rowKey="product_code"
                  dataSource={pre.bad_products}
                  columns={[
                    { title: '产品编码', dataIndex: 'product_code', width: 150 },
                    { title: '名称', dataIndex: 'name', ellipsis: true },
                    { title: 'SKU数', dataIndex: 'sku_count', width: 70, align: 'center' as const },
                    { title: '当前报名价', dataIndex: 'report_price', width: 100, render: (v: number | null) => v != null ? `¥${v}` : '-' },
                    { title: '判定原因', dataIndex: 'reason', ellipsis: true },
                  ]} />
              </div>
            )}

            {pre.conflict_count > 0 && (
              <div>
                <Divider orientation="left" style={{ margin: '4px 0' }}>
                  <Typography.Text strong style={{ color: '#d46b08' }}>
                    <WarningOutlined /> 15 天最低价冲突（计划大促到手 高于 近 {pre.floor_days} 天真实最低成交 → 淘宝会判"涨价"）</Typography.Text>
                </Divider>
                <Typography.Paragraph type="secondary" style={{ fontSize: 12, margin: '0 0 8px' }}>
                  多为小差(消费券拉低实付, 通常不计最低价)；大差(≥15%)可能是样品/异常单，需人工核。共 {pre.conflict_count} 个，显示差幅最大前 30。
                </Typography.Paragraph>
                <Table size="small" pagination={{ pageSize: 10 }} rowKey="sku_code"
                  dataSource={pre.conflicts.slice(0, 30)}
                  columns={[
                    { title: 'SKU编码', dataIndex: 'sku_code', width: 150 },
                    { title: '名称', dataIndex: 'name', ellipsis: true },
                    { title: '计划到手', dataIndex: 'planned_shoudao', width: 90, render: (v: number) => `¥${v}` },
                    { title: '近期最低成交', dataIndex: 'recent_min_paid', width: 110, render: (v: number) => `¥${v}` },
                    { title: '差幅', dataIndex: 'gap_pct', width: 80,
                      render: (v: number) => <Tag color={v >= 15 ? 'red' : 'orange'}>+{v}%</Tag> },
                  ]} />
              </div>
            )}

            {pre.unmapped_total > 0 && (
              <div>
                <Divider orientation="left" style={{ margin: '4px 0' }}>
                  <Typography.Text strong type="secondary">未上架 · 不推送（无淘宝 SKUID = 淘宝未上架，按设计不进任何表，正确行为）</Typography.Text>
                </Divider>
                <Typography.Paragraph type="secondary" style={{ fontSize: 12, margin: '0 0 8px' }}>
                  报名/立减表只推送有淘宝 SKUID 的 SKU；这些没上架的自动跳过，不用管。
                </Typography.Paragraph>
                <Space wrap size={[8, 8]}>
                  {Object.entries(pre.unmapped_by_product).map(([pc, n]) => (
                    <Tag key={pc}>{pc}: {n}</Tag>
                  ))}
                </Space>
              </div>
            )}
          </Space>
        )}
      </Card>

      {/* ── 三步生成 ── */}
      <Row gutter={[16, 16]}>
        <Col xs={24} lg={8}>
          <StepCard n={1} title="批量改商品价格（SKU级）"
            tip="正常不动价——报名价体系已保证到手价，商品标价平时不改。仅 618/双11 需要 15% 让利、换新 SKU 时才改价，走改价台操作。">
            <Link to="/shop-price-board"><Button icon={<TableOutlined />} block>打开改价台</Button></Link>
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              到手价不能在大促时提升；标价改动前先跑上方预检看 15 天冲突。
            </Typography.Text>
          </StepCard>
        </Col>

        <Col xs={24} lg={8}>
          <StepCard n={2} title="批量单品立减（减金额）"
            tip="SKU 级减金额表，表头对齐淘宝「单品立减」模板可直接上传。与活动同时生效当缓冲（护栏：档位≥中促到手红线）。">
            <Button icon={<DownloadOutlined />} block loading={busy === 'si-mid'}
              onClick={() => dl('si-mid', '单品立减·超级立减10%', '淘宝单品立减_超级立减10%.xlsx', () => downloadSingleItemDiscount('mid'))}>
              超级立减 10% <Tag color="green" style={{ marginLeft: 4 }}>减金额</Tag></Button>
            <Button icon={<DownloadOutlined />} block loading={busy === 'si-big'}
              onClick={() => dl('si-big', '单品立减·88VIP大促12%', '淘宝单品立减_88VIP大促12%.xlsx', () => downloadSingleItemDiscount('big'))}>
              88VIP大促 12% <Tag color="blue" style={{ marginLeft: 4 }}>减金额</Tag></Button>
            <Button icon={<DownloadOutlined />} block loading={busy === 'si-618'}
              onClick={() => dl('si-618', '单品立减·大促15%', '淘宝单品立减_大促15%.xlsx', () => downloadSingleItemDiscount('big618'))}>
              超级大促(618/双11) 15% <Tag color="purple" style={{ marginLeft: 4 }}>减金额</Tag></Button>
            <Divider style={{ margin: '8px 0' }} plain><Typography.Text type="secondary" style={{ fontSize: 12 }}>自动上传（预演·可比对）</Typography.Text></Divider>
            <Button type="primary" ghost icon={<CloudUploadOutlined />} block
              loading={staging && upChannel === 'single_item_discount'}
              onClick={() => doStage('single_item_discount', 'big')}>
              上传到千牛（88VIP12%，先比对）</Button>
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              点后自动挂到千牛+预校验，出比对表给你核对，确认了才真提交。
            </Typography.Text>
          </StepCard>
        </Col>

        <Col xs={24} lg={8}>
          <StepCard n={3} title="批量报名活动价"
            tip="照千牛「大促活动报名」模板生成，只填 商品ID/SKUID/活动价(报名价A)。超级立减10% 与 88VIP大促12% 同一个报名价。">
            <Button type="primary" icon={<DownloadOutlined />} block loading={busy === 'ps-big'}
              onClick={() => dl('ps-big', '大促报名·88VIP大促12%', '大促活动报名_88VIP大促12%.xlsx', () => downloadPromoSignup('big'))}>
              88VIP大促 12% 报名表</Button>
            <Button icon={<DownloadOutlined />} block loading={busy === 'ps-mid'}
              onClick={() => dl('ps-mid', '大促报名·超级立减10%', '大促活动报名_超级立减10%.xlsx', () => downloadPromoSignup('mid'))}>
              超级立减 10% 报名表</Button>
            <Button icon={<DownloadOutlined />} block loading={busy === 'ps-618'}
              onClick={() => dl('ps-618', '大促报名·超级大促15%', '大促活动报名_超级大促双11 15%.xlsx', () => downloadPromoSignup('big618'))}>
              超级大促 15% 报名表（换SKU）</Button>
            <Divider style={{ margin: '8px 0' }} plain><Typography.Text type="secondary" style={{ fontSize: 12 }}>超级立减活动（14列·只填补贴金额）</Typography.Text></Divider>
            <Button icon={<DownloadOutlined />} block loading={busy === 'sr'}
              onClick={() => dl('sr', '超级立减活动·补贴金额', '超级立减活动_补贴金额.xlsx', downloadSuperReduceSignup)}>
              超级立减活动 补贴金额表 <Tag color="green" style={{ marginLeft: 4 }}>A×10%</Tag></Button>
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              补贴金额 = 报名价A × 10%（到手 = 中促到手）。活动营销ID每期不同，上传前把此表 3 列贴进当期超级立减模板对应列。
            </Typography.Text>
          </StepCard>
        </Col>
      </Row>

      {/* ── 千牛上传·比对表确认 (stage 完成后弹出) ── */}
      <Modal
        open={!!stageRes}
        title={<Space><CloudUploadOutlined /><b>{stageRes?.channel_name} · 上传比对（确认前请核对）</b></Space>}
        width={820}
        onCancel={() => { setStageRes(null); setUpChannel(null); }}
        footer={[
          <Button key="cancel" onClick={() => { setStageRes(null); setUpChannel(null); }}>取消（不提交）</Button>,
          <Button key="ok" type="primary" danger loading={committing} icon={<CloudUploadOutlined />}
            onClick={doCommit}>✅ 确认最后一步上传（不可逆）</Button>,
        ]}
      >
        {stageRes && (
          <Space direction="vertical" style={{ width: '100%' }} size="middle">
            <Alert
              type={stageRes.validation?.failed ? 'warning' : 'success'} showIcon
              message={`千牛预校验：${stageRes.validation?.ok ?? '?'} 条成功` +
                (stageRes.validation?.failed ? `，${stageRes.validation.failed} 条失败（下方比对表核对，失败的不会上）` : '，全部通过')}
              description="文件已挂到千牛、尚未提交。核对下方「系统要的价」无误后，点确认才真提交。"
            />
            {stageRes.screenshot_base64 && (
              <img alt="千牛已挂文件截图" style={{ width: '100%', border: '1px solid #eee', borderRadius: 6 }}
                src={`data:image/png;base64,${stageRes.screenshot_base64}`} />
            )}
            <Table size="small" rowKey="sku_code" pagination={{ pageSize: 8 }}
              dataSource={stageRes.compare_rows || []}
              columns={[
                { title: 'SKU编码', dataIndex: 'sku_code', width: 150 },
                { title: '淘宝SKUID', dataIndex: 'taobao_sku_id', width: 130 },
                { title: '名称', dataIndex: 'name', ellipsis: true },
                { title: () => stageRes.compare_rows?.[0]?.value_label || '系统值', dataIndex: 'system_value',
                  width: 100, render: (v: number | null) => v != null ? `¥${v}` : '-' },
                { title: '目标到手', dataIndex: 'target_shoudao', width: 100,
                  render: (v: number | null) => v != null ? `¥${v}` : '-' },
              ]} />
          </Space>
        )}
      </Modal>
    </Space>
  );
}
