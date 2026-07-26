/**
 * 活动生命周期向导（P3，spec = docs/活动生命周期系统_执行plan.md 四/五节）。
 * 四步每步确认制：①预检(R1~R12) → ②推单品立减(两段式 stage→commit) → ③推报名 → ④自动核对。
 * 铁则：ERP 价格唯一标准；报名价恒=日常价；每场只变单品立减；
 *   单品立减/报名导入即生效无草稿(R12) → 立减先 stage 挂文件看千牛校验、人工确认后才 commit；
 *   报名没有 stage/commit 两段（promo_signup 导入即报名成功），文案写明、推前勾选确认。
 * ★契约对齐：所有字段名/枚举以后端 /api/campaigns 实际返回为准（campaigns.ts 已同步）。
 */
import { useEffect, useMemo, useState } from 'react';
import {
  Alert, Button, Card, Checkbox, Col, Collapse, Divider, Image, Row, Space, Statistic, Steps,
  Table, Tag, Typography, Upload, message,
} from 'antd';
import type { UploadFile } from 'antd';
import {
  AuditOutlined, CheckCircleOutlined, CloudUploadOutlined, ExperimentOutlined,
  PaperClipOutlined, RightOutlined, WarningOutlined,
} from '@ant-design/icons';
import {
  CAMPAIGN_STATUS_LABEL, CAMPAIGN_TYPES, NO_SALES_FORMULA, SIGNUP_PRICE_RULE, TIER_FORMULA,
  fetchCampaignRows, pushCampaignDiscount, pushCampaignSignup, reconVerdictKey, reconVerdictMeta,
  runCampaignPrecheck, runCampaignRecon, runCampaignReconManual,
  type CampaignPlan, type CampaignPrecheckResult, type CampaignPushResult,
  type CampaignReconResult, type CampaignStatus, type PrecheckCheck,
} from '../api/campaigns';

const STEP_TITLES = ['预检 R1~R12', '推单品立减', '推报名', '自动核对', '完成'];

function stepFromStatus(s: CampaignStatus): number {
  switch (s) {
    case 'draft': return 0;
    case 'precheck': return 1;
    case 'discount_pushed': return 2;
    case 'signup_pushed': return 3;
    case 'reconciled':
    case 'alarmed': return 4;
    default: return 0;
  }
}

const CHECK_LEVEL_META = {
  error: { color: 'red', label: '阻塞' },
  warn: { color: 'orange', label: '警示' },
  info: { color: 'blue', label: '提示' },
  pass: { color: 'green', label: '通过' },
} as const;

// 预检命中明细通用渲染：R6 是商品ID字符串数组，其余规则是异构对象数组 → 动态列
function CheckItemsView({ items }: { items: unknown[] }) {
  if (!items.length) return <Typography.Text type="secondary">无命中明细</Typography.Text>;
  if (typeof items[0] === 'string') {
    return <>{(items as string[]).map((s) => <Tag key={s} style={{ marginBottom: 4 }}>{s}</Tag>)}</>;
  }
  const objs = (items as Record<string, unknown>[]).map((o, i) => ({ ...o, _k: i }));
  const keys = Array.from(new Set(objs.flatMap((o) => Object.keys(o)))).filter((k) => k !== '_k');
  const columns = keys.map((k) => ({
    title: k, dataIndex: k, ellipsis: true,
    render: (v: unknown) => {
      if (v == null) return '-';
      if (Array.isArray(v)) return v.map(String).join('、');
      if (typeof v === 'object') return JSON.stringify(v);
      return String(v);
    },
  }));
  return <Table size="small" rowKey="_k" pagination={{ pageSize: 8 }}
    dataSource={objs} columns={columns} scroll={{ x: true }} />;
}

