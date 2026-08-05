/**
 * 活动「高级 · 手动」面板 —— 从 ActivityAutoFillTab 抽出的存量手动工具（生命周期向导的兜底通道）：
 *   各档下载表 / 单步上传千牛(stage 比对 → 确认 commit) / 标价对照表与改价指引。
 * 走的是既有 /api/pricing/activity-upload 通道，与新 /api/campaigns 向导互不影响。
 */
import { useState } from 'react';
import { Link } from 'react-router-dom';
import {
  Alert, Button, Card, Col, Divider, Image, Input, Modal, Popconfirm, Row, Space, Statistic,
  Table, Tag, Typography, message,
} from 'antd';
import {
  CheckCircleOutlined, CloudUploadOutlined, DownloadOutlined, ExperimentOutlined, TableOutlined,
} from '@ant-design/icons';
import type { Dayjs } from 'dayjs';
import {
  activityUploadCommit, activityUploadCommitStatus, activityUploadStage, applySkuRotation,
  downloadProductPriceQuickEdit, downloadPromoSignup, downloadSingleItemDiscount,
  downloadSuperReduceSignup, fetchSkuRotation,
  type SkuRotationPlan, type UploadCommitStatus, type UploadStageResult,
} from '../api/catalog';
import { triggerBlobDownload } from '../utils/download';

type Tier = 'mid' | 'big' | 'big618';
type CompareRow = NonNullable<UploadStageResult['compare_rows']>[number];

// 千牛 excel导出【下载中心】—— 同事按对照表改一口价; 需看千牛现价可来这导出
const QN_EXPORT_CENTER = 'https://item.upload.taobao.com/taobao/excel/tool/render.htm?tab=export';

function showPriceGuide() {
  Modal.info({
    title: '标价（一口价）怎么改 · 给我和同事看',
    width: 580,
    okText: '知道了',
    content: (
      <div style={{ lineHeight: 2 }}>
        <p style={{ margin: '4px 0' }}><b>1. 下载「对照表」= 改成什么价</b><br />
          点本页 <b>下载对照表</b> 按钮，每个在售 SKU 一行「应改一口价 = 日常价 ÷ 0.75」。
          发给同事，照着在千牛把每个 SKU 的一口价改成这个数。</p>
        <p style={{ margin: '4px 0' }}><b>2. 千牛现价从哪导（可选核对）</b><br />
          千牛商品页：全选 → 更多批量操作 → excel商品批量导出；约 2 分钟后到{' '}
          <a href={QN_EXPORT_CENTER} target="_blank" rel="noreferrer">千牛下载中心（点这里）</a>{' '}
          下载导出表，与对照表比对。<br />
          <Typography.Text type="secondary">千牛单次导出上限约 20 个、无跨页全选 —— 商品多就分页多导几次。</Typography.Text></p>
        <p style={{ margin: '4px 0' }}><b>3. 改完</b> → 回向导重跑预检。批量改也可用 <b>改价台</b>。</p>
      </div>
    ),
  });
}

