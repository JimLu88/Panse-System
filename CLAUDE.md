# CLAUDE.md — 畔色孚格 ERP 项目记忆

> 家具电商内部 ERP（用户主力项目）。本文件是长期架构/约定记忆，每次会话自动加载。
> 某次具体会话的提交清单与日期相关细节见 `docs/会话交接-*.md`。

## 1. 项目概览

- **定位**：家具电商内部 ERP — 产品/物料/BOM、定价、库存、订单、对账、营销/售后、供应商对账 OCR、AI 辅助 全流程。
- **技术栈**
  - 后端：FastAPI + SQLAlchemy 2.0（`Mapped`/`mapped_column` 风格）+ Alembic + PostgreSQL 16（测试用 SQLite 内存库）。
  - 前端：Vite + React 18 + TypeScript + Ant Design 5 + TanStack Query + react-router。页面全部 `lazy` 加载。
  - 部署：Docker Compose 四服务 `db / api / web / backup`。api 启动 `alembic upgrade head && uvicorn`（**无 `--reload`**，单进程，看门狗 SIGTERM → Docker `unless-stopped` 自动拉起）。backup 每天 03:00 `pg_dump | gzip`，保留 30 天。
  - Windows 托盘部署：`deploy/windows/`（`panse_tray.py` + `*.bat`）。
- **迁移最新 head = `0127`**（活动生命周期；历史迁移只增不改，运行时用 `alembic current` 与 `alembic heads` 动态核对）。
- **Git / 生产发布**：`origin/main` 是唯一源码事实源；群晖部署目录没有 `.git`，只运行构建镜像。正式发布必须用 `scripts/deploy_release_nas.sh` 从同一 `origin/main` 提交构建 API/Web，并用 `scripts/verify_release_nas.sh` 验收。**提交信息不带 `Co-Authored-By`**。
- **认证**：JWT HS256；角色 `admin / operator / viewer`；默认 `admin/admin`，启动会把弱密码账号标记 `must_change_password` 强制改密。AI/OCR/物流 key 加密存 `system_settings`（后台可改，无需重启）。

## 2. 本地开发 & 环境坑（重要）

- **改后端路由**：`docker compose restart api`（bind-mount 但无热重载）。跑诊断/单测可 `docker compose exec api python ...`（新进程读最新代码）。
- **前端类型检查**：web 容器仅 512MB，`tsc` 会 OOM。用 2GB 临时容器复用镜像 node_modules：
  ```bash
  MSYS_NO_PATHCONV=1 docker run --rm -m 2g -w /app \
    -v D:/AI/畔色ERP系统/ERP程序/frontend:/app -v /app/node_modules \
    panse-system-web npx tsc --noEmit
  ```
- **Git-Bash MSYS 路径改写**：`docker compose exec`/`docker run` 里的绝对路径会被改成 Windows 路径 → 前缀 `MSYS_NO_PATHCONV=1`。
- **PowerShell here-string** 会把代码里中文字面量变 `????` → 含中文的脚本用文件写入 + `docker compose cp` 进容器再 exec，别走 stdin 管道。
- 后端测试：`docker compose exec api python -m pytest tests/ -q`（当前约 287 个测试文件；数量随功能增长，不在文档写死用例数）。
- 不提交的未跟踪文件：`backups/`、`storage/`、`docker-compose.override.yml`、`deploy/windows/build|*.spec`。

## 3. 代码结构

```
backend/app/
  api/        FastAPI 路由（main.py 里逐个 include_router）
  models/     SQLAlchemy ORM（当前约 94 个 __tablename__，models/__init__.py 汇总）
  services/   业务逻辑（当前约 198 个 service 文件，逻辑都在这层）
  schemas/    Pydantic I/O
  alembic/    迁移（当前 head=0127）
  main.py     应用装配：日志(北京时间)/中间件/异常处理/限速/lifespan(看门狗+调度器)/路由
frontend/src/
  pages/      每个一级菜单页一个文件（当前约 80 个）
  api/        后端调用封装（client.ts + 按域拆分 orders.ts/finance.ts/...）
  components/  CommandPalette / AiAssistantWidget / NotificationBell / VersionTag ...
  App.tsx     菜单(11 组) + 路由表 + 角色控制
```

