# Current state — logistics bill product analytics

## 2026-09-06 已验证活动成果与01操作步骤固化

- 当前入口：`docs/campaign-signup-frozen-steps.md`；机器规则：
  `docs/campaign-signup-frozen-contract.json`。AGENTS 已指向此入口；旧准备包与
  备用槽方案标注为历史，不再作为新增预检或自动轮换的依据。
- 原价格审计回执保持原字节与哈希，保存成功范围和未解决项，不改写为全店完成。
  `scripts/verify_campaign_frozen_outcomes.py` 和对应离线回归仅校验仓库成果；
  无网络、无浏览器、无数据库、无报名动作，不增加报名当天步骤。
- 用户已指定 01 执行报名。本次不重启/部署运行时，不改业务价格、SKU 映射或在途批次；
  后续进展由 01 追加新回执。本段不宣称新短流程运行时已经实现或部署。

## 2026-09-06 Super-88 official success and incomplete price closeout

- User requested read-only full price verification before final workflow solidification.
  Future routine signup owner is `01｜畔色ERP系统`
  (verified current title, thread `01a04666-6895-7b40-a07d-c7cfe38d9a02`).
- Receipt: `docs/receipts/campaign-49462-price-audit-20260906.json`.
  Fresh official export `20260906002843`, SHA-256
  `5009e541e6ba8fcb7c267d01ffb15057dfc1bc54bcf2c04f7ad41b1a7b5fe4a6`.
  All 51 exported items are NOT successful: 45 items / 354 SKU rows are
  已发布设定; 6 items / 70 rows remain 草稿.
- The repaired 15+9 items / 228 SKU rows all have exact submitted signup prices
  and 已发布设定 readback. Batches 790318164, 793048029 (six successful items
  only), 794292184 and 794707209 must not be replayed.
- Full-price verification is incomplete: two items (1038071596128,
  840659847455) lack official final prices for 24 rows; three custom rows have
  0.04–0.07 yuan final-price differences from the local low-price formula.
- Outside the repaired 24-item scope, 1047741358718, 1047744482178 and
  1047742974354 have 12 official final prices above current ERP big targets
  by 537.96–1304.48 yuan. Their values equal signup less official discount
  only; missing/unapplied concurrent single-item discount is an inference.
  No new discount, signup or price change was performed.
- Item 1001358847694 has two signup prices below 20% of current ERP daily
  price; verify first-original-price provenance before any correction.
  Four other published items / 16 rows lack ERP SKU mapping.
- This receipt records successful outcomes, exact mappings and unresolved
  evidence separately. It does not mark the campaign fully price-correct,
  mutate ERP prices/mapping tables, or claim the short-flow runtime deployed.
  Current user-frozen short flow remains authoritative: fresh exact template,
  full sellable scope first attempt, one price version, single-item discount
  then signup, bounded terminal waits, failure report once, no automatic retry,
  no candidate scan / R16/R17 / evidence-refresh preflight. Inactive reserve
  SKUs use off/gray switches with stock preserved, not stock zero.
- Existing historical state sections below are retained for audit, not permission
  to resume V28 or other retired attempts. This post-signup audit must not become
  a new mandatory pre-submit gate.

## 2026-09-04 plan-8 ERP claim-step allowlist continuation V28

- V27 ran once and stopped before file selection because ERP did not recognize
  its own `platform_write_claim_claimed_preupload_resume_v27` audit step. The
  frozen result has `platform_write=false`, `claim_created=false` and automatic
  retry disabled; V27 is retired.
- V28 accepts only that exact V27 zero-write result and the exact reviewed
  14994-byte manual export. It maps V27/V28 audit steps to the unchanged V21
  frozen claim SHA-256, preserving campaign identity, SKU, price, discount,
  CAS, one-shot and no-retry gates.
- Formal one-time operator command is
  `scripts/campaign_recover_plan8_final_v8_manual_export_v28_nas.ps1 -ExportPath <exact reviewed xlsx>`.
  Maintenance installs but does not invoke it; task 03 owns the single business
  run after production and Web-Agent runtime verification.

## 2026-09-04 plan-8 verified manual-export continuation V25

- V24 stopped before file selection with `campaign_title_mismatch`; its exact
  ERP attempt is `edaf6b609dad46fbab90c7e8`, `claim_created=false`, and no
  platform write was observed. V24 is retired.
- V25 accepts only the reviewed user export
  `「26年淘宝9月超级88超级88现货」活动商品导出20260904182846.xlsx`, 14994 bytes,
  SHA-256 `c7c22b57a95e7db5f3cc8d8a0319ee4b1920a13e73204f1004be3760d71d25da`.
  It proves 83 rows = 70 exact frozen drafts + 13 protected published rows and
  exactly the eight expected missing SKUs. The broken automatic pre-export is
  skipped; live price, SKU, discount, identity, CAS, one-shot and no-retry gates
  remain mandatory.
- Formal one-time operator command is
  `scripts/campaign_recover_plan8_final_v8_manual_export_v25_nas.ps1 -ExportPath <exact reviewed xlsx>`.
  Maintenance installs but does not invoke it; task 03 owns the single business
  run.

## 2026-09-04 plan-8 V8 read-only export retry continuation V24

- V23 passed the corrected claim contract, then its inspection-only QianNiu
  export task failed with the official result `哎呀，系统开小差了，请稍后再试`.
  The Web-Agent job finished `phase=inspect`, `claim_created=false` and
  `execution_boundary.platform_write=false`; no upload or submission occurred.
- V24 accepts only the exact frozen ERP V23 failure hashes and unchanged V21
  claim, then permits one new normal inspection/export attempt. All write,
  identity, price, scope, CAS and no-retry gates are unchanged. V23 is retired.
- Formal one-time operator command:
  `D:\AI\畔色ERP系统\ERP程序\scripts\campaign_recover_plan8_final_v8_preupload_v24_nas.ps1`.

## 2026-09-04 plan-8 V8 frozen-claim contract correction V23

- V22 was invoked once and stopped during local claimed-preupload inspection;
  it made no upload or platform write. The claim itself was unchanged. The
  mismatch was limited to the frozen diagnostic text: the real V21 claim says
  `Timeout 45000ms`, while V22 expected `Timeout 15000ms`.
- V23 retains the exact claim SHA-256, attempt, plan/workflow, campaign
  identity, scope, CAS, price, SKU and no-retry gates, but validates the real
  45-second text. V22 is permanently retired.
- Formal one-time operator command:
  `D:\AI\畔色ERP系统\ERP程序\scripts\campaign_recover_plan8_final_v8_preupload_v23_nas.ps1`.
  Maintenance installs but does not invoke it; task 03 owns the sole run.

## 2026-09-04 plan-8 V8 official-template modal close continuation V22

- The sole V21 continuation generated and populated the exact official
  Super-88 workbook, but the template-generation modal remained open and
  blocked the underlying batch-update dialog. It stopped before file
  selection/upload; ERP and Web-Agent both record `platform_write=false`, no
  publish/discount checkpoint, and automatic retry disabled. The frozen claim
  SHA-256 is
  `e40cc78c05a0d003b2c602465efe9597fe400c59e406bf3deef3b0ceed359b78`.
- V22 closes only one visible semantic close control located in the top-right
  100 pixels of the exact bound template-generation modal. It then requires
  that modal to disappear and the original batch-update dialog to remain
  visible. Duplicate/missing/covered controls fail closed; it never force
  clicks, and all campaign identity, SKU, price, claim, CAS and one-shot gates
  remain unchanged.
- Formal one-time operator command:
  `D:\AI\畔色ERP系统\ERP程序\scripts\campaign_recover_plan8_final_v8_preupload_v22_nas.ps1`.
  V1--V21 are retired. Maintenance installs but does not invoke it; task 03
  owns the sole business invocation after production/runtime verification.

## 2026-09-04 计划7样块单品立减补报已完成并封口

- 商品 `719436834260` 的 4 个正常 SKU 已补报到活动 `143939511827`，单品立减均为
  `5.99`。平台官方终态为 `4 成功 / 0 失败`；提交后逐 SKU 只读回读均为
  `classification=correct_effective`、`actual_deduct=5.99`、`status=进行中`、
  `activity_ids=["143939511827"]`。4 个 SKU 为 `5024477897617`、
  `6120623944056`、`6282622238127`、`6285733543660`。
- 原 attempt `a7280fed1f9d638c41b8f8ae` 已精确复用并封口，禁止再次执行。最终只读回读
  `snapshot_id=20`，证据 SHA-256 为
  `d97f24b909cd6d2e867e57439ee0983e7b868cf00fd257315fc17229ba970ca9`。
- Web-Agent 已修复两个真实页面漂移：`重要消息` 按钮严格绑定所在消息卡片的真实关闭
  控件；820px 窗口下先滚动普通操作列，再点击唯一实际接收点击的固定列入口。Web-Agent
  GitHub `main` 为 `d240ccea5fc45b6d0192cb06b0d632fbe925c3c6`，ERP GitHub/生产为
  `df8a9d2`；计划7专项测试 `40 passed`。
- 正式运行目录的零写入复验已证明活动唯一、浮层可关闭、4 个 SKU 在执行前确实缺失且
  添加商品窗口能打开，同时保持选择 0、上传 0、提交 0。Web-Agent 重启后父 PID
  `36964`、8500 监听 PID `33308`，本机健康访问返回 HTTP 200。
- 本次未碰定制/咨询 SKU、未改商品或 ERP 日常价、未轮换 SKU、未撤回/暂停/删除活动、
  未扩大到整批。计划8仍是独立的 unknown/readback-not-exact 状态，禁止因本次成功而自动
  重试或干预。

## 2026-09-04 plan-8 V8 semantic batch-import continuation V14

- The sole V13 continuation passed the claim handshake and reached the exact
  QianNiu campaign page, but stopped at `draft_patch_terminal` before file
  selection. Its durable Web-Agent claim SHA-256 is
  `809914e7bf28f99ef912cb57e65cc128c3b0d19d819d52fc88ea80090ce3220e`;
  `draft_patch.submitted=false`, no publish/discount checkpoint exists, and
  ERP recorded `platform_write_observed=false` with automatic retry disabled.
- V14 accepts only that exact claim plus the frozen ERP V13 result, inspection
  and commit hashes. It adds a semantic modal-root binder anchored to the one
  exact `批量导入` title and requires the same modal to contain one exact
  `下载模板`, `导入表格` and button-like `关闭`. Duplicate or page-level
  controls fail closed; activity identity, price, SKU, claim and one-shot gates
  are unchanged.
- Formal one-time operator command:
  `D:\AI\畔色ERP系统\ERP程序\scripts\campaign_recover_plan8_final_v8_preupload_v14_nas.ps1`.
  V1--V13 are retired. Maintenance installs but does not run it; task 03 owns
  the sole business invocation after production/runtime verification.

## 2026-09-04 plan-8 V8 zero-write readback continuation V5

- V4 was invoked exactly once and stopped before Web-Agent/platform access
  because the intervening readback had correctly changed the same attempt's
  audit cursor from `draft_patch_terminal` to `readback_not_complete`.  The V4
  CAS rejected that drift; its request `28bbb16f1d3f` caused no platform read
  or write and V4 is now permanently retired.
- The intervening readback is frozen by full result-summary and readback
  SHA-256 values.  It proves six drafts still total 70 SKUs, the same eight
  target SKUs remain missing, no new discount row exists, protected records
  are unchanged and the Web-Agent V8 claim is still the original exact
  pre-upload claim.  V5 accepts only that exact database/readback state plus
  all V4 claim, manifest, policy, campaign identity, reservation and CAS gates.
- Formal one-time operator command:
  `D:\AI\畔色ERP系统\ERP程序\scripts\campaign_recover_plan8_final_v8_preupload_v5_nas.ps1`.
  V1--V4 are retired.  Task 03 owns the sole V5 business invocation; any
  post-file-selection failure remains no-retry and requires official readback.

## 2026-09-04 plan-8 V8 claimed pre-upload continuation V4

- The sole V3 execution created the expected durable V8 claim, then stopped at
  `draft_patch_terminal` with `plan8_v6_update_dialog_not_exact`.  Its frozen
  claim SHA-256 is
  `4b71a1d5337e0de45fc732ea1ee0007eb35ea9f49995a6cf2f3f59ab82c7a37f`;
  `draft_patch.submitted=false`, no publish or discount checkpoint exists, and
  ERP recorded `platform_write_observed=false` with automatic retry disabled.
- A fresh readback after that stop still returned the original 70 campaign
  SKUs, the same protected-record hashes, no new eight-SKU discount rows and
  exactly the same eight missing SKU ids.  This independently proves that V3
  made no platform change; V1--V3 execution commands are permanently retired.
- V4 can reuse only that exact claim and the same attempt.  Its distinct
  machine route, reservation owner, confirmation, claim SHA, plan/workflow,
  campaign identity, policy, 78-SKU manifest and fresh platform scope must all
  match.  It CAS-locks the existing attempt, never creates a second attempt,
  and enters the unchanged patch/publish/discount/readback sequence.  Any
  mismatch stops before file selection; once file selection may have happened,
  automatic retry remains forbidden.
- Formal one-time operator command:
  `D:\AI\畔色ERP系统\ERP程序\scripts\campaign_recover_plan8_final_v8_preupload_v4_nas.ps1`.
  Maintenance installs but does not run it; task 03 owns its sole business
  invocation after production and Web-Agent runtime fingerprint verification.

## 2026-09-04 plan-8 V8 verified pre-claim continuation V3

- V2 passed the repaired exact editor binding, then Web-Agent stopped before
  creating its durable V8 claim because the enriched commit manifest carried
  `inspection_baseline` into V8's final zero-write inspect adapter. V6 correctly
  rejected that phase-invalid field with
  `ValueError: plan8_v6_manifest_fields_invalid`; no platform write occurred.
