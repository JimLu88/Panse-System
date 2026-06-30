# 新品开发(NPD)板块 — 执行 Plan(v2)

> 决策(2026-06-29 定框架 / 2026-06-30 修订 v2):**混合方案(轻量看板骨架 + 成本门G3 + 时间线SLA两道硬卡 + 木作款叠 NPI 验收门 + AI Copilot 增强)+ 吸收苹果 NPI 内核**;**先做新品板块**(物流对账随后);**全量落地**。
> 落点:产品 Tab 下新建 `/npd`。原则:**抄现成范式 + 复用畔色现有 service,不重造轮子**。
>
> **v2 四项修订(用户 2026-06-30)**:
> 1. **量产/小批试产阶段默认关闭**(现状=样品做完→客户买→直接发货,按单生产无量产);受"生产线"开关 `npd_mass_production_enabled` 控制,未来量大再打开。
> 2. **打样验收清单细化 + 强制过门**:外观、尺寸必须做成逐项勾选/实测项,全部必检项完成才能进下一步。
> 3. **系统联动自动建档**:新品设计落地后自动建 Product+BOM,自动算价生成定价表(="定位表",同一张),新配件自动入库(价取询价选定供应商报价)→ 全自动。
> 4. **成本门聚焦"打样成本上浮"真因**:巨亏发生在打样工艺改进(供应商承诺OK但打样出状况→改工艺→成本上浮)。对策不是加价(下策),而是 多供应商对齐工艺 + 排查问题点 + 后备供应商低价解决工艺问题。

---

## 0. 吸收苹果 NPI 的 5 条内核(及落点)

1. **打样轮次编号化 EVT/DVT(/PVT)** → S12 工程样=EVT(结构/五金/工艺可行)、S15 确认样=DVT(外观定稿+送检);PVT 小批试产属量产组,**默认关闭**(见 §1)。
2. **Build to data(用实测数据放行,非评审拍脑袋)** ★核心 → 验收逐项填实测读数/勾选自动判过/不过,必检项不全不能过门(见 §4)。
3. **设计冻结纪律** → G2/G4 门;冻结后改单留痕(谁/为何/成本影响),客户改单可收费。
4. **认证卡确定节点** → G4 安规门统一送检(甲醛/承重/力学),报告齐才过。
5. **量产就绪门**(未来开生产线时启用) → PVT + 成本门合并成"敢不敢放量"。
> 不吸收:苹果 12–18 月周期、数百~数千台原型、重资产开模、强保密、大团队(小团队过重)。量级裁到家具小团队(打样 1–3 件,靠测湿仪/色卡/检测报告这类轻量数据放行)。

---

## 1. 最终阶段/门模型(默认 6 组 + 5 门;量产组默认关闭)

```
【1 立项 PLAN】✚立项申请 · ✚市场调研+竞品          ══G1 立项门: 价位靶/成本上限钉死══
【2 设计 DESIGN】概念策划(给边界非死稿)→初版→修改/改策划→再设计→工厂工程讨论(✚寻源前置≥2家)→修改设计→设计落地(★自动建产品档案,见§5)
                                                  ══G2 设计冻结门: 工程可行+BOM齐+≥2供应商+设计冻结══
【3 寻源 SOURCING】供应商询价(AI给话术)→配件采购     ══G3 成本预算门(★硬Kill): 工艺改进后量产成本 vs 价位靶, 见§6══
【4 打样 PROTOTYPE】✚EVT工程样(验结构/工艺,樱桃木先防护)→白胚验收(细化)→修改变动/复验→✚DVT确认样(外观定稿+送检)
                                                  ══G4 确认样+安规门: 外观/尺寸逐项达标+检测全过+设计冻结══
                    →整体安装验收(装好再打包)→包装设计+运输破损测试
【5 量产 PRODUCTION】【默认关闭 · 受 npd_mass_production_enabled 开关】 PVT小批试产(良率/一致性/色差)→量产
【6 上架 LAUNCH】评价图拍摄→详情页摄影→详情页制作→重新入库✚+定价录入✚+淘宝上架✚   ══G5 上市放行门: 图齐+详情齐+定价+库存就绪══
【7 复盘 REVIEW】✚上市后复盘(实际毛利 vs 预算/退货/差评→反哺选品)
```