## 4. 导航与功能地图（11 个一级菜单组）

| 菜单组 | 主要页面（路由） |
| --- | --- |
| **数据分析** | 待办台账 `/ops-checklist`、大盘 `/dashboard`、销售排行榜 `/sales-ranking`、销售预测 `/forecast` |
| **产品** | 产品总表 `/products`、新产品录入 `/new-product`、淘宝对应表 `/taobao-listings` |
| **价格** | 定价表 `/pricing`、竞品价库/报价参数 `/customization`、（公式 `/pricing-formulas`） |
| **库存** | 配件库存 `/inventory`、成品库存 `/product-inventory`、可生产数 `/producibility` |
| **订单** | 订单 `/orders`、看板 `/orders/kanban`、客户 `/customers`、退货售后 `/aftersales` |
| **物流** | 物流账单 `/logistics-bills`、万师傅对账单 `/wanshifu-bills` |
| **营销** | 推广/品牌/样品/日常经营/人员外包（`/marketing?tab=`） |
| **供应链** | 供应商 `/suppliers`、评分 `/supplier-scores`、采购 `/purchases`、物料单价库 `/materials`、木材损耗 |
| **财务** | 剩余流水 `/cash-flow`、支付宝/余额 `/alipay`、对账 `/reconciliation`、**结算对账 `/settlements`**、补单记录、会计期间、资产 |
| **分析** | 报表 `/reports`、异常 `/exceptions` |
| **工具** | Excel导入 `/importer`、全列数据浏览 `/data-explorer`、飞书 `/feishu`、管理/运维(admin) |
| 顶栏按钮 | 截图录单 `/screenshots`、定制报价 `/custom-quote`、全局搜索 Ctrl+K |

## 5. 数据模型总览（当前约 94 张 ORM 表，按域分组）

- **产品/物料/BOM**：`products`、`materials`、`bom_lines`、`pricing_sku` + `pricing_sku_costs` + `pricing_sku_promo`、`pricing_formula_rules`、`price_change_logs`、`competitor_prices`、`custom_variants`、`taobao_listings`。
- **库存**：`part_inventory`、`product_inventory`、`inventory_lock_ledger`（append-only 台账）。
- **订单**：`orders`（P&L 全字段）、`factory_orders`、`part_purchases` + `purchase_files`、`order_details`、`order_accessory_items`、`order_events`、`shipments`。
- **财务/对账**：`alipay_flows`、`order_settlements`(0058)、`account_balances`、`refill_records`、`wanshifu_bills`、`logistics_bills`、`factory_reconciliations`、`accounting_periods`。
- **营销/售后**：`after_sales`、`promotion_flows`、`brand_marketing`、`samples`、`wood_losses`、`outsourcing_expenses`。
- **供应商**：`suppliers`、`delivery_notes` + `delivery_note_lines` + `delivery_files`、`supplier_scores`。
- **客户/分析**：`customers`、`sales_daily_rollup`、`daily_briefing`、`data_exceptions`、`approval_requests`、`alerts`。
- **集成/系统**：`feishu_sync_map` + `feishu_table_bindings`、`ai_chat_logs` + `ai_code_patches` + `ai_knowledge`、`import_jobs`、`scheduled_job_runs`、`system_settings`、`system_events`、`system_health_logs`、`users` + `audit_logs`。

## 6. 核心子系统