- V3 removes `inspection_baseline` only from that derived inspect manifest. It
  does not alter the original bound commit manifest or any activity, price,
  SKU, identity, policy, claim, readback or one-shot gate. The formal V3 command
  is `D:\AI\畔色ERP系统\ERP程序\scripts\campaign_recover_plan8_final_v8_manifest_fix_v3_nas.ps1`.
  V1/V2 commands are retired and must not be rerun.

## Superseded V1/V2 history

- The first pre-claim continuation inspection (`request_id=8ec5e20be478`)
  stopped before any claim or platform write because QianNiu exposed nested or
  duplicated editor containers and the legacy reader required exactly one
  visible `[role=dialog]`. Its command is retired. The V2 continuation uses a
  new confirmation and keeps the same exact attempt/CAS gates; it is the only
  operator command allowed after the editor locator repair.
- ERP now preserves the Web-Agent step, facts, claim flag and login flag when
  inspection fails, instead of presenting an all-null price summary without the
  DOM cause. The V2 command is
  `D:\AI\畔色ERP系统\ERP程序\scripts\campaign_recover_plan8_final_v8_editor_fix_v2_nas.ps1`.

- The sole V8 attempt `edaf6b609dad46fbab90c7e8` was left
  `unknown_no_retry` because ERP created its database claim before the
  Web-Agent job failed, and the transport wrapper discarded the terminal job
  exception. The subsequent readback returned no official artifact because the
  Web-Agent durable V8 claim had never been created. The authoritative runtime
  claim file is absent; the Web-Agent writes that file before any platform
  write, so this is a verified pre-write stop rather than an ambiguous Taobao
  submission.
- Terminal Web-Agent job failures now preserve their status, error code,
  checkpoint and job id in ERP audit details. A separate
  `resume_preclaim` mode accepts only the exact attempt, request, old scope,
  job id, alarmed plan, policy and 78-SKU scope. It re-inspects the live V7
  zero-write baseline and requires the V8 durable claim to still be absent.
  Only then does it reuse the same attempt with CAS and enter the unchanged
  V8 commit/readback pipeline. Any claim, scope, policy or state drift stops
  before platform write; the original execute/readback commands remain retired.
- Formal one-time operator command:
  `D:\AI\畔色ERP系统\ERP程序\scripts\campaign_recover_plan8_final_v8_preclaim_resume_nas.ps1`.
  Maintenance installs but does not run it; task 03 owns the single business
  continuation after production and runtime fingerprint verification.

## 2026-09-04 plan-7 V4 pre-write residue recovery (V5)

- The sole V4 invocation (`a531725a704ce1a910ddc008`) passed the exact
  14-signup-row / 10-discount-row identity checks but stopped before platform
  write because V4 consumed bundle `d7c563f9a793233e1ceab7b4` immediately
  before its own push-context validator required that bundle to be unconsumed.
  Signup attempt `c7df358081734428cbf05cea` remains `prepared`, with
  `write_claimed=false` and no observed platform write; plan 7 was left in
  `resume_executing`.
- V4 now consumes its bundle only after the guarded push returns (and also on
  its fail-closed exception path), preventing this ordering defect in future
  code paths. V1--V4 remain non-replayable.
- The new V5 route and CLI accept only the exact plan, V4 invocation, prepared
  attempt, consumed bundle, source/manifest hashes, request id and archived
  receipt hash above. V5 rechecks that no write was claimed, releases the
  bundle only inside the same guarded call, delegates to the unchanged V4
  price/SKU/official-readback gates, and consumes it again before returning.
  It cannot touch plan 8, alter prices, rotate SKUs, or withdraw/pause items;
  any drift stops before platform write and V5 itself is one-shot.
- Formal operator command:
  `D:\AI\畔色ERP系统\ERP程序\scripts\campaign_execute_plan7_final_closeout_v5_nas.ps1`.
  Maintenance does not execute this command; task 03 owns the sole business
  invocation after production version and script-fingerprint verification.
- Verification: the focused V4/V5/auth set passed 34 tests; the complete
  campaign-named backend suite exited successfully with two intentional skips.
  Python compilation and scoped diff checks passed. No migration is required.

## 2026-09-03 活动报名统一准备包与提交前流水线已正式上线

- ERP GitHub 主干代码提交和生产版本均为 `e1dbb6cc78a7a3917eee66433e77b4881f904901`。
  正式统一发布验收通过：`/api/health`、`/api/ready` 正常，API/Web 同一提交，
  数据库为 `0152 (head)`，API/Web/DB/backup 四个容器正常；回滚镜像为
  `panse-system-api:rollback-20260903-134844` 和
  `panse-system-web:rollback-20260903-135214`。
- 新增不可变的活动最终准备包：固定 plan/workflow/活动身份，保存 ERP 日常价、活动目标价、
  SKU 映射、只读平台证据、逐商品决定、门禁结果、策略/来源/清单哈希和有效期。准备包最长
  有效 6 小时；编译器语义版本为 `2026-09-03.2`，规则或口径升级后不会复用旧包。
- 机器身份 `service:campaign-preparation-bundle` 只允许调用
  `POST /api/campaigns/prepare-final-bundle`。正式脚本为
  `scripts/campaign_prepare_final_bundle_nas.ps1`，只支持 `compile`、
  `refresh_and_compile` 和 `read_latest`；没有上传、提交、改价、SKU 轮换、通知或
  自动重试能力。
- 永久原则已写入策略与代码：真实 SKU 报名价必须逐分等于 ERP 日常价，任何差异都整品暂缓；
  不自动轮换真实 SKU；定制/占位 SKU 只能在不可变原始日常价 20% 的永久最终价保护线以上
  调整；历史无动销仅提示，本场失败可安静排除，下场重新纳入；单个商品缺证据/价格冲突/SKU
  不完整只暂缓该商品，活动身份不完整或已有写入 claim 未收口才阻断全场。
- 对架构、证据时效、无动销、价格冲突、SKU 轮换、局部隔离、幂等、平台异步、UI 漂移、
  机器权限、并发、通知降噪、迁移/回滚做了 13 轮反方检查；结论与可复核门禁见
  `docs/活动报名统一准备包与自动化流水线_20260903.md`。程序层没有遗留反方意见；
  平台登录/验证码、平台不可用、官方规则不允许和官方没有只读证据仍是不可绕过的外部硬门。
- 全部活动命名回归测试通过并保留 2 条原有 skip；Python 编译和 diff 检查通过。全后端的
  31 个成本/库存/供应商/旧 Excel/重复订单历史失败已在未修改的 V6 基线复现，确认不是本次
  引入，不把它们伪报为本次通过。
- 03 的在途 Web-Agent `job3` 已先等待到安全终态再发布：只读发现计划 8 的 6 个草稿共
  70 个 SKU（预期 78），新增 8 条单品立减实际为 0，返回
  `plan8_v6_read_facts_not_exact`；`claim_created=false`，平台写入、账号动作、改价、
  SKU 轮换、撤回/暂停/移出、通知和自动重试均为 false。
- 已在生产只读编译计划 8 准备包 revision 3：
  `bundle_id=1c37f48a2d82968ad402d069`，总决策商品 60 = 暂缓 58 + 明确排除 2，
  其中有 ERP SKU 映射 58，可提交 0。状态为 `blocked_before_submission`；历史写入
  attempt（包括 V5/V6 unknown-no-retry）未正式收口前，03 不得重报。该包没有读取平台，
  没有创建写入 claim，也没有平台动作。

## 2026-09-02 计划8六条既有草稿原位恢复 V3（程序已准备，未执行业务）

- V2 已确认错误地用候选页完整性判断已有草稿，现永久退役；V3 改为固定绑定 6 条官方
  草稿 record，先补固定 8 条单品立减，只对两个草稿补 4+4 SKU，再发布 6 条草稿。
- V3 最终范围固定为 6 商品 / 78 SKU / 18 定制 SKU。3 条既有已发布记录、旧 53 条
  单品立减、零动销商品 `793202812082` 和仓库商品 `1038725569412` 都是硬保护范围。
- Web-Agent inspect 必须持有 reservation 到 commit；任务忙碌发生在 ERP 写 claim 之前，
  不消耗机会。commit 后任何未知结果禁止重试，只能 `-ReadbackOnly` 做官方只读判定。
- ERP 只建立一个 V3 父 attempt，不调用通用 push_signup，也不会生成内层 signup attempt。
  父 attempt 的 claim 与计划状态 CAS 原子落库；Web-Agent 消耗租约前必须通过固定只读
  claim-verification 回查。inspect 同时要求新增 8 个真实 SKU 的官方候选当前价、最低
  标价、合格活动价上限全部新鲜且满足现有价格硬门，并将完整 8 行和旧 53 行绑定 scope。
  正式脚本必须显式选择 `-ExecuteOnce` 或 `-ReadbackOnly`，无参数拒绝。
  正式命令与完整接口契约见
  `docs/计划8六条草稿原位恢复V3_20260902.md`。维护任务尚未执行计划 8 真实恢复。

## 2026-09-02 售后超时提醒排除自动入账台账

- 生产只读核对确认 14:00 提醒中的 347 条全部不是待人工处理案件：341 条状态为
  `auto`、6 条为 `auto_linked`，且全部已有 `processed_at`。其中 346 条已关联平台订单，
  另 1 条为历史自动台账；不要求用户逐条处理。
- 售后追踪现与订单模块口径一致：平台退款、支付宝流水和万师傅记录自动生成的
  `auto` / `auto_linked` 台账继续保留用于财务与售后追溯，但不再进入“售后超时待处理”
  提醒。真正未完成且超过 3 天的人工售后仍照常提醒。
- 回归测试覆盖自动台账与真实待办混合、纯自动台账不推送两种场景。

## 2026-09-02 活动整品 SKU 完整报名规则与计划8待处理映射

- ERP 规则提交 `cdbbda026b3be0164410f85b10ea056517634c3a` 已把用户确认的口径写入
  唯一活动策略和报名生成器：`is_custom_placeholder=true` 的权威定制/占位 SKU
  必须随目标整品进入报名清单；已知平台现价时允许降到券后安全上限。只改活动报名价，
  不改 ERP 日常价。缺平台现价或券后线仍在上传前整品停止，禁止静默删掉定制行后传半套。
- 只凭商家编码尾号识别的旧定制行仍必须逐淘宝 SKU ID 精确授权；商品名、规格名中的
  “定制/咨询”不改变分类。仓库商品 `1038725569412/6060112621275` 和平台已判定
  近60天零动销的 `793202812082` 继续不报名、不改价，现有规则未放宽。
- 计划8的官方商品表证明 6 个待报名商品共有 78 个平台 SKU。旧程序将其中 26 个都当作
  定制排除，实际为 18 个真定制和 8 个普通规格，因而旧 52 行文件被淘宝以
  `整商品SKU不全(缺SKUID)` 全部拒绝。失败 recovery attempt
  `26b67a144f9448d65ef56c66` 和 signup attempt `a3d7dfd9d65d7a5e62ad4afd`
  均为 `failed_no_retry`，不得重放。
- 8 个普通 SKU 的建议新 ERP 编码尚未写库、尚未保存淘宝商品，等待用户确认：
  `1036279566778` 的 `6234601898881/8883/8885/8887` 建议按 1.2/1.35/1.5/1.8 米
  映射到 `PPS2633008032223/2224/2225/2226`；`1074244132390` 的
  `6287431318354/8356/8358/8360` 建议映射到
  `PPS2633010022523/2524/2525/2526` 并去掉旧定制分类。8 个建议编码均已核对为
  ERP 定价表和活动扩展表未占用。
- 单品立减先前已完成 `53/53`，本轮未重跑。最近一次已完成的活动回读和随后官方失败
  终态共同证明已报名仍为 `1001358847694/805268708396/863525290377`，6 个待报名商品
  在 04:33 的官方反馈中成功 `0`、失败 `6`；此后 ERP 没有新的活动写 attempt。
  本轮全新只读回读的 Web-Agent `job1` 最终为 `export_poll_timeout`，没有平台写入，
  因此不得把它当作新的平台现状确认或自动重试依据。
- 修改后的全部 `test_campaign*.py` 回归通过（保留 2 条原有 skip），Python 编译、策略
  JSON 解析和 diff 检查通过。后续只有在用户确认 8 条映射并完成全新官方只读回读后，
  才能创建新的数据修复和全新一次性报名 scope；旧 attempt 永久不重放。

## 2026-09-02 升降桌轮换 SKU 已保存为唯一平台草稿并完成只读闭环

- 商品 `793202812082` 的唯一草稿仍为 `dbDraftId=1355242198`，原保存时间仍为
  `2026-09-01 22:54:21`。草稿现有 13 个唯一商家编码；新增规格为
  `130cm 带高台升降桌` / `PPS2441004051311B1`，标价 `9100.00`、库存 `9`，
  桌腿材质 `钢`、电机 `2个`、桌面长度 `750mm`，其余可读字段与源规格一致。
- 最终只读回读先固定线上商品 12 行，再打开该唯一草稿逐行比较。结果为
  `sku_row_count=13`、`merchant_code_count=13`、`diff=[]`、
  `invalid_rows=[]`、`preexisting_sku_diff=[]`；原 12 个 SKU 的全部非空业务字段
  零差异。商品仍未上架，活动仍未报名，最终回读
  `platform_product_write=false`。
- ERP 台账已收口为 `saved_draft_verified` / `created_in_platform_draft`，
  `campaign_signup_status=not_submitted`，证据 SHA-256 为
  `354b084bbbe949ce8fe467f0d621b8beac1eb2b0b15aeee25c2889ea88d967e8`。