export default function ActivityManualPanel({ range }: { range: [Dayjs | null, Dayjs | null] }) {
  const [busy, setBusy] = useState<string | null>(null);

  const dl = async (key: string, title: string, filename: string, run: () => Promise<BlobPart>) => {
    setBusy(key);
    message.loading({ content: `正在生成「${title}」…`, key, duration: 0 });
    try {
      triggerBlobDownload(await run(), filename);
      message.success({ content: `已下载「${title}」`, key, duration: 1.6 });
    } catch {
      message.error({ content: `「${title}」生成失败`, key });
    } finally { setBusy(null); }
  };

  // ── 千牛上传 (stage → 比对表 → 确认 → commit) ──
  const [upChannel, setUpChannel] = useState<string | null>(null);
  const [upTier, setUpTier] = useState<Tier>('big');
  const [staging, setStaging] = useState(false);
  const [committing, setCommitting] = useState(false);
  const [stageRes, setStageRes] = useState<UploadStageResult | null>(null);
  const [superProgress, setSuperProgress] = useState<UploadCommitStatus | null>(null);

  const doStage = async (channel: string, tier: Tier) => {
    setUpChannel(channel); setUpTier(tier); setStageRes(null); setStaging(true);
    message.loading({ content: '正在挂到千牛并预校验（不提交）…约30秒', key: 'up', duration: 0 });
    try {
      // 单品立减: 把选定档期(精确到秒)一起传 → 千牛『活动时间』自动填成它
      const FMT = 'YYYY-MM-DD HH:mm:ss';
      const sd = (channel === 'single_item_discount' && range[0]) ? range[0].format(FMT) : undefined;
      const ed = (channel === 'single_item_discount' && range[1]) ? range[1].format(FMT) : undefined;
      const r = await activityUploadStage(channel, tier, sd, ed);
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

  const closeStageModal = () => {
    setStageRes(null); setUpChannel(null); setSuperProgress(null);
  };

  const doCommit = async () => {
    if (!upChannel) return;
    setCommitting(true);
    message.loading({ content: '正在提交到千牛…', key: 'commit', duration: 0 });
    try {
      const FMT = 'YYYY-MM-DD HH:mm:ss';
      const sd = (upChannel === 'single_item_discount' && range[0]) ? range[0].format(FMT) : undefined;
      const ed = (upChannel === 'single_item_discount' && range[1]) ? range[1].format(FMT) : undefined;
      const r = await activityUploadCommit(upChannel, upTier, sd, ed);
      if (r.async_job) {   // 超级立减: 逐商品原地改价异步 → 轮询
        message.destroy('commit');
        setSuperProgress({ status: 'running' });
        await pollSuperCommit(r.async_job);
        return;
      }
      message.destroy('commit');
      if (r.ok && r.submitted) {
        message.success(`已提交「${r.channel_name}」到千牛`);
      } else {
        message.error(r.error || '提交未成功，请到千牛核对');
      }
      setUpChannel(null); setStageRes(null);
    } catch {
      message.destroy('commit'); message.error('提交失败');
    } finally { setCommitting(false); }
  };

  const pollSuperCommit = async (job: string) => {
    const deadline = Date.now() + 20 * 60 * 1000;
    while (Date.now() < deadline) {
      await new Promise((res) => setTimeout(res, 6000));
      let s: UploadCommitStatus;
      try { s = await activityUploadCommitStatus(job); }
      catch { continue; }
      setSuperProgress(s);
      if (s.status === 'done' || s.status === 'error') {
        const ok = s.result?.ok;
        const sub = s.result?.submitted ?? 0;
        if (s.status === 'done' && ok) {
          message.success(`超级立减已提交 ${sub} 个商品`);
        } else {
          message.error(s.result?.message || s.error || '超级立减提交未完成，请看明细');
        }
        return;
      }
    }
    message.warning('轮询超时，请到千牛核对或稍后再看');
  };

  // ── 超大促 SKU 轮换 ──
  const [rotPc, setRotPc] = useState('');
  const [rotPlan, setRotPlan] = useState<SkuRotationPlan | null>(null);
  const [rotLoading, setRotLoading] = useState(false);
  const [rotApplying, setRotApplying] = useState(false);
  const doRotPreview = async () => {
    if (!rotPc.trim()) { message.warning('先填产品编码'); return; }
    setRotLoading(true); setRotPlan(null);
    try {
      const r = await fetchSkuRotation(rotPc.trim());
      if (!r.ok) { message.error(r.error || '预览失败'); return; }
      setRotPlan(r);
    } catch { message.error('预览失败'); } finally { setRotLoading(false); }
  };
  const doRotApply = async () => {
    if (!rotPc.trim()) return;
    setRotApplying(true);
    try {
      const r = await applySkuRotation(rotPc.trim());
      if (r.ok) message.success(`已同步 ERP 映射：改动 ${r.changed} 条`);
      else message.error('同步失败');
    } catch { message.error('同步失败（需 admin）'); } finally { setRotApplying(false); }
  };

  return (
    <Space direction="vertical" style={{ width: '100%' }} size="middle">
      {/* 标价工具 */}
      <Card size="small" title="标价（一口价）工具 —— ERP 价为准，千牛一口价 = 日常价 ÷ 0.75">
        <Space wrap>
          <Button type="primary" icon={<DownloadOutlined />} onClick={showPriceGuide}>
            标价怎么改 · 指引</Button>
          <Button icon={<DownloadOutlined />} loading={busy === 'pq'}
            onClick={() => dl('pq', '商品价格快速编辑表', '商品价格快速编辑_ERP标准.xlsx', downloadProductPriceQuickEdit)}>
            下载对照表</Button>
          <Link to="/shop-price-board"><Button icon={<TableOutlined />}>打开改价台</Button></Link>
        </Space>
      </Card>

      <Row gutter={[16, 16]}>
        <Col xs={24} lg={12}>
          <Card size="small" title="单品立减 · 各档下载(减金额)">
            <Space direction="vertical" style={{ width: '100%' }} size={6}>
              <Button icon={<DownloadOutlined />} block loading={busy === 'si-mid'}
                onClick={() => dl('si-mid', '单品立减·中促10%', '淘宝单品立减_中促10%.xlsx', () => downloadSingleItemDiscount('mid'))}>中促/超级立减 10%</Button>
              <Button icon={<DownloadOutlined />} block loading={busy === 'si-big'}
                onClick={() => dl('si-big', '单品立减·88VIP12%', '淘宝单品立减_88VIP大促12%.xlsx', () => downloadSingleItemDiscount('big'))}>88VIP大促 12%</Button>
              <Button icon={<DownloadOutlined />} block loading={busy === 'si-618'}
                onClick={() => dl('si-618', '单品立减·大促15%', '淘宝单品立减_大促15%.xlsx', () => downloadSingleItemDiscount('big618'))}>超级大促 15%</Button>
            </Space>
          </Card>
        </Col>
        <Col xs={24} lg={12}>
          <Card size="small" title="报名表 · 各档下载">
            <Space direction="vertical" style={{ width: '100%' }} size={6}>
              <Button type="primary" ghost icon={<DownloadOutlined />} block loading={busy === 'sr'}
                onClick={() => dl('sr', '超级立减长期报名表', '超级立减长期活动_报名表.xlsx', downloadSuperReduceSignup)}>
                超级立减长期(14列, 活动价=日常价)</Button>
              <Button icon={<DownloadOutlined />} block loading={busy === 'ps-big'}
                onClick={() => dl('ps-big', '大促报名·88VIP12%', '大促活动报名_88VIP大促12%.xlsx', () => downloadPromoSignup('big'))}>88VIP大促 报名表</Button>
              <Button icon={<DownloadOutlined />} block loading={busy === 'ps-mid'}
                onClick={() => dl('ps-mid', '大促报名·中促10%', '大促活动报名_中促10%.xlsx', () => downloadPromoSignup('mid'))}>中促 报名表</Button>
              <Button icon={<DownloadOutlined />} block loading={busy === 'ps-618'}
                onClick={() => dl('ps-618', '大促报名·超大促15%', '大促活动报名_超级大促15%.xlsx', () => downloadPromoSignup('big618'))}>超大促 15% 报名表</Button>
            </Space>
          </Card>
        </Col>
      </Row>

      {/* 单步手动上传 */}
      <Card size="small" title="单步上传到千牛（先比对再提交）">
        <Space wrap>
          <Button icon={<CloudUploadOutlined />} loading={staging && upChannel === 'single_item_discount'}
            onClick={() => doStage('single_item_discount', 'big')}>单品立减(88VIP12%)</Button>
          <Button disabled icon={<CloudUploadOutlined />}>大促报名（仅自动程序）</Button>
          <Button disabled icon={<CloudUploadOutlined />}>超级立减（仅自动程序）</Button>
        </Space>
      </Card>

      {/* SKU身份轮换按2026-07-26方案3加强版暂停 */}
      <Card size="small" title="SKU身份轮换（已暂停）">
        <Space direction="vertical" style={{ width: '100%' }} size="small">
          <Alert type="error" showIcon style={{ fontSize: 12 }}
            message="双11前执行方案3加强版：真实SKU与定制SKU身份保持不变，禁止通过轮换绕历史价格线。"
            description="冲突商品进入价保冷静期；能安全报名的其他商品先报，有潜在退差/亏损的飞书提醒后由运营决定。" />
          <Space>
            <Input placeholder="产品编码，如 PPS26330140117" value={rotPc} style={{ width: 260 }}
              disabled onChange={(e) => setRotPc(e.target.value)} onPressEnter={doRotPreview} />
            <Button disabled type="primary" loading={rotLoading} onClick={doRotPreview}>预览轮换</Button>
            {rotPlan?.ok && (
              <Popconfirm title="确认千牛已把规格/价格/编码都轮换好了？" description="这会把 ERP 的 skuId 映射按新轮换重刷（不可逆）"
                okText="已轮换完，同步" cancelText="还没" onConfirm={doRotApply}>
                <Button disabled danger loading={rotApplying}>千牛轮换完 → 同步系统映射</Button>
              </Popconfirm>
            )}
          </Space>
          {rotPlan?.ok && rotPlan.ladders?.map((lad, i) => (
            <div key={i}>
              <Divider orientation="left" style={{ margin: '4px 0' }}>
                <Typography.Text strong style={{ fontSize: 12 }}>{lad.ladder} · buffer槽 {lad.buffer || '—'}</Typography.Text>
                {lad.warnings.map((w) => <Tag color="orange" key={w} style={{ marginLeft: 6 }}>{w}</Tag>)}
              </Divider>
              <Table size="small" pagination={false} rowKey={(r) => r.skuId}
                dataSource={lad.qn_instructions}
                columns={[
                  { title: '物理 skuId', dataIndex: 'skuId', width: 150 },
                  { title: '→ 改成商家编码', dataIndex: 'new_sku_code', width: 150 },
                  { title: '→ 规格(尺寸)', dataIndex: 'new_size', ellipsis: true },
                  { title: '→ 价格', dataIndex: 'new_price', width: 90, render: (v: number | null) => v != null ? `¥${v}` : '-' },
                ]} />
            </div>
          ))}
        </Space>
      </Card>

      <Alert type="warning" showIcon
        message="618/双11 报名模板接入步骤"
        description={<span>618/双11 报名开启后，在千牛该活动「商品批量导入」页<b>下载平台报名模板</b>→发给研发接入→此后 618 场次可一键推送。报名价仍 = ERP 日常价口径。</span>} />

      {/* ── 千牛上传·比对表确认 (stage 完成后弹出) ── */}
      <Modal
        open={!!stageRes}
        title={<Space><CloudUploadOutlined /><b style={{ fontSize: 17 }}>{stageRes?.channel_name} · 上传比对（确认前请核对）</b></Space>}
        width={1400}
        style={{ maxWidth: '96vw', top: 24 }}
        onCancel={closeStageModal}
        footer={
          stageRes?.channel === 'single_item_discount'
            ? [
                <Button key="cancel" onClick={closeStageModal}>取消（不提交）</Button>,
                <Button key="ok" type="primary" danger loading={committing} icon={<CloudUploadOutlined />}
                  onClick={doCommit}>确认最后一步上传（不可逆）</Button>,
              ]
            : stageRes?.channel === 'super_reduce'
            ? [
                <Button key="cancel" disabled={committing || superProgress?.status === 'running'}
                  onClick={closeStageModal}>取消（不提交）</Button>,
                <Button key="ok" type="primary" danger
                  loading={committing || superProgress?.status === 'running'}
                  disabled={superProgress?.status === 'done'}
                  icon={<CloudUploadOutlined />} onClick={doCommit}>
                  确认批量导入并一键发布（不可逆）</Button>,
              ]
            : [
                <Typography.Text key="note" type="secondary" style={{ marginRight: 12, fontSize: 12 }}>
                  已挂到千牛「草稿」，去千牛商品管理核对后手动发布报名
                </Typography.Text>,
                <Button key="close" type="primary" onClick={closeStageModal}>知道了</Button>,
              ]
        }
      >
        {stageRes && (() => {
          const okN = stageRes.validation?.ok ?? 0;
          const failN = stageRes.validation?.failed ?? 0;
          const isSuper = stageRes.channel === 'super_reduce';
          const asyncPending = !isSuper
            && stageRes.validation?.ok == null && stageRes.validation?.failed == null;
          const failCodes: string[] = stageRes.validation?.failed_sku_codes || [];
          const misN = stageRes.mismatch_count ?? 0;
          const totalN = stageRes.compare_total ?? (stageRes.compare_rows?.length ?? 0);
          const priceOk = misN === 0;
          const label0 = stageRes.compare_rows?.[0]?.value_label || '系统值';
          const sp = superProgress;
          return (
          <Space direction="vertical" style={{ width: '100%' }} size="middle">
            {isSuper && (
              <Card size="small" style={{ background: '#e6f4ff', border: '1px solid #91caff' }}
                styles={{ body: { padding: '12px 16px' } }}>
                <Typography.Text style={{ fontSize: 13 }}>
                  超级立减走<b>报名页批量导入</b>：系统把报名表（活动价=日常价、让利10%）自动传进
                  千牛「商品批量导入」→ 落<b>草稿</b>（可撤销），出导入结果给你核对；确认后点下方按钮
                  <b>一键发布</b>才真生效。价格都是<b>降价</b>方向。<br/>
                  失败通常是：<b>0销量商品</b>、价格触<b>最低标价/红线</b>、或<b>无系统映射的SKU</b>——明细见下。
                </Typography.Text>
                {sp && (
                  <div style={{ marginTop: 10 }}>
                    <Divider style={{ margin: '6px 0' }} />
                    {sp.status === 'running' && (
                      <Typography.Text style={{ fontSize: 13 }}>
                        <ExperimentOutlined spin /> 正在批量导入并发布…
                        {sp.result?.validation ? `（成功 ${sp.result.validation.ok} · 失败 ${sp.result.validation.failed}）` : ''}
                      </Typography.Text>
                    )}
                    {(sp.status === 'done' || sp.status === 'error') && (
                      <>
                        <Space>
                          <Statistic title="导入成功" value={sp.result?.validation?.ok ?? 0}
                            valueStyle={{ color: '#389e0d', fontSize: 24 }} suffix="商品" />
                          <Divider type="vertical" style={{ height: 40 }} />
                          <Statistic title="失败" value={sp.result?.validation?.failed ?? 0}
                            valueStyle={{ color: (sp.result?.validation?.failed ?? 0) ? '#cf1322' : '#8c8c8c', fontSize: 24 }} suffix="商品" />
                        </Space>
                        {sp.result?.message && <Alert style={{ marginTop: 8 }} type="error" showIcon message={sp.result.message} />}
                        {!!sp.result?.validation?.failed_reasons?.length && (
                          <Table size="small" style={{ marginTop: 8 }} pagination={false} rowKey="reason"
                            dataSource={sp.result.validation.failed_reasons}
                            columns={[
                              { title: '失败原因', dataIndex: 'reason', ellipsis: true },
                              { title: '商品', dataIndex: 'codes', width: 90, align: 'center' as const,
                                render: (c: string[]) => (c?.length ?? 0) },
                            ]} />
                        )}
                      </>
                    )}
                  </div>
                )}
              </Card>
            )}
            <Card size="small" style={{ background: asyncPending ? '#e6f4ff' : failN ? '#fffbe6' : '#f6ffed',
                border: `1px solid ${asyncPending ? '#91caff' : failN ? '#ffe58f' : '#b7eb8f'}` }}
              styles={{ body: { padding: '12px 16px' } }}>
              <Row align="middle" gutter={16}>
                {asyncPending ? (
                  <>
                    <Col><Statistic title="千牛校验" value="已收单 · 处理中"
                      valueStyle={{ color: '#1677ff', fontSize: 26 }} /></Col>
                    <Col flex="auto" style={{ textAlign: 'right' }}>
                      <Typography.Text type="secondary" style={{ fontSize: 13 }}>
                        此渠道千牛是<b>异步导入</b>：成功/失败数稍后出现在<b>千牛活动页（草稿/异常 tab）</b><br/>
                        发布报名前去那里核对，「异常」里的就是失败的
                      </Typography.Text>
                    </Col>
                  </>
                ) : (
                  <>
                    <Col><Statistic title="千牛校验·通过" value={okN} valueStyle={{ color: '#389e0d', fontSize: 30 }} suffix="条" /></Col>
                    <Col><Divider type="vertical" style={{ height: 44 }} /></Col>
                    <Col><Statistic title="千牛校验·失败" value={failN}
                      valueStyle={{ color: failN ? '#cf1322' : '#8c8c8c', fontSize: 30 }} suffix="条" /></Col>
                    <Col flex="auto" style={{ textAlign: 'right' }}>
                      <Typography.Text type="secondary" style={{ fontSize: 13 }}>
                        文件已挂千牛「尚未提交」<br/>核对无误后点下方按钮才真上传
                      </Typography.Text>
                    </Col>
                  </>
                )}
              </Row>
              {failN > 0 && (
                <div style={{ marginTop: 8, paddingTop: 8, borderTop: '1px dashed #ffe58f' }}>
                  <Typography.Text strong style={{ color: '#cf1322' }}>失败的 {failN} 条不会上传</Typography.Text>
                  {failCodes.length > 0
                    ? <div style={{ marginTop: 4 }}>
                        {failCodes.map(c => <Tag key={c} color="red" style={{ marginBottom: 4 }}>{c}</Tag>)}
                        <Typography.Text type="secondary" style={{ fontSize: 12 }}>（多为已下架/不在活动范围的 SKU，不影响其余 {okN} 条）</Typography.Text>
                      </div>
                    : <Typography.Text type="secondary" style={{ fontSize: 12, marginLeft: 8 }}>（具体哪条千牛未回明细，通常是已下架 SKU，不影响其余 {okN} 条）</Typography.Text>}
                </div>
              )}
            </Card>

            {(stageRes.validation?.failed_reasons?.length ?? 0) > 0 && (
              <Alert type="error" showIcon
                message={<b style={{ fontSize: 15 }}>失败原因（已自动解析千牛操作反馈）</b>}
                description={
                  <Space direction="vertical" size={2} style={{ fontSize: 14 }}>
                    {stageRes.validation!.failed_reasons!.map((r) => (
                      <div key={r.reason}><b>{r.items} 件</b>：{r.reason}</div>
                    ))}
                    <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                      向导预检页有对应红字明细（券后价超线 / 整商品SKU不全 / 动销）——按预检修完再导。
                    </Typography.Text>
                  </Space>
                } />
            )}

            {stageRes.screenshot_base64 && (
              <div>
                <Typography.Text type="secondary" style={{ fontSize: 13 }}>
                  千牛页面截图（文件已挂的实证，<b>点图可放大看清</b>）
                </Typography.Text>
                <div style={{ marginTop: 6, border: '1px solid #eee', borderRadius: 6, overflow: 'hidden' }}>
                  <Image alt="千牛已挂文件截图" style={{ width: '100%' }}
                    src={`data:image/png;base64,${stageRes.screenshot_base64}`} />
                </div>
              </div>
            )}

            <Alert showIcon type={priceOk ? 'success' : 'error'}
              icon={priceOk ? <CheckCircleOutlined /> : undefined}
              message={priceOk
                ? `上传价 = 系统价：全部 ${totalN} 行一分不差（严格核对，无出入）`
                : `有 ${misN} 行「上传值 ≠ 系统价」！下方红字行，未按系统价，别提交、先叫我查`}
              description={priceOk
                ? '「上传值」直接读自即将上传千牛的那份表，「系统应填」按定价独立重算，两者逐条对到分。'
                : '这不该出现——上传表和系统定价理应一致。请把红字行截图发我，我立刻定位。'} />
            <div>
              <Typography.Text type="secondary" style={{ fontSize: 13 }}>
                逐 SKU 核对：<b>上传值</b>（真正传千牛的数）vs <b>系统应填</b>（按定价重算）；差 &gt;1 分即标红
              </Typography.Text>
              <Table size="middle" rowKey="taobao_sku_id" pagination={{ pageSize: 12 }} style={{ marginTop: 6 }}
                dataSource={stageRes.compare_rows || []}
                onRow={(r: CompareRow) => (
                  (r.mismatch || failCodes.includes(r.sku_code)) ? { style: { background: '#fff1f0' } } : {}
                )}
                columns={[
                  { title: 'SKU编码', dataIndex: 'sku_code', width: 150,
                    render: (v: string) => failCodes.includes(v)
                      ? <Typography.Text type="danger">{v} <Tag color="red">千牛失败</Tag></Typography.Text> : v },
                  { title: '淘宝SKUID', dataIndex: 'taobao_sku_id', width: 120 },
                  { title: '名称', dataIndex: 'name', ellipsis: true },
                  { title: `上传值·${label0}`, dataIndex: 'uploaded_value', width: 110,
                    render: (v: number | null, r: CompareRow) => v == null
                      ? '-' : <Typography.Text type={r.mismatch ? 'danger' : undefined} strong>¥{v}</Typography.Text> },
                  { title: `系统应填·${label0}`, dataIndex: 'system_value', width: 110,
                    render: (v: number | null) => v != null ? `¥${v}` : '-' },
                  { title: '核对', dataIndex: 'mismatch', width: 76, align: 'center' as const,
                    render: (m: boolean, r: CompareRow) => m
                      ? <Tag color="red">差¥{r.uploaded_value != null && r.system_value != null
                          ? Math.abs(r.uploaded_value - r.system_value).toFixed(2) : '?'}</Tag>
                      : <Tag color="green">一致</Tag> },
                  { title: '目标到手', dataIndex: 'target_shoudao', width: 96,
                    render: (v: number | null) => v != null ? `¥${v}` : '-' },
                ]} />
            </div>
          </Space>
          );
        })()}
      </Modal>
    </Space>
  );
}