### 6.1 产品 / 物料 / BOM 编码与成本
- **产品编码** `product_coder.py`：`P<品牌><年><品类><流水><MMDD>`，品牌码 `PS`(畔色)/`FG`(孚格)。同一实物跨品牌共享数字核心 → `PPS{X}`/`PFG{X}`/`P{X}`（订单表用 `P{X}`）。`brand_variants()` 用于跨品牌聚合。
- **物料编码** `material_coder.py`：`<前缀>-<3+位流水>`。前缀：`AC`配件 / `MP`人工费 / `MW`木材 / `SP`特殊件。流水 ≥1000 为定制（`is_custom`）。`MW-*/MP-*` 物料 `is_factory_provided=True`（工厂提供，免采购）；`AC-*/SP-*` 需采购。
- **BOM 成本** `order_cost_service.py`：`单位成本 = Σ(BomLine.qty_per_product × Material.price)`。木材行（`WD-`/木材）单价取 SKU 级 `PricingSku.wood_cost`（定制感知，多木材行共享、只计一次）。`is_custom` 单追加 `custom_surcharge`。缺价标记不完整。

### 6.2 定价表 + 公式引擎
- `pricing_sku`：四档零售价（`list_price` 标价 / `daily_price` 日常价 / `small_promo` / `mid_promo` / `big_promo`）+ 成本拆解（`factory_cost`/`wood_cost`/`packaging_cost`/`external_parts_cost`/`logistics_cost`/`install_cost`/`tax`/`platform_fee_rate`）+ 毛利率/大促绝对利润 + `size_category`(小/中/大型)。
- `pricing_sku_costs`：22 项配件成本明细，聚合进 `external_parts_cost`。`pricing_sku_promo`：淘宝/店铺活动/小红书 平台分渠道促销价。
- **公式引擎** `formula_engine_service.py`：规则存 `pricing_formula_rules`（中文表达式如「物理总成本 / 0.4」）；**安全 AST 求值（非 eval）**，支持 `IF/SUM/MIN/MAX/ABS/ROUND`，拓扑解析依赖；~8 条内置规则。改字段后 `pricing_calc_service.recompute` 重算毛利。
- `smart_pricing_service.py`：历史均价 + 成本目标价 + 库存压力 → 建议价。

### 6.3 双层库存 + 锁定台账 + 可生产数
- **两层**：`part_inventory`(配件) / `product_inventory`(成品)。字段 `physical_qty`/`locked_qty`/`defective_qty` 均 `Numeric(14,3)`（Phase 6 从 Integer 改 Decimal，避免分数 BOM 向上取整丢量）；`available = physical − locked`。
- **锁定台账** `inventory_lock_service.py` + `inventory_lock_ledger`（append-only，库存可由台账重建）：工厂单创建按 `sku_code` 展开 BOM 锁料（Postgres `SELECT…FOR UPDATE` 防并发超卖，缺料不阻断但告警）；取消释放、出货消耗、退货完好入库。
- **可生产数** `producibility_service.py`：每物料 `floor(可用/qty_per_product)`，取最小为 `can_build`，给瓶颈物料与缺口。
- **日均销量/备货预警** `product_inventory_service.py` / `part_inventory_service.py`：`reorder_point = safety_stock + lead_time×日均`，`days_of_stock = 可用/日均`，状态 critical/danger/warning/excess/ok。`lead_time` 优先手填 > 工厂单实际中位数 > 兜底 30 天。
  - ⚠️ **成品日均销量按产品级聚合**（订单 SKU 是淘宝SKU、库存 SKU 是描述串，口径不一致致 SKU 级匹配全 0）→ 用 `brand_variants` 跨品牌求和，同产品各 SKU 行共享该日均（commit ada363c）。配件日均由「订单×BOM 展开」近 90 天反算。