- 恢复入口锁定草稿 ID、标题和保存时间；只允许保存同一草稿，绝不点击
  `提交宝贝信息`、上架或报名。一次恢复预检因淘宝把行控件从 18 个变为 16 个而在
  写前停止，未生成恢复锁、未点击保存。程序现按同批行结构和非空业务字段比较，
  仍硬校验 13 行、唯一编码、目标字段及线上 12 行基线；Web-Agent 实现提交为
  `e5f9fca59ea7ab3d50c3d5b5e7fc7439abbe6479`，ERP 台账提交为
  `dd005bd0b917e64f915e3a4e16f5f52a6fac0641`。
- 原保存锁
  `product_sku_slot_draft_save_793202812082.claim.json` 必须永久保留；恢复锁不存在，
  不得再运行保存或恢复入口。后续只能用只读回读确认草稿，除非用户重新明确授权新的
  平台动作。


## 2026-09-01 nightly finance false-alert and export recovery

- ERP code commit `97802bd39ffeb81d24ea6886fa5ae693b7618e38` is on GitHub
  `main` and is the verified production API/Web version.  The unified release
  verifier passed health, ready, API/Web parity, migration `0149 (head)`,
  required routes and all four production containers.  The API health window
  expired seconds before its first healthy response during the initial release;
  the approved standalone Web recovery path completed the interrupted second
  half, after which the full verifier passed.
- A process-local scheduler claim now prevents the on-time 20:30 finance job
  and the 60-second startup catch-up from running the same job concurrently.
  A duplicate trigger is logged as ignored and never becomes a finance failure
  or user notification.
- The official Alipay API response `请求的账单时间无业务数据` is now stored as
  durable zero-business-day coverage.  Only that exact official evidence is
  accepted; other empty/error responses remain failures.  Covered zero days
  are not requested or reported again in later nightly runs.
- Deterministic Web-Agent selector/download contract errors are routed once to
  program maintenance and close the automatic retry chain.  Login/QR remains a
  user-input gate rather than a program failure; the existing pipeline pauses
  after its single account-level reminder.
- Focused ERP regression passed 48 tests plus Python compilation and scoped
  diff checks.  No finance pull, account login, QR flow, import, notification or
  other business action was used for release verification.  Rollback images:
  `panse-system-api:rollback-20260901-214111` and
  `panse-system-web:rollback-20260901-215124`.

## 2026-09-01 五项价格修正已完成并由全新官方导出闭环

- Windows 防火墙规则 `Panse Web-Agent LAN 8500` 已启用并回读为：仅
  `Private`、`Inbound Allow`、`TCP 8500`、`RemoteAddress=LocalSubnet`；没有公网
  入站范围。Web-Agent 仍按任务启动、空闲关闭，规则保留。
- 单品立减 attempt `357d5091e2318ec36f542ff5` 已完成；商品 `717418169535`
  的 17 个目标 SKU 随后再次走纯只读平台回读，17/17 均为活动 `143780562424`
  “进行中”且金额精确。最终 JSON SHA-256 为
  `0AE96AA09A2AEBF6427AF195B431580B798C06E45305895DB69EBD9C45307458`。
- 超级立减 V4 attempt `c45ba2842be3aab330a5c34b` 已完成：
  `1046992283533/6241476755540=285`、
  `717418169535/5011017605466=288`、
  `840643621692/5606206268612,5917906151868=276`、
  `840659847455` 的 `5917936191346,5777849039084,5917936191344,5917936191345=277`。
- 全新官方文件
  `活动准备/官方导出分析/20260901-五商品最终核验-20260901-212313/超级立减已报商品列表_20260901_五商品最终核验.xlsx`
  的 SHA-256 为
  `72A896EEAC8DE04939C5CDB70AF4F5587E18CBAF86CA174F7AA444F63E1473A8`。
  XLSX 对账证明上述 4 个营销记录均为“活动中”，8 个目标 SKU 全部为新价，所有
  非目标 SKU 前后无变化；`1044450741007` 的当前营销记录 `10030597697995` 仍为
  “活动中”，10 个 SKU 与上一份官方表完全一致。
- `793202812082` 按平台近 60 天零销量规则跳过；仓库商品
  `1038725569412/6060112621275` 永久不报名、不改价；定制/占位 SKU 低价不列为
  未解决问题。失败 attempts `797c3857548bd818cf6065fb` 和
  `cfeb16011f1afcf71a01aa15` 及其恢复证据必须保留，不得覆盖或删除。
- ERP 恢复保护已发布为 `4d2993cbde2983a76cca011db668c5114ac9ffc0`；Web-Agent
  最终导出恢复已推送为 `d558f8e370ce2bcb3f859d544a1583d1ed73d6f3`。本轮未改日常价、
  仓库价，未撤销、暂停、删除、重报或扩大商品范围。

## 2026-09-01 五项价格修正的精确超时恢复入口

- 五项范围固定：`793202812082` 是平台明确近 60 天零销量商品，永久排除本次报名、
  改价和重试；其余只允许修正 `717418169535` 的 17 个既有单品立减尾差及 4 个活动中
  商品的 8 个指定定制 SKU。不得改日常价、仓库商品、普通非目标 SKU，也不得撤销、
  暂停、重新报名或扩大范围。
- 原单品立减 attempt `b8b0ddcb5633cbe6a1b69681` 已由生产库证明为群晖连接
  `192.168.31.91:8500` 超时：`web_agent_job_id` 为空、平台写入观察为空、自动重试关闭。
  原 attempt 保持不变，不能删除、覆盖或直接重跑。
- 新恢复入口只接受上述固定 attempt、固定 manifest 和人工确认“未到达 Web-Agent”的
  一次性请求；先另建独立 recovery claim，再调用原精确 Web-Agent 入口。原 attempt
  任一字段漂移、已有 recovery claim 或恢复终态不精确都会永久停止，不自动重跑。
- 超级立减阶段现在硬性要求单品立减原 attempt 或精确 recovery attempt 已完成；第一阶段
  未完成时不能启动第二阶段。正式恢复脚本为
  `scripts/campaign_recover_five_price_single_discount_nas.ps1`。
- 当前仅完成程序准备，未执行恢复。Windows 端必须先获用户明确批准，新增仅 Private、
  LocalSubnet、TCP 8500 的入站规则，并先证明群晖使用既有令牌可访问 Web-Agent；网络
  未验通前严禁消费 recovery claim。

## 2026-09-01 计划7小促价入口与已撤销的仓库改价方案

- 活动 `143780562424` 固定修正 20 个普通 SKU：商品 `1036273574687` 8 个、
  `1074244132390` 12 个，从当前中促到手价对应立减金额改到 ERP 小促价对应金额。
  配件 `6280268983408` 以及定制、咨询、无小促价行全部排除。
- 写前复核 ERP、来源快照、三个活动和 20 个旧金额，持久化一次性 claim 后两商品
  各确认一次；部分成功、失败、未知都保留逐商品状态并禁止自动重试。成功后还要独立
  只读复核 20 个目标金额。正式脚本为
  `scripts/campaign_correct_plan7_small_promo_nas.ps1`。
- 仓库商品 `1038725569412` / SKU `6060112621275` 的 `1500 → 1420` 方案已被用户
  明确撤销：仓库中的商品不报名，也不改价。历史清单、哈希、文件名和脚本仅保留作审计，
  不再构成执行指令。
- ERP API、service、CLI、PowerShell 兼容入口和下游 Web-Agent 写入口均永久 fail-closed；
  任意调用只返回 `user_rule_excluded`、`platform_write=false`、`price_change=false`，不得
  创建 claim、启动浏览器或触达平台。
- 活动公共入口还把该商品写成不可由数据库停用的 `fixed_user_rule`：报名行、单品立减、
  目标价、价格冲突处置、动销分组和核对均整品静默排除；来源固定记录为
  `user_rule_20260901`。这不改变定制/占位 SKU 精确白名单或平台终态零动销规则。
- 02 只交付程序，不执行真实业务命令。完整规则见
  `docs/计划7单品立减小促价与仓库定制SKU一次性修正_20260901.md`。

## 2026-09-01 计划7升降桌单品立减固定补报入口

- 唯一范围固定为 workflow `campaign:super-reduce:2026-09-01`、plan 7、商品
  `1007407909979`、目标既有活动 `143939511827` 和 4 个已审核真实 SKU；占位 SKU
  `6169015583658` 永久不进入本次文件。活动窗口固定为 9 月 1 日至 9 月 5 日。
- 正式入口先只读复核三个已知活动的 ID、名称、创建时间、SKU级、减钱、活动状态、
  活动窗口与导入状态，再确认 4 个 SKU 在三个活动里全部缺失。随后重新校验 ERP
  生成的 4 行与范围摘要，先写入 `CampaignExecutionAttempt` 一次性 claim，才允许
  Web-Agent 对目标活动点击一次“添加商品”。
- 平台成功、失败或结果未知都消耗这次 claim，不自动重试；成功必须收到官方 4 成功、
  0 失败终态，并再次只读证明 4 个 SKU 只存在于目标活动、金额分别为 3508.50、
  3315.99、3101.93、2911.38 且状态生效。触发、平台提交、官方终态和回读分别留证。
- 入口不能新建活动、改活动时间/状态/价格、轮换 SKU、碰其他商品或计划 8，也不发送
  通知。01 唯一正式脚本为
  `scripts/campaign_supplement_plan7_single_discount_nas.ps1`；维护任务不得执行该脚本。
- 现场只读探针已证明目标抽屉真实路径是“新增商品”→“批量导入”→官方上传区，最终按钮
  是“确认修改”。随后正式 attempt `78838fdc2a7a5ac3a9c2380b` 已得到官方 4 成功、
  0 失败终态，并由只读快照 `14` 逐 SKU 完成回读；attempt 已是 `completed`、
  `automatic_retry_allowed=false`。一次性脚本
  `scripts/campaign_supplement_plan7_single_discount_nas.ps1` 已永久禁用，严禁再次补报。
  ERP 直接入口在发现该 completed attempt 后也固定返回 HTTP 409，不生成文件、不访问
  Web-Agent；Web-Agent 写入口同样在解析文件或打开浏览器前返回 409。

## 2026-09-01 计划7三项改时 V3 只读收口

- V2 attempt `17f82907f8f3707736bbe2b2` 已确认三个固定活动，平台写入边界已经发生；
  V2 API 与脚本因此永久禁用，严禁再次打开编辑器或点击确认。
- 原 job1 预检证明三条业务身份：固定活动名、ID、自选商品活动、SKU级、减钱、创建时间
  以及原导入状态；原整行签名同时包含派生提示“即将到期”。提交后截图中活动
  `143939511827` 的时间已变为 9 月 1 日至 9 月 5 日，派生提示变为“进行中”，固定业务
  字段与导入状态未见变化。V3 已逐 ID 回读三条活动，均为精确新档期、进行中且固定
  业务字段无差异；attempt 已为 `completed/readback_verified`，计划结束时间已同步。
- 唯一收口入口为
  `POST /api/campaigns/closeout-super-reduce-plan7-discount-times-v3` 和
  `scripts/campaign_closeout_plan7_discount_times_v3_nas.ps1`。它固定绑定外部 request
  `f5d44ce64b90`、内部 request、attempt、job2 和三项 confirmed ID；Web-Agent 只搜索和
  回读，不打开编辑器。只有三条新时间精确且固定业务字段无差异时，ERP 才把原 attempt
  原位改为 `completed/readback_verified` 并同步计划结束时间；当前已经完成。Web-Agent
  旧 `preflight/commit` 写路径已永久退役，仅保留该无写入 `readback` 能力。

## 2026-09-01 计划7三项单品立减原位改时入口

- 唯一允许的活动 ID 是 `143780562424`、`143936811502`、`143939511827`；调用端必须
  显式传入旧档期 `2026-09-01 00:00:00` 至 `2026-09-01 23:59:59` 和新档期
  `2026-09-01 00:00:00` 至 `2026-09-05 23:59:59`。顺序、ID 或任一时间不同均在
  ERP 内停止。
- 正式入口在平台写入前复核计划 7 当前单品立减范围为 392 行、55 商品、SHA-256
  `38c967e5a08acd378ff6c4778494f450613926a9cf32e7ee51b51a1d81b75d8f`，再执行 Web-Agent
  只读预检。预检逐活动确认旧时间、SKU 级、减钱方式、可编辑状态和活动行身份。
- 只读预检通过后，ERP 先写入数据库一次性 claim，再允许一个 Web-Agent commit job。
  每项确认前再次 CAS 读取；确认点击后无论失败或终态未知都禁止重试。成功还必须只读
  回读三个活动的新时间，并证明活动行除时间外的可见字段未改变。
- 本入口不能改优惠金额、商品、SKU、活动范围或价格，不能新建、撤回、取消、暂停、
  删除活动，不能触碰计划 8、通知或自动重试。01 的正式执行脚本为
  `scripts/campaign_update_plan7_discount_times_nas.ps1`；维护任务不得执行该脚本。
- 计划 7 原只读审计脚本已同步到 392 行/55 商品的新范围；千牛新版页面进入商品中心
  骨架时不再在 3.5 秒后误报“活动列表不存在”，而是等待真实工具身份并尝试
  `myseller` 新入口、旧 `qn` 入口和只读菜单导航后再失败关闭。
- 原入口的两次调用 `feb0a38dc0ec`、`32110b92632a` 均在平台写入前停止；第二次的
  失败 claim `9cf79b441a5fdbd56de061a7` 保留为 `failed`，数据库和 Web-Agent 回执均证明
  `platform_write=false`、`submitted=false`、`confirmed_activity_ids=[]`。原脚本
  `campaign_update_plan7_discount_times_nas.ps1` 已永久禁用。
- 首次恢复 `ecee536af3b8` 因页面没有“重置”按钮而在只读预检停止；回执证明
  `platform_write=false`、`submitted=false`、`confirmed_activity_ids=[]` 且未创建
  recovery claim。V1 API 与脚本均永久禁用。
