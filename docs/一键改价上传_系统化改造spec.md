# 一键改价上传 · 系统化改造 spec（2026-07-16，据 7-15/16 通宵实战固化）

> 目标：ERP「全自动推标价」按钮一键完成 **WA导出 → 生成金标准导入文件 → WA上传+提交 → 回执 → 自动对账验证 mismatch=0**，全程不经 Claude、不经人工填表。
> 实战依据：94行+32行改价经此格式全部成功（2026-07-15晚）；参考实现代码在会话 scratchpad `gen_golden_clone.py` / `gen_fix32.py`（逻辑已验证，直接移植）。攻略正文见根目录 `Panse-System_完整交接.md` §三。

## 现状缺口（四处）

1. `data_export_service.build_product_price_upload_from_export`：生成的是"改导出表"老格式（千牛拒收"导出模板数据不能直接用于导入"），且无 E列一口价规则；
2. WA 导出触发 bug：`触发后(created=False, 已选40件), 导出记录未等到新的已完成行(超时)`——一键流程的第一步就断；
3. WA uploader `product_prices` 通道 commit=None（挂完文件停在提交前，routes.py 注释"commit待录制"）；
4. 端到端编排 `activity_upload_service.product_price_auto_push` 停在 staged，无提交/回执/复验环节。

## 改造点1：builder 重写（金标准克隆）

- 模板底 = `backend/app/assets/taobao_templates/qn_quick_edit_golden.xlsx`（已入库，=用户手填上传成功的实战文件，勿改勿删）。
- zipfile 只重写 `sheet1.xml` + `sharedStrings.xml`，其余13部件字节原样：
  - 数据行完全复刻金标准第4行：只写 **A商品id / B占位 / E一口价(命中时) / G skuId / H价格 / J SKU商家编码**；
  - 值全走 `t="s"` 共享字符串（追加去重，count/uniqueCount 精确更新）；样式 `s="1"`(A/E) `s="3"`(B) `s="4"`(G/H/J)；
  - **整段删 `<sheetProtection/>`**；dimension 更新；价格整数不带小数（如 4630）；
  - ❌ 绝不用 `t="str"`/inlineStr（千牛读不出→报"类目为空"）❌ 绝不用 openpyxl 改存模板（丢部件→"Unexpected end of JSON input"）。
- 输入 = 千牛新鲜导出（发布模板：col1商品id / col10销售属性 / col12 skuId / col13现价 / col14库存 / col16 SKU商家编码，数据第4行起）+ ERP `daily_price`。
- 变更集 = `|现价 − daily÷0.75| > 0.005` 的 SKU 行（÷0.75 HALF_UP 到分，与 `pricing_recon_service._should_list_price` 同源）。
- **★E列（宝贝一口价）规则**（7-15 实战：漏它=8宝贝32行整组回滚）：
  - 千牛规则：宝贝一口价必须 = 改后某个**有库存**SKU 的价格；
  - 对每个宝贝算"改后有库存SKU价集合"（改动SKU用新价、未动SKU用现价、col14库存>0）；
  - 若当前一口价（col5，宝贝级）∉ 该集合 → 该宝贝**所有行**补 E列，取值=**保语义**：原一口价==哪个SKU的旧价 → 填那个SKU的新价；无匹配 → 集合最低价。

## 改造点2：WA 导出触发修复

- 症状：选中40件（pageSize硬限）后点导出，`created=False`，下载中心无新任务。
- 排查方向：导出弹窗可能已改版——需要点选"**导出全部商品**"选项而非默认"选中商品"；用 inspect 式只读 dump 弹窗 DOM 定位真按钮/单选项（照 `inspect_single_item_date` 的套路写一次性探查）。
- 验收：`export_product_prices` 能稳定拿到全店行数（≈571行）的**新**文件（文件名时间戳晚于触发时刻）。

## 改造点3：WA uploader `product_prices` commit 实现

- stage 已通（挂文件+解析）；commit = 点「提交」→ 二次确认弹窗『确定』（多策略选择器，照抄 `commit_super_reduce` 的确认修复：点到才算 True，绝不假阳性）→ 轮询「查看任务处理进度」至成功/失败 → 回执（成功N/失败M+错误明细下载）。

## 改造点4：端到端编排升级（product_price_auto_push）

export → build(金标准) → upload(commit) → 等任务成功 → **自动重新导出** → `pricing_recon_service.reconcile` 验证 **mismatch=0** → 结果进 UI（含每步截图）；任一步失败即停+截图+原因，绝不静默。

## 测试

- builder 单测：①克隆结构断言（15部件一致/无sheetProtection/无t=str/回读与源零差）②E列三场景（无需E / 保语义命中 / fallback最低价）③价格取整；
- WA 侧先人工 stage 验证一轮（比对表+截图）再放开 commit；
- 验收：人为制造 1 个 SKU 价差 → ERP 点按钮 → 千牛任务成功 → 自动对账 mismatch=0，全程零人工。

## 部署

三方铁律（NAS 热补+docker commit / PC git / GitHub 同 commit）；UI 按钮文案升级为「一键推标价（全自动）」。
