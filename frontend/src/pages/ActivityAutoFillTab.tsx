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
  Alert, Button, Card, Checkbox, Col, Divider, Image, Input, Modal, Popconfirm, Row, Space, Statistic, Table, Tag,
  Typography, message,
} from 'antd';
import {
  DownloadOutlined, ExperimentOutlined, TableOutlined, WarningOutlined, CheckCircleOutlined,
  CloudUploadOutlined,
} from '@ant-design/icons';
import {
  downloadSingleItemDiscount, downloadPromoSignup, downloadSuperReduceSignup,
  fetchActivityPreflight, type ActivityPreflight,
  activityUploadStage, activityUploadCommit, type UploadStageResult,
  fetchSkuRotation, applySkuRotation, type SkuRotationPlan,
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
  const [skipFloor, setSkipFloor] = useState(false);   // 本次按初始报价跳过15天最低价校验(默认不跳, 未来照跑)

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
      setPre(await fetchActivityPreflight(15, skipFloor));
      message.success({ content: '预检完成', key: 'pre', duration: 1.4 });
    } catch {
      message.error({ content: '预检失败', key: 'pre' });
    } finally { setPreLoading(false); }
  };

  // 缺映射(无淘宝SKUID)= 未上架, 按设计本就不推送, 不算问题 → 不纳入"全绿"判定 (用户 2026-07-11)
  // 券后价超线/整商品不完整 = 淘宝必拒 → 纳入; 动销0销量只是警示不拦 (2026-07-12)
  const clean = pre && pre.bad_product_count === 0 && pre.conflict_count === 0
    && (pre.skuid_collision_count ?? 0) === 0
    && (pre.floor_conflict_count ?? 0) === 0;

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

  // ── 超大促 SKU 轮换 (预览→人工千牛轮换→同步映射) ──
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
        extra={<Space>
          <Checkbox checked={skipFloor} onChange={(e) => setSkipFloor(e.target.checked)}>
            <Typography.Text style={{ fontSize: 12 }}>本次初始报价 · 跳过15天校验</Typography.Text>
          </Checkbox>
          <Button type="primary" icon={<ExperimentOutlined />} loading={preLoading} onClick={runPreflight}>
            {pre ? '重新预检' : '开始虚拟推送'}</Button>
        </Space>}>
        {!pre && <Typography.Text type="secondary">点右上角「开始虚拟推送」跑一次生成前体检。</Typography.Text>}
        {pre && (
          <Space direction="vertical" style={{ width: '100%' }} size="middle">
            <Row gutter={16}>
              <Col span={6}><Statistic title="坏价产品(排除)" value={pre.bad_product_count}
                valueStyle={{ color: pre.bad_product_count ? '#cf1322' : '#3f8600' }} suffix={`/${pre.bad_sku_count} SKU`} /></Col>
              <Col span={6}><Statistic title="未上架(不推送)" value={pre.unmapped_total}
                valueStyle={{ color: '#8c8c8c' }} suffix="SKU" /></Col>
              <Col span={6}>{pre.floor_check_skipped
                ? <Statistic title="15天最低价冲突" value="已跳过" valueStyle={{ color: '#8c8c8c', fontSize: 22 }} />
                : <Statistic title="15天最低价冲突" value={pre.conflict_count}
                    valueStyle={{ color: pre.conflict_count ? '#d46b08' : '#3f8600' }} suffix="SKU" />}</Col>
              <Col span={6}><Statistic title="报名表可生成" value={pre.signup_big.rows}
                valueStyle={{ color: '#3f8600' }} suffix="行" /></Col>
            </Row>

            {clean && <Alert type="success" showIcon icon={<CheckCircleOutlined />}
              message={pre.floor_check_skipped
                ? '预检通过：无坏价 / 无SKUID撞号；15 天最低价校验本次按【初始报价】已跳过（未来会照跑）。'
                : '预检全绿：无坏价 / 无SKUID撞号 / 无 15 天冲突，可放心逐步生成上传表。'} />}

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

            {(pre.skuid_collision_count ?? 0) > 0 && (
              <div>
                <Divider orientation="left" style={{ margin: '4px 0' }}>
                  <Typography.Text strong type="danger">
                    <WarningOutlined /> 淘宝SKUID撞号（一个SKUID绑了多个商家编码 — 上传表两行打架必串价，先改映射再推）</Typography.Text>
                </Divider>
                <Table size="small" pagination={false} rowKey="taobao_sku_id"
                  dataSource={pre.skuid_collisions || []}
                  columns={[
                    { title: '淘宝SKUID', dataIndex: 'taobao_sku_id', width: 170,
                      render: (v: string) => <Typography.Text type="danger" strong>{v}</Typography.Text> },
                    { title: '被这些商家编码共用（去 定价·总表 改 SKUID 映射，一个 SKUID 只能归一个编码）',
                      dataIndex: 'names',
                      render: (names: string[]) => names.map((n) => <Tag color="red" key={n} style={{ marginBottom: 4 }}>{n}</Tag>) },
                  ]} />
              </div>
            )}

            {(pre.floor_conflict_count ?? 0) > 0 && (
              <div>
                <Divider orientation="left" style={{ margin: '4px 0' }}>
                  <Typography.Text strong type="danger">
                    <WarningOutlined /> 券后价超线（报名价 高于 已生效活动价=校验硬底 → 淘宝必拒整个商品）</Typography.Text>
                </Divider>
                <Typography.Paragraph type="secondary" style={{ fontSize: 12, margin: '0 0 8px' }}>
                  处理：去定价页把该 SKU 大促到手调到 ≤ 已生效价，或本场放弃该商品。共 {pre.floor_conflict_count} 个 SKU。
                </Typography.Paragraph>
                <Table size="small" pagination={{ pageSize: 10 }} rowKey="sku_code"
                  dataSource={pre.floor_conflicts || []}
                  columns={[
                    { title: 'SKU编码', dataIndex: 'sku_code', width: 150 },
                    { title: '名称', dataIndex: 'name', ellipsis: true },
                    { title: '计划报名价', dataIndex: 'planned', width: 100, render: (v: number) => `¥${v}` },
                    { title: '已生效活动价(硬底)', dataIndex: 'enrolled_floor', width: 140, render: (v: number) => `¥${v}` },
                    { title: '超出', dataIndex: 'over', width: 90,
                      render: (v: number) => <Tag color="red">+¥{v}</Tag> },
                  ]} />
              </div>
            )}

            {(pre.incomplete_item_count ?? 0) > 0 && (
              <div>
                <Divider orientation="left" style={{ margin: '4px 0' }}>
                  <Typography.Text strong type="danger">
                    <WarningOutlined /> 整商品SKU不全（淘宝要求全SKU报名，缺价SKU的商品已整个剔除，补价后自动纳入）</Typography.Text>
                </Divider>
                <Table size="small" pagination={{ pageSize: 8 }} rowKey="taobao_item_id"
                  dataSource={pre.incomplete_items || []}
                  columns={[
                    { title: '商品ID', dataIndex: 'taobao_item_id', width: 140 },
                    { title: '商品', dataIndex: 'product', ellipsis: true, width: 220 },
                    { title: '已有价SKU', dataIndex: 'ok_skus', width: 90, align: 'center' as const },
                    { title: '缺价的SKU（去定价页补价）', dataIndex: 'missing_skus',
                      render: (ms: string[]) => ms.map((m) => <Tag color="red" key={m} style={{ marginBottom: 3 }}>{m}</Tag>) },
                  ]} />
              </div>
            )}

            {(pre.no_sales_count ?? 0) > 0 && (
              <div>
                <Divider orientation="left" style={{ margin: '4px 0' }}>
                  <Typography.Text strong style={{ color: '#d46b08' }}>
                    <WarningOutlined /> 疑似动销不达标（近60天0销量；上架超60天的会被平台拒，新品不受限 → 仅警示不拦表）</Typography.Text>
                </Divider>
                <Space wrap size={4}>
                  {(pre.no_sales_items || []).map((it) => (
                    <Tag key={it.taobao_item_id} color="orange">{it.product}（{it.taobao_item_id}）</Tag>
                  ))}
                </Space>
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
            <Divider style={{ margin: '8px 0' }} plain><Typography.Text type="secondary" style={{ fontSize: 12 }}>自动上传（挂到千牛草稿·可比对）</Typography.Text></Divider>
            <Button type="primary" ghost icon={<CloudUploadOutlined />} block
              loading={staging && upChannel === 'promo_signup'}
              onClick={() => doStage('promo_signup', 'big')}>
              大促报名 上传到千牛（先比对）</Button>
            <Button type="primary" ghost icon={<CloudUploadOutlined />} block
              loading={staging && upChannel === 'super_reduce'}
              onClick={() => doStage('super_reduce', 'big')}>
              超级立减活动 上传到千牛（先比对）</Button>
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              自动挂进千牛活动「商品批量导入」+出比对表；最终「发布报名」目前在千牛手动点（导入已到草稿）。
            </Typography.Text>
          </StepCard>
        </Col>
      </Row>

      {/* ── 超大促 SKU 轮换 (618/双11 15% 让利) ── */}
      <Card size="small" title={<Space><TableOutlined /><b>超大促 SKU 轮换（618/双11 15% 让利）</b>
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>尺寸标签循环下移，绕过 15 天最低价</Typography.Text></Space>}>
        <Space direction="vertical" style={{ width: '100%' }} size="small">
          <Alert type="info" showIcon style={{ fontSize: 12 }}
            message="系统按尺寸阶梯算出「每个 skuId 该改成的 商家编码/规格/价格」。规格(尺寸)千牛没有批量口 → 照下表在千牛「编辑商品」逐个改；改完点「同步系统映射」，ERP 自动重刷 skuId 映射（商家编码永远跟尺寸走，不串位）。" />
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

      {/* ── 千牛上传·比对表确认 (stage 完成后弹出) ── */}
      <Modal
        open={!!stageRes}
        title={<Space><CloudUploadOutlined /><b style={{ fontSize: 17 }}>{stageRes?.channel_name} · 上传比对（确认前请核对）</b></Space>}
        width={1400}
        style={{ maxWidth: '96vw', top: 24 }}
        onCancel={() => { setStageRes(null); setUpChannel(null); }}
        footer={
          stageRes?.channel === 'single_item_discount'
            ? [
                <Button key="cancel" onClick={() => { setStageRes(null); setUpChannel(null); }}>取消（不提交）</Button>,
                <Button key="ok" type="primary" danger loading={committing} icon={<CloudUploadOutlined />}
                  onClick={doCommit}>✅ 确认最后一步上传（不可逆）</Button>,
              ]
            : [
                <Typography.Text key="note" type="secondary" style={{ marginRight: 12, fontSize: 12 }}>
                  已挂到千牛「草稿」，去千牛商品管理核对后手动发布报名
                </Typography.Text>,
                <Button key="close" type="primary" onClick={() => { setStageRes(null); setUpChannel(null); }}>知道了</Button>,
              ]
        }
      >
        {stageRes && (() => {
          const okN = stageRes.validation?.ok ?? 0;
          const failN = stageRes.validation?.failed ?? 0;
          // 大促报名/超级立减的千牛导入是异步的(页面只回"正在处理中"), 抓不到当场成功/失败数 →
          // 显示"已收单·处理中"而非误导的 0 条 (用户 2026-07-12: "为什么校验通过0条还能下一步")
          const asyncPending = stageRes.validation?.ok == null && stageRes.validation?.failed == null;
          const failCodes: string[] = stageRes.validation?.failed_sku_codes || [];
          const misN = stageRes.mismatch_count ?? 0;
          const totalN = stageRes.compare_total ?? (stageRes.compare_rows?.length ?? 0);
          const priceOk = misN === 0;
          const label0 = stageRes.compare_rows?.[0]?.value_label || '系统值';
          return (
          <Space direction="vertical" style={{ width: '100%' }} size="middle">
            {/* ① 千牛校验结果 —— 大号醒目; 异步渠道(大促报名/超级立减)千牛只回"处理中", 显示已收单而非误导的0条 */}
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
                        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                          （多为已下架/不在活动范围的 SKU，不影响其余 {okN} 条）
                        </Typography.Text>
                      </div>
                    : <Typography.Text type="secondary" style={{ fontSize: 12, marginLeft: 8 }}>
                        （具体哪条千牛未回明细，通常是已下架 SKU，不影响其余 {okN} 条）
                      </Typography.Text>}
                </div>
              )}
            </Card>

            {/* ①b 失败原因归类(自动下载千牛操作反馈解析, 人不用去千牛翻) */}
            {(stageRes.validation?.failed_reasons?.length ?? 0) > 0 && (
              <Alert type="error" showIcon
                message={<b style={{ fontSize: 15 }}>失败原因（已自动解析千牛操作反馈）</b>}
                description={
                  <Space direction="vertical" size={2} style={{ fontSize: 14 }}>
                    {stageRes.validation!.failed_reasons!.map((r) => (
                      <div key={r.reason}>🔴 <b>{r.items} 件</b>：{r.reason}</div>
                    ))}
                    <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                      预检页有对应红字明细（券后价超线 / 整商品SKU不全 / 动销）——按预检修完再导。
                    </Typography.Text>
                  </Space>
                } />
            )}

            {/* ② 千牛页面截图 —— 带标签、点击放大 */}
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

            {/* ③ 上传价 vs 系统价 —— 严格 0 容差核对 (用户: 必须按系统价) */}
            <Alert showIcon type={priceOk ? 'success' : 'error'}
              message={priceOk
                ? `✅ 上传价 = 系统价：全部 ${totalN} 行一分不差（严格核对，无出入）`
                : `⛔ 有 ${misN} 行「上传值 ≠ 系统价」！下方红字行，未按系统价，别提交、先叫我查`}
              description={priceOk
                ? '「上传值」直接读自即将上传千牛的那份表，「系统应填」按定价独立重算，两者逐条对到分。'
                : '这不该出现——上传表和系统定价理应一致。请把红字行截图发我，我立刻定位。'}
            />
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
    </Space>
  );
}
