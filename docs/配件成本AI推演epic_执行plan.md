# 配件成本 AI 推演 epic — 执行 Plan(用户 2026-06-28)

> 压缩上下文后照此逐阶段执行。每阶段:改 → 部署 → live验数字 → 给用户看口径 → 再往下。

## ⚠️ 当前架构(压缩后必读,已变!)
- **NAS = 唯一生产**(api/web/db + 飞书长连接 + 报表 + 解密 + 全部41个定时任务)。
- **PC ERP docker 已停**(`docker stop panse-system-api/web/db/backup`, 架构统一)→ **旧"三方部署"现在=NAS镜像 + GitHub 两方**(不再部署PC docker)。
- **取数 = PC 原生 Web-Agent**(系统python uvicorn :8500, watchdog.vbs 守, 自带调度器17:30拉单, 独立于docker)。
- **飞书 = NAS 长连接**(`ENABLE_FEISHU_BOT=1`, feishu_ws_service)。
- NAS SSH: `ssh -i /c/Users/lzdwy/.ssh/panse_nas -o BatchMode=yes -p 2222 15068803006@DS923plus`, docker 要 `sudo /usr/local/bin/docker`。
- NAS 部署: `cd /d/Panse-System && BUILD=1 bash scripts/deploy_api_nas.sh`(PC build→save|gzip|ssh load→up api→重启web→健康检查)。
- NAS agent_output 是**只读挂载**(容器内改不动)。