- **量产组默认关闭**:`npd_stage.requires_mass_production=true` 的阶段(PVT小批试产、量产),当 `system_settings.npd_mass_production_enabled` 为 false 时**不实例化、看板不显示**;打样组验收+包装做完后直接进上架。未来在设置里打开开关 → 新项目自动带回量产组(并提示是否给在途项目补)。
- **三道硬门治三痛**:G1 立项预算门 + G3 量产成本门 → 治巨亏;寻源前置≥2家 → 治寻源慢+工艺对齐;飞书阶段 SLA → 治延误。

---

## 2. 每阶段待办模板(进阶段自动 instantiate,带相对截止/是否必做影响过门)

(★=必做影响过门;📷=摄影;🏭=工厂;⏸=量产开启才有)
- S01 立项:★机会陈述 · ★目标价位+毛利率(写成本门基线) · 战略契合自评
- S02 调研:★竞品3-5款参数/价 · 客群+场景 · 销量预估
- S03 概念策划:★定设计边界(尺寸/主辅材/价位/品牌语言) · AI检索同类案例/材质
- S04-S06 设计:★效果图 · 上传图库 · 评审意见(Timeline评论)
- S07 工程讨论:★🏭工艺难点+可行性 · ★寻源登记≥2家候选 · AI给工艺方法+设计边界
- S08-S09 落地:★设计冻结 · ★生成产品档案(自动 product_composer:产品+BOM+定价+新配件,见§5) · ★出BOM+线框尺寸图
- S10 询价:★🏭对每家候选发询价(AI话术) · ★收齐报价并选定(配件价由此入库)
- S11 采购:★🏭下采购单(PartPurchase) · 🏭跟进到货
- G3 成本门:★算工艺改进后量产成本(PricingSkuCosts 22部件) · ★对价位靶红绿灯(上浮→§6)
- S12 EVT:★🏭首件生产 · ★🏭樱桃木等中途先防护 · 🏭配件到场对照BOM · ★🏭记录打样工艺问题点(见§6)
- S13 白胚验收:★🏭逐项检查(见 §4 细化清单,必检项全过才能下一步)
- S14 返工/复验:🏭返工项记录+复验
- S15 DVT确认样:★外观/尺寸逐项验收(见§4) · ★送检甲醛/承重/力学(传报告) · 📷道具采买计划
- S17 整体安装验收:★🏭全部安装后再打包(见 §4)
- S18 包装:★包装方案 · ★运输破损测试
- ⏸S16 PVT试产:🏭产线一致性/良率/色差(达门槛) · ⏸S19 量产:🏭排期确认
- S20 评价图:📷★道具采买 · 📷★样品搬运 · 📷★拍摄 · 📷策划脚本
- S21 详情页摄影:📷★摄影 · 📷分镜策划
- S22 详情页制作:★排版 · 文案(AI辅助)
- S23 上架:★定价录入 · ★淘宝上架 · ★库存就绪
- S24 复盘:★实际vs估算成本 · ★销量/退货/口碑→反哺选品

---

## 3. 飞书阶段 SLA 默认天数表(进阶段日 + N 天;设置窗口可调)

| 阶段 | 天 | 阶段 | 天 |
|---|---|---|---|
| S01立项 | 2 | S13白胚验收 | 3 |
| S02调研竞品 | 3 | S14返工/验收 | 5 |
| S03概念策划 | 3 | S15确认样DVT | 7(+7检测) |
| S04初版 | 5 | S17安装验收 | 3 |
| S05改 | 3 | S18包装+运输测试 | 5 |
| S06再设计 | 3 | ⏸S16试产PVT | 10 |
| S07工程讨论 | 3 | ⏸S19量产 | 15 |
| S08改设计 | 3 | S20评价图 | 5 |
| S09落地 | 3 | S21详情页摄影 | 3 |
| G3成本门 | 2 | S22详情页制作 | 5 |
| S10询价 | 5 | S23入库+定价+上架 | 3 |
| S11采购 | =Material.lead_time_days | S24复盘 | 7(上市后) |

分级:days_left≤2→critical(自动飞书推群,12h冷却);≤5→warn(并入 daily_10 综合日报);卡某阶段超 SLA×1.5→升 critical+@负责人。