export default function ActivityCampaignWizard({ plan, onPlanChange, onRestart }: {
  plan: CampaignPlan;
  onPlanChange: (p: CampaignPlan) => void;
  onRestart: () => void;
}) {
  const [step, setStep] = useState(() => stepFromStatus(plan.status));
  // 步骤①预检
  const [pre, setPre] = useState<CampaignPrecheckResult | null>(null);
  const [preLoading, setPreLoading] = useState(false);
  const [revokeConfirmed, setRevokeConfirmed] = useState(false);   // ★前端本地卡点，不传后端
  const [rowCounts, setRowCounts] = useState<{ signup?: number; discount?: number }>({});
  // 步骤②推立减（两段式 stage→commit）
  const [stageRes, setStageRes] = useState<CampaignPushResult | null>(null);
  const [commitRes, setCommitRes] = useState<CampaignPushResult | null>(null);
  const [stageChecked, setStageChecked] = useState(false);         // stage 回执核对确认
  // 步骤③推报名
  const [signupRes, setSignupRes] = useState<CampaignPushResult | null>(null);
  const [signupConfirmed, setSignupConfirmed] = useState(false);   // 报名导入即生效 → 推前确认
  const [pushing, setPushing] = useState<'stage' | 'commit' | 'signup' | null>(null);
  // 步骤④核对
  const [recon, setRecon] = useState<CampaignReconResult | null>(null);
  const [reconLoading, setReconLoading] = useState<'auto' | 'manual' | null>(null);
  const [itemsFiles, setItemsFiles] = useState<UploadFile[]>([]);
  const [discountFiles, setDiscountFiles] = useState<UploadFile[]>([]);
  const [productFiles, setProductFiles] = useState<UploadFile[]>([]);

  // 换了计划 → 向导全量重置（步骤按该计划状态续跑）
  useEffect(() => {
    setStep(stepFromStatus(plan.status));
    setPre(null); setRevokeConfirmed(false); setRowCounts({});
    setStageRes(null); setCommitRes(null); setStageChecked(false);
    setSignupRes(null); setSignupConfirmed(false); setRecon(null);
    setItemsFiles([]); setDiscountFiles([]); setProductFiles([]);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [plan.id]);

  const typeDef = CAMPAIGN_TYPES.find((t) => t.value === plan.campaign_type);
  const statusMeta = CAMPAIGN_STATUS_LABEL[plan.status];

  // ── ① 预检 ──
  const doPrecheck = async () => {
    setPreLoading(true);
    setRevokeConfirmed(false);
    message.loading({ content: '预检中：按平台规则库 R1~R12 全量体检（不产文件、不碰千牛）…', key: 'pre', duration: 0 });
    try {
      const r = await runCampaignPrecheck(plan.id);
      setPre(r);
      onPlanChange(r.plan);
      message.success({ content: '预检完成', key: 'pre', duration: 1.4 });
      // 顺手拉两类行预览的行数（确认口径用；失败不挡流程）
      Promise.all([
        fetchCampaignRows(plan.id, 'signup').catch(() => null),
        fetchCampaignRows(plan.id, 'discount').catch(() => null),
      ]).then(([s, d]) => setRowCounts({ signup: s?.stats.rows, discount: d?.stats.rows }));
    } catch {
      message.error({ content: '预检失败（后端 /api/campaigns 预检接口未响应）', key: 'pre' });
    } finally { setPreLoading(false); }
  };

  const blockChecks = useMemo(() => (pre?.checks ?? []).filter((c) => c.level === 'error'), [pre]);
  const warnChecks = useMemo(() => (pre?.checks ?? []).filter((c) => c.level === 'warn'), [pre]);
  const r6Check = useMemo(() => (pre?.checks ?? []).find((c) => c.rule === 'R6'), [pre]);
  const revokeChecks = useMemo(
    () => (pre?.checks ?? []).filter((c) => (c.rule === 'R5' || c.rule === 'R11') && c.level !== 'pass'),
    [pre],
  );
  // 本地卡点：有阻塞不放行；R5/R11 撤销类警示存在时必须勾选确认（revoke_confirmed 不传后端）
  const precheckPassable = !!pre && !pre.has_error
    && (revokeChecks.length === 0 || revokeConfirmed);

  // ── ② 推立减（两段式）/ ③ 推报名 ──
  const doPushDiscount = async (phase: 'stage' | 'commit') => {
    setPushing(phase);
    const label = phase === 'stage' ? '单品立减·预校验(stage)' : '单品立减·正式提交(commit)';
    message.loading({ content: `正在${label}（Web-Agent 自动化，约1~3分钟）…`, key: 'push', duration: 0 });
    try {
      const r = await pushCampaignDiscount(plan.id, phase);
      message.destroy('push');
      if (phase === 'stage') { setStageRes(r); setStageChecked(false); setCommitRes(null); }
      else setCommitRes(r);
      if (r.ok) {
        if (phase === 'commit') {
          onPlanChange({ ...plan, status: 'discount_pushed' });
          message.success('单品立减已正式提交（导入即生效），核对回执后走下一步');
        } else {
          message.success('文件已挂到千牛、停在提交前——先核对下方校验结果，确认无误再点正式提交');
        }
      } else {
        message.error(r.need_scan ? '淘宝登录态过期，请先扫码再重试' : (r.error || r.message || `${label}失败`));
      }
    } catch {
      message.destroy('push');
      message.error('推送服务未响应（确认 PC 上 Web-Agent 在线）');
    } finally { setPushing(null); }
  };

  const doPushSignup = async () => {
    setPushing('signup');
    message.loading({ content: '正在推送活动报名到千牛（导入即报名成功，Web-Agent 自动化）…', key: 'push', duration: 0 });
    try {
      const r = await pushCampaignSignup(plan.id);
      message.destroy('push');
      setSignupRes(r);
      if (r.ok) {
        onPlanChange({ ...plan, status: 'signup_pushed' });
        message.success('报名已推送并生效，先核对下方千牛回执，确认无误再走核对步');
      } else {
        message.error(r.need_scan ? '淘宝登录态过期，请先扫码再重试' : (r.error || r.message || '报名推送失败'));
      }
    } catch {
      message.destroy('push');
      message.error('推送服务未响应（确认 PC 上 Web-Agent 在线）');
    } finally { setPushing(null); }
  };

  // ── ④ 核对 ──
  const doRecon = async () => {
    setReconLoading('auto');
    message.loading({ content: '自动核对中：Web-Agent 去千牛按活动标题找活动 → 导出已报商品 → 逐SKU比对…', key: 'recon', duration: 0 });
    try {
      const r = await runCampaignRecon(plan.id);
      setRecon(r);
      onPlanChange({ ...plan, status: r.summary.title_ok === false || r.alarm_count > 0 ? 'alarmed' : 'reconciled' });
      message.destroy('recon');
      if (r.summary.title_ok === false) message.error('活动名称校验失败，疑推错活动（详见下方红色横幅，飞书已报警）');
      else if (r.alarm_count > 0) message.warning(`核对完成：有 ${r.alarm_count} 个 SKU 差异超2元，见红榜`);
      else message.success('核对完成：无超2元差异');
    } catch {
      message.destroy('recon');
      message.error('自动核对失败（WA 导出没成时用下方「手动上传导出文件」兜底）');
    } finally { setReconLoading(null); }
  };

  const doReconManual = async () => {
    const items = itemsFiles[0]?.originFileObj as File | undefined;
    const disc = discountFiles[0]?.originFileObj as File | undefined;
    const prod = productFiles[0]?.originFileObj as File | undefined;
    if (!items && !disc && !prod) {
      message.warning('至少传一份导出表（推荐「活动商品导出」：J列=活动普惠券后价，核对的主依据）');
      return;
    }
    setReconLoading('manual');
    message.loading({ content: '解析上传文件并逐SKU比对…', key: 'recon', duration: 0 });
    try {
      const r = await runCampaignReconManual(plan.id, {
        activity_file: items, discount_file: disc, product_file: prod,
      });
      setRecon(r);
      onPlanChange({ ...plan, status: r.summary.title_ok === false || r.alarm_count > 0 ? 'alarmed' : 'reconciled' });
      message.destroy('recon');
      message.success('手动核对完成');
    } catch {
      message.destroy('recon');
      message.error('手动核对失败（检查文件是不是千牛原样导出、没改过表头）');
    } finally { setReconLoading(null); }
  };

  const stepStatus = (idx: number): 'finish' | 'process' | 'wait' =>
    step > idx ? 'finish' : step === idx ? 'process' : 'wait';

  return (
    <Card size="small"
      title={<Space><CloudUploadOutlined /><b>③ 生命周期向导「{plan.name}」</b>
        <Tag color={statusMeta.color}>{statusMeta.label}</Tag>
        {typeDef && <Tag>{typeDef.label} · 官方立减{typeDef.rate} · 到手={typeDef.targetLabel}</Tag>}
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          {plan.start_at}{plan.end_at ? ` ~ ${plan.end_at}` : ' 起'}
        </Typography.Text></Space>}>
      <Steps size="small" current={step} style={{ marginBottom: 16 }}
        items={STEP_TITLES.map((t, i) => ({ title: t, status: stepStatus(i) }))} />

      {/* ── 步骤① 预检 ── */}
      {step === 0 && (
        <Space direction="vertical" style={{ width: '100%' }} size="middle">
          <Alert type="info" showIcon
            message="预检 = 推送前的全量体检，不产文件、不改数据、不碰千牛。"
            description={<span style={{ fontSize: 13 }}>
              按平台规则库 R1~R14 逐条过：历史标价线、券后线、整品 SKU 完整性、下架 SKU、
              已报名冲突、动销门等（全部为 2026-07-17 实战实锤规则）。有阻塞项就先修再推，别硬推。
            </span>} />
          <Button type="primary" icon={<ExperimentOutlined />} loading={preLoading} onClick={doPrecheck}>
            {pre ? '重新预检' : '运行预检'}</Button>

          {pre && (
            <Space direction="vertical" style={{ width: '100%' }} size="middle">
              <Row gutter={16}>
                <Col span={6}>
                  <Card size="small" styles={{ body: { padding: 12 } }}
                    style={{ borderColor: blockChecks.length ? '#ffccc7' : '#b7eb8f' }}>
                    <Statistic title="阻塞规则（必须先修）" value={blockChecks.length}
                      valueStyle={{ color: blockChecks.length ? '#cf1322' : '#3f8600' }} suffix="条" />
                  </Card>
                </Col>
                <Col span={6}>
                  <Card size="small" styles={{ body: { padding: 12 } }}
                    style={{ borderColor: warnChecks.length ? '#ffe58f' : '#b7eb8f' }}>
                    <Statistic title="警示规则（看过再确认）" value={warnChecks.length}
                      valueStyle={{ color: warnChecks.length ? '#d46b08' : '#3f8600' }} suffix="条" />
                  </Card>
                </Col>
                <Col span={6}>
                  <Card size="small" styles={{ body: { padding: 12 } }}>
                    <Statistic title="报名行（全量出行）" value={rowCounts.signup ?? '…'} suffix="行" />
                  </Card>
                </Col>
                <Col span={6}>
                  <Card size="small" styles={{ body: { padding: 12 } }}>
                    <Statistic title="单品立减行" value={rowCounts.discount ?? '…'} suffix="行" />
                  </Card>
                </Col>
              </Row>

              {!pre.has_error && revokeChecks.length === 0 && (
                <Alert type="success" showIcon icon={<CheckCircleOutlined />}
                  message="预检全绿：无阻塞、无需撤销，可直接进入推送。" />
              )}

              {/* 禁撤名单（红字，R6 动销门单行道） */}
              {!!r6Check && r6Check.items.length > 0 && (
                <Alert type="error" showIcon icon={<WarningOutlined />}
                  message={<b>禁撤名单（{r6Check.items.length} 品）——这些在场报名绝对不要去千牛撤销</b>}
                  description={<div>
                    <Typography.Text style={{ fontSize: 13 }}>
                      近60天零动销的品撤销后会触发平台「动销门」（销量≥1 才能报名），撤了就报不回来，单行道（R6）。
                      系统推送时按「到手=中促+1」单独压价，你什么都不用做。
                    </Typography.Text>
                    <div style={{ marginTop: 6 }}>
                      {(r6Check.items as string[]).map((iid) => (
                        <Tag color="red" key={iid} style={{ marginBottom: 4 }}>{iid}</Tag>
                      ))}
                    </div>
                  </div>} />
              )}

              {/* 撤销类警示（R5/R11）：先去千牛处理，回来打勾（前端本地卡点，不传后端） */}
              {revokeChecks.length > 0 && (
                <Alert type="warning" showIcon
                  message={<b>撤销类警示（{revokeChecks.length} 条）——涉及的品先去千牛撤销/删旧批，处理完回来打勾</b>}
                  description={<Space direction="vertical" style={{ width: '100%' }} size={8}>
                    {revokeChecks.map((c) => (
                      <div key={c.rule}>
                        <Tag color="orange">{c.rule}</Tag>
                        <Typography.Text style={{ fontSize: 13 }}>{c.title}</Typography.Text>
                      </div>
                    ))}
                    <Checkbox checked={revokeConfirmed} onChange={(e) => setRevokeConfirmed(e.target.checked)}>
                      <Typography.Text strong>
                        我已核对：要改价重报的品已在千牛撤销、在场旧立减批已删除（没有的跳过）
                      </Typography.Text>
                    </Checkbox>
                  </Space>} />
              )}

              {/* R1~R12 全量明细（pass 也展示，留档可查） */}
              <Collapse
                items={pre.checks.map((c: PrecheckCheck) => ({
                  key: c.rule,
                  label: <Space>
                    <Tag color={CHECK_LEVEL_META[c.level].color}>{c.rule} · {CHECK_LEVEL_META[c.level].label}</Tag>
                    <Typography.Text>{c.title}</Typography.Text>
                    {c.items.length > 0 && <Typography.Text type="secondary">命中 {c.items.length}</Typography.Text>}
                  </Space>,
                  children: <CheckItemsView items={c.items} />,
                }))} />

              <Space>
                <Button type="primary" icon={<RightOutlined />} disabled={!precheckPassable}
                  onClick={() => setStep(1)}>
                  {pre.has_error ? '有阻塞项，先修再来' : '预检通过，去推单品立减'}
                </Button>
                {pre.has_error && (
                  <Typography.Text type="danger" style={{ fontSize: 12 }}>
                    阻塞项修完（改千牛对齐 ERP / 等待价格线解除 / 人工批准）后点「重新预检」。
                  </Typography.Text>
                )}
              </Space>
            </Space>
          )}
        </Space>
      )}

      {/* ── 步骤② 推单品立减（两段式：stage 预校验 → 确认 → commit 正式提交） ── */}
      {step === 1 && (
        <Space direction="vertical" style={{ width: '100%' }} size="middle">
          <Alert type="info" showIcon
            message="推单品立减：中间差额全部用一个立减金额补齐，每场活动只变这个数。"
            description={<span style={{ fontSize: 13 }}>
              公式：<b>{TIER_FORMULA[plan.tier]}</b>。<br />
              {NO_SALES_FORMULA}。<br />
              贴线：让幅≤1元记录在案；超过1元的整品暂缓，不轮换、不强降，等待价格线解除或人工决定。
            </span>} />
          <Alert type="warning" showIcon
            message="单品立减导入即生效、没有草稿（平台规则 R12）——所以拆成两段："
            description={<span style={{ fontSize: 13 }}>
              <b>第1步 stage</b>：把表挂到千牛、停在提交前，带回千牛校验结果给你核对；
              <b>第2步 commit</b>：你确认无误后才正式提交，点了就是真上线、不可逆。
            </span>} />

          {stageRes && <PushResultView res={stageRes} title="stage 预校验回执" />}
          {stageRes?.ok && (
            <Checkbox checked={stageChecked} onChange={(e) => setStageChecked(e.target.checked)}>
              <Typography.Text strong>stage 校验结果我已核对，行数/失败原因都没问题，可以正式提交</Typography.Text>
            </Checkbox>
          )}
          {commitRes && <PushResultView res={commitRes} title="commit 提交回执" />}

          <Space wrap>
            <Button onClick={() => setStep(0)}>返回预检</Button>
            <Button type="primary" icon={<CloudUploadOutlined />}
              loading={pushing === 'stage'} onClick={() => doPushDiscount('stage')}>
              {stageRes ? '重新 stage 预校验' : '第1步 · stage 预校验（不提交）'}</Button>
            <Button type="primary" danger icon={<CloudUploadOutlined />}
              disabled={!stageRes?.ok || !stageChecked}
              loading={pushing === 'commit'} onClick={() => doPushDiscount('commit')}>
              第2步 · 正式提交（导入即生效，不可逆）</Button>
            {commitRes?.ok && (
              <Button type="primary" icon={<RightOutlined />} onClick={() => setStep(2)}>
                回执无误，去推报名</Button>
            )}
          </Space>
        </Space>
      )}

      {/* ── 步骤③ 推报名（一次 stage 即生效，推前确认） ── */}
      {step === 2 && (
        <Space direction="vertical" style={{ width: '100%' }} size="middle">
          <Alert type="info" showIcon
            message="推报名：报名价填的永远是 ERP 日常价。"
            description={<span style={{ fontSize: 13 }}>
              {SIGNUP_PRICE_RULE}。<br />
              整品必须全 SKU 一次带齐，缺一个整品被拒（R3，系统已按映射全量出行并断言完整）；
              下架 SKU 已自动剔除（R4）。
            </span>} />
          <Alert type="warning" showIcon
            message={<b>报名没有两段式：promo_signup 导入即报名成功、无需再点发布（R12）——点一次就是真报名。</b>} />
          <Checkbox checked={signupConfirmed} onChange={(e) => setSignupConfirmed(e.target.checked)}>
            <Typography.Text strong>我明白报名导入即生效，确认现在推送</Typography.Text>
          </Checkbox>
          {signupRes && <PushResultView res={signupRes} title="报名回执" />}
          <Space wrap>
            <Button onClick={() => setStep(1)}>返回上一步</Button>
            <Button type="primary" danger icon={<CloudUploadOutlined />}
              disabled={!signupConfirmed}
              loading={pushing === 'signup'} onClick={doPushSignup}>
              {signupRes?.ok ? '重推报名（只会补报，成功品会被判重复）' : '推送活动报名（导入即报名）'}</Button>
            {signupRes?.ok && (
              <Button type="primary" icon={<RightOutlined />} onClick={() => setStep(3)}>
                回执无误，去自动核对</Button>
            )}
          </Space>
        </Space>
      )}

      {/* ── 步骤④ 自动核对 ── */}
      {step === 3 && (
        <Space direction="vertical" style={{ width: '100%' }} size="middle">
          <Alert type="info" showIcon
            message="自动核对：系统去千牛把已报商品导出来，和目标到手逐 SKU 对账。"
            description={<span style={{ fontSize: 13 }}>
              Web-Agent 按活动标题从活动列表点进（活动链接每次不同，不写死）→
              先校验页面标题与「{plan.qn_campaign_title || plan.name}」一致（防推错活动）→ 导出已报商品 →
              逐 SKU 比「活动普惠券后价 vs 目标到手」。差异 &gt; 2 元红榜 + 飞书报警；贴线让幅 0~1 元记录在案。
            </span>} />
          <Space wrap>
            <Button onClick={() => setStep(2)}>返回上一步</Button>
            <Button type="primary" icon={<AuditOutlined />} loading={reconLoading === 'auto'} onClick={doRecon}>
              {recon ? '重新自动核对' : '开始自动核对'}</Button>
          </Space>

          {/* 手动上传兜底 */}
          <Collapse items={[{
            key: 'manual',
            label: <Typography.Text type="secondary">
              手动上传导出文件兜底（WA 点击/下载失败时：自己去千牛导出，传这里继续核对）</Typography.Text>,
            children: (
              <Space direction="vertical" size={8}>
                <Space wrap>
                  <Upload maxCount={1} beforeUpload={() => false} accept=".xlsx,.xls"
                    fileList={itemsFiles} onChange={({ fileList }) => setItemsFiles(fileList)}>
                    <Button icon={<PaperClipOutlined />}>活动商品导出（主依据）</Button>
                  </Upload>
                  <Upload maxCount={1} beforeUpload={() => false} accept=".xlsx,.xls"
                    fileList={discountFiles} onChange={({ fileList }) => setDiscountFiles(fileList)}>
                    <Button icon={<PaperClipOutlined />}>单品立减导出（可选）</Button>
                  </Upload>
                  <Upload maxCount={1} beforeUpload={() => false} accept=".xlsx,.xls"
                    fileList={productFiles} onChange={({ fileList }) => setProductFiles(fileList)}>
                    <Button icon={<PaperClipOutlined />}>商品批量导出（可选）</Button>
                  </Upload>
                </Space>
                <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                  三种表都用千牛原样导出、别改表头：活动商品导出（核对到手价的主依据）、
                  单品立减导出（核对立减金额+活动名称校验）、商品批量导出（核对一口价）。
                </Typography.Text>
                <Button type="primary" ghost loading={reconLoading === 'manual'} onClick={doReconManual}>
                  上传并核对</Button>
              </Space>
            ),
          }]} />

          {recon && <ReconPanel report={recon} expectedTitle={plan.qn_campaign_title || plan.name} />}

          {recon && (
            <Button type={recon.summary.title_ok !== false && recon.alarm_count === 0 ? 'primary' : 'default'}
              icon={<CheckCircleOutlined />} onClick={() => setStep(4)}>
              {recon.summary.title_ok !== false && recon.alarm_count === 0
                ? '核对通过，完成本场活动' : '问题已知晓，仍标记完成'}</Button>
          )}
        </Space>
      )}

      {/* ── 完成 ── */}
      {step === 4 && (
        <Alert type={plan.status === 'alarmed' ? 'warning' : 'success'} showIcon icon={<CheckCircleOutlined />}
          message={`「${plan.name}」生命周期走完：${statusMeta.label}。`}
          description={plan.status === 'alarmed'
            ? '有超2元差异或名称校验问题挂着（飞书已报警）。修完可回到第④步重新核对。'
            : '预检、单品立减、报名、核对全部完成。差异明细已存档（核对报告）。'}
          action={<Space direction="vertical">
            <Button size="small" onClick={() => setStep(3)}>回到核对</Button>
            <Button size="small" type="primary" onClick={onRestart}>再来一场</Button>
          </Space>} />
      )}
    </Card>
  );
}

// ── 推送回执视图（立减 stage/commit + 报名共用）：R10 真相以千牛批量操作记录为准 ──
function PushResultView({ res, title }: { res: CampaignPushResult; title: string }) {
  const okN = res.validation?.ok;
  const failN = res.validation?.failed ?? 0;
  return (
    <Card size="small" title={title} style={{ background: res.ok ? '#f6ffed' : '#fff1f0',
      border: `1px solid ${res.ok ? '#b7eb8f' : '#ffccc7'}` }} styles={{ body: { padding: '12px 16px' } }}>
      <Space direction="vertical" style={{ width: '100%' }} size={8}>
        <Row align="middle" gutter={16}>
          <Col><Statistic title="千牛校验 · 通过" value={okN ?? res.stats?.rows ?? 0}
            valueStyle={{ color: '#389e0d', fontSize: 26 }} /></Col>
          <Col><Divider type="vertical" style={{ height: 40 }} /></Col>
          <Col><Statistic title="千牛校验 · 失败" value={failN ?? 0}
            valueStyle={{ color: failN ? '#cf1322' : '#8c8c8c', fontSize: 26 }} /></Col>
          {res.stats?.rows != null && (
            <Col><Statistic title="本次生成行数" value={res.stats.rows as number}
              valueStyle={{ fontSize: 26 }} /></Col>
          )}
          <Col flex="auto" style={{ textAlign: 'right' }}>
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              成败只认千牛「批量操作记录」结果表（R10），不信中间回执
            </Typography.Text>
          </Col>
        </Row>
        {!res.ok && (
          <Alert type="error" showIcon message={res.need_scan ? '淘宝登录态过期，请先扫码' : (res.error || res.message || '推送失败')} />
        )}
        {!!res.validation?.failed_reasons?.length && (
          <Table size="small" pagination={false} rowKey="reason"
            dataSource={res.validation.failed_reasons}
            columns={[
              { title: '失败原因（自动归类到 R1~R12）', dataIndex: 'reason', ellipsis: true },
              { title: '件数', dataIndex: 'items', width: 70, align: 'center' as const },
              { title: '涉及编码', dataIndex: 'codes', ellipsis: true,
                render: (c: string[] | undefined) => (c || []).map((x) => <Tag key={x}>{x}</Tag>) },
            ]} />
        )}
        {res.screenshot_base64 && (
          <div style={{ border: '1px solid #eee', borderRadius: 6, overflow: 'hidden' }}>
            <Image alt="千牛页面截图" style={{ width: '100%' }}
              src={`data:image/png;base64,${res.screenshot_base64}`} />
          </div>
        )}
      </Space>
    </Card>
  );
}

// ── 核对面板：汇总卡 + 逐SKU表（>2元差异红色置顶）+ 名称校验失败横幅 ──
function ReconPanel({ report, expectedTitle }: { report: CampaignReconResult; expectedTitle: string }) {
  // 报警行置顶：按判定严重度排序（超2元报警 > 偏差 > J未刷新 > 无映射 > 贴线 > 占位 > 一分不差）
  const sortedRows = useMemo(
    () => [...report.rows].sort((a, b) => reconVerdictMeta(a.verdict).order - reconVerdictMeta(b.verdict).order),
    [report.rows],
  );
  const s = report.summary;
  const exactN = s.verdicts['一分不差'] ?? 0;
  const fitN = s.verdicts['贴线'] ?? 0;
  const deviationN = s.verdicts['偏差'] ?? 0;
  const coverageN = s.coverage_missing.length + s.coverage_extra.length;
  const otherTags = (['J未刷新', '占位', '无映射'] as const)
    .filter((k) => (s.verdicts[k] ?? 0) > 0);
  return (
    <Space direction="vertical" style={{ width: '100%' }} size="middle">
      {/* 活动名称校验失败横幅（后端按单品立减导出首行活动名称比对） */}
      {s.title_ok === false && (
        <Alert type="error" showIcon banner icon={<WarningOutlined />}
          message={<b>活动名称校验失败——可能点进了别的活动，已标记报警</b>}
          description={<span style={{ fontSize: 13 }}>
            期望活动标题「<b>{expectedTitle}</b>」与导出表里的活动名称对不上。
            确认千牛营销列表里活动名与计划一字不差后重跑；飞书已同步报警。
          </span>} />
      )}

      {/* 汇总卡 */}
      <Row gutter={12}>
        <Col span={6}>
          <Card size="small" styles={{ body: { padding: 12 } }} style={{ borderColor: '#b7eb8f' }}>
            <Statistic title="一分不差" value={exactN} valueStyle={{ color: '#3f8600' }} suffix="SKU" />
          </Card>
        </Col>
        <Col span={6}>
          <Card size="small" styles={{ body: { padding: 12 } }} style={{ borderColor: '#91caff' }}>
            <Statistic title="贴线（让幅≤1元，在案）" value={fitN}
              valueStyle={{ color: '#1677ff' }} suffix="SKU" />
          </Card>
        </Col>
        <Col span={6}>
          <Card size="small" styles={{ body: { padding: 12 } }}
            style={{ borderColor: s.alarm ? '#ff4d4f' : '#d9d9d9', borderWidth: s.alarm ? 2 : 1 }}>
            <Statistic title="差>2元 红榜（飞书已报警）" value={s.alarm}
              valueStyle={{ color: s.alarm ? '#cf1322' : '#8c8c8c' }} suffix="SKU" />
          </Card>
        </Col>
        <Col span={6}>
          <Card size="small" styles={{ body: { padding: 12 } }}
            style={{ borderColor: deviationN + coverageN ? '#ffe58f' : '#d9d9d9' }}>
            <Statistic title="偏差 + 覆盖缺口（漏报/多报）" value={deviationN + coverageN}
              valueStyle={{ color: deviationN + coverageN ? '#d46b08' : '#8c8c8c' }} suffix="SKU" />
          </Card>
        </Col>
      </Row>
      {(otherTags.length > 0 || s.discount_mismatch.length > 0) && (
        <Space wrap>
          {otherTags.map((k) => (
            <Tag key={k} color={RECON_TAG_COLOR[k]}>{k} × {s.verdicts[k]}</Tag>
          ))}
          {s.discount_mismatch.length > 0 && (
            <Tag color="orange">立减金额出入 × {s.discount_mismatch.length}</Tag>
          )}
        </Space>
      )}

      {/* c维度：单品立减导出 vs builder 应填值 出入明细 */}
      {s.discount_mismatch.length > 0 && (
        <Table size="small" rowKey={(r) => `${r.sku_id}-${r.item_id || ''}`}
          pagination={{ pageSize: 8 }}
          dataSource={s.discount_mismatch}
          columns={[
            { title: 'SKU ID', dataIndex: 'sku_id', width: 160 },
            { title: '商品ID', dataIndex: 'item_id', width: 140 },
            { title: '应填立减', dataIndex: 'expected', width: 110,
              render: (v: number | null) => (v != null ? `¥${v}` : '不在应推清单') },
            { title: '导出立减', dataIndex: 'actual', width: 110,
              render: (v: number | null) => (v != null ? `¥${v}` : '-') },
          ]} />
      )}

      {/* d维度：覆盖缺口明细 */}
      {coverageN > 0 && (
        <Alert type="warning" showIcon
          message={`覆盖完整性：应报未报 ${s.coverage_missing.length} 个 / 多报 ${s.coverage_extra.length} 个`}
          description={<div style={{ fontSize: 12 }}>
            {s.coverage_missing.length > 0 && (
              <div>缺失：{s.coverage_missing.slice(0, 30).map((x) => <Tag key={x}>{x}</Tag>)}
                {s.coverage_missing.length > 30 ? `…共 ${s.coverage_missing.length} 个` : ''}</div>
            )}
            {s.coverage_extra.length > 0 && (
              <div>多出：{s.coverage_extra.slice(0, 30).map((x) => <Tag key={x}>{x}</Tag>)}
                {s.coverage_extra.length > 30 ? `…共 ${s.coverage_extra.length} 个` : ''}</div>
            )}
          </div>} />
      )}

      {/* 逐SKU 表（报警行红色置顶） */}
      <div>
        <Typography.Text type="secondary" style={{ fontSize: 13 }}>
          逐 SKU 核对（共 {s.total} 行）：到手 = 千牛活动普惠券后价（J列）；
          目标 = 大促价/中促价（无动销 = 中促+1）；差额超 2 元的排最前、标红。
        </Typography.Text>
        <Table size="small" rowKey={(r) => `${r.sku_id || ''}-${r.sku_code || ''}`}
          pagination={{ pageSize: 12 }} style={{ marginTop: 6 }}
          dataSource={sortedRows}
          onRow={(r) => (r.verdict === '超2元报警' ? { style: { background: '#fff1f0' } } : {})}
          columns={[
            { title: 'SKU编码', dataIndex: 'sku_code', width: 160,
              render: (v: string | null) => v || '-' },
            { title: 'SKU ID / 商品ID', dataIndex: 'sku_id', width: 170,
              render: (v: string | null, r) => <span>{v || '-'}
                {r.item_id ? <Typography.Text type="secondary" style={{ fontSize: 11 }}>
                  <br />{r.item_id}</Typography.Text> : null}</span> },
            { title: '到手（千牛实际）', dataIndex: 'actual', width: 130,
              render: (v: number | null, r) => v == null ? '-'
                : <Typography.Text type={r.verdict === '超2元报警' ? 'danger' : undefined} strong>¥{v}</Typography.Text> },
            { title: '目标到手', dataIndex: 'target', width: 100,
              render: (v: number | null) => (v != null ? `¥${v}` : '-') },
            { title: '差额', dataIndex: 'diff', width: 90,
              render: (v: number | null, r) => v == null ? '-'
                : <Typography.Text type={r.verdict === '超2元报警' ? 'danger' : undefined}>
                    {v > 0 ? '+' : ''}{v.toFixed(2)}</Typography.Text> },
            { title: '活动价=日常价?', dataIndex: 'signup_price_ok', width: 120,
              render: (v: boolean | null | undefined) => v == null ? '-'
                : v ? <Tag color="green">一致</Tag> : <Tag color="red">不一致</Tag> },
            { title: '判定', dataIndex: 'verdict', width: 130,
              render: (v: string) => <Tag color={reconVerdictMeta(v).color}>{v}</Tag> },
          ]} />
      </div>
    </Space>
  );
}

const RECON_TAG_COLOR: Record<string, string> = {
  J未刷新: 'gold', 占位: 'default', 无映射: 'volcano',
};
