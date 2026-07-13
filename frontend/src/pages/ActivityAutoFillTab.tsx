/**
 * 定价页「🚀 活动自动填写」Tab —— 2026-07-13 按用户口述重构为【场次驱动】。
 *   顶部选场次(超级立减长期/88VIP大促/中促/618双11) → 选档期(自动结束=下一档) →
 *   虚拟推送【三档核对】(档1商品价 vs 千牛 / 档2单品立减 / 档3报名价) →
 *   「一键推送本场次」向导: ①核价(无差异跳过)→②单品立减→③报名价, 每步停下等用户确认(用户拍板)。
 *   散落的下载/单步上传/SKU轮换 收进折叠「高级 · 手动」。
 * ★第一铁律: 以 ERP 价格为准, 平台报不进就改千牛一口价到 日常价÷0.75, 绝不反过来改 ERP。
 */
import { useCallback, useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import {
  Alert, Button, Card, Checkbox, Col, Collapse, DatePicker, Divider, Empty, Image, Input, Modal,
  Popconfirm, Row, Segmented, Select, Space, Statistic, Steps, Table, Tag, Typography, message,
} from 'antd';
import {
  CalendarOutlined, CheckCircleOutlined, CloudUploadOutlined, DownloadOutlined, EditOutlined,
  ExperimentOutlined, TableOutlined, ThunderboltOutlined, WarningOutlined,
} from '@ant-design/icons';
import dayjs, { type Dayjs } from 'dayjs';
import {
  activityUploadCommit, activityUploadCommitStatus, activityUploadStage,
  applySkuRotation, downloadProductPriceQuickEdit, downloadPromoSignup, downloadSingleItemDiscount,
  downloadSuperReduceSignup, fetchActivityCalendar, fetchActivityPreflight, fetchAutoEnd,
  fetchSkuRotation, saveActivityCalendar,
  type ActivityPeriod, type ActivityPreflight, type AutoEndResult, type SkuRotationPlan,
  type UploadCommitStatus, type UploadStageResult,
} from '../api/catalog';

const XLSX_MIME = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet';
function triggerDownload(data: BlobPart, filename: string) {
  const url = URL.createObjectURL(new Blob([data], { type: XLSX_MIME }));
  const a = document.createElement('a');
  a.href = url; a.download = filename; a.click();
  URL.revokeObjectURL(url);
}

// ── 场次定义 (每个场次 = 一套单品立减档 + 一套报名渠道/档) ──
type CampaignKey = 'super_reduce' | 'big' | 'mid' | 'big618';
type Tier = 'mid' | 'big' | 'big618';
interface CampaignDef {
  key: CampaignKey; label: string; tag: string; tagColor: string;
  siTier: Tier;                                   // 并行单品立减档
  signupChannel: 'super_reduce' | 'promo_signup'; // 报名渠道
  signupTier: Tier;                               // 报名档(promo_signup 用)
  signupLabel: string;
  signupCommittable: boolean;                     // super_reduce 可一键发布; promo_signup 挂草稿手动发布
  needRotation: boolean;
  templatePending: boolean;
  desc: string;
}
const CAMPAIGNS: CampaignDef[] = [
  {
    key: 'super_reduce', label: '超级立减长期', tag: '常年', tagColor: 'green', siTier: 'mid',
    signupChannel: 'super_reduce', signupTier: 'big', signupLabel: '超级立减·一键发布', signupCommittable: true,
    needRotation: false, templatePending: false,
    desc: '常年在线。报名活动价 = 日常价（ERP标准），叠加并行单品立减到手≈中促。14列模板、apply.htm 报名页。',
  },
  {
    key: 'big', label: '88VIP大促', tag: '12%', tagColor: 'blue', siTier: 'big',
    signupChannel: 'promo_signup', signupTier: 'big', signupLabel: '大促报名(挂草稿)', signupCommittable: false,
    needRotation: false, templatePending: false,
    desc: '有档期的平台大促。报名价 = 报名价A（大促到手÷0.88），官方力度12%。7列大促报名模板，挂草稿后到千牛手动发布。',
  },
  {
    key: 'mid', label: '中促', tag: '10%', tagColor: 'cyan', siTier: 'mid',
    signupChannel: 'promo_signup', signupTier: 'mid', signupLabel: '中促报名(挂草稿)', signupCommittable: false,
    needRotation: false, templatePending: false,
    desc: '中促场次。报名价 = 报名价A（与88大促同一个A），官方力度10%，到手 = 中促到手。',
  },
  {
    key: 'big618', label: '618/双11', tag: '15%·换SKU', tagColor: 'purple', siTier: 'big618',
    signupChannel: 'promo_signup', signupTier: 'big618', signupLabel: '超大促报名', signupCommittable: false,
    needRotation: true, templatePending: true,
    desc: '618/双11 超大促。15% 让利、要换 SKU 绕 15 天最低价。报名价 = 报名价618。报名模板与其他不同——待接入。',
  },
];

const WIZARD_STEPS = ['核价（标价 vs 千牛）', '单品立减', '活动报名'];

export default function ActivityAutoFillTab() {
  const [busy, setBusy] = useState<string | null>(null);
  const [campaignKey, setCampaignKey] = useState<CampaignKey>('super_reduce');
  const campaign = CAMPAIGNS.find((c) => c.key === campaignKey)!;

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

  // ── 虚拟推送(三档核对) ──
  const [pre, setPre] = useState<ActivityPreflight | null>(null);
  const [preLoading, setPreLoading] = useState(false);
  const [skipFloor, setSkipFloor] = useState(false);
  const [wizardStep, setWizardStep] = useState(0);       // 0核价 1单品立减 2报名 3完成

  const runPreflight = async () => {
    setPreLoading(true);
    message.loading({ content: '三档核对中（不产文件、不改数据）…', key: 'pre', duration: 0 });
    try {
      setPre(await fetchActivityPreflight(15, skipFloor, campaign.signupTier));
      message.success({ content: '核对完成', key: 'pre', duration: 1.4 });
    } catch {
      message.error({ content: '核对失败', key: 'pre' });
    } finally { setPreLoading(false); }
  };
  // 切场次清掉上次核对结果 (力度不同, 需重核)
  useEffect(() => { setPre(null); setWizardStep(0); }, [campaignKey]);

  const tier1Bad = (pre?.price_too_low_count ?? 0) > 0;                 // 档1 千牛偏低(阻塞报名)
  const cleanAll = pre && !tier1Bad && (pre.bad_product_count === 0)
    && (pre.skuid_collision_count ?? 0) === 0 && (pre.floor_conflict_count ?? 0) === 0
    && (pre.incomplete_item_count ?? 0) === 0;

  // ── 活动档期日历 ──
  const [periods, setPeriods] = useState<ActivityPeriod[]>([]);
  const [calStatus, setCalStatus] = useState<{ today: string; active: ActivityPeriod[]; upcoming: ActivityPeriod[] } | null>(null);
  const [range, setRange] = useState<[Dayjs | null, Dayjs | null]>([null, null]);
  const [autoEnd, setAutoEnd] = useState<AutoEndResult | null>(null);
  const [calOpen, setCalOpen] = useState(false);

  const loadCalendar = useCallback(async () => {
    try {
      const c = await fetchActivityCalendar();
      setPeriods(c.periods); setCalStatus(c.status);
    } catch { /* 静默: 日历没配也不挡主流程 */ }
  }, []);
  useEffect(() => { loadCalendar(); }, [loadCalendar]);

  // 选了开始日 → 拉自动结束(下一档前一刻); 单品立减默认跟这个结束
  const onStartChange = async (start: Dayjs | null) => {
    setRange((r) => [start, r[1]]);
    setAutoEnd(null);
    if (!start) return;
    try {
      const r = await fetchAutoEnd(start.format('YYYY-MM-DD'), campaign.label);
      setAutoEnd(r);
      if (r.end) setRange([start, dayjs(r.end)]);   // 自动结束 = 下一档前一天
    } catch { /* 无日历时不阻断 */ }
  };

  // ── 千牛上传 (stage → 比对表 → 确认 → commit) ──
  const [upChannel, setUpChannel] = useState<string | null>(null);
  const [upTier, setUpTier] = useState<Tier>('big');
  const [staging, setStaging] = useState(false);
  const [committing, setCommitting] = useState(false);
  const [stageRes, setStageRes] = useState<UploadStageResult | null>(null);
  const [superProgress, setSuperProgress] = useState<UploadCommitStatus | null>(null);
  const [inWizard, setInWizard] = useState(false);       // 本次 stage/commit 是否属于一键推送向导

  const doStage = async (channel: string, tier: Tier, wizard = false) => {
    setInWizard(wizard);
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

  const closeStageModal = () => {
    // promo_signup 无 commit: 挂到草稿即成功; 若在向导里, 关闭=完成报名步 → 收尾
    if (inWizard && stageRes && stageRes.channel === 'promo_signup' && stageRes.ok) {
      setWizardStep(3); setInWizard(false);
    }
    setStageRes(null); setUpChannel(null); setSuperProgress(null);
  };

  const doCommit = async () => {
    if (!upChannel) return;
    setCommitting(true);
    message.loading({ content: '正在提交到千牛…', key: 'commit', duration: 0 });
    try {
      const r = await activityUploadCommit(upChannel, upTier);
      if (r.async_job) {   // 超级立减: 逐商品原地改价异步 → 轮询
        message.destroy('commit');
        setSuperProgress({ status: 'running' });
        await pollSuperCommit(r.async_job);
        return;
      }
      message.destroy('commit');
      if (r.ok && r.submitted) {
        message.success(`已提交「${r.channel_name}」到千牛`);
        if (inWizard && upChannel === 'single_item_discount') { setWizardStep(2); setInWizard(false); }
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
          if (inWizard) { setWizardStep(3); setInWizard(false); }
        } else {
          message.error(s.result?.message || s.error || '超级立减提交未完成，请看下方明细');
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

  const stepStatus = (idx: number): 'finish' | 'process' | 'wait' =>
    wizardStep > idx ? 'finish' : wizardStep === idx ? 'process' : 'wait';

  return (
    <Space direction="vertical" style={{ width: '100%' }} size="middle">
      {/* ── 顶部说明 + ★第一铁律 ── */}
      <Alert
        type="info" showIcon
        message="活动自动填写 = 选场次 → 三档核对 → 一键推送（每步停下等你确认才提交，不会偷偷上线）。"
        description="生成表只推送有淘宝 SKUID 的 SKU（没上架的自动跳过）。先「三档核对」体检，再走「一键推送」。"
      />
      <Alert
        type="warning" showIcon banner
        message={<b>★ 第一铁律：以 ERP 价格为准，不迁就平台</b>}
        description={<span style={{ fontSize: 13 }}>
          所有报名价按 <b>ERP 日常价</b> 口径。平台报不进（"活动价/标价不得高于近15天最低标价"）几乎都是
          <b>千牛一口价填低了</b> → 去改<b>千牛一口价</b>到 <b>日常价 ÷ 0.75</b>（0.75=单品宝标准折，永不改），
          <b>绝不反过来改 ERP</b>。「活动价只能下调不能上调」——要往上抬得<b>先在千牛撤销该活动报名</b>再重报。
        </span>}
      />

      {/* ── 场次选择 ── */}
      <Card size="small" title={<Space><ThunderboltOutlined /><b>① 选择活动场次</b></Space>}>
        <Segmented
          block value={campaignKey} onChange={(v) => setCampaignKey(v as CampaignKey)}
          options={CAMPAIGNS.map((c) => ({
            value: c.key,
            label: <Space size={4}><span>{c.label}</span><Tag color={c.tagColor} style={{ margin: 0 }}>{c.tag}</Tag></Space>,
          }))}
        />
        <Typography.Paragraph type="secondary" style={{ fontSize: 12, margin: '10px 0 0' }}>
          {campaign.desc}
          {campaign.templatePending && <Tag color="purple" style={{ marginLeft: 6 }}>报名模板待接入</Tag>}
          {campaign.needRotation && <Tag color="orange" style={{ marginLeft: 6 }}>需换 SKU</Tag>}
        </Typography.Paragraph>
      </Card>

      {/* ── 档期 (日期 + 自动结束) ── */}
      <Card size="small" title={<Space><CalendarOutlined /><b>② 活动档期</b>
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>单品立减自动结束 = 下一档期开始前一刻</Typography.Text></Space>}
        extra={<Button size="small" icon={<EditOutlined />} onClick={() => setCalOpen(true)}>管理档期</Button>}>
        <Space direction="vertical" style={{ width: '100%' }} size={8}>
          <Space wrap>
            <DatePicker.RangePicker
              value={range} allowEmpty={[false, true]}
              onChange={(v) => { if (v && v[0]) onStartChange(v[0]); else { setRange([null, null]); setAutoEnd(null); } }}
            />
            {periods.length > 0 && (
              <Select
                style={{ minWidth: 200 }} placeholder="或从日历选一档"
                options={periods.map((p, i) => ({
                  value: i, label: `${p.name}（${p.tier_label}｜${p.start}${p.end ? '~' + p.end : '起 常年'}）`,
                }))}
                onChange={(i) => {
                  const p = periods[i as number];
                  const s = dayjs(p.start); setRange([s, p.end ? dayjs(p.end) : null]);
                  onStartChange(s);
                }}
              />
            )}
          </Space>
          {autoEnd && (autoEnd.next
            ? <Alert type="success" showIcon style={{ fontSize: 12 }}
                message={<span>单品立减将<b>自动结束于 {autoEnd.end_dt}</b>（下一档「{autoEnd.next.name}·{autoEnd.next.tier_label}」{autoEnd.next.start} 开始前一刻，自动让位）。</span>} />
            : <Alert type="warning" showIcon style={{ fontSize: 12 }}
                message={<span><b>无下次活动</b>：日历里这之后没有排下一档。单品立减请自行选结束日，或作长期挂着。</span>} />)}
          {calStatus && (calStatus.active.length > 0 || calStatus.upcoming.length > 0) && (
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              {calStatus.active.map((p) => <Tag color="green" key={p.name}>进行中 {p.name}</Tag>)}
              {calStatus.upcoming.map((p) => <Tag key={p.name}>{p.name} {p.days_to_start}天后</Tag>)}
            </Typography.Text>
          )}
          <Typography.Text type="secondary" style={{ fontSize: 11 }}>
            注：把选定日期<b>自动填进千牛</b>的日期控件需一次录制后生效；当前档期用于规划与自动结束计算。
          </Typography.Text>
        </Space>
      </Card>

      {/* ── ③ 虚拟推送(三档核对) ── */}
      <Card size="small" title={<Space><ExperimentOutlined /><b>③ 虚拟推送 · 三档核对</b>
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>不产文件 · 不改数据 · 不上传</Typography.Text></Space>}
        extra={<Space>
          <Checkbox checked={skipFloor} onChange={(e) => setSkipFloor(e.target.checked)}>
            <Typography.Text style={{ fontSize: 12 }}>初始报价 · 跳过15天校验</Typography.Text></Checkbox>
          <Button type="primary" icon={<ExperimentOutlined />} loading={preLoading} onClick={runPreflight}>
            {pre ? '重新核对' : '开始三档核对'}</Button>
        </Space>}>
        {!pre && <Typography.Text type="secondary">
          点右上角「开始三档核对」——档1 商品价(ERP日常价 vs 千牛标价)、档2 单品立减、档3 报名价，一次体检。</Typography.Text>}
        {pre && (
          <Space direction="vertical" style={{ width: '100%' }} size="middle">
            <Row gutter={16}>
              {/* 档1 商品价格 */}
              <Col span={8}>
                <Card size="small" styles={{ body: { padding: 12 } }}
                  style={{ borderColor: tier1Bad ? '#ffccc7' : '#b7eb8f' }}>
                  <Statistic title="档1 商品价 · 千牛偏低(阻塞)" value={pre.price_too_low_count ?? 0}
                    valueStyle={{ color: tier1Bad ? '#cf1322' : '#3f8600' }} suffix="SKU" />
                  <Typography.Text type="secondary" style={{ fontSize: 11 }}>
                    核 {pre.price_checked ?? 0} 个 · 一致 {pre.price_ok ?? 0}
                    {pre.price_has_snapshot
                      ? `｜千牛快照 ${pre.price_snapshot_date || '?'}`
                      : '｜⚠无千牛快照(先导入淘宝商品导出)'}
                  </Typography.Text>
                </Card>
              </Col>
              {/* 档2 单品立减 */}
              <Col span={8}>
                <Card size="small" styles={{ body: { padding: 12 } }}
                  style={{ borderColor: (pre.conflict_count ?? 0) ? '#ffe58f' : '#b7eb8f' }}>
                  <Statistic title="档2 单品立减 · 就绪行" value={pre.signup_big.rows}
                    valueStyle={{ color: '#3f8600' }} suffix="行" />
                  <Typography.Text type="secondary" style={{ fontSize: 11 }}>
                    {pre.floor_check_skipped ? '15天校验已跳过' : `15天最低价冲突 ${pre.conflict_count}`}
                    ｜坏价产品 {pre.bad_product_count}
                  </Typography.Text>
                </Card>
              </Col>
              {/* 档3 报名价 */}
              <Col span={8}>
                <Card size="small" styles={{ body: { padding: 12 } }}
                  style={{ borderColor: (pre.floor_conflict_count ?? 0) || (pre.incomplete_item_count ?? 0) ? '#ffccc7' : '#b7eb8f' }}>
                  <Statistic title="档3 报名价 · 券后超线(拒)" value={pre.floor_conflict_count ?? 0}
                    valueStyle={{ color: (pre.floor_conflict_count ?? 0) ? '#cf1322' : '#3f8600' }} suffix="SKU" />
                  <Typography.Text type="secondary" style={{ fontSize: 11 }}>
                    整商品不全 {pre.incomplete_item_count ?? 0}｜SKUID撞号 {pre.skuid_collision_count ?? 0}｜未上架 {pre.unmapped_total}
                  </Typography.Text>
                </Card>
              </Col>
            </Row>

            {cleanAll && <Alert type="success" showIcon icon={<CheckCircleOutlined />}
              message="三档全绿：商品价与千牛一致、单品立减就绪、报名价不超线，可放心一键推送。" />}

            {/* 档1 明细: 千牛偏低/不一致 → 该改成多少 */}
            {(pre.price_mismatch_count ?? 0) > 0 && (
              <div>
                <Divider orientation="left" style={{ margin: '4px 0' }}>
                  <Typography.Text strong type="danger"><WarningOutlined /> 档1 商品价不一致（千牛偏低的会让报名被拒 → 去千牛把一口价抬到"应改一口价"）</Typography.Text>
                </Divider>
                <Table size="small" pagination={{ pageSize: 8 }} rowKey="sku_code"
                  dataSource={pre.price_mismatches || []}
                  columns={[
                    { title: 'SKU编码', dataIndex: 'sku_code', width: 150 },
                    { title: '名称', dataIndex: 'name', ellipsis: true },
                    { title: 'ERP日常价', dataIndex: 'erp_daily', width: 96, render: (v: number) => `¥${v}` },
                    { title: '现千牛标价', dataIndex: 'qn_price', width: 100,
                      render: (v: number, r: any) => <Typography.Text type={r.too_low ? 'danger' : undefined}>¥{v}</Typography.Text> },
                    { title: '应改一口价(÷0.75)', dataIndex: 'expected_qn', width: 130, render: (v: number) => <b>¥{v}</b> },
                    { title: '', dataIndex: 'too_low', width: 80,
                      render: (t: boolean) => t ? <Tag color="red">偏低·急</Tag> : <Tag color="orange">偏高</Tag> },
                  ]} />
              </div>
            )}

            {/* 档3 明细: 券后超线 */}
            {(pre.floor_conflict_count ?? 0) > 0 && (
              <div>
                <Divider orientation="left" style={{ margin: '4px 0' }}>
                  <Typography.Text strong type="danger"><WarningOutlined /> 档3 券后价超线（报名价 高于 已生效活动价 → 淘宝必拒整商品）</Typography.Text>
                </Divider>
                <Table size="small" pagination={{ pageSize: 8 }} rowKey="sku_code"
                  dataSource={pre.floor_conflicts || []}
                  columns={[
                    { title: 'SKU编码', dataIndex: 'sku_code', width: 150 },
                    { title: '名称', dataIndex: 'name', ellipsis: true },
                    { title: '计划报名价', dataIndex: 'planned', width: 100, render: (v: number) => `¥${v}` },
                    { title: '已生效价(硬底)', dataIndex: 'enrolled_floor', width: 120, render: (v: number) => `¥${v}` },
                    { title: '超出', dataIndex: 'over', width: 90, render: (v: number) => <Tag color="red">+¥{v}</Tag> },
                  ]} />
              </div>
            )}

            {/* 其余检查(整商品不全/撞号/坏价/动销) 折叠 */}
            {((pre.incomplete_item_count ?? 0) > 0 || (pre.skuid_collision_count ?? 0) > 0
              || pre.bad_product_count > 0 || (pre.no_sales_count ?? 0) > 0) && (
              <Collapse ghost items={[{
                key: 'more', label: <Typography.Text type="secondary">其他检查明细（整商品不全 / SKUID撞号 / 坏价 / 动销）</Typography.Text>,
                children: (
                  <Space direction="vertical" style={{ width: '100%' }} size="small">
                    {(pre.incomplete_item_count ?? 0) > 0 && (
                      <Table size="small" pagination={{ pageSize: 5 }} rowKey="taobao_item_id"
                        title={() => <Typography.Text strong type="danger">整商品SKU不全（缺价SKU的商品被整个剔除，补价后自动纳入）</Typography.Text>}
                        dataSource={pre.incomplete_items || []}
                        columns={[
                          { title: '商品ID', dataIndex: 'taobao_item_id', width: 140 },
                          { title: '商品', dataIndex: 'product', ellipsis: true },
                          { title: '缺价SKU', dataIndex: 'missing_skus',
                            render: (ms: string[]) => (ms || []).map((m) => <Tag color="red" key={m}>{m}</Tag>) },
                        ]} />
                    )}
                    {(pre.skuid_collision_count ?? 0) > 0 && (
                      <Table size="small" pagination={false} rowKey="taobao_sku_id"
                        title={() => <Typography.Text strong type="danger">淘宝SKUID撞号（一个SKUID绑多个编码，先改映射）</Typography.Text>}
                        dataSource={pre.skuid_collisions || []}
                        columns={[
                          { title: '淘宝SKUID', dataIndex: 'taobao_sku_id', width: 170 },
                          { title: '被这些编码共用', dataIndex: 'names',
                            render: (names: string[]) => (names || []).map((n) => <Tag color="red" key={n}>{n}</Tag>) },
                        ]} />
                    )}
                    {pre.bad_product_count > 0 && (
                      <Table size="small" pagination={{ pageSize: 5 }} rowKey="product_code"
                        title={() => <Typography.Text strong type="danger">坏价产品（各尺寸报名价雷同=未真实定价，已排除；改真实价后自动纳入）</Typography.Text>}
                        dataSource={pre.bad_products}
                        columns={[
                          { title: '产品编码', dataIndex: 'product_code', width: 150 },
                          { title: '名称', dataIndex: 'name', ellipsis: true },
                          { title: 'SKU数', dataIndex: 'sku_count', width: 70 },
                          { title: '原因', dataIndex: 'reason', ellipsis: true },
                        ]} />
                    )}
                    {(pre.no_sales_count ?? 0) > 0 && (
                      <Space wrap size={4}>
                        <Typography.Text strong style={{ color: '#d46b08' }}>近60天0销量(疑似动销不达标，仅警示)：</Typography.Text>
                        {(pre.no_sales_items || []).map((it) => <Tag key={it.taobao_item_id} color="orange">{it.product}</Tag>)}
                      </Space>
                    )}
                  </Space>
                ),
              }]} />
            )}
          </Space>
        )}
      </Card>

      {/* ── ④ 一键推送本场次 (向导, 每步停下确认) ── */}
      <Card size="small" title={<Space><CloudUploadOutlined /><b>④ 一键推送「{campaign.label}」</b>
        <Tag color={campaign.tagColor}>{campaign.tag}</Tag>
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>每步停下等你看比对、确认了才提交</Typography.Text></Space>}>
        <Steps size="small" current={wizardStep} style={{ marginBottom: 16 }}
          items={WIZARD_STEPS.map((t, i) => ({ title: t, status: stepStatus(i) }))
            .concat([{ title: '完成', status: wizardStep >= 3 ? 'finish' : 'wait' }])} />

        {/* 步骤①核价 */}
        {wizardStep === 0 && (
          <Space direction="vertical" style={{ width: '100%' }} size={8}>
            {!pre && <Alert type="info" showIcon message="先在上方「③ 三档核对」跑一次，才知道商品价要不要改。" />}
            {pre && !tier1Bad && (
              <Alert type="success" showIcon message={`档1 无差异（${pre.price_ok ?? 0}/${pre.price_checked ?? 0} 一致）——商品价不用改，直接下一步。`} />
            )}
            {pre && tier1Bad && (
              <Alert type="error" showIcon
                message={`有 ${pre.price_too_low_count} 个 SKU 千牛一口价偏低，会导致报名被拒。`}
                description={<span>ERP 价为准：去千牛把这些一口价抬到"应改一口价(=日常价÷0.75)"。可下载对照表照着改；改完点"已改好"。
                  <br/><Typography.Text type="secondary">（真·自动推标价到千牛需一次录制后一键完成；当前先出对照表。）</Typography.Text></span>} />
            )}
            <Space wrap>
              <Button icon={<DownloadOutlined />} loading={busy === 'pq'}
                onClick={() => dl('pq', '商品价格快速编辑表', '商品价格快速编辑_ERP标准.xlsx', downloadProductPriceQuickEdit)}>
                下载标价对照表（现价 vs 应改）</Button>
              <Link to="/shop-price-board"><Button icon={<TableOutlined />}>打开改价台</Button></Link>
              <Button type="primary" icon={<CheckCircleOutlined />} onClick={() => setWizardStep(1)}>
                {tier1Bad ? '已改好千牛标价，下一步 →' : '✓ 无差异，下一步 →'}</Button>
            </Space>
          </Space>
        )}

        {/* 步骤②单品立减 */}
        {wizardStep === 1 && (
          <Space direction="vertical" style={{ width: '100%' }} size={8}>
            <Alert type="info" showIcon
              message={`挂本场次单品立减（${campaign.siTier === 'mid' ? '10%' : campaign.siTier === 'big' ? '12%' : '15%'} 档减金额）到千牛，先出比对表，你确认了才真提交。`} />
            <Space>
              <Button onClick={() => setWizardStep(0)}>← 上一步</Button>
              <Button type="primary" icon={<CloudUploadOutlined />} loading={staging && upChannel === 'single_item_discount'}
                onClick={() => doStage('single_item_discount', campaign.siTier, true)}>
                挂到千牛比对 · 单品立减</Button>
              <Button type="text" onClick={() => { setWizardStep(2); setInWizard(false); }}>跳过（本场不改单品立减）→</Button>
            </Space>
          </Space>
        )}

        {/* 步骤③报名 */}
        {wizardStep === 2 && (
          <Space direction="vertical" style={{ width: '100%' }} size={8}>
            {campaign.templatePending
              ? <Alert type="warning" showIcon message="618/双11 报名模板尚未接入系统，先看下方「高级·手动」里的接入步骤；此场报名暂不能一键推送。" />
              : <Alert type="info" showIcon
                  message={campaign.signupCommittable
                    ? '超级立减：挂到 apply.htm 批量导入→落草稿→比对→确认后一键发布（可撤销、全是降价）。'
                    : '大促报名：挂进千牛「商品批量导入」到草稿+比对；确认无误后到千牛手动「发布报名」。'} />}
            <Space>
              <Button onClick={() => setWizardStep(1)}>← 上一步</Button>
              <Button type="primary" danger={campaign.signupCommittable} icon={<CloudUploadOutlined />}
                disabled={campaign.templatePending}
                loading={staging && upChannel === campaign.signupChannel}
                onClick={() => doStage(campaign.signupChannel, campaign.signupTier, true)}>
                挂到千牛 · {campaign.signupLabel}</Button>
            </Space>
          </Space>
        )}

        {/* 完成 */}
        {wizardStep >= 3 && (
          <Alert type="success" showIcon icon={<CheckCircleOutlined />}
            message={`「${campaign.label}」一键推送完成。`}
            description={campaign.signupCommittable
              ? '单品立减已提交、超级立减已一键发布。到千牛活动页刷新可见。'
              : '单品立减已提交、大促报名已挂草稿——最后到千牛点「发布报名」即生效。'}
            action={<Button size="small" onClick={() => setWizardStep(0)}>再走一遍</Button>} />
        )}
      </Card>

      {/* ── 高级 · 手动 (折叠: 各档下载表、单步上传、SKU轮换、618接入) ── */}
      <Collapse items={[{
        key: 'adv',
        label: <Space><TableOutlined /><b>高级 · 手动</b>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>各档下载表 / 单步上传 / SKU轮换 / 618模板接入</Typography.Text></Space>,
        children: (
          <Space direction="vertical" style={{ width: '100%' }} size="middle">
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
                      onClick={() => dl('ps-618', '大促报名·超大促15%', '大促活动报名_超级大促15%.xlsx', () => downloadPromoSignup('big618'))}>超大促 15% 报名表(换SKU)</Button>
                  </Space>
                </Card>
              </Col>
            </Row>

            {/* 单步手动上传 */}
            <Card size="small" title="单步上传到千牛（先比对再提交）">
              <Space wrap>
                <Button icon={<CloudUploadOutlined />} loading={staging && upChannel === 'single_item_discount' && !inWizard}
                  onClick={() => doStage('single_item_discount', 'big')}>单品立减(88VIP12%)</Button>
                <Button icon={<CloudUploadOutlined />} loading={staging && upChannel === 'promo_signup' && !inWizard}
                  onClick={() => doStage('promo_signup', 'big')}>大促报名(挂草稿)</Button>
                <Button icon={<CloudUploadOutlined />} loading={staging && upChannel === 'super_reduce' && !inWizard}
                  onClick={() => doStage('super_reduce', 'big')}>超级立减长期(草稿→一键发布)</Button>
              </Space>
            </Card>

            {/* 超大促 SKU 轮换 */}
            <Card size="small" title="超大促 SKU 轮换（618/双11 15% 让利，绕15天最低价）">
              <Space direction="vertical" style={{ width: '100%' }} size="small">
                <Alert type="info" showIcon style={{ fontSize: 12 }}
                  message="系统按尺寸阶梯算出每个 skuId 该改成的 商家编码/规格/价格。规格千牛没批量口→照下表在千牛逐个改；改完点「同步系统映射」，ERP自动重刷。商家编码永远跟尺寸走、不串位。" />
                <Space>
                  <Input placeholder="产品编码，如 PPS26330140117" value={rotPc} style={{ width: 260 }}
                    onChange={(e) => setRotPc(e.target.value)} onPressEnter={doRotPreview} />
                  <Button type="primary" loading={rotLoading} onClick={doRotPreview}>预览轮换</Button>
                  {rotPlan?.ok && (
                    <Popconfirm title="确认千牛已把规格/价格/编码都轮换好了？" description="这会把 ERP 的 skuId 映射按新轮换重刷（不可逆）"
                      okText="已轮换完，同步" cancelText="还没" onConfirm={doRotApply}>
                      <Button danger loading={rotApplying}>千牛轮换完 → 同步系统映射</Button>
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
          </Space>
        ),
      }]} />

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
                <Button key="cancel" onClick={() => { setStageRes(null); setUpChannel(null); }}>取消（不提交）</Button>,
                <Button key="ok" type="primary" danger loading={committing} icon={<CloudUploadOutlined />}
                  onClick={doCommit}>✅ 确认最后一步上传（不可逆）</Button>,
              ]
            : stageRes?.channel === 'super_reduce'
            ? [
                <Button key="cancel" disabled={committing || superProgress?.status === 'running'}
                  onClick={() => { setStageRes(null); setUpChannel(null); setSuperProgress(null); }}>取消（不提交）</Button>,
                <Button key="ok" type="primary" danger
                  loading={committing || superProgress?.status === 'running'}
                  disabled={superProgress?.status === 'done'}
                  icon={<CloudUploadOutlined />} onClick={doCommit}>
                  ✅ 确认批量导入并一键发布（不可逆）</Button>,
              ]
            : [
                <Typography.Text key="note" type="secondary" style={{ marginRight: 12, fontSize: 12 }}>
                  已挂到千牛「草稿」，去千牛商品管理核对后手动发布报名
                </Typography.Text>,
                <Button key="close" type="primary" onClick={closeStageModal}>
                  {inWizard ? '✓ 已挂草稿，完成' : '知道了'}</Button>,
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
                  <Typography.Text strong style={{ color: '#cf1322' }}>⚠️ 失败的 {failN} 条不会上传</Typography.Text>
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
                      <div key={r.reason}>🔴 <b>{r.items} 件</b>：{r.reason}</div>
                    ))}
                    <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                      三档核对页有对应红字明细（券后价超线 / 整商品SKU不全 / 动销）——按核对修完再导。
                    </Typography.Text>
                  </Space>
                } />
            )}

            {stageRes.screenshot_base64 && (
              <div>
                <Typography.Text type="secondary" style={{ fontSize: 13 }}>
                  👇 千牛页面截图（文件已挂的实证，<b>点图可放大看清</b>）
                </Typography.Text>
                <div style={{ marginTop: 6, border: '1px solid #eee', borderRadius: 6, overflow: 'hidden' }}>
                  <Image alt="千牛已挂文件截图" style={{ width: '100%' }}
                    src={`data:image/png;base64,${stageRes.screenshot_base64}`} />
                </div>
              </div>
            )}

            <Alert showIcon type={priceOk ? 'success' : 'error'}
              message={priceOk
                ? `✅ 上传价 = 系统价：全部 ${totalN} 行一分不差（严格核对，无出入）`
                : `⛔ 有 ${misN} 行「上传值 ≠ 系统价」！下方红字行，未按系统价，别提交、先叫我查`}
              description={priceOk
                ? '「上传值」直接读自即将上传千牛的那份表，「系统应填」按定价独立重算，两者逐条对到分。'
                : '这不该出现——上传表和系统定价理应一致。请把红字行截图发我，我立刻定位。'} />
            <div>
              <Typography.Text type="secondary" style={{ fontSize: 13 }}>
                👇 逐 SKU 核对：<b>上传值</b>（真正传千牛的数）vs <b>系统应填</b>（按定价重算）；差 &gt;1 分即标红
              </Typography.Text>
              <Table size="middle" rowKey="taobao_sku_id" pagination={{ pageSize: 12 }} style={{ marginTop: 6 }}
                dataSource={stageRes.compare_rows || []}
                onRow={(r: any) => (
                  (r.mismatch || failCodes.includes(r.sku_code)) ? { style: { background: '#fff1f0' } } : {}
                )}
                columns={[
                  { title: 'SKU编码', dataIndex: 'sku_code', width: 150,
                    render: (v: string) => failCodes.includes(v)
                      ? <Typography.Text type="danger">{v} <Tag color="red">千牛失败</Tag></Typography.Text> : v },
                  { title: '淘宝SKUID', dataIndex: 'taobao_sku_id', width: 120 },
                  { title: '名称', dataIndex: 'name', ellipsis: true },
                  { title: `上传值·${label0}`, dataIndex: 'uploaded_value', width: 110,
                    render: (v: number | null, r: any) => v == null
                      ? '-' : <Typography.Text type={r.mismatch ? 'danger' : undefined} strong>¥{v}</Typography.Text> },
                  { title: `系统应填·${label0}`, dataIndex: 'system_value', width: 110,
                    render: (v: number | null) => v != null ? `¥${v}` : '-' },
                  { title: '核对', dataIndex: 'mismatch', width: 66, align: 'center' as const,
                    render: (m: boolean, r: any) => m
                      ? <Tag color="red">✗ 差¥{r.uploaded_value != null && r.system_value != null
                          ? Math.abs(r.uploaded_value - r.system_value).toFixed(2) : '?'}</Tag>
                      : <Tag color="green">✓</Tag> },
                  { title: '目标到手', dataIndex: 'target_shoudao', width: 96,
                    render: (v: number | null) => v != null ? `¥${v}` : '-' },
                ]} />
            </div>
          </Space>
          );
        })()}
      </Modal>

      {/* ── 管理档期日历 Modal ── */}
      <CalendarModal open={calOpen} periods={periods} onClose={() => setCalOpen(false)}
        onSaved={(next) => { setPeriods(next); loadCalendar(); }} />
    </Space>
  );
}