### 6.4 订单 + 状态机 + 导入
- `orders` 状态机：`pending_payment→paid→shipped→signed→aftersales(↔signed)/cancelled`，迁移图见 `models/order.py: ORDER_STATUS_TRANSITIONS`。双核对签收 `tracking_confirmed`/`manual_confirmed`。
- 财务列（迁移 0046）：`buyer_payable_amount`(应付)/`paid_amount`(实付)/`shop_received_amount`(实收)/`tax`/`platform_fee`/`refund_*`/`alipay_flow_no`。
- **订单导入** `order_import.py` / `taobao_order_import.py`：支持千牛多表 Excel(.xlsx，入口 `POST /api/orders/import-taobao`) + 销售明细 CSV(GBK/UTF-8)；SKU `PPS+13位` → 旧 `P+11位`；订单号科学计数法标 needs_review 不静默丢。
  - ⚠️ **订单状态必须从「订单状态」列映射**（`_STATUS_MAP`：等待买家付款→pending_payment / 等待卖家发货→paid / 卖家已发货→shipped / 交易成功→signed / 交易关闭→cancelled）。历史 bug：导入写死 `pending_payment` 致**所有按状态门的统计（现金流在途/资产订单利润/平台费）全算成 0**。
  - **再导走 UPSERT**（按订单号更新已存在单的状态/金额，不再 skip-duplicate）；**`is_historical=False`**（活跃单要进现金流）；财务列分开存：`买家应付货款→buyer_payable_amount`、`买家实付金额→paid_amount`、`卖家服务费→platform_fee`、`退款金额→refund_amount`、`打款商家金额→shop_received_amount`、`发货时间→ship_date`、`店铺名称→shop`。
  - 标准 xlsx 三表：`订单报表`(单级全财务列) / `销售明细`(行级驱动) / `发货报表`(收货人联系方式)。

### 6.5 财务对账闭环 ⭐（本批重点，已逐行读核心代码）
- **逐笔四方对账** `order_reconciliation_service.py`（财务→结算对账→逐笔对账）：每单一行铺开 收入侧 应付→实付→(补贴)→实收→**实际到账**，成本侧 理论↔实际。
  - `理论应到账 = 店铺实收`，缺则 `应付 − 2%补贴税 − 软件服务费`，再缺退实付。容差 `max(±1元, 0.5%)`。
  - 实际到账 = 微信/聚合(`order_settlements` 按 `order_no` 净额) + 支付宝(`alipay_flows` 按流水号净额)。无证据诚实标 `pending`，不强判差异。`summary()` 给应付/实付/补贴/实收/2%税合计 + 状态分布 + **到账覆盖率**。
- **结算导入** `settlement_import_service.py`：微信/聚合 billDetail xlsx → `order_settlements`，**按支付流水号去重**（`income−expense`=净额）。`/api/settlements/import|summary|""(list)|reconciliation|reconciliation/summary`。
- **支付宝反向回填** `alipay_backfill_service.py`：用订单号**尾12位倒排索引 + 多规则抽取器**(exact/strip_prefix/tail_index)从脏流水文本(`platform_order_no`/`related_order_no`/`remark`/对手账户)掏订单号，唯一命中才写 `Order.alipay_flow_no` 并标流水 `matched`；歧义跳过。`backfill_transaction_time`：交易时间空的流水从**流水号前 8 位(YYYYMMDD)**补交易日。入口 `/api/finance/order-flow-match/analyze|backfill`（backfill 顺带补日期，返回 `filled_dates`）。
- **销售排行榜** `sales_analytics.product_ranking`（数据分析→销售排行榜）：按月/年 × 销量/销售额，每期冠军时间线。口径=正式销售(`is_refill=False`、未取消、有下单日期)，销售额=买家实付；**排除非产品关键词**(差价/邮费/补拍/专拍/专链/运费/补差/改价)。`/api/reports/sales/ranking`。
- 同文件还有 `summary`/`product_breakdown`/`forecast_30d`(移动平均×1.2安全系数)/`stock_advice`/`slow_moving_split`。

### 6.6 待办台账（运营 SOP）
- `ops_checklist_service.py`：日/周/月例行清单（导单/截图录单/发货/售后/盘点/对账/调价/导流水/导账单/ROI/更新投资…）。完成态存 `system_settings` JSON（`mark="task@period_key"`，period 日=YYYY-MM-DD 周=YYYY-Www 月=YYYY-MM），**跨周期自动重置，免建表/迁移**。`/api/ops-checklist|/toggle`。

