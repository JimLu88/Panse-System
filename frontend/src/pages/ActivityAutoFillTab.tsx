/**
 * 定价页「活动自动填写」Tab —— 2026-07-17 改造为【活动生命周期向导】(P3)。
 * 权威 spec = docs/活动生命周期系统_执行plan.md：
 *   ① 创建活动计划（类型点选 / 活动名称 / 档期秒级点选，复用 Fusion RangePicker）
 *   ② 动销分组（有动销 / 无动销，无动销到手永远 = 中促价 + 1）
 *   ③ 生命周期向导：预检 R1~R12 → 推单品立减 → 推报名 → 自动核对（>2元红榜），每步确认制
 *   底部保留「高级 · 手动」存量工具（下载表 / 单步上传 / SKU 轮换）作兜底。
 * ★第一铁律: 以 ERP 价格为准, 平台报不进就改千牛一口价到 日常价÷0.75, 绝不反过来改 ERP。
 */
import { useCallback, useEffect, useState } from 'react';
import {
  Alert, Button, Card, Col, Collapse, DatePicker, Empty, Input, Modal, Popconfirm, Radio, Row,
  Select, Space, Statistic, Table, Tag, Typography, message,
} from 'antd';
import {
  CalendarOutlined, DeleteOutlined, DownloadOutlined, EditOutlined, PieChartOutlined,
  PlusOutlined, ReloadOutlined, SendOutlined, TableOutlined, ThunderboltOutlined,
} from '@ant-design/icons';
import dayjs, { type Dayjs } from 'dayjs';
import {
  fetchActivityCalendar, fetchAutoEnd, saveActivityCalendar,
  type ActivityPeriod, type AutoEndResult,
} from '../api/catalog';
import {
  CAMPAIGN_STATUS_LABEL, CAMPAIGN_TYPES, NO_SALES_FORMULA, SIGNUP_PRICE_RULE, TIER_FORMULA,
  createCampaign, deleteCampaign, downloadNoSalesGroupXlsx, fetchNoSalesGroup, listCampaigns,
  pushNoSalesGroupFeishu, updateCampaign,
  type CampaignPlan, type CampaignType, type NoSalesGroup,
} from '../api/campaigns';
import { triggerBlobDownload } from '../utils/download';
import ActivityCampaignWizard from './ActivityCampaignWizard';
import ActivityManualPanel from './ActivityManualPanel';

const FMT = 'YYYY-MM-DD HH:mm:ss';