- 已完成的 V2 恢复入口原为
  `POST /api/campaigns/recover-super-reduce-plan7-discount-times-v2` 和
  `scripts/campaign_recover_plan7_discount_times_v2_nas.ps1`。它固定绑定原失败 attempt、
  原两份零写入回执和首次恢复零写入回执，并要求数据库不存在 V1 claim；先逐活动 ID
  搜索并证明三条仍是旧时间，之后才建立独立 V2 claim。旧 attempt 不覆盖，V2 也只有
  一次且不自动重试；现因三条确认均已发生而永久禁用，只允许上方 V3 只读收口。

## 2026-09-01 个人支付宝历史售后/送装费用已按用户确认归账

- production code `d6c4dd31c607028134f07d1fad52e24463acca0d` 已统一发布；API/Web 同版本，
  health/ready 通过，migration `0149 (head)`，四个生产容器正常。
- migration `0149` 给关联账增加明确入账去向：客户差价、返现、赔付、维修、返厂进入
  售后；正常“送装/直达”进入订单安装费。安装费确认和作废都带版本、金额、决定人及
  字段修改档案，不能覆盖万师傅或人工已有金额。
- 正式历史扫描建立 33 条候选。用户批准且完成防重核验后，确认 9 条、合计 ¥889.20：
  6 条售后 ¥429.20（其中明确差价/返现/补偿 4 条 ¥129.20，后续维修 2 条 ¥300）；
  3 条正常送装/直达进入订单安装费 ¥460。余下 24 条继续 proposed，零 rejected、零
  voided，不因本轮执行而猜测归账。
- 三个安装订单的 `install_fee` 与 `actual_install` 已分别回写 ¥180、¥180、¥100；财务
  成本拆分回读的安装分量与上述金额一致。六条售后均由系统托管行承担，逐单额外售后
  回读一致。阿哲两笔个人维修发生在万师傅首修完成后，其中一笔明确为第三次维修；
  因此保留万师傅首修 ¥179，并将两笔后续维修 ¥300 分开入账，逐单售后合计 ¥479。
- 正数“物流拦截费”¥425 未建立支出关联，仍是原始收入流水。第一次执行在安装费审计
  来源字段长度保护处失败，整个事务已回滚并验证零候选、零流水变化、零订单变化；随后
  修复为 16 字符内的 `alipay_link`，专项回归通过后重新发布并一次事务成功写入。
- 相关验证：186 项支付宝/售后/对账/大盘回归通过，前端生产构建通过；9 条确认记录的
  `decided_by` 均为 `operator:user-approved:2026-09-01`，三笔安装产生 6 条字段审计记录。

## 2026-09-01 个人支付宝售后打款统一关联（已正式发布）

- migration `0148` 新增 `after_sales_payment_links`，以支付宝流水为原始事实，保存订单、
  售后、可选万师傅单、金额、分类、证据、决定人和版本。错误决定只作废，不删除。
- 新流水导入后只扫描本次新增记录；“明确差价/返现/赔付 + 唯一未取消 ERP 订单”
  自动确认。送装、直达、维修、退回、同名多单、缺订单和万师傅可能重复项只建候选。
- 正数收回款永不计为售后支出。平台退款仍在订单退款字段；个人支付宝打款作为平台外
  售后成本，避免把同一笔钱同时减收入又加成本。
- 售后页新增历史扫描、候选核对、人工订单号/类型修正和排除；确认后财务流水状态、
  售后、数据大盘、报表与逐单核对共享同一关系链。
- 旧支付宝路由已修复空订单号孤儿问题：只有非空且真实存在的 ERP 订单才可建售后。
- 详细关系、历史现场分类和反方审查见
  `docs/个人支付宝售后打款联动_20260901.md`。
- GitHub、生产 API 和生产 Web 的功能提交均为
  `46e0eefefc707c444a5ed2ad3b146d4a11ef2da8`；统一发布通过 health、ready、
  API/Web 版本一致、OpenAPI 路由、在线前端文案、migration `0148 (head)`、数据库
  结构和四个容器检查。API/Web 回滚镜像分别为
  `panse-system-api:rollback-20260901-003810`、
  `panse-system-web:rollback-20260901-004228`。
- 175 项关联/支付宝/售后/权限回归与前端生产构建通过。全库测试仍有 27 项与本次无关的
  既有失败；相同失败已在未改代码的 `d8c2bcb` 基线复现。
- 上线后只读预览 2026-07-01 至 2026-08-31 得到 33 条候选：4 条安全唯一、29 条
  需复核。此次只生成预览，未建立候选、未确认历史金额、未改任何财务或业务记录。

## 2026-08-31 支付宝主力号扫码续跑按本次文件验收

- 已确认 22:51 下载的 `alipay_main_2026-08-01_2026-08-31.zip` 实际成功入库
  201 条；“失败 3 份、待处理 3 份”来自共享目录中的历史文件，被旧逻辑错误归到
  本次扫码续跑。
- 主力号续跑现在以任务开始前后的文件签名差生成本次精确清单，先只导入并验收这份
  清单；共享目录全量扫描继续保留历史问题，但不再决定本次主力号成败。
- 成功证据要求本次清单至少匹配一份文件，并出现 `alipay/主力` 的 `imported` 记录；
  本次文件解析失败、待处理、未匹配或缺少主力号入库证据仍会失败即停。
- 本次修复不补跑流水、不触发扫码或账号动作；部署后仅依据上述既有入库证据修正
  被误留的待扫码状态和原失败链。

## 2026-08-31 SKU 身份账本与升降桌新命名校准（待发布）

- migration 0147 新建当前身份、追加观察和未保存槽提案三张表。回填只读 ERP 数据库，不访问淘宝；未知历史字段不猜。
- 活动预检 R7 显示账本状态；正式写入前的淘宝官方全量导出会追加观察并做 item/SKU 三方精确比较，缺失、额外或身份冲突均停止。
- 升降桌固定目标改为 `130cm 带高台升降桌` / `PPS2441004051311B1`。旧名称只作为禁止项；重载页面若出现即失败。
- 新行逐字段复制源行；只允许规格名称和商家编码不同。源行页面价格必须关联 ERP 标价 9100 或日常价 6825，2000 默认值未消除即失败。
- 只读查询/导出：`scripts/sku_identity_ledger_query_nas.ps1`；新预览：`scripts/campaign_stage_lift_desk_named_slot_nas.ps1`。详细边界见 `docs/campaign-sku-identity-ledger-20260831.md`。

## 2026-08-31 lift-desk visual checkpoint ready

- The exact unsaved calibration preserved 12 existing options and created one
  unsaved `130cm 带高台（备用1）` row, making 13 options in the editor preview.
- No target merchant code was filled and no product save, activity withdrawal
  or campaign action occurred.  The screenshot is
  `D:\AI\畔色ERP系统\Web-Agent程序\data\output\_upload_tmp\product_sku_slot_stage_793202812082.png`.
- The one requested user visual confirmation is now the only gate before
  implementing final field copy and one-shot save support.

## 2026-08-31 lift-desk template-15 retry gate

- QianNiu expanded the exact lift-desk SKU option template from one column to
  15 columns.  Web-Agent release `f83100f01ec338e86ecfea1f63c4e580f3da9777`
  preserves every official header and writes only the exact option column.
- The new one-time operator entry is
  `scripts/campaign_stage_lift_desk_sku_slot_template15_nas.ps1`.  It first
  proves the main workstation has the repaired source markers, then delegates
  to the same exact item-scoped calibration.  It still closes before product
  save and cannot withdraw an activity or submit a campaign.
- The follow-up recognition modal is handled only by the separately gated
  `scripts/campaign_stage_lift_desk_sku_slot_unsaved_apply_nas.ps1`.  It allows
  one exact `在当前规格后添加` click after the one-option guard and still
  closes the browser without saving the product.
- The current one-time entry is
  `scripts/campaign_stage_lift_desk_sku_slot_recognition_wait_nas.ps1`; it
  additionally proves the 90-second same-modal, no-click recognition wait is
  present before delegating to the exact calibration.
- Because QianNiu rebuilds that dialog DOM, the superseding one-time entry is
  `scripts/campaign_stage_lift_desk_sku_slot_dialog_rebind_nas.ps1`.  It proves
  fresh per-poll dialog lookup and the bounded zero-dialog transition grace.
- The current superseding entry is
  `scripts/campaign_stage_lift_desk_sku_slot_topmost_dialog_nas.ps1`.  It also
  proves hidden/covered/nested dialog auditing and refuses two independently
  interactable dialogs.
- The current superseding entry is
  `scripts/campaign_stage_lift_desk_sku_slot_semantic_close_nas.ps1`.  It also
  proves the top-chrome semantic close control is separated from the exact two
  footer business actions.

## 2026-08-31 campaign unattended price and SKU-slot foundation

- Historical no-sales registration no longer pre-excludes a product. Every new
  campaign starts from the complete current ERP in-sale scope; an exact
  current-campaign no-sales terminal is isolated to that workflow and does not
  alarm or suppress the next campaign.
- Fresh platform price-line conflicts now share one audited resolver. Ordinary
  SKUs may receive at most CNY 2.00 combined correction; larger differences
  request a clean physical SKU slot. Explicit custom SKUs use an immutable
  first-managed ERP baseline and may never produce a final buyer price below
  20% of that baseline.
- Migration `0146` adds append-only logical-to-physical SKU slots and one-shot
  mutation attempts. Old Taobao SKU IDs never change meaning; cooling slots
  become reusable only after exact fresh platform evidence, never merely after
  elapsed time.
- The lift-desk pilot is fixed to item `793202812082`, logical SKU
  `PPS2441004051311`. The Web-Agent can inspect the exact editor, download the
  official template and transiently stage `130cm 带高台（备用1）` with physical
  code `PPS2441004051311B1`, then closes without clicking `提交宝贝信息`.
  A saved product mutation and any campaign withdrawal remain task-01 business
  actions after the one-time visual calibration; maintenance did not execute
  them.
- Task 01's credential-free one-time calibration entry is
  `scripts/campaign_stage_lift_desk_sku_slot_nas.ps1`. It uses the encrypted
  Web-Agent token inside ERP, retains the screenshot in the normal Agent output
  directory and returns `platform_product_write=false` plus the unsaved target
  row. The first calibration does not fill the proposed physical merchant code;
  it exists to obtain the one user visual check before enabling any saved edit.
- Full design and adversarial review:
  `docs/活动报名无人值守与SKU备用槽实施计划_20260830.md`.

Last updated: 2026-09-01 (Asia/Shanghai)

## 2026-08-30 campaign automation failure-history closeout

- The former plan-7 "existing draft publish" interpretation is retired.  A row
  in the official enrolled-item export with status `暂停` is an enrolled but
  paused record, not a platform draft.  The ERP and Web-Agent publish routes now
  fail before browser work; the historical snapshot is corrected by migration
  `0145`.  All older sections below remain incident history only and must not be
  used as current operating instructions.
- Every final campaign upload now has a database-backed immutable attempt keyed
  by workflow, operation and exact manifest hash.  A write claim is committed
  before the browser upload.  After that claim, success, failure and unknown
  outcomes are all no-retry, including after container/process restart.
- Immediately before the claim, ERP downloads the official current-product
  workbook and requires the full exact SKU set for every pending product.  A
  missing/new/extra SKU, stale mapping, nonnumeric identity or workbook parsing
  gap stops before platform write.  Merged blank continuation rows are
  forward-filled so additional official SKUs cannot disappear from the guard.
- Read-only connectivity/startup failures before any write claim retain the
  current plan state and may retry in the next scheduler window.  Login, QR,
  CAPTCHA, campaign identity, SKU, price, no-sales and policy failures remain
  hard stops.  Automatic discovery now persists a stable workflow key for every
  exact campaign/phase/window so scheduler restarts cannot create a second
  identity.
- Price rules are unchanged: real-SKU signup price equals ERP daily price; final
  price must equal the ERP mid/big target; no price reduction, SKU rotation,
  withdrawal, pause or removal is introduced.  Known no-sales items are kept
  out of campaign signup.
- Historical review covered 76 campaign API audit rows, all 48 failed scheduled
  campaign runs, 15 reconciliation reports, 9 evidence snapshots and the NAS
  campaign artifact series since July.  The durable failure taxonomy and the
  prevention mapped to each class are recorded in
  `docs/活动报名全量失败复盘与自动化闭环_20260830.md`.

## 2026-08-30 plan 7 exact existing-draft publication entry

- The only permitted scope is the two platform drafts created by attempt
  `782299846f10d86ef4742c20`: items `717809819543` and `793084818113`, 29 SKUs,
  immutable price fingerprint
  `0355c293c277330e490858df4f6b4bb57484881fcea9897f27c194b68fb7231b`.
- Before the irreversible claim, a fresh full enrolled export must prove that
  every `暂停` item on the long-running Super Reduce sign record is exactly one
  of those two items, and that all 29 SKU IDs/prices still match snapshot `9`.
  One extra draft, one missing SKU or one price/status drift stops before the
  publish click.
- The entry never uploads a workbook, changes a price, rebuilds a draft,
  withdraws/pauses/removes an item, touches the other three failures, plan 8 or
  the 388 single-discount rows. It claims once before the platform confirmation;
  unknown/failure outcomes are permanently no-retry. A successful confirmation
  still requires a fresh 29-SKU readback in `已发布设定`/`活动中` state.
- Task 01 owns the sole business invocation through
  `scripts/campaign_publish_plan7_existing_drafts_nas.ps1`; maintenance only
  ships and verifies the program and must not run the command.

## 2026-08-30 plan 7 partial draft-import audit