// ── 档期日历编辑弹窗 ──
function CalendarModal({ open, periods, onClose, onSaved }: {
  open: boolean; periods: ActivityPeriod[]; onClose: () => void; onSaved: (p: ActivityPeriod[]) => void;
}) {
  const [rows, setRows] = useState<ActivityPeriod[]>(periods);
  const [saving, setSaving] = useState(false);
  useEffect(() => { if (open) setRows(periods); }, [open, periods]);

  const setRow = (i: number, patch: Partial<ActivityPeriod>) =>
    setRows((rs) => rs.map((r, j) => (j === i ? { ...r, ...patch } : r)));
  const addRow = () => setRows((rs) => [...rs, { name: '', tier: 'big', start: dayjs().format('YYYY-MM-DD'), end: null }]);
  const delRow = (i: number) => setRows((rs) => rs.filter((_, j) => j !== i));
  const save = async () => {
    setSaving(true);
    try {
      const clean = rows.filter((r) => r.name.trim() && r.start);
      const res = await saveActivityCalendar(clean);
      message.success('档期已保存');
      onSaved(res.periods); onClose();
    } catch { message.error('保存失败（需 admin/operator）'); } finally { setSaving(false); }
  };

  const TIER_OPTS = [
    { value: 'mid', label: '中促 10%' }, { value: 'big', label: '88VIP大促 12%' },
    { value: 'big618', label: '618/双11 15%' }, { value: 'super_reduce', label: '超级立减长期' },
  ];

  return (
    <Modal open={open} onCancel={onClose} width={860} title={<Space><CalendarOutlined /><b>管理活动档期</b></Space>}
      okText="保存档期" confirmLoading={saving} onOk={save}>
      <Alert type="info" showIcon style={{ marginBottom: 12 }} banner
        message="把接下来几波活动的名称/力度/起止都排进来，单品立减就能自动算出结束时间（下一档开始前一刻）。" />
      {rows.length === 0 && <Empty description="还没排档期" />}
      <Space direction="vertical" style={{ width: '100%' }} size={8}>
        {rows.map((r, i) => (
          <Row key={i} gutter={8} align="middle">
            <Col span={6}><Input placeholder="活动名，如 88VIP大促" value={r.name}
              onChange={(e) => setRow(i, { name: e.target.value })} /></Col>
            <Col span={5}><Select style={{ width: '100%' }} value={r.tier} options={TIER_OPTS}
              onChange={(v) => setRow(i, { tier: v as ActivityPeriod['tier'] })} /></Col>
            <Col span={10}><DatePicker.RangePicker style={{ width: '100%' }} allowEmpty={[false, true]}
              value={[r.start ? dayjs(r.start) : null, r.end ? dayjs(r.end) : null]}
              onChange={(v) => setRow(i, {
                start: v && v[0] ? v[0].format('YYYY-MM-DD') : r.start,
                end: v && v[1] ? v[1].format('YYYY-MM-DD') : null,
              })} /></Col>
            <Col span={3}><Button danger size="small" onClick={() => delRow(i)}>删</Button></Col>
          </Row>
        ))}
        <Button type="dashed" block onClick={addRow}>+ 加一档</Button>
      </Space>
    </Modal>
  );
}