### 6.7 飞书双向同步
- `feishu_*` + `feishu_sync_map`(行级配对) + `feishu_table_bindings`(表级映射，`field_mapping` JSON 含主键)。24 张表可同步；双向(products/pricing/orders/customers/materials) + 入向(其余)。
- 冲突检测：`system_hash` vs `feishu_hash`，两边都变 → 写 `data_exceptions(feishu_conflict)` 不自动覆盖，UI 让用户选。凭证(app_id/secret)存 `system_settings`，tenant token 内存缓存 ~2h。值归一 `_normalize`(bool→是/否、datetime→ms 等)。

### 6.8 AI / OCR
- `ai_provider.py`：抽象 `anthropic`(官方 SDK + prompt 缓存) / `openai 兼容`(Qwen-VL/GLM-4V/豆包/vLLM，需 `base_url`)。每次调用记 `ai_chat_logs`（含 token/cache 统计）。key 优先读 `system_settings`，缺则 `.env`，未配置友好降级不崩。
- 用途：异常诊断 `ai_assistant.diagnose_exception`；送货单 OCR `ocr_service.parse_delivery_note`；千牛订单/采购发票截图 `vision_ocr_service`；定制报价估价 `customization_ai_service`。OCR 推荐 `claude-opus`/`qwen-vl-max`，诊断用 `haiku`。

### 6.9 供应商对账 + 送货单 OCR
- 拍照送货单 → OCR → `delivery_notes`/`_lines`/`_files`（按 supplier/year/month 归档）；行级 `delivery_matcher.py` 模糊匹配工厂订单(+AI 兜底)。
- **支付宝→供应商付款自动核销** `supplier_payment_matcher.py`：按 `counterparty` 关键词认供应商，单据金额 **精确单笔 → 子集和组合(≤6 张穷举) → 歧义人工**；命中标 `note.paid` + `flow.matched`。
- `alipay_flow_router_service.py`：智能分类后把流水分派到 售后/推广/日常/外包/采购/工厂付款 各表。`factory_reconciliation_service.py`：按工厂×周期重建对账（balanced/underpaid/overpaid，±5元容差）。

### 6.10 调度器 / 看门狗 / 系统监控
- `scheduler.py`：定时任务（17:00 退款检查、每小时数据基线、06:00 预测、07:00 低库存、08:00 激活远期单、09:00 物流、10:00 财务对账等），记 `scheduled_job_runs`，连续 3 次失败告警一次。可 UI 改时间(存 `system_settings`)。
- `system_monitor.py`：60s 健康检查写 `system_health_logs`（DB/磁盘/内存/待迁移/Storage）；**自救看门狗**：连续 3 次失败 SIGTERM 自己 → Docker 拉起（10 分钟冷却）；PID 文件杀孤儿进程；重启 diff（内存 95%→42%）。admin→「系统监控/看门狗」。

### 6.11 导入器（异步大文件）
- `import_jobs` + `import_job_service.py`：`/api/importer/commit-async` 落临时文件 → ThreadPool 后台跑，每 50 行写进度，前端 2s 轮询，可取消；失败留 traceback 不影响主事务。`excel_importer.preview/infer_mapping(AI推断列映射)/commit_sheet`；`smart_import_service` 按 sheet 名识别实体 + 质量分(good/needs_review/messy)。`bill_import_service` 解析万师傅/物流/推广/售后 CSV。

### 6.12 客户 / 售后 / 营销 ROI / 定制
- `customer_service.aggregate_all`：从订单聚合(仅真实姓名+电话)，算 LTV/分级，记购买产品。
- 售后 `return_service`/`aftersales_followup_service`：建退货→确认入库→入库/补发；逾期跟进按原因建议动作。`part_return_service`：配件退/修/报废财务台账。
- `roi_service.compute`：推广支出 vs 订单销售额 → ROI（**剩余 TODO：按月占比，剔除补单**）。
- 定制 `custom_quote_service`(整板逐行成本→工厂利润 25%→畔色加价 15%) / `customization_service`(尺寸微改克隆 BOM 生成 `改NN` 变体)。