- The one claimed five-item job returned the official terminal `5 total / 2
  import-success / 3 failed`. Super Reduce stops before `一键发布` whenever
  import failures exist, so this is a partial platform draft write, not zero
  platform activity and not proof that the two items are enrolled.
- Attempt `782299846f10d86ef4742c20` and manifest
  `2fa747d77823ed63baee82c5dbcc0d0fff6e248f77583dd4c9b074fa57d5c30d`
  are permanently no-retry. The fixed audit entry downloads the original
  failure workbook, re-exports enrolled items, verifies all five per SKU and
  recomputes final-price math. Evidence is append-only and every execution
  boundary remains false.
- No failed-only recovery is exposed by this release. Exact draft/enrolled
  status and all three official failure reasons must be known first; the two
  draft-import successes, any already-enrolled item and every price conflict
  are forbidden from another upload.
- The one permitted audit is complete as snapshot `9`. Official feedback
  SHA-256 is
  `699a962e46d8a68daaeaabd3a29d1322c337510f68c5339f7d8983f5aaeabaae`.
  Items `717809819543` and `793084818113` were imported only into platform
  draft; every expected SKU price matches exactly, but every row is `暂停` and
  neither item is proven active. Items `1036273574687`, `1074244132390` and
  `793202812082` failed respectively because the upload omitted current
  platform SKU `6280268983409`, the official coupon-after price ceiling would
  require a lower signup price, and official 60-day sales were zero.
- The audit performed platform reads only and recorded `platform_write=false`,
  `account_action=false`, `price_change=false`, `sku_rotation=false`,
  `notification=false` and `automatic_retry=false`. Failed-only recovery
  remains closed: publishing could also affect the two existing drafts, the
  missing SKU has no trusted ERP daily-price mapping, and the other two failures
  are hard price/no-sales gates. The attempt must not be rerun or described as
  a completed signup.

## 2026-08-30 plan 7 fresh official-scope partition

- Fresh official export SHA-256
  `0faefce8f97c1b470fefcea4ece33bfc62c14beaf5471c9b88826ac8bee718c0`
  contains 25 of the formerly reviewed 30 items. A product-name hit is not
  success: every expected SKU was compared to its exact activity price, and
  real-SKU final price was recalculated from official plus existing discount.
- 21 items are exact and are read-only closeouts. Four existing items are hard
  stops and cannot be uploaded or repriced: `1046992283533`, `717418169535`,
  `840643621692`, `840659847455`. Five wholly missing items are the only new
  upload scope: `1036273574687`, `1074244132390`, `717809819543`,
  `793084818113`, `793202812082` (70 rows: 52 real + 18 placeholder; scope
  SHA-256 `1f66d114e711b0fb3448a8a1503120bb5edd35a2d6416105f66545392f15bc86`).
- The recovery entry re-exports and re-partitions all 30 before claiming. Any
  drift stops without a write. Only the exact five missing whole items can be
  claimed; each still requires R16/R17, a platform terminal and exact per-SKU
  post-submit export. The four conflicts stay in the plan hard-failure marker,
  so the plan remains alarmed after the safe five complete. The old incident
  payload is rejected.

## 2026-08-30 plan 7 pre-claim export recovery hardening

- The second plan-7 remaining-signup request reached ERP and returned HTTP 409
  after about 394.6 seconds, but the caller session disappeared before showing
  its JSON. Live state proved the one-shot setting was absent and no execution
  receipt was added, so no batch was claimed and no platform write occurred.
- The fixed payload is now bound to recovery incident
  `plan7-preclaim-export-e222849772c5`; stale copies of the old command fail
  schema/request guards. While the server is working, the container CLI emits a
  heartbeat every 20 seconds so SSH/Codex does not appear silently stalled.
- Any future pre-claim evidence failure is persisted as
  `plan7_remaining_preclaim` evidence with `attempt_claimed=false` and
  `platform_write=false`, while the one-shot attempt key remains untouched.
  Focused route/service/CLI regressions passed 25. No campaign execution was
  used for verification.

## 2026-08-30 plan 7 submitted identity-recovery readback closeout

- Recovery attempt `ab51f002ac5570c9bb407d00` has an authoritative platform
  terminal: four rows validated, four imported successfully, zero failed and
  `submitted=true`.  It must never be uploaded or submitted again.
- Its immediate readback failed before reading rows because the persistent
  browser's first restored tab was Product Center, where `活动列表` correctly
  had zero matches.  This was a readback page-binding failure, not a platform
  import or pricing failure.
- The dedicated closeout route/CLI is fixed to the same workflow, plan,
  attempt, terminal evidence request and four-row scope.  It contains no upload
  call, claims one readback receipt before contacting Web-Agent and updates the
  existing attempt rather than creating a business attempt.  Completion still
  requires current SKU IDs `6127845548093` through `6127845548096`, exact
  deductions 612.63/627.08/644.50/662.44, status `未开始` and an immutable fresh
  artifact.  Any difference is terminal and cannot retry automatically.
- Focused ERP closeout/service tests passed 28; all 16 campaign test files
  passed with two existing fixture skips.  Maintenance did not execute the
  readback command or any platform write.


## 2026-08-30 plan 7 stale Taobao SKU identity recovery

- The first exact four-row correction stopped safely with platform terminal
  `参数错误:skuId不是商品的有效sku` for all four rows.  No row was accepted,
  `submitted=false`, and the existing 384 exact rows were untouched.
- A fresh official one-item QianNiu product export proves that merchant SKU
  codes `PFG2521002122211` through `PFG2521002122214` still represent the same
  four table sizes, but their current Taobao SKU IDs are `6127845548093`
  through `6127845548096`; ERP still held obsolete IDs `6279984722445`
  through `6279984722448`.  This is a one-to-one external identity repair, not
  SKU rotation.  The official artifact SHA-256 is
  `cdf6502bbf4c048824a0ad5f1545d6335faa117a854f3c624773c1e610a9a72b`.
- `POST /api/campaigns/recover-super-reduce-plan7-discount-sku-identity` and
  its controlled container CLI are fixed to workflow plan 7, item
  `1047741902625`, the failed attempt, immutable snapshot 1, the exact nine-row
  official export and four fixed merchant-code mappings.  They CAS-repair only
  those four external IDs, preserve every price/specification, verify the new
  388-row digest, perform one exact read-before-write and allow at most one new
  four-row import.  Every terminal is claimed, automatic retry is forbidden,
  and a successful terminal still requires a fresh exact post-readback.
- The route cannot change price, rotate SKU, touch plan 8 or the existing 384
  rows, call campaign signup, withdraw/pause/remove anything, notify, or accept
  a different export.  The identity evidence and official workbook are stored
  immutably before the platform phase.
- Verification passed: 28 focused identity/correction/service tests, all
  campaign tests (223 passed, 2 intentional fixture skips), Python compilation,
  PowerShell parsing and diff checks.  Maintenance did not execute the recovery
  command or any platform write.


## 2026-08-30 plan 7 exact four-row single-discount correction

- Immutable snapshot `1` (`plan7-discount-audit-464fc409dce0`) proves that the
  388-row target was not wholly missing: 384 rows are present in activity
  `143780562424`, all at the exact ERP deduction, and all have platform status
  `未开始`.  Because their fixed window begins at `2026-09-01 00:00:00`, these
  384 rows are correctly configured and waiting for the window; they must not
  be resubmitted or called effective before the start time.
- The only missing scope is item `1047741902625`, four physical SKUs:
  `6279984722445` (daily 3390.00, deduct 612.63, final 2438.37),
  `6279984722446` (3465.00, 627.08, 2490.92), `6279984722447`
  (3577.50, 644.50, 2575.00), and `6279984722448` (3667.50, 662.44,
  2638.06).  All are non-placeholder no-sales fallback rows, have zero price
  concession, keep ERP daily price as the calculation base, and satisfy
  `daily - official 10% - deduct = ERP final` exactly.
- Root cause is a real platform partial import, not readback parsing: the exact
  window activity is visibly marked `部分导入失败`, its canonical readback has no
  row for this item, and all other 384 rows are visible and exact.  The stale
  Taobao listing export still contains older SKU IDs, while the authoritative
  campaign mapping has used the current four IDs since 2026-08-07; the fixed
  correction binds only the current mapping and forbids SKU rotation.
- `POST /api/campaigns/correct-super-reduce-plan7-discount` and the controlled
  container CLI are fixed to workflow plan 7, snapshot 1, its raw artifact
  SHA-256 and the four-row canonical digest.  They first read the exact four
  rows.  If already exact, the call finishes without a write; if still missing,
  exactly one four-row single-item-discount workbook is submitted.  Any failed
  or unknown terminal is permanently claimed with no retry.  A successful
  terminal must be followed by a fresh exact four-row readback and an immutable
  evidence snapshot.  The route cannot call official campaign signup, change
  prices, rotate SKUs, touch the existing 384 rows, touch plan 8, withdraw,
  pause, delete, notify or automatically retry.
- Code/GitHub/production commit is
  `19e79784f6aea6e54ef5c96879621137a8623f96`.  Focused correction/audit/service
  identity tests passed 26/26; all 217 campaign tests passed except two
  intentional skips (215 passed, 2 skipped).  Python compilation, PowerShell
  parsing and diff checks passed.  The broader backend suite still has 27
  pre-existing failures in unrelated cost, inventory, supplier matching and
  order-detail tests; campaign tests have no failure.
- Unified NAS release passed health, ready, API/Web commit parity, migration
  `0144 (head)`, deployed route/CLI import and all API/Web/DB/backup containers.
  Rollback images are `panse-system-api:rollback-20260830-150408` and
  `panse-system-web:rollback-20260830-150721`.  The authoritative wrapper matches
  the released source (SHA-256
  `8A4D4B640B4776BDA796AE24F7A98F186D4E1E4AA24FB722628A8EE4DD3D625D`), and the
  production one-shot attempt is still absent: maintenance did not execute a
  readback or platform write.
- Task 01 may execute the single formal command once:
  `& 'D:\AI\畔色ERP系统\ERP程序\scripts\campaign_correct_plan7_single_discount_nas.ps1'`.
  Its structured terminal plus post-submit readback is the only completion
  authority; a nonzero result must be returned to maintenance and must not be
  rerun.

## 2026-08-30 plan 7 single-discount evidence and terminal receipts

- The earlier one-item Super Reduce submission did not prove that the separate
  388-row SKU-level single-discount workbook was applied.  Plan 7 now has a
  fixed, service-authenticated read-only audit route and NAS wrapper.  It locks
  workflow `campaign:super-reduce:2026-09-01`, plan `7`, the canonical 388-row / 54-item
  scope digest, the exact update window, and the plan-only exclusion
  `805268708396`.
- The Web-Agent may only search the SKU-level single-discount list, open an exact
  activity's `修改优惠` readback and read visible SKU amounts/status.  It cannot
  select, fill, upload, submit, activate, pause, delete, notify or automatically
  retry.  Ambiguous activity IDs, missing rows and unknown states remain explicit
  differences instead of being called success.
- Migration `0144` adds append-only `campaign_evidence_snapshots`.  The audit
  stores the complete per-SKU classification plus the canonical raw readback
  artifact.  Future single-discount commits also persist the submitted target
  workbook, final platform counters, every failed row and the complete platform
  failure workbook in an independent transaction, so a later rollback cannot
  erase a partial-write receipt.
- A single-discount commit is now complete only when the platform terminal says
  `complete`, failed rows are zero and the successful row count exactly equals
  the requested row count.  Partial platform success fails closed and is never
  treated as full completion or an automatic retry signal.
- Code/production commit `0252a40276f6800f87a1099ea81b5c22d70154f0` passed
  94 focused ERP tests, campaign regression and Python compilation.  The unified
  NAS release passed `/api/health`, `/api/ready`, API/Web version parity,
  migration `0144 (head)` and all API/Web/DB/backup container checks.  No audit,
  signup, discount upload, submit, price change or platform account action was
  run during maintenance.
- Task 01 may run the only formal read-only audit command:
  `& 'D:\AI\畔色ERP系统\ERP程序\scripts\campaign_audit_plan7_single_discount_nas.ps1'`.
  Its structured result, not the historical workbook alone, is the authority for
  deciding which of the 388 rows are correct, missing, mismatched or not effective.

## 2026-08-30 campaign plan-scope persistence incident

- The plan-7 platform submission was a one-item delta for item `797294092429`
  (two SKUs), not proof that every ERP product or every planned single-item
  discount had completed.  The latest read-only activity export contains 56
  item IDs and 496 SKU evidence rows, while the generated discount plan contains
  54 items and 388 rows; those are separate scopes and must not be collapsed
  into a single "all products completed" statement.
- Root cause: terminal platform classification replaced request-owned
  `official_all_store=true; official_exempt_items=805268708396` with a derived
  `official_active_items` marker.  That made later read-only rebuilds put the
  plan-scoped exempt bookcase back into signup and discount candidates.
- Terminal classification and qualification now preserve an operator-owned
  all-store scope and its explicit exemptions.  Explicit active-item plans keep
  their existing behavior.  Migration `0143` restores only workflow
  `campaign:super-reduce:2026-09-01`, removes its stale derived active marker,
  and restores item `805268708396` as a plan-only exemption.
- This repair performs no signup, upload, discount push, price change, retry,
  withdrawal, account action or notification.  Plan 7 remains incomplete until
  an audited coverage reconciliation separately proves existing official scope,
  the current one-item delta, and all required single-item-discount rows.
- Code/production commit `01ae63a84fd539659a070becce536e2b3e061708` passed
  203 campaign tests (201 passed, 2 intentional skips), Python compilation and
  diff checks.  The unified NAS release passed health/ready, API/Web version
  parity, migration `0143 (head)` and API/Web/DB/backup container checks.
  Rollback images: `panse-system-api:rollback-20260830-122920` and
  `panse-system-web:rollback-20260830-123341`.