---

## 4. 工厂跟进 + 验收清单(★v2 细化 + 强制过门)

做成"模板→逐项勾选/填实测读数→自动判过/不过";**所有 is_required 项全部完成(通过)才能点"完成本阶段/过门",任一不通过→项目转 rework,禁止进下一步**(对应用户"完成以后才能进行下一步")。挂 S13/S15/S17。

### 4.1 外观验收(逐项勾选,每项 通过/不通过 + 备注/拍照)
- ★表面无划痕/磕碰  · ★无气泡/流挂/橘皮(漆面)  · ★封边平整无翘起/无开胶
- ★颜色对标准色卡无明显色差  · ★木纹/拼缝对称美观、拼缝均匀  · ★五金外露件无瑕疵/无锈
- 无补土痕/砂痕  · ★整体观感(对称/比例/手感)合格

### 4.2 尺寸验收(逐项填实测值 vs 设计值 + 公差,自动判过)
- ★长(mm)±公差 · ★宽(mm)±公差 · ★高(mm)±公差 · ★对角线差(验方正)≤公差
- ★板厚(mm) · 脚高/离地(mm) · ★五金孔距/孔位(mm) · 抽屉/门缝隙均匀度(mm)

### 4.3 樱桃木/易变色木材专项
★白胚阶段就上底油/封闭防氧化(不可裸放等后续,否则不可逆局部色差) · ★含水率实测(测湿仪,匹配销售地平衡含水率,北方8-12%) · 同批取材+留标准色块 · 避光养护+静置养护期 · 车间湿度40-60% · 详情页主动说明"樱桃木会变色"

### 4.4 异材质 + 安装打包
岩板(易碎,查暗裂/崩边,分件+厚护角+立放运输) · 玻璃(钢化3C/磨边倒角) · 五金(承重等级/开合寿命/防锈,异形件双源) · 贴皮(诚实标注,封边无翘起/气泡) · ★全部安装后再打包(严禁拆件分装) · 试装后拍记录+标配件+附五金包清单+安装说明

---

## 5. 系统联动:新品完成自动建档(★v2 全自动)

**触发**:S09 设计落地完成 / 过 G2 时,自动调用**扩展版 product_composer**(`api/product_composer.py` 现为「产品+BOM+定价 一事务」,扩展点见下):

1. **自动建产品**:Product(编码走 `product_coder.next_product_code`)。
2. **自动建 BOM**:按设计的物料清单建 BomLine。
3. **新配件自动入库**:现 product_composer 对缺失物料**报错拒绝**;改为 `auto_create_material=true` 时自动建 Material —— **价格取该配件在 S10 询价中选定供应商的报价**(用户拍板),`category` 按配件类型,关联选定供应商。**所以询价/选定要在自动建档前完成**(链路:S10 选定报价 → 配件入库带价 → BOM → 定价)。
4. **自动算价生成定价表**:按 22 部件成本口径(`PricingSkuCosts`+`formula_engine`)算单位成本,× (1+target_margin) → 写 PricingSku(="定位表",同一张定价表,用户确认)。
5. 回写:把生成的 `product_code` 绑回 `npd_project.product_code`。

> 扩展点:① product_composer 加 `auto_create_material` 选项(缺料→按供应商报价建档,而非报错);② 加"按 target_margin 反算定价"的服务函数(`pricing` 模块已有成本口径,补一个"成本→建议定价")。失败回滚(保持现有事务语义),并给清晰报错(哪个配件缺价等)。

---

## 6. 成本门 + 打样工艺问题追踪(★v2 治"打样成本上浮"真因)

**真因(用户复盘)**:巨亏不在量产,而在**打样环节**——供应商口头承诺没问题,但打样时出各种状况,改进工艺后成本上浮。