export default function ActivityAutoFillTab() {
  // ── ① 活动计划创建区 ──
  const [ctype, setCtype] = useState<CampaignType>('super_reduce');   // 默认超级立减(到手=中促价)
  const typeDef = CAMPAIGN_TYPES.find((t) => t.value === ctype)!;
  const [cname, setCname] = useState('');
  const [range, setRange] = useState<[Dayjs | null, Dayjs | null]>([null, null]);
  const [autoEnd, setAutoEnd] = useState<AutoEndResult | null>(null);
  const [saving, setSaving] = useState(false);
  const [plan, setPlan] = useState<CampaignPlan | null>(null);        // 当前操作中的计划
  const [plans, setPlans] = useState<CampaignPlan[]>([]);             // 历史计划（可续跑）

  // ── 活动档期日历（复用存量：选档 + 自动结束 = 下一档前一刻）──
  const [periods, setPeriods] = useState<ActivityPeriod[]>([]);
  const [calStatus, setCalStatus] = useState<{ today: string; active: ActivityPeriod[]; upcoming: ActivityPeriod[] } | null>(null);
  const [calOpen, setCalOpen] = useState(false);

  const loadCalendar = useCallback(async () => {
    try {
      const c = await fetchActivityCalendar();
      setPeriods(c.periods); setCalStatus(c.status);
    } catch { /* 静默: 日历没配也不挡主流程 */ }
  }, []);
  useEffect(() => { loadCalendar(); }, [loadCalendar]);

  const loadPlans = useCallback(async () => {
    try { setPlans((await listCampaigns()).items); }
    catch { /* 静默: 后端 /api/campaigns 未就绪时不挡页面 */ }
  }, []);
  useEffect(() => { loadPlans(); }, [loadPlans]);

  // 选了开始时刻 → 拉自动结束（下一档开始前一秒）；结束时间默认跟它
  const onStartChange = async (start: Dayjs | null) => {
    setRange((r) => [start, r[1]]);
    setAutoEnd(null);
    if (!start) return;
    try {
      const r = await fetchAutoEnd(start.format(FMT), cname || typeDef.label);
      setAutoEnd(r);
      if (r.end) setRange([start, dayjs(r.end)]);
    } catch { /* 无日历时不阻断 */ }
  };

  const isDraftLoaded = !!plan && plan.status === 'draft';

  const doCreateOrSave = async () => {
    if (!cname.trim()) { message.warning('先填活动名称——要与千牛活动标题一字不差（核对时按它校验，防推错活动）'); return; }
    if (!range[0]) { message.warning('先点选档期开始时间（精确到秒，对齐淘宝）'); return; }
    setSaving(true);
    try {
      const payload = {
        name: cname.trim(), campaign_type: ctype,          // 档位 tier 由后端按类型派生
        start_at: range[0].format(FMT), end_at: range[1] ? range[1].format(FMT) : null,
        qn_campaign_title: cname.trim(),
      };
      const p = isDraftLoaded
        ? await updateCampaign(plan!.id, payload)
        : await createCampaign(payload);
      setPlan(p);
      setPlans((prev) => [p, ...prev.filter((x) => x.id !== p.id)]);
      message.success(isDraftLoaded ? '计划已保存' : `活动计划「${p.name}」已创建，往下走向导`);
    } catch {
      message.error('保存失败（后端 /api/campaigns 未就绪或校验不过）');
    } finally { setSaving(false); }
  };

  const doDeleteDraft = async () => {
    if (!plan) return;
    try {
      await deleteCampaign(plan.id);
      setPlans((prev) => prev.filter((x) => x.id !== plan.id));
      resetForm();
      message.success('草稿已删除');
    } catch { message.error('删除失败'); }
  };

  const resetForm = () => {
    setPlan(null); setCname(''); setRange([null, null]); setAutoEnd(null); setCtype('super_reduce');
  };

  // 从历史列表载入一个计划继续跑（向导按其状态续步）
  const loadExistingPlan = (id: number) => {
    const p = plans.find((x) => x.id === id);
    if (!p) return;
    setPlan(p);
    setCtype(p.campaign_type); setCname(p.name);
    setRange([p.start_at ? dayjs(p.start_at) : null, p.end_at ? dayjs(p.end_at) : null]);
    setAutoEnd(null);
  };

  return (
    <Space direction="vertical" style={{ width: '100%' }} size="middle">
      {/* ── 顶部说明 + ★第一铁律 ── */}
      <Alert
        type="info" showIcon
        message="活动生命周期向导：建计划 → 看分组 → 预检 → 推立减 → 推报名 → 自动核对。每一步停下等你确认才动真格。"
        description={<span style={{ fontSize: 13 }}>
          {SIGNUP_PRICE_RULE}；中间差额全部用<b>单品立减</b>一个数补齐，每场活动只变这一个数。
        </span>} />
      <Alert
        type="warning" showIcon banner
        message={<b>★ 第一铁律：以 ERP 价格为准，不迁就平台</b>}
        description={<span style={{ fontSize: 13 }}>
          平台报不进（"活动价/标价不得高于近15天最低标价"）几乎都是<b>千牛一口价填低了</b>
          → 去改<b>千牛一口价</b>到 <b>日常价 ÷ 0.75</b>（0.75=标准折扣，永不改），<b>绝不反过来改 ERP</b>。
          「活动价只能下调不能上调」——要往上抬得<b>先在千牛撤销该活动报名</b>再重报（预检会列出清单）。
        </span>} />

      {/* ── ① 活动计划创建区 ── */}
      <Card size="small"
        title={<Space><ThunderboltOutlined /><b>① 创建活动计划</b>
          {plan && <Tag color={CAMPAIGN_STATUS_LABEL[plan.status].color}>
            当前：{plan.name} · {CAMPAIGN_STATUS_LABEL[plan.status].label}</Tag>}</Space>}
        extra={<Space>
          {plans.length > 0 && (
            <Select
              style={{ minWidth: 240 }} placeholder="载入历史计划继续跑" size="small"
              value={plan?.id}
              options={plans.map((p) => ({
                value: p.id,
                label: `${p.name}（${CAMPAIGN_STATUS_LABEL[p.status].label}｜${p.start_at}）`,
              }))}
              onChange={(id) => loadExistingPlan(id as number)}
            />
          )}
          {plan && <Button size="small" icon={<PlusOutlined />} onClick={resetForm}>新建</Button>}
          {isDraftLoaded && (
            <Popconfirm title="删除这个草稿计划？" okText="删除" cancelText="不删" onConfirm={doDeleteDraft}>
              <Button size="small" danger icon={<DeleteOutlined />}>删草稿</Button>
            </Popconfirm>
          )}
        </Space>}>
        <Space direction="vertical" style={{ width: '100%' }} size={10}>
          {/* 活动类型点选（超级立减默认；除它外全部按大促价） */}
          <div>
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>活动类型</Typography.Text>
            <div style={{ marginTop: 4 }}>
              <Radio.Group optionType="button" buttonStyle="solid" value={ctype}
                onChange={(e) => setCtype(e.target.value as CampaignType)}>
                {CAMPAIGN_TYPES.map((t) => (
                  <Radio.Button key={t.value} value={t.value}>
                    {t.label}{t.value === 'super_reduce' ? '（到手=中促价）' : t.value === 'big_other' ? '（到手=大促价）' : ''}
                  </Radio.Button>
                ))}
              </Radio.Group>
            </div>
            <Typography.Paragraph type="secondary" style={{ fontSize: 12, margin: '8px 0 0' }}>
              {typeDef.label}：官方立减 <b>{typeDef.rate}</b>，顾客到手 = <b>{typeDef.targetLabel}</b>。
              公式：{TIER_FORMULA[typeDef.tier]}。{NO_SALES_FORMULA}。
            </Typography.Paragraph>
          </div>

          {/* 活动名称（= 千牛活动标题，核对时校验） */}
          <div>
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              活动名称（照抄千牛活动标题，一字不差——核对时按它校验，防推错活动）</Typography.Text>
            <Input style={{ marginTop: 4, maxWidth: 480 }} placeholder="如：88VIP尊享·大牌狂欢"
              value={cname} onChange={(e) => setCname(e.target.value)} />
          </div>

          {/* 档期秒级点选（复用存量 RangePicker 秒级控件 + 档期日历） */}
          <div>
            <Space size={8}>
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                档期开始 / 结束（精确到秒，对齐淘宝；结束自动 = 下一档开始前一秒）</Typography.Text>
              <Button size="small" icon={<EditOutlined />} onClick={() => setCalOpen(true)}>管理档期</Button>
            </Space>
            <div style={{ marginTop: 4 }}>
              <Space wrap>
                <DatePicker.RangePicker
                  value={range} allowEmpty={[false, true]} showTime={{ format: 'HH:mm:ss' }}
                  format={FMT}
                  onChange={(v) => { if (v && v[0]) onStartChange(v[0]); else { setRange([null, null]); setAutoEnd(null); } }}
                />
                {periods.length > 0 && (
                  <Select
                    style={{ minWidth: 220 }} placeholder="或从日历选一档"
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
            </div>
            {autoEnd && (autoEnd.next
              ? <Alert type="success" showIcon style={{ fontSize: 12, marginTop: 6 }}
                  message={<span>结束时间已自动填为 <b>{autoEnd.end_dt}</b>（下一档「{autoEnd.next.name}·{autoEnd.next.tier_label}」{autoEnd.next.start} 开始前一刻，自动让位）。</span>} />
              : <Alert type="warning" showIcon style={{ fontSize: 12, marginTop: 6 }}
                  message={<span><b>无下次活动</b>：日历里这之后没排下一档。结束时间自行点选，或留空长期挂着。</span>} />)}
            {calStatus && (calStatus.active.length > 0 || calStatus.upcoming.length > 0) && (
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                {calStatus.active.map((p) => <Tag color="green" key={p.name}>进行中 {p.name}</Tag>)}
                {calStatus.upcoming.map((p) => <Tag key={p.name}>{p.name} {p.days_to_start}天后</Tag>)}
              </Typography.Text>
            )}
          </div>

          <Button type="primary" loading={saving} onClick={doCreateOrSave}>
            {isDraftLoaded ? '保存修改' : '创建活动计划'}</Button>
        </Space>
      </Card>

      {/* ── ② 动销分组视图 ── */}
      <NoSalesGroupCard />

      {/* ── ③ 生命周期向导 ── */}
      {plan ? (
        <ActivityCampaignWizard plan={plan}
          onPlanChange={(p) => {
            setPlan(p);
            setPlans((prev) => prev.map((x) => (x.id === p.id ? p : x)));
          }}
          onRestart={resetForm} />
      ) : (
        <Card size="small" title={<Space><ThunderboltOutlined /><b>③ 生命周期向导</b></Space>}>
          <Empty description="先在上方创建（或载入）一个活动计划，向导才开工" />
        </Card>
      )}

      {/* ── 高级 · 手动（存量兜底工具） ── */}
      <Collapse items={[{
        key: 'adv',
        label: <Space><TableOutlined /><b>高级 · 手动</b>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            各档下载表 / 单步上传 / 标价对照 / SKU轮换 —— 向导出问题时的兜底通道</Typography.Text></Space>,
        children: <ActivityManualPanel range={range} />,
      }]} />

      {/* ── 管理档期日历 Modal ── */}
      <CalendarModal open={calOpen} periods={periods} onClose={() => setCalOpen(false)}
        onSaved={(next) => { setPeriods(next); loadCalendar(); }} />
    </Space>
  );
}

// ── ② 动销分组卡：有动销 / 无动销 两列（spec 四.1/四.2；后端返回商品ID列表+item_names） ──
function NoSalesGroupCard() {
  const [grp, setGrp] = useState<NoSalesGroup | null>(null);
  const [loading, setLoading] = useState(false);
  const [loadFailed, setLoadFailed] = useState(false);
  const [exporting, setExporting] = useState(false);
  const [pushing, setPushing] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setGrp(await fetchNoSalesGroup());
      setLoadFailed(false);
    } catch { setLoadFailed(true); }
    finally { setLoading(false); }
  }, []);
  useEffect(() => { load(); }, [load]);

  const doExport = async () => {
    setExporting(true);
    try {
      triggerBlobDownload(await downloadNoSalesGroupXlsx(), '无动销名单.xlsx');
      message.success('无动销名单已下载');
    } catch { message.error('导出失败'); }
    finally { setExporting(false); }
  };
  const doPushFeishu = async () => {
    setPushing(true);
    try {
      const r = await pushNoSalesGroupFeishu();
      if (r.ok && r.sent) message.success(`无动销名单（${r.count ?? '?'} 品）已推送飞书给运营（促成交）`);
      else if (r.ok) message.info(r.message || '当前没有无动销商品，无需推送');
      else message.error(r.message || '推送失败');
    } catch { message.error('推送失败'); }
    finally { setPushing(false); }
  };

  const toRows = (ids: string[]) =>
    ids.map((iid) => ({ item_id: iid, name: grp?.item_names[iid] || '' }));
  const groupColumns = [
    { title: '淘宝商品ID', dataIndex: 'item_id', width: 160 },
    { title: '产品名', dataIndex: 'name', ellipsis: true },
  ];

  return (
    <Card size="small"
      title={<Space><PieChartOutlined /><b>② 动销分组</b>
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          近{grp?.days ?? 60}天平台动销（含刷单=平台口径），零动销自动进登记表</Typography.Text></Space>}
      extra={<Button size="small" icon={<ReloadOutlined />} loading={loading} onClick={load}>刷新</Button>}>
      {loadFailed && (
        <Alert type="warning" showIcon style={{ marginBottom: 8 }}
          message="动销分组接口未就绪（GET /api/campaigns/no-sales-group）——不影响创建计划，向导预检时会再算一遍。" />
      )}
      {!grp && !loadFailed && <Empty description={loading ? '分组加载中…' : '暂无分组数据'} />}
      {grp && (
        <Space direction="vertical" style={{ width: '100%' }} size="middle">
          <Alert type="info" showIcon
            message={<span><b>无动销组顾客到手永远 = 中促价 + 1 元</b>（+1 防零头导致未来报名撞线）。</span>}
            description={<span style={{ fontSize: 13 }}>
              公式：单品立减 = 日常价 − (中促价 + 1)，占位不出报名行。
              这些品一旦撤销在场报名就触发平台「动销门」报不回来（禁撤，预检会红字拦）。
              卖出 1 单系统自动检测，提示转正（撤无动销立减 → 报大促）。
            </span>} />
          {grp.promote_candidates.length > 0 && (
            <Alert type="success" showIcon
              message={<b>转正候选（{grp.promote_candidates.length} 品）：登记为无动销但近{grp.days}天已出单</b>}
              description={<div style={{ fontSize: 13 }}>
                建议转正：撤 nosales 立减 → 报名大促（系统不自动移除登记，动销门单行道 R6）。
                <div style={{ marginTop: 4 }}>
                  {grp.promote_candidates.map((iid) => (
                    <Tag color="green" key={iid}>{iid} {grp.item_names[iid] || ''}</Tag>
                  ))}
                </div>
              </div>} />
          )}
          <Row gutter={16}>
            <Col xs={24} lg={12}>
              <Card size="small" style={{ borderColor: '#b7eb8f' }}
                title={<Statistic title="有动销" value={grp['有动销'].length} suffix="品"
                  valueStyle={{ color: '#3f8600', fontSize: 22 }} />}>
                <Table size="small" rowKey="item_id" pagination={{ pageSize: 5 }}
                  dataSource={toRows(grp['有动销'])} columns={groupColumns} />
              </Card>
            </Col>
            <Col xs={24} lg={12}>
              <Card size="small" style={{ borderColor: '#ffd591' }}
                title={<Statistic title="无动销（到手=中促+1）" value={grp['无动销'].length} suffix="品"
                  valueStyle={{ color: '#d46b08', fontSize: 22 }} />}
                extra={<Space>
                  <Button size="small" icon={<DownloadOutlined />} loading={exporting} onClick={doExport}>
                    导出名单</Button>
                  <Button size="small" type="primary" icon={<SendOutlined />} loading={pushing} onClick={doPushFeishu}>
                    推送飞书</Button>
                </Space>}>
                <Table size="small" rowKey="item_id" pagination={{ pageSize: 5 }}
                  dataSource={toRows(grp['无动销'])} columns={groupColumns} />
              </Card>
            </Col>
          </Row>
        </Space>
      )}
    </Card>
  );
}