## 目标
让用户支付宝表里的**零星配件采购**(如山东岩板/PDD岩板, 走支付宝、非月结固定供应商)按**淘宝订单号自动归账→覆盖对应订单成本**;分类默认月结、供应商可覆盖为零星;月度对账中心拆月结/零星。
- 用户支付宝表: `C:\Users\lzdwy\Desktop\支付宝流水-20260424.xlsx`, **Sheet1=38行**(9c/爱群号账户), 列=交易时间/交易流水号/交易类型/交易对象/交易账户/收支金额/关联订单号(=支付宝**商户**单号)/余额/核销状态/核销类型/备注(含"岩板:人名")/**平台订单号(=淘宝订单号, 有时多行\n分隔)**。导入预览已确认: suggested_entity=alipay_flow, 映射对(平台订单号→platform_order_no, 备注→remark, 关联订单号→related_order_no)。

## 用户已拍板的口径决策
1. 多单分摊 = **按各单该配件 BOM 用量(面积/数量)占比分**(非平均)。
2. **缺尺寸的 BOM 由 AI(我)按 SKU 尺寸推演** → 写 est_size 标"预估" → 人工可编辑 → 编辑后置"确认值" + **二次确认**。不污染原 remark。
3. 供应商级结算(不是纯分类级): 一个分类(岩板)大部分月结(工厂), 但偶尔换新供应商零星小采(山东岩板=现付/零星)。

## 口径红线(绝不违反)
- 配件成本**只算一次**, 不双算。月结部分(工厂月度PartsMonthlyRecon) vs 零星部分(现付供应商支付宝采购) 不重复(工厂月度只含工厂的货)。
- 打包/运费已在 physical_cost(口径第16条), 月度对账中心是供应商应付AP核对、不进产品成本。
- AI 推演只标"预估"、人工可纠、不动 remark。
- 改完自己 live 测(NAS真数据), 小批量先验口径再全量。

---

## 阶段1: BOM 尺寸 AI 推演

### 1a 模型 + 迁移(地基已铺一半)
- 迁移 `backend/alembic/versions/0098_bom_est_size.py` **已建**(BomLine 加 est_size String128 + size_status String16, additive nullable, down_revision=0097)。
- 待做: `backend/app/models/bom.py` BomLine 加两字段:
  ```python
  est_size: Mapped[Optional[str]] = mapped_column(String(128))   # 推演/确认尺寸串, 不覆盖remark
  size_status: Mapped[Optional[str]] = mapped_column(String(16)) # 'inferred'|'confirmed'|NULL
  ```
- 部署: 改model → commit/push → NAS镜像rebuild → NAS 跑迁移 `ssh ... "sudo /usr/local/bin/docker exec panse-system-api-1 alembic upgrade head"`(先确认api是否启动时自动迁移;若是则只需重建)。

### 1b 推演服务 `backend/app/services/bom_size_infer_service.py`(新建)
- 选缺尺寸的 BomLine: remark 无可解析尺寸(`parts_recon_service._size_area(remark)==0`, 即remark里不足两个数字) 且属面积料(按 Material.category∈{岩板,玻璃,...} 或 size_type=='组合')。岩板真数据=166行中49行缺尺寸(POC实测)。
- 复用 `backend/app/services/custom_quote_v2_service.py` 解析器: `parse_length_m(sku或product_name)`取长度米(实测干净: "基础柜-下柜-2.1米"→2.1✓), `_parse_size_info("长度:Xmm;深度:Ymm")`取长深高cm, `parse_height_cm`。BomLine 有 sku/product_name 字段。
- 推演规则:
  - 面积料(岩板/玻璃/桌面板/洞洞板): est_size = `"{长}*{深}"`(长=parse_length_m×1000 或 size_info长; 深=size_info深 或 品类默认深度如柜400/桌750)。
  - SKU无长度的(如"黑胡桃木床头柜-岩板台面"): 调**本地 qwen3.5:9b**(经 Web-Agent `/api/ai/chat`, think=False — 见 [[project_panse-local-llm]]; 走 web_agent_service._post)推理"产品X的部位Y尺寸"。
- 写 est_size + size_status='inferred'; **幂等**(只填 size_status 为空或 'inferred' 的, 不动 'confirmed')。`run(db, apply=False)`预览 / `apply=True`落库。

### 1c 小批量验口径(★必做, 别跳)
- 先对岩板49行 NAS真数据跑 `apply=False`, 把 [SKU → 推演尺寸] 列出来发用户看, **用户确认口径对**再全量 apply。

### 1d 复核 UI + 二次确认
- 前端: BOM尺寸复核页(或 BomViewerPage 加 Tab): 列 size_status='inferred' 的行, est_size 可编辑; 编辑保存 → size_status='confirmed' + **二次确认弹窗**。
- API(backend/app/api): GET 列inferred / PATCH est_size(带 confirm=true 参数才置confirmed)。

### 1e 全量推演 apply=True(口径确认后)。

---

## 阶段2: P4 支付宝配件按平台订单号归账
- `backend/app/services/alipay_flow_router_service.py` `create_purchases_from_unclassified`(约line304-359): 建 PartPurchase 时 `related_order_no=f.related_order_no`(=支付宝**商户**单号, 对不上淘宝Order.order_no)→ **改 `f.platform_order_no or f.related_order_no`**(优先淘宝单号)。多行平台订单号(\n)→见阶段3。
- 默认 `run_all(create_purchases=False)`(agent_ingest:636)不建采购单 → 本epic要的是: 导入支付宝表后**显式**跑 `run_all(create_purchases=True)`。
- 链路: 导入Sheet1→AlipayFlow → `alipay_flow_router_service.run_all(create_purchases=True)`→PartPurchase(related_order_no=淘宝单号) → `accessory_capture_service.run_capture`(填material_code/分类) → `parts_recon_service.aggregate_related_purchases`→Order.actual_parts→physical_cost。

## 阶段3: 多单 BOM 占比分摊
- 一笔 PartPurchase 的 platform_order_no 多行(N个淘宝订单)→ 按各订单该配件分类的 BOM 用量(面积=阶段1 est_size算, 或数量)占比, 把金额拆到各 order 的 actual_parts。
- 单订单: 全额。多订单: Σ各单占比×金额。
- 落点: 扩 `aggregate_related_purchases` 或新分摊函数; related_order_no 支持多单(拆分前先按 \n split)。

## 阶段4: 供应商级结算 + 月度对账拆月结/零星
- `Supplier.payment_terms`(月结/现付/预付)字段**已存在**(models/supplier.py:47)。岩板厂=月结、山东岩板/PDD=现付(零星)。
- `parts_recon_service` settle_mode 现按分类(`_MONTHLY_SETTLE_CATEGORIES`=五金/电力轨道/岩板/玻璃)。改: **分类默认 + 供应商级覆盖**。一分类月度"实际" = 月结供应商部分(工厂月度PartsMonthlyRecon) + 零星供应商部分(现付供应商的 PartPurchase 支付宝采购汇总)。
- 月度对账中心 `monthly_settlement_service`(见 [[project_panse-monthly-settlement-center]]) 月结类行拆两小行: 月结(工厂月度) + 零星(现付供应商支付宝采购); **不重复**。

## 阶段5: 收尾
- 清 NAS 16份旧加密报表: 从 **NAS 主机**(容器只读) SSH `sudo` 把 agent_output 里非2026-06-27的加密 ExportOrderList 改名加 `_abandoned_` 前缀(reingest 跳过 `_` 开头)。
- **最后**导入支付宝表 Sheet1: 走系统「导入表格」`/api/importer/preview`+`/commit`(entity_type=alipay_flow, 账户=爱群号/9c), 不手填DB(见 [[feedback_import-via-system-entry]]); 经阶段2-4链路→覆盖订单成本。⚠爱群号是货款户但这是支出/采购流水, 不污染营收对账。

---

## 每阶段部署/验证模板
1. 后端改 → `docker exec`(任意在跑的)`python -m py_compile` 校验 → `git add`+`commit`(直接push origin main, 不开PR, 不加AI署名 见 [[feedback_auto-push-github]])→ `BUILD=1 bash scripts/deploy_api_nas.sh` → 有迁移则 NAS `alembic upgrade head`。**PC docker已停, 不部署PC。**
2. 前端改 → `cd frontend && npx tsc -b` → `BUILDX_NO_DEFAULT_ATTESTATIONS=1 docker build -f deploy/web.lan.Dockerfile -t panse-system-web:lan .` → `docker save | gzip | ssh NAS gunzip|load` → ssh NAS `compose -p panse-system -f docker-compose.lan.yml up -d --force-recreate web`。
3. live测: NAS `docker exec panse-system-api-1 python -c "..."` 验真数字。
4. 测试: 加 pytest `backend/tests/test_*_0628.py`。

## 关键文件锚点
- 模型: models/bom.py(BomLine), models/order.py(Order/PartPurchase est_packing/est_logistics/actual_parts/est_parts), models/material.py(category), models/supplier.py(payment_terms:47), models/finance.py(AlipayFlow/PackingBill/LogisticsBill)。
- 服务: parts_recon_service(_size_area/_settle_mode/_MONTHLY_SETTLE_CATEGORIES:38/bulk_material_recon:297/aggregate_related_purchases/_settled_shipped_orders/_ym), alipay_flow_router_service(create_purchases_from_unclassified:304), accessory_capture_service(run_capture/match_material_code/link_orders_from_remark), custom_quote_v2_service(parse_length_m:74/_parse_size_info:194/parse_height_cm:84), monthly_settlement_service, web_agent_service(_post→/api/ai/chat qwen3.5 think=False)。
- API: api/importer.py(preview/commit), api/purchases.py(bulk-material-recon/monthly-recon), api/monthly_settlement.py。
- 前端: pages/BomViewerPage.tsx, pages/PurchasesPage.tsx+components/BulkMaterialReconPanel.tsx, pages/MonthlySettlementCenterPage.tsx, api/finance.ts。
- 迁移: alembic/versions/0098_bom_est_size.py(已建), 最新head=0098。