**对策(系统化)**:
- **不靠加售价**(明确标为下策,系统不推荐这条路)。
- **工艺问题点台账** `npd_craft_issue`:打样(EVT/白胚/DVT)中每发现一个工艺问题就登记(描述/发现阶段/根因/`cost_impact` 成本影响/状态 open→solved)。
- **多供应商对齐工艺 + 排查**:每个工艺问题 → 向多家供应商(含后备)征集解决方案+报价,比对谁能解决且更便宜(**后备供应商可能以更低价解决工艺问题**——用户洞察)。方案挂 `npd_supplier_candidate`(加 `can_solve_craft_issue`/`craft_solution`/`solved_cost` 字段)。
- **成本门 G3 用"工艺改进后的真实成本"**:不是打样初始成本,而是把所有 open 工艺问题的 cost_impact 纳入后的成本,对 S01 价位靶判红绿灯。
- **成本上浮联动**:G3 算出成本上浮超阈值(如 >价位靶对应成本的 X%)→ 系统**不建议加价**,而是建议「扩大供应商池/启用后备供应商排查工艺问题」,并把该项打回 S07/S10 重新对齐工艺。
- **寻源前置双源**现在双重作用:① 供货保障 ② 工艺对齐与排查(出问题有第二家比对/接手)。

---

## 7. AI 知识库 + 护栏

- 检索(MVP不建向量库):SQL LIKE(`fuzzy_search`)+`AiKnowledge`归一化缓存,检索 Product大文本/Material/BomLine/CATEGORY_PROFILE → 喂 `ai_assistant.chat(extra_system=...)` 给材质/工艺/设计边界/询价话术;沉淀 `npd_knowledge_note`(仿 AiKnowledge context_hash 归一)。增强(后续)建 embedding。
- **护栏(红线)**:AI建议标注"来自本库数据"vs"AI通用推断";AI不可用降级只给确定性检索;成本门/安规门过不过由确定性计算+人确认;**AI 绝不编造 检测数(含水率/甲醛/承重/钢化)/供应商报价/材质合规事实;绝不自动下采购单/定价上架/验收签字**——测出来的数、报出来的价、对外承诺的事实、花钱放行的动作,真值必来自现实、终须人拍板。

---

## 8. 数据模型(`models/npd.py` + schemas + api + service + alembic)