- Production read-only rebuild after migration keeps plan 7 `alarmed`, produces
  exactly two signup rows for item `797294092429` and 388 discount rows across
  54 items, and excludes `805268708396` from both scopes.  This is scope-safety
  evidence only; it does not prove those 388 discount rows were applied.

## 2026-08-30 plan 7 submitted-price / paused-state verification

- The one approved plan-7 upload was submitted once and must never be replayed. Its terminal platform batch receipt was 1 item successful / 0 failed.
- Fresh export SHA256 `c2400fe896bc5f6da1e544faef099b7074a0d79e941aba9a7e9e58fbcd8ee88f` contains both reviewed SKU IDs with exact activity prices `1582.50` and `1410.00`, but the platform status is `暂停`. The old verifier discarded paused rows and incorrectly reported them as missing.
- `POST /api/campaigns/verify-super-reduce-plan7-post-submit` and `scripts/campaign_verify_super_reduce_plan7_post_submit_nas.ps1` are fixed to workflow `campaign:super-reduce:2026-09-01`, plan 7, attempt `dd0215218c70f952bb0865f8`, and the reviewed scope digest. They perform a fresh export and comparison only: no signup, upload, submit, price change, activation, notification, or retry.
- Exact-price paused rows now report `platform_imported_but_paused` and leave the plan alarmed. Only a later fresh export proving both exact SKU prices in `已发布设定`/`活动中` may reconcile ERP to `signup_pushed`; no platform action is performed by that reconciliation.

## 2026-08-29 Super Reduce plan 7 one-shot resume gate

- A dedicated audited route and container CLI now resume only
  `campaign:super-reduce:2026-09-01` / plan 7 from `alarmed`.  The local NAS
  wrapper has no campaign parameters and cannot select plan 8.
- The route locks workflow+plan, requires the exact reviewed two-SKU scope and
  price/final-price SHA-256, the exact plan-level exemption `805268708396`, and
  fresh plan-7 R16/R17 evidence.  It uses an explicit `resume_executing` CAS
  state and a durable one-shot receipt; a claimed, failed or unknown attempt is
  never automatically retried.
- The final preflight checks the actual two rows even when an older empty
  platform-qualified marker exists.  The recovery reuses the already persisted
  plan-scoped evidence and performs no duplicate pre-submit platform export.
  It preserves the mandatory post-submit exact export/SKU verification.
- This entry allows one Super Reduce signup upload only.  It cannot change
  prices, rotate SKUs, withdraw/pause/remove existing rows, touch plan 8, or
  auto-create a single-item discount after a no-sales result.
- Campaign regression, deployment commit, migration/head and production
  version/health evidence are recorded in the release handoff for this change.

## 2026-08-29 Super88 candidate price-evidence gate

- Formal `/api/campaigns/refresh-evidence` keeps the existing enrolled-item H/I
  export and, only for an exact fixed-window sign record, adds the Web-Agent's
  read-only not-yet-enrolled candidate evidence for the remaining ERP item/SKU
  allowlist.
- Candidate evidence stores `最低标价` and the platform's explicit
  `符合要求的建议活动价` as a distinct maximum eligible activity-price ceiling;
  it never fabricates a coupon-after line. R17 accepts this stricter platform
  ceiling only from the exact audited candidate source. If ERP daily/signup
  price exceeds it, the whole item is held without price mutation. Existing
  enrolled items still require the original fresh minimum-list and
  minimum-coupon-after H/I pair.
- Candidate `一口价` also refreshes R16 placeholder current-price evidence.
  Missing or ambiguous candidates remain hard-blocked. The same service-only
  refresh command remains read-only: no signup, upload, price change, account
  action, notification or automatic retry.
- Focused evidence/policy tests passed 33/33; the complete ERP campaign suite
  passed 187 tests with 2 intentional sample-dependent skips. Python compilation
  and diff checks passed.

## 2026-08-29 Feishu live QR handoff for finance login gates

- ERP code commit `70fb4d2` makes the existing live scan path explicit in the Feishu alert group. Finance/login alerts now tell the user to reply `@Panse System 扫码`; `发二维码`, `发送二维码`, `二维码`, `扫码登录`, `开始扫码` and `我要扫码` are accepted aliases.
- A reply starts the existing bounded pending-scan worker with `wait_scan=true`; the QR image is routed only to the configured Feishu alert group, never the order group, and remains live for at most 10 minutes. The scheduled finance run still does not open an unattended expiring QR session.
- The login alert and QR path keep finance amounts redacted. Successful scan, timeout and repeated copies of the same open login incident do not create duplicate notices.
- ERP verification: 53 focused tests and 124 alert/order/finance regression tests passed (2 intentional skips); Python compilation and diff checks passed. No live scan, login, bill pull, account action, finance replay or external test notification was performed.

## 2026-08-29 unsubmitted campaign plan resume/correction repair

- Code commit `6c09059d903b50290e7c2a0a638a2464d18fd3bf` repairs two bounded resume defects without weakening R16/R17 price-evidence gates or the one-shot submission boundary.
- A read-only activity-evidence refresh no longer replaces an operator-owned `official_all_store=true; official_exempt_items=...` scope with derived `official_active_items`. Formal `prepare` compares only request-owned scope/free text and ignores runtime evidence markers while preserving them on the plan; a real official-scope change still conflicts.
- The audited plan-scoped exemption correction accepts `alarmed` only when the plan has no submitted receipt, reconciliation report, or platform-write marker. Malformed receipt state fails closed. It can repair the exact legacy empty-active-scope drift with compare-and-swap semantics, including when the intended exemption list remains empty.
- This repair restores the safe correction path for workflows `campaign:super-reduce:2026-09-01` (plan 7) and `campaign:super88:49462:49469` (plan 8). It does not itself run correction, prepare, evidence refresh, signup, upload, price change, submission, account action, notification, or automatic retry.
- All campaign-named backend suites passed: 185 tests, 0 failures/errors, 2 intentional skips. Python compilation and diff checks also passed. No database migration is required; the schema head remains `0142`.

## 2026-08-28 audited prepare-only service identity for task 01

- GitHub `main` contains code commit `0d7de3bff2a63570f89eeea13f0703ebd322ab34` (`feat: add audited campaign prepare service identity`); production API and production Web are deployed from that code commit. The official unified NAS release passed health, ready, API/Web commit parity, required routes/features, database migration `0141 (head)` and API/Web/DB/backup container checks. Rollback images: `panse-system-api:rollback-20260828-021640` and `panse-system-web:rollback-20260828-022900`.
- Task 01 can invoke the formal preparation endpoint without a browser token, password or exported secret through `scripts/campaign_prepare_nas.ps1`. The wrapper accepts one local JSON file, sends it on stdin to the controlled production-container CLI, and the CLI calls the real `POST /api/campaigns/prepare` HTTP route. The exact command and request contract are in `docs/ERP活动正式后端入口_20260828.md`.
- Migration `0141` creates a dedicated encrypted `campaign_prepare_service_token`. It is not printed, returned or stored in the wrapper. Its machine identity is accepted only on the exact prepare path; it cannot list plans, change exclusions, upload, submit, retry, withdraw or call any other ERP API. Admin/operator Bearer access remains supported, while viewer and invalid credentials remain rejected.
- The controlled CLI uses the same global auth gate, endpoint dependency, Pydantic validation, durable `workflow_key` idempotency, campaign service and audit middleware as normal HTTP callers. It does not import a route handler, fabricate a `User`, write through ORM directly, read browser storage, or contact Taobao.
- A production-safe end-to-end probe submitted only `{\"workflow_key\":\"campaign:auth-probe\"}`. It reached the real endpoint and returned the expected HTTP 422 before handler/business writes because required campaign fields were absent. Audit row `32541` recorded `service:campaign-prepare`, `POST /api/campaigns/prepare`, status `422` and `authenticated path-scoped machine request`; the same identity received HTTP 401 on `/api/campaigns`. No plan, activity signup, upload, price change, account action, platform submission or external notification was created.
- New service-identity tests passed 4/4, the focused campaign/auth scope passed, and all campaign-named suites passed with 2 intentional skips. Python compilation, PowerShell/Bash syntax, Docker migration upgrade from `0140` to `0141`, encrypted-setting checks and release verification all passed.

## 2026-08-28 campaign internal preparation API and hard eligibility gates

- GitHub `main` contains code commit `2a67f6ad59e54a592ff7213336fa8e84f22f982d` (`fix: harden campaign internal preparation`); production API and production Web are deployed from that code commit. The unified NAS release passed health, ready, API/Web commit parity, migration `0140 (head)`, required routes/features and API/Web/DB/backup container checks. Rollback images: `panse-system-api:rollback-20260828-014516` and `panse-system-web:rollback-20260828-014921`.
- The formal ERP-internal entry is `POST /api/campaigns/prepare`. It is the only supported preparation path for ERP product mappings, daily/target prices, no-sales exclusions, durable campaign-plan identity, generated signup/discount rows and preflight. It never opens ERP pages and never submits to Taobao. The complete request contract and September examples are in `docs/ERP活动正式后端入口_20260828.md`.
- A stable `workflow_key` provides durable idempotency across process restarts: the same key and identity regenerate fresh rows/preflight on the same plan; a changed identity under the same key is rejected. `fixed_window` plans require exact title, both numeric platform IDs and exact start/end; `long_running_update` expresses a long-running Super Reduce activity with a bounded ERP update window plus `platform_active_until`, without inventing a short platform campaign.
- Historical no-sales registration is now a hard signup exclusion, not an advisory. Production migration/sanitization retained 34 valid registrations and removed the structurally invalid values; a post-release service read returned `invalid_registry_values=[]`. Invalid IDs such as `5`, `待定` and `暂无` cannot be added by grouping or feedback ingestion.
- Whole-item dedicated-link exclusion is auditable through `GET/POST /api/campaigns/item-exclusions` and `DELETE /api/campaigns/item-exclusions/{item_id}`. Automatic whole-item exclusion is allowed only when every authoritative mapped SKU is `is_custom_placeholder=true`; names and keywords are never used. Production verification classified all-placeholder item `1001358847694` as excluded while mixed normal-product items `792992319206`, `793128577437` and `793135173033` remained included. No unconfirmed candidate link was marked.
- Real-SKU signup price remains ERP daily price; final price remains the ERP mid/big campaign target. SKU rotation is disabled and any legacy rotation request is a hard preflight error. Existing active campaign items outside the new safe scope are preserved and reported; withdrawal, pause, cancellation and removal still require current explicit authorization for the exact item list.
- Structured official scope (`official_all_store`, `official_active_item_ids`, `official_exempt_item_ids`) is accepted directly by the preparation API. R15/R16/R17/R20 and fresh per-SKUID price-floor evidence remain hard gates; the preparation endpoint does not weaken or fabricate missing platform evidence.
- Focused campaign regression passed 137 tests; the broader campaign scope passed about 205 tests with 2 skipped. Python compilation, OpenAPI import, policy/image fingerprint and diff checks passed. The full backend suite still has 27 unrelated pre-existing failures in cost, inventory, supplier matching and legacy order import; representative failures reproduced on a clean reference worktree.
- The local Docker Desktop failure was recovered without a Windows reboot or factory reset by quarantining only stale zero-byte runtime socket directories and allowing Docker to recreate clean endpoints. Docker Engine 29.5.2 then responded twice and preserved the existing 21 containers and 96 images. This was host-runtime recovery only; no Docker data reset occurred.
- No activity signup, upload, price change, account/login action, withdrawal, business replay or external test notification was performed by task 02. Task 01 may now call the preparation API, but platform upload/submission and official receipts remain separate browser-controlled business steps.

## 2026-08-26 order notice and finance retry recovery

- Incident review: the 2026-08-25 order chain eventually completed at 20:37 after the matching shipping-report password arrived. The 20:00 health check had sent the ambiguous text “暂未进行订单更新” to the order group while the batch was still pending, so the message did not represent the final order result.
- The order group now receives a daily idempotent completion receipt only after a fresh successful order closure with zero new order images: “订单已完成更新，暂无新增需推送下单图”. Stale, failed, offline and password-pending states stay in the alert group and never masquerade as a completed order update.
- The 2026-08-25 20:30 finance run successfully refreshed Wanshifu, Wanshifu balance, Taobao aggregate balance, Wanxiangtai flow, enterprise Alipay balance and three enterprise Alipay bill days. Separate failures were promotion-balance OCR layout drift, a WeChat bill download timeout, one missing enterprise Alipay bill day, and expired personal-Alipay login.
- A personal-Alipay login gate no longer closes the whole finance pipeline. Promotion OCR, WeChat bill, enterprise Alipay gaps and other automatic failures retain their bounded 21:00/21:30/22:00 retries; the pipeline pauses only when every remaining failure truly needs user input.
- Enterprise Alipay daily recovery now retains the exact failed bill date and a bounded non-secret reason. Successful/non-login task runs clear stale scan-queue entries so an old login alert cannot pause a later automatic retry.
- The promotion balance reader supports both the old “万相台无界版·账户总余额” layout and the new “推广业务账户 → 万相台(元)” layout. It still rejects aggregate, deposit, withdrawable, frozen, direct-train and super-recommendation values. A read-only production OCR check on the 2026-08-25 screenshot returned high confidence, an explicit Wanxiangtai label and a numeric value; it did not write or expose the balance.
- ERP code commit/GitHub `main`/production API+Web commit: `c7389c9c2dffe85ce810ee24cb0da2858ae5b396`. Unified NAS verification passed health, ready, API/Web commit parity, migration `0139 (head)`, required routes/features and API/Web/DB/backup containers. Rollback images: `panse-system-api:rollback-20260826-002820` and `panse-system-web:rollback-20260826-003556`.
- Scheduler restarted with 62 jobs; order pull, finance pull, both finance retry shifts and ingest health are enabled. Startup catch-up reported no missed same-day critical shift. The primary-PC Wake Bridge is enabled/running, the legacy Watchdog is disabled, and repeated post-release bridge polls returned HTTP 200 through the NAS.
- Focused ERP regression: 41 passed; Python compilation and diff checks passed. The full backend suite still has 28 previously existing failures in unrelated cost, inventory, pricing, supplier matching and legacy order-import areas. No business replay, bill pull, login, password submission or external test notification was performed by task 02.
- Remaining interaction gate: personal Alipay balance/flow still requires the user to re-login in the ERP automatic-data page. Its last persisted dates remain 2026-08-02 (balance) and 2026-08-01 (flow). The next normal finance shift at 20:30 will provide the first fresh production proof for the repaired automatic sources.