### 6.13 剩余流水 / 可用资金 / 投资回收 ⭐（财务→剩余流水, `cash_flow_service.py`）
- **可用资金 = Σ加项 − Σ减项**（实时算，无手动结算）。**总投资费用不在公式内**——它是沉没本金，单列「投资回收」与累计总利润对比（早期 bug：把总投资当减项 + 历史病根，致可用资金算成 −82万；实为正 ~30万+）。
- **加项**：平台保证金(手动) + 支付宝余额(**全部**支付宝账户) + 聚合余额 + 推广余额 + 其他账户(银行卡/个体户私账，也是真金) + 订单待确认收货(`Σpaid status=shipped`) + 订单未发货(`Σpaid status=paid`)。账户按账户名子串分类(`_classify_account`)。
- **减项**：待扣平台费 = (待确认收货+未发货)×**千分之六**(卖家服务费列常空，故估) + 工厂打样(未付·无平台单号) + 工厂结算已开账单未付(有平台单号) + **工厂结算未开账单预估**(活跃单预测工厂成本 `_predicted_factory_cost`=theoretical_cost×qty>BOM现算>定价表factory_cost；**跳过已开账单单防双算**；缺成本单数如实提示) + 代付补单佣金(补单记录 commission 未结)。
- **投资回收** `compute_total_profit`：累计总利润 = 真实销售(非补单/非取消/有收入) 营收(店铺实收>应付−2%税−费>实付) − 成本(实际/理论) − 售后费用；**标注缺成本单数防高估**。回收率 = 总利润/总投资。
- **账户余额统计日期** `AccountBalance.as_of_date`(迁移 0059)：余额是某天手填的快照，**新鲜度按 as_of_date / 订单按 max(order_date)**，不再用 `updated_at`(=导入那天=今天)。导入 `_h_balance` 把「统计日期」整日存入 as_of_date 并据此定年月(中文「2026年5月20日」可解析；缺日期则 as_of_date 留空→新鲜度标未知)。
- **定制单缺需求** `custom_order_spec_service.scan`(`POST /api/orders/scan-custom-specs`)：SKU含定制/其他尺寸 且无具体尺寸规格 → 异常分类 `custom_order_missing_spec`(幂等去重，补需求后自动 resolve)，补全后可用定制定价精确核算工厂成本。

## 7. ⚠️ 关键数据真相（实测确认，勿重新踩坑）

1. **`AlipayFlow.amount` 带符号**（正=收入 负=支出，负值确实存在），不是绝对值；`sum(-amount)` 判支出是对的。
2. **2% 补贴税已物化**：`Order.tax == buyer_payable_amount × 0.02`（805/805 精确）；`shop_received_amount = 应付 − 2%税 − platform_fee(软件费~0.6%)`（±1元取整内）。对账据此：`理论应到账 = 应付 − 2%税 − 软件费 = 店铺实收`。
3. **订单表 `alipay_flow_no` 与库内 `AlipayFlow.transaction_no` 不是一套**（986 单仅 13 命中，全爱群号）。企业号 `platform_order_no` 是 45… 号段、订单表 51…/33… 号段 → 企业号是更早订单、尚未进 `orders` 表。
4. **企业号流水号前 8 位 = 交易日**；导入兜底 `alipay_import.date_from_flow_no`，修历史 `backfill_transaction_time`。
5. **到账证据覆盖率仅 3.5%**（45/1278）。逐笔对账诚实标 `pending`；订单侧金额是完整的。提升覆盖率靠补导早期订单 + 更多 billDetail/企业号流水。
6. **聚合结算只走微信**(billDetail=`order_settlements`)；支付宝货款在企业号(9A)。
7. **销售排行榜排除补差价/邮费/专拍**（否则差价链接 58066 件霸榜）。
8. **支付宝账户**：企业号(9A 淘宝结算) / 爱群号(9C 货款转账个人) / 主力号(komo转账/采购/广告) / 佳宝号(理财)。
9. 实测：1278 单纳入逐笔对账，2%补贴税合计 ≈¥21,246；年度销量冠军=榉木餐桌 138 件/¥16.6万。
10. **导入订单状态曾被写死 `pending_payment`**（病根）：致现金流在途/资产订单利润/平台费全算 0、可用资金假性巨负。已修：从「订单状态」列映射 + 再导 UPSERT 刷新。标准 xlsx(970单) 实测：未发货 85 / 待确认收货 27 / 成功 549 / 关闭 304。
11. **账户余额是「某天手填的快照」不是实时**（用户实测 5月20日填的）：模型加 `as_of_date`，新鲜度按统计日期算（旧逻辑用 `updated_at` 致全标"今天·绿"、月份还错标本月）。企业号期末余额常「待补(流水截至上月)」即不完整，勿当实时现金。
12. **可用资金本应为正**：账上现金 ~30万、无突发巨额应付，剩余流水 ≈ +30万~60万；早期 −82万 是「总投资当负债 + 工厂欠款虚高(payment_status 默认 unpaid 从未回填) + 订单利润算0」三 bug 叠加的假象。工厂未付款若虚高需对账回填 `FactoryOrder.payment_status`。