- `npd_stage`(阶段定义,可配):code/name/group/sequence/color/is_gate/is_default/is_closed/is_final/done_ratio/allow_release/**requires_mass_production**(量产组标记)/default_sla_days/warn_days/critical_days
- `npd_project`:code/name/category/brand/product_line(泳道)/current_stage_id/state(draft/active/rework/done/cancelled)/kanban_state/owner_id/priority/target_launch_date/percent_done/**target_price/target_margin_rate**(成本门基线)/product_code(落地后绑Product)
- `npd_stage_instance`:project_id/stage_id/status/entered_at/deadline/completed_at/alert_level/alert_reason/gate_result/gate_decided_by/gate_comment
- `npd_stage_task_template` / `npd_task`(模板→实例,offset_days/is_required/category/assignee_role)
- `npd_supplier_candidate`:project_id/supplier_id/material_category/is_backup/quote_amount/quote_status/lead_time_days/score_snapshot/**can_solve_craft_issue/craft_solution/solved_cost**(v2)
- `npd_cost_gate`:prototype_cost/est_mass_cost/target_price/target_margin/actual_margin/verdict/decided_by/cost_breakdown_json
- **`npd_craft_issue`(v2)**:project_id/stage_code/title/desc/root_cause/cost_impact/status(open/solved)/chosen_supplier_id/created_at
- `npd_inspection_template` / `npd_inspection`(借 ERPNext:params_json 检验项[外观逐项/尺寸数值+公差/材质专项] / readings_json 读数 → result accepted/rejected;**必检项不全→stage 不能过门**)
- `npd_knowledge_note`:category/material/tags/title/body/context_hash/source_project_id/usage_count
- `npd_stage_history`(过门审计)
- 设置(`system_settings`):`npd_stage_config`(JSON,每阶段 SLA/审批人/提醒,仿 scheduler_overrides)、**`npd_mass_production_enabled`(默认 false)**、`npd_min_supplier_candidates`(默认 2)、`npd_cost_overrun_threshold`(成本上浮告警阈值)

---

## 9. UI(产品 Tab 下 `/npd`,顶部 Segmented/Tabs)

- 看板 `?tab=board`(套 `OrdersKanbanPage` dnd-kit,列=阶段组,拖拽改 current_stage_id,门未过/必检项未全→禁流转;量产组按开关显隐)
- 清单 `?tab=list`(`PresetTable tableKey="npd"`)
- 单品详情 `/npd/:id`(左:阶段Timeline+任务Checkbox+评论;右:成本门红绿灯/供应商候选+工艺问题/物料备料/图库GalleryModal/AI助手/验收单)
- 创意池 `?tab=ideas` · 知识库 `?tab=knowledge` · 设置 `?tab=settings`(按阶段调SLA/审批人/提醒 + **生产线开关** + 后备供应商最少家数 + 成本上浮阈值 + 调度时间)
- 移动端:看板降级 Segmented+整宽 StatusCard;清单走 GenericTableCard

挂载:`App.tsx` 4处。

---

## 10. 落地分期(全量,分批可部署)

- **P0 骨架**:`npd_stage`+`npd_project`+`npd_stage_instance` 三表+迁移+seed(阶段5门,量产组标 requires_mass_production)+ 设置项 npd_mass_production_enabled/min_supplier_candidates;`/npd` 挂页+清单(PresetTable)+看板(dnd-kit,量产组按开关隐藏)+立项Modal。验证:能立项/拖拽流转/桌面手机可看/量产组默认不显示。
- **P1 核心**:任务模板→实例(复用 accessory_checklist 幂等)+单品详情(OpsChecklist勾选+Timeline)+**验收模板库(外观/尺寸逐项细化+必检项全过才过门)**[v2 提前到P1]+飞书截止提醒(register_job 每日扫+alert_service npd_stage_due)+**成本门G3**(npd_cost_gate+量产成本红绿灯,不过拦流转)+**工艺问题台账 npd_craft_issue + 多供应商对齐**+供应商候选≥N校验+图库。
- **P2 系统联动+增强**:**设计落地自动建档**(扩展 product_composer:auto_create_material 按询价报价建配件 + 成本→定价自动算价 + 回写product_code)[v2]+AI知识库(ai_assistant.chat+npd_knowledge_note+护栏)+飞书入站标记完成(send_card+op-dispatch)+设置窗口(生产线开关/SLA/阈值)+门控审批。
- **P3 长尾**:量产组启用流程(开关打开补量产阶段)+语义检索embedding+复盘自动拉销量/退货/实际成本对比估算反哺选品+飞书多维表双向同步。

---

## 11. 复用映射(每能力对应现成入口,绝对路径)

| 能力 | 入口 |
|---|---|
| 挂页/路由/菜单 | `frontend/src/App.tsx`(4处) |
| 清单+导出+手机卡 | `frontend/src/components/PresetTable.tsx` |
| 看板dnd-kit | `frontend/src/pages/OrdersKanbanPage.tsx` |
| 任务清单勾选 | `OpsChecklistPage.tsx` + `services/ops_checklist_service.py` |
| 阶段时间线/评论 | `components/OrderTimelineDrawer.tsx` |
| 逐项清单生成/对齐/预警 | `services/accessory_checklist_service.py` |
| 自动建产品+BOM+定价(扩展auto建料) | `api/product_composer.py`(compose_product) |
| 调度/SLA覆盖 | `services/scheduler.py`(register_job/set_schedule) |
| 阶段截止告警 | `services/alert_service.py`(upsert) |
| 飞书推送/卡片/入站 | `services/feishu_client.py` + `feishu_bot_service.py` |
| AI工艺/设计建议 | `services/ai_assistant.py`(chat+extra_system) |
| 成本/定价口径 | `models/pricing_ext.py`(22部件)+`formula_engine_service.py` |
| 供应商评分/后备 | `services/supplier_score_service.py` + `Material.alt_supplier_ids` |
| 设计规格知识源 | `services/custom_board_template.py`(CATEGORY_PROFILE) |
| 图库 | `components/GalleryModal.tsx` + `api/gallery.py` |

---

## 附:物流对账(本期之后再做,设计已就绪)
运费估算(国标6位区划码 区→市→省回退+MAD稳健剔除+置信%/回测准确率)+双重估算(历史EST_A/定价表EST_B)三道闸异常判级+寄出/寄回双基线+附加费/幽灵附加费检测+索赔抵扣(导入口)+可审计openpyxl导出(公式引用参数页)+月度对账(照 PartsMonthlyRecon)+freight_anomaly规则配 recheck/autoclose 自愈。落点 `LogisticsBillsPage` 加 Tab。