## 2026-08-25 Feishu order/alert group split

- GitHub `main` code commit and deployed production commit: `444613c8d28512037d2fad1ff316b131b404fa18`.
- Added an explicit notification route mode. `feishu_push_chat_id` remains the order group for order images, order delivery results and order-update notices; `feishu_alert_chat_id` is the separate target for automation errors, reports, password/login prompts, QR images, monthly inventory plans and remote-order reminder cards.
- Enabling `feishu_split` is fail-closed unless both order and alert groups are configured. Until then, `legacy` mode keeps the existing Enterprise WeChat webhook active so alerts are not silently lost. The webhook and inbound WeCom callback configuration are preserved as rollback paths.
- Production activation is complete. The existing `畔色系统群` remains the order group (masked suffix `921f5b`), `畔色ERP提醒` is bound as the alert group (masked suffix `732c4c`), and `notify_route_mode=feishu_split`; the targets are distinct.
- A production-code dry-run replaced the Feishu send function only inside an isolated diagnostic process: both `notify()` and `broadcast_text()` selected the alert-group suffix, while the configured order-group suffix remained unchanged. No external test message, order replay, password submission or business write was performed.
- Notification/order/reminder scope passed 30 focused tests; Python compilation and the frontend production build passed. The broader backend run still shows the previously documented unrelated cost, pricing, supplier-matching and legacy order-import failures; no new failure appeared in the changed scope.
- Unified NAS release passed at `444613c`: health, ready, API/Web commit parity, migration `0139 (head)`, required routes/features and API/Web/DB/backup containers. Rollback images: `panse-system-api:rollback-20260825-215223` and `panse-system-web:rollback-20260825-215651`.
- The production scheduler restarted with 62 registered jobs and its delayed startup catch-up check reported no missed critical shift. No order replay, password submission, business write or notification send was used for this change.

## 2026-08-25 Taobao quota timeout self-healing and failure routing

- Incident evidence separates the layers. The 18:09 order run stopped on a single Taobao decryption-quota page `TimeoutError`; a later attempt on the same host reached the Agent and completed all three report downloads, with two reports imported and one new shipping report waiting for its matching password. This proves a transient execution failure plus a program defect that escalated the first timeout too early.
- Web-Agent GitHub `main` commit `65ea203ca65a1b97aeced37ce5a117fa19ecc2e4` adds three bounded quota-page reads and three bounded pre-submit browser-session attempts. DOM content is accepted even when `networkidle` times out. Export remains fail-closed when quota evidence is unavailable.
- A post-submit verification failure never retries the quota write: it records `submitted=true` / `verification_failed=true`, stops report export, and waits for review. This prevents a recovery loop from requesting quota twice.
- ERP GitHub `main` code commit and deployed production commit: `956d944bf271b11d5c1357945726f071f978c2eb`. Timeout, network, offline, rate-limit, login and password gates route to `execution` and retain the existing bounded schedule. Deterministic code/selector/schema errors route to `program_maintenance`, stop blind business retries, and enter a deduplicated, capped, structured maintenance queue that resolves on later success.
- Notification text now shows `处理归属`; the maintenance queue is available through the read-only `list_program_maintenance` service. No Tachikoma control-plane receipt is used because the user did not request one.
- ERP related regression passed 82 tests after excluding the already documented unrelated cross-midnight password-file test; the focused routing suite passed all 19 tests. Web-Agent focused quota/export tests passed 23 tests and its full suite passed 96 tests. Python compilation and diff checks passed in both programs.
- Unified NAS release passed: API/Web commit parity at `956d944`, health, ready, required routes/features, migration `0139 (head)`, and API/Web/DB/backup containers. Rollback images: `panse-system-api:rollback-20260825-195623` and `panse-system-web:rollback-20260825-200201`.
- The deployed API registered 62 scheduler jobs and its startup catch-up check reported no missed critical shift. On the primary Windows PC, `Panse-Web-Agent-Wake-Bridge` is enabled/running, the legacy always-on Watchdog remains disabled, and the Agent remains intentionally on-demand.
- Current business gate is separate from this repair: `ExportOrderList27021942261.xlsx` is waiting for a new matching shipping password. The old password was not retried blindly. No manual order replay, account action, quota write, password submission, or external test message was performed by maintenance task 02.

## 2026-08-25 campaign signup one-shot hardening

- The former `qualify_signup_scope` upload probe is disabled. QianNiu creates a real batch operation when the signup workbook is attached, so the automation now performs local/read-only evidence checks and exactly one final campaign signup submission.
- Every signup row has a required relative shipping time. The policy default is `30天`; exact item overrides remain possible, but blank rows and bare numbers are rejected.
- Campaign I/O now requires the immutable title, `campaignId`, `unitedActivityId`, exact start/end timestamps, and official discount rate. Feedback and post-submit exports are read from that exact identity, not a title-only activity list search.
- Historical no-sales registration is advisory before submission. After the one terminal signup result, only exact items whose sole failure is no-sales receive the no-official-discount single-item fallback. Hard failures and incomplete/stale feedback are isolated and stop without a second campaign submission.
- Completion requires terminal item counts, exact failed-item scope, fresh exact-ID export, and per-SKU signup-price verification. A bounded, non-secret structured receipt records the job ID, terminal counts, feedback artifact, export SHA-256, per-SKU verification, and fallback result.
- Invalid no-sales registry values such as `5`, `待定`, and `暂无` are ignored. The public write-probe entry and Web-Agent `promo_signup` stage route both fail closed.
- The primary Windows host demonstrated a cold-start time beyond the former 150-second bridge limit. The Web-Agent bridge now waits 360 seconds and ERP callers wait 420 seconds, so a slow healthy launch is not falsely marked failed; the Agent remains on-demand and the legacy Watchdog stays disabled.
- Verification for this change: campaign-related ERP suites passed; Web-Agent full suite passed 93 tests with the required async test plugin loaded temporarily. The broader ERP suite still has unrelated pre-existing failures outside campaign files; no live campaign submission, account action, price change, withdrawal, or business retry was used for testing.

## 2026-08-25 Enterprise WeChat group-chat shipping password

- GitHub `main` code commit and deployed production: `89f00c60c5b9cafe672878e88ee5a26f7bb3c4a1`.
- Added the Enterprise WeChat intelligent-bot JSON/AES callback at `/api/wechat/aibot/callback`; the previous self-built-app single-chat callback remains at `/api/wechat/callback`, with separate credentials.
- Group messages are accepted only after protocol signature/AES validation and member UserID allowlisting. Supported forms include `发货密码xxx`, whitespace/colon/equal variants, `密码xxx`, and `口令xxx`. A bare ASCII password is accepted only after the exact configured bot `@name` is removed from an authenticated intelligent-bot callback. Passwords are never echoed or logged; message IDs are deduplicated.
- The callback uses the authenticated one-time WeCom `response_url` to acknowledge receipt in the originating chat, while the existing password -> decrypt -> ingest -> manifest closeout -> pending factory-image delivery chain continues asynchronously.
- Verification: relevant callback/config/notification/order-isolation scope `32 passed`; Python compilation, frontend production build, shell syntax, and diff checks passed. Public HTTPS reached the new callback with the expected HTTP 422 when protocol signature parameters were intentionally omitted.
- Unified NAS release passed: health, ready, API/Web commit parity, migration `0139 (head)`, both callback routes, deployed group-bot admin UI, and all API/Web/DB/backup containers verified. Rollback images: `panse-system-api:rollback-20260825-141943` and `panse-system-web:rollback-20260825-142233`.
- Scheduler restarted with 62 registered jobs; the delayed startup catch-up check reported no missed critical shift.
- Activation is waiting only on the WeCom account gate: production `wechat_aibot_enabled=false`, and bot Token/AESKey/name are unset; the existing allowed-member list remains set. The user must create an internal intelligent bot under WeCom “安全与管理 → 管理工具 → 智能机器人”, configure the public HTTPS callback plus `/api/wechat/aibot/callback`, save the same credentials/name in ERP admin, pass URL verification, and add the bot to the target group. No account action, real password submission, business replay, or external test message was performed.

## 2026-08-25 Enterprise WeChat inbound shipping password

- GitHub `main` code commit and deployed production: `003f4a8ca76911681347b9c7c73b5bcc322cb881`.
- ERP now exposes a WeCom self-built-app callback at `/api/wechat/callback`. It verifies the protocol signature and timestamp, decrypts AES messages, verifies CorpID, enforces an explicit member UserID allowlist, accepts only an explicit `发货密码` command, and persists message IDs for retry deduplication before invoking the existing password -> decrypt -> ingest -> exact manifest closeout -> factory-image delivery recovery chain.
- Callback Token, EncodingAESKey, and all future `taobao_shipping_pwd_latest` values are encrypted at rest and never returned by the admin API. The admin console contains a separate “企业微信接收发货密码” configuration card; the outbound group-robot webhook remains independent.
- Verification: callback/config/security and order-recovery scope `89 passed`; Python compile, frontend production build, shell syntax, and diff checks passed. The unrelated legacy full-suite failures were reproduced individually and were not changed in this repair.
- Unified NAS release passed: health, ready, API/Web commit parity, migration `0139 (head)`, callback route, deployed admin UI, and API/Web/DB/backup containers all verified. Rollback images: `panse-system-api:rollback-20260825-131645` and `panse-system-web:rollback-20260825-132017`.
- Scheduler restarted normally with 62 registered jobs and no missed critical shift.
- Activation remains intentionally closed: production has no `wechat_inbound_*` configuration, and the ERP is currently LAN-only. The code is deployed but direct WeCom message input is not active until the user supplies/configures CorpID, callback Token, EncodingAESKey and allowed member UserID, and explicitly approves a public HTTPS callback route. No NAS exposure, account operation, password submission, business replay, or external test notification was performed.

## 2026-08-25 operational notifications moved to Enterprise WeChat

- Screenshot contract is now explicit in code: Web-Agent automatically requests the current shipping-report password through Taobao's `data-secretquery` endpoint, records only bounded delivery evidence, and never stores a password from the response. `too_frequent` remains `sent=false`; it proves only that a recent request was triggered, not that the user received a password.
- Taobao, not ERP, selects whether the password goes to the seller's bound phone or main account. After the matching password is provided to the existing Feishu ERP bot input, ERP immediately retries decryption, imports the current shipping report, closes the exact order-pull manifest, and resumes pending factory-image delivery. Passwords are not rejected by age; mismatches stay pending without blind retries.
- Operational text, login-expiry notices, and login QR images now go only to the configured Enterprise WeChat webhook. The Feishu factory-order chat remains image-only. Scan recovery instructions point to the ERP automatic-data page instead of asking the user to reply `扫码` in Feishu.
- ERP GitHub `main` code commit: `2d448172adee4e7511ed24d5c398c5fe9efa20b6`. Web-Agent GitHub `main` companion commit: `598993aef82badc04d761f1f6a196149f55860a7`.
- Verification: ERP notification/password/order-recovery suite `85 passed`; Web-Agent full suite `91 passed`; Python compile and frontend production build passed. No real notification, password request, account action, or order pull was used for testing.
- Production configuration was checked without exposing the webhook: `notify_provider=wechat_work` and the webhook is set. ERP API and Web were released together from `2d44817`; health, ready, commit parity, migration `0139 (head)`, required routes/features, and API/Web/DB/backup containers all passed the official NAS release verification. Rollback images: `panse-system-api:rollback-20260825-123500` and `panse-system-web:rollback-20260825-123947`.
- Production scheduler restarted normally with 62 registered jobs and reported no missed critical shift. The Windows on-demand Wake Bridge remains enabled/running, the legacy always-on Watchdog remains disabled, and the bridge continues polling the NAS successfully. No business replay was triggered.

## 2026-08-25 factory dispatch integrity repair