## 8. 约定与模式

- **service 层承载业务逻辑**，api 薄、model 纯。中文注释为主，注释解释"为什么"（含历史踩坑）。
- 金额一律 `Decimal/Numeric`，前端出参转 float。日志时间戳统一北京时间。
- 飞书同步键：自增 id 两端对不上 → 用业务字段拼 `sync_key`（SQLAlchemy `before_insert/update` 事件自动生成）。
- `AlipayFlow` 唯一键 = `(account, transaction_no, transaction_type, amount, balance)`（同号成对货款+分账/多次扣费都要入库，五者全同才算重复，见迁移 0039/0057）。
- 免迁移的轻状态存 `system_settings` JSON（如待办台账、调度覆盖）。
- 历史水位线 `is_historical=True` 的订单不进库存/财务核对；`is_refill=True` 补单不进正式销售统计。

## 9. 验证方式

- 后端：`docker compose exec api python -m pytest tests/ -q`（按子系统跑单文件，如 `test_alipay_backfill.py`/`test_order_import.py`/`test_reconciliation_service.py`）。
- 前端：见 §2 的 2GB 临时容器 `tsc --noEmit`（每次改完都验，保持零错误）。
- 新路由：本地 API 的 `/openapi.json` 或 Swagger `/docs` 确认已注册；群晖 LAN nginx 只代理 `/api/*`，生产验收由 `verify_release_nas.sh` 在 API 容器内读取 `/openapi.json`。

## 10. 当前状态 & 剩余 TODO

- 2026-07-21 基线：`origin/main` 已包含活动生命周期（迁移 0127）、报名价规则升级、定制报价双口径、真实 BOM 带出部件与板单核对。
- 群晖生产要求：API `/api/version` 与 Web `/build-version.json` 必须返回同一完整提交；数据库必须为 `(head)`；四容器必须运行。不要再用单个后端版本标签代替整套发布版本。
- 发布与回滚：只用 `scripts/deploy_release_nas.sh`；每次部署自动保留 `panse-system-api/web:rollback-时间戳` 镜像。验收只用 `scripts/verify_release_nas.sh`。
- 历史功能完成情况以 Git 提交、迁移和各专题 `docs/*plan.md` 为准；本节不再保存容易过期的临时分支名与“剩余 TODO”快照。

## 11. 相关文档

- `docs/会话交接-2026-06-07.md` — 本批 9 提交清单 + 数据真相 + 环境坑（会话级）。
- `docs/feishu-sync-mapping.md` / `docs/backup-restore.md` / `docs/permissions.md`。
- `docs/群晖统一发布流程.md` — 群晖唯一正式发布、验收与回滚流程。
- `README.md` — 启动/升级/模块清单。