// ── 档期日历编辑弹窗（存量保留） ──
function CalendarModal({ open, periods, onClose, onSaved }: {
  open: boolean; periods: ActivityPeriod[]; onClose: () => void; onSaved: (p: ActivityPeriod[]) => void;
}) {
  const [rows, setRows] = useState<ActivityPeriod[]>(periods);
  const [saving, setSaving] = useState(false);
  useEffect(() => { if (open) setRows(periods); }, [open, periods]);

  const setRow = (i: number, patch: Partial<ActivityPeriod>) =>
    setRows((rs) => rs.map((r, j) => (j === i ? { ...r, ...patch } : r)));
  const addRow = () => setRows((rs) => [...rs, { name: '', tier: 'big', start: dayjs().format(FMT), end: null }]);
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
        message="把接下来几波活动的名称/力度/起止（精确到时分秒，对齐淘宝）都排进来，单品立减就能自动算出结束时间（下一档开始前一秒）。" />
      {rows.length === 0 && <Empty description="还没排档期" />}
      <Space direction="vertical" style={{ width: '100%' }} size={8}>
        {rows.map((r, i) => (
          <Row key={i} gutter={8} align="middle">
            <Col span={6}><Input placeholder="活动名，如 88VIP大促" value={r.name}
              onChange={(e) => setRow(i, { name: e.target.value })} /></Col>
            <Col span={5}><Select style={{ width: '100%' }} value={r.tier} options={TIER_OPTS}
              onChange={(v) => setRow(i, { tier: v as ActivityPeriod['tier'] })} /></Col>
            <Col span={10}><DatePicker.RangePicker style={{ width: '100%' }} allowEmpty={[false, true]}
              showTime={{ format: 'HH:mm:ss' }} format={FMT}
              value={[r.start ? dayjs(r.start) : null, r.end ? dayjs(r.end) : null]}
              onChange={(v) => setRow(i, {
                start: v && v[0] ? v[0].format(FMT) : r.start,
                end: v && v[1] ? v[1].format(FMT) : null,
              })} /></Col>
            <Col span={3}><Button danger size="small" onClick={() => delRow(i)}>删</Button></Col>
          </Row>
        ))}
        <Button type="dashed" block onClick={addRow}>+ 加一档</Button>
      </Space>
    </Modal>
  );
}