- Incident cause: the automatic ERP -> Feishu factory projection had not completed successfully since 2026-08-15. Two missing wood-cost values blocked the whole table, while the six-hour scheduler wrapper incorrectly recorded the inner factory failure as `ok`.
- Shipment-note cause: the old rule treated seller memo `开始制作` as both production release and shipment approval. A buyer instruction such as `延迟发货 发货前通知` therefore fell through to `做好直接发货`.
- Repair commits on GitHub `main`: `ceaa3a7cfc52adadbba81190556a90506303ca85` (projection/filter/note/scheduler repair), `abc1feb214bff290380fe1e649d1c30487ef10b6` (bounded Feishu delete retry), and `5b05e4dc6e6e2c509ead6d92ab7bf202a2f88514` (prevent delete/recreate loops when a Taobao child ID overlaps an active main-order ID).
- Production API and Web are both deployed from `5b05e4d`; `/api/health`, `/api/ready`, the reverse proxy, migration `0139 (head)`, and API/Web/DB/backup containers passed the official NAS release verification. Rollback images: `panse-system-api:rollback-20260825-014342` and `panse-system-web:rollback-20260825-014705`.
- A pre-correction Feishu backup is stored on NAS at `/volume1/docker/panse/storage/_ops_backups/factory_dispatch_before_integrity_sync_20260825T012526+0800.json`, mode `0600`, 260 records, SHA-256 `2918a7f0288df501b743c83938f8babd142e7a1c71a2a273b2dc46f820180842`.
- Live correction removed 58 ERP-confirmed refunded/cancelled records. Final reconciliation: 221 projected rows, zero missing projected entities, zero duplicate entity keys, zero invalid remote rows. Three non-projected/manual-or-blank rows were preserved because their origin was not safe to infer.
- Factory order 368 now has exactly one live row and `发货安排=做好后等通知发货`. Two consecutive post-release syncs both returned `ok=true`, `created=0`, `updated=0`, `deleted_ineligible=0`.
- Missing wood cost remains a visible warning for 2 orders but no longer blocks status, refund cleanup, or shipment-gate updates. The values were not guessed or written.
- Final changed-scope regression: factory/order-note tests 51 passed; Feishu tests 43 passed and 2 skipped; Python compile and diff checks passed. The full backend suite still contains 27 pre-existing unrelated failures; representative failures were reproduced on the clean pre-repair baseline.
- Scheduler runtime: `feishu_sync_30min` is enabled with no override at 360 minutes. The deployed API registered 62 jobs and completed its startup catch-up check with no missed critical shift.
- Windows on-demand bridge verification passed end to end on the primary PC `192.168.31.91`: NAS requested a start, Web-Agent became HTTP 200 from both NAS and Windows, then an explicit stop restored the intended idle state. `Panse-Web-Agent-Wake-Bridge` remains running; the legacy always-on Watchdog remains disabled.
- Upstream freshness is a separate remaining business gate: the last successful Taobao report timestamp is 2026-08-23 18:27. Web-Agent GitHub `main` and the runtime order orchestrator both contain `97fb6c76f9dc792b376e884c208c62625b79b9ac`, but a fresh Taobao order pull was not triggered from maintenance task 02. That pull/login interaction belongs to task 01 before claiming newly changed marketplace refunds are current.

## 2026-08-24 order-export repair and live runtime audit

- ERP repair branch: `fix/order-export-current-batch`; code commit `ab3edba0e400c1fd0d873573298050ee2fd5f3d5` is present on the same remote branch.
- Web-Agent companion branch: `fix/order-export-resilience`; commit `97fb6c76f9dc792b376e884c208c62625b79b9ac` is present on its remote branch.
- ERP now limits order-only ingest and password checks to artifacts from the active export batch; an empty current-batch artifact list no longer falls back to scanning historical files.
- Web-Agent export detection now tolerates the current Taobao button/dialog variants and both legacy and current export POST endpoints.
- Verification: ERP relevant suite `73 passed`; Web-Agent full suite `90 passed`; Python compile and scoped diff checks passed. One unrelated pre-existing cross-midnight scheduler test remains outside this repair.
- Production is not changed by these branch pushes. Live API and Web remain `eed15b0b4b6e1244721673893e657900c2a003fc`; no merge, deployment, restart, account action, business replay, or external notification was performed.
- The production scheduler has no `scheduler_overrides` row. On 2026-08-24 it fired the order chain at 18:00 and catch-up at 19:17, 20:17, and 21:17; the schedule started normally, while the old production export code failed inside the Taobao trigger stage.
- The intended Windows runtime is on-demand: `Panse-Web-Agent-Wake-Bridge` is enabled and running; the legacy evening Watchdog remains disabled by design and must not be re-enabled as an always-on substitute.
- Fresh NAS checks passed: API `/api/health` and `/api/ready` returned 200, DB migration is `0139 (head)`, API/Web/DB/backup containers are running with zero restarts, SMB read plus create/write/flush/no-residue probe passed, SSH control path is reachable, and production Web-Agent `192.168.31.231:8500/healthz` returned 200.

To make the repair affect the next production order pull, merge both repair branches and perform the separately authorized ERP/Web-Agent release procedure. Branch push alone is not deployment evidence.

## Summary

The logistics bill page now shows product/SKU context and includes a read-only analytics tab for region, weight/volume band, product, product-region, monthly trend, carrier, and high-price anomaly analysis.

The feature is implemented, tested, committed, pushed, deployed to Synology NAS, and verified against production data.

## Current version

- Feature code commit: `c5aeed024bab7ff6b2c88ebba2423c946229e1e6`
- GitHub `origin/main`: contains the feature commit and may contain a later documentation-only archive commit; inspect it live with `git rev-parse origin/main`
- Production API: `c5aeed0`
- Production Web: `c5aeed0`
- Feature commit subject: `feat: add logistics product price analytics`
- Database migration at release verification: `0139 (head)`

Documentation-only commits do not require a container release. Production identity must be read from `/api/version` and `/build-version.json`.

Rollback images created before this release:

- `panse-system-api:rollback-20260813-163601`
- `panse-system-web:rollback-20260813-164144`

## Production evidence snapshot

Verified 2026-08-13 after unified release:

- Analytics endpoint HTTP 200 with real production data
- 183 logistics rows
- Total freight ¥51,405
- 107 order-matched rows
- 94 eligible single-product/single-quantity rows
- 2 multi-product shipments
- 9 same-product multi-quantity shipments
- 78 rows without product context
- Billing-weight coverage 99.5%
- Actual-weight coverage 68.9%
- Volume coverage 90.2%
- 2 anomaly alerts under the current rule

These are a dated snapshot, not constants.

## Completed decisions

- No database migration and no historical write-back for this feature.
- Imported child-order details are authoritative for product context.
- Refunded and service lines do not enter physical product analysis.
- Main order is only a fallback when imported details do not exist at all.
- A single-item average requires exactly one unique physical SKU and quantity 1.
- Multi-product and multi-quantity shipments are shown but excluded from single-item averages.
- Low sample sizes are visibly marked.
- Anomaly detection is advisory only.
- Filters are shared across all analytics outputs.
- Production deployment must release API and Web from one Git commit under the NAS lock.

## Verification status

- Backend related tests: 32 passed
- Frontend TypeScript/Vite production build: passed
- Python compile check: passed
- Scoped diff check: passed
- API and Web unified NAS release: passed
- GitHub remote commit match: passed
- Live analytics API and deployed bundle inspection: passed

## Open work

1. Resolve or classify the 78 rows without product context.
2. Apply non-physical/service filtering to the `Order` fallback without breaking real custom-product links.
3. Replace long/repeated marketplace titles with canonical ERP product names while keeping traceability.
4. Audit suspicious sample-block shipments with implausible weight/volume/freight.
5. Normalize full autonomous-region display names.
6. Add like-for-like trend analysis that controls province, weight/volume band, and carrier if the business needs price-change conclusions.
7. Consider export of the current filtered analytics with methodology and generated timestamp.
8. Revisit query aggregation/caching only when history grows enough to create measured latency.

## Current dirty-work boundary

At this update, the primary working tree also contains unrelated Tachikoma connection/AEO work and scratch artifacts. They are not part of the logistics feature and must not be staged, overwritten, deleted, or deployed accidentally. Always re-run `git status --short` because this list can change.

## Where to continue

Start with `docs/logistics-bill-product-analytics.md`. For the next engineering task, the safest first target is the 78 unmatched rows plus the service-link leakage test, because both directly affect analysis credibility.
## 2026-09-04 plan-8 V8 editor identity continuation V15

- Production V14 stopped before file selection with
  `plan8_v6_bound_draft_editor_identity_mismatch`; the Web-Agent claim remained
  byte-for-byte at the frozen V13 SHA-256 and no platform write was observed.
- V15 accepts only the exact frozen ERP V14 result/inspection/commit hashes and
  the unchanged V13 Web-Agent claim. It cannot change plan/campaign identity,
  scope, SKU prices or retry an ambiguous write.
- The only formal continuation command is
  `D:\AI\畔色ERP系统\ERP程序\scripts\campaign_recover_plan8_final_v8_preupload_v15_nas.ps1`.
  V14 is retired.

## 2026-09-04 plan-8 V8 nested modal continuation V16

- V15 ended at `draft_patch_terminal` with `platform_write=false`; the frozen
  Web-Agent claim SHA-256 is
  `fe35182f8740a64482707f68ceb307f7bd34c86caa3114612291300e7a817d91`.
- V16 accepts only the exact V15 ERP result, inspection and commit hashes plus
  that claim, then performs one new inspect/CAS/commit attempt. All campaign,
  plan, signRecordId, SKU, price and no-retry boundaries remain unchanged.
- The only formal continuation command is
  `D:\AI\畔色ERP系统\ERP程序\scripts\campaign_recover_plan8_final_v8_preupload_v16_nas.ps1`.
  V15 is retired.

## 2026-09-04 plan-8 V8 下载模版 continuation V17

- V16 stopped before file selection because the live platform label is
  `下载模版`; its exact frozen claim SHA-256 is
  `babe9e91701a4a35d054aa950a05309dfc3b83a62430f35639a362505a08b17d`.
- V17 accepts only exact V16 ERP hashes and that claim. All identity, scope,
  price, one-shot and no-retry gates remain unchanged.
- Formal command:
  `D:\AI\畔色ERP系统\ERP程序\scripts\campaign_recover_plan8_final_v8_preupload_v17_nas.ps1`.
  V16 is retired.

## 2026-09-04 plan-8 V8 detached import-result continuation V18

- V17 selected the exact file, but QianNiu replaced the import modal and the
  detached locator produced an unknown outcome. No continuation was allowed
  until an independent official readback completed.
- The official readback proved that the eight intended SKU rows were still
  missing, no unexpected SKU or discount row existed, the protected records
  and all record IDs were unchanged, and the legacy 53-row discount snapshot
  was unchanged. V18 accepts only that exact ERP state and the frozen V17
  Web-Agent claim SHA-256
  `77972047e50b33f9e03ab08926c0536e6e06ee64e2d2a912a9d2b86f88c5082e`.
- V18 snapshots every visible pre-upload terminal context so the historical
  six-item success row cannot be mistaken for the new task. It observes the
  live replacement modal or a genuinely new page-level terminal result, then
  keeps the existing exact 78-row official readback gate.
- The only formal continuation command is
  `D:\AI\畔色ERP系统\ERP程序\scripts\campaign_recover_plan8_final_v8_preupload_v18_nas.ps1`.
  V17 is retired and must not be rerun.

## 2026-09-04 plan-8 V8 live official-template continuation V19

- V18 reached QianNiu's batch processor, which rejected the hand-built
  three-column workbook with `导入模版有误！第一行应包含【基础信息】`.
  An independent official readback (`request_id=b0431bd5a4ff`, job4) proved
  that all eight intended SKUs were still missing, no new discount existed,
  and every protected/legacy record was unchanged.
- V19 accepts only the exact V18 claim SHA-256
  `46bb20b863686ffc992a32f1f66a8fa5bbeaa27a49c7c2e1477a5899713dde7e`
  plus official readback artifact SHA-256
  `3c44dc294b8dc8b098d09e944c942a6bfea27814df1b78f2b90fc7d8998cd010`.
- The Web-Agent now downloads the template from the exact live campaign
  dialog before selecting any upload file, requires the `模版说明` and
  `商品SKU导入列表` sheets plus the official `基础信息`/column headers, clears
  only data rows from row 4 onward, then writes the frozen 78-row scope.
- The only formal continuation command is
  `D:\AI\畔色ERP系统\ERP程序\scripts\campaign_recover_plan8_final_v8_preupload_v19_nas.ps1`.
  V18 is retired and must not be rerun.

## 2026-09-04 plan-8 V8 template-generation continuation V20

- V19 opened QianNiu's exact `下载模版` configuration modal and then
  timed out while waiting for an immediate download. The frozen V19 claim
  SHA-256 is
  `4f4b6fbd4878985302ad6b25bcf3708f5c773c3ea69a1e715cb43c99a5b35740`;
  it records `submitted=false`, so no upload, publish or discount write
  started.
- V20 accepts only the exact V19 ERP summary/inspection/commit fingerprints
  and that frozen claim. It selects only `空模板（仅包含表头）` in the exact
  template modal and clicks the unique enabled `生成文件` action before
  validating and populating the live official workbook.
- The only formal continuation command is
  `D:\AI\畔色ERP系统\ERP程序\scripts\campaign_recover_plan8_final_v8_preupload_v20_nas.ps1`.
  V19 is retired and must not be rerun.
# 2026-09-04 Plan 8 V21 claim-step repair

- V20 stopped before any Taobao read or write because the ERP stored the V20 reservation under the default preupload step number. The claim verifier therefore rejected the otherwise exact V19 source claim.
- V21 accepts only the frozen production V20 failure (`attempt_id=edaf6b609dad46fbab90c7e8`, `platform_write_observed=false`, exact summary/inspection/commit/resume hashes), writes the correct `platform_write_claim_claimed_preupload_resume_v21` step, and uses a dedicated Web-Agent V21 endpoint.
- The fixed scope, prices, item/SKU set, activity identity, no-retry boundary, and mandatory official readback remain unchanged. V1-V20 are retired and must not be rerun.
- Operator entry after production deployment: `scripts/campaign_recover_plan8_final_v8_preupload_v21_nas.ps1` (one execution only).
## 2026-09-04 plan-8 manual-export field forwarding V26

- V25 stopped before Web-Agent/platform work because the API route failed to
  forward the four already validated manual-export fields into the V8 service.
  V25 is permanently retired; its zero-write result did not change the frozen
  V24 attempt.
- V26 fixes that exact route binding, revalidates the same fixed filename,
  size, SHA-256 and 83-row scope, and uses a distinct CLI, confirmation and
  Web-Agent route. The existing Taobao-profile busy gate and bounded wait still
  prevent overlap with order pulling; all identity, price, CAS, one-shot,
  terminal and readback gates remain unchanged.
