# 01 本场文件工具：显式力度与只读价格

只做本地取数/填表，不操作浏览器，不上传、不报名、不改数据库，不启动旧 prepare 链。
本页不改变 `campaign-signup-frozen-steps.md` 的固定短流程。

2026-09-06 舍入修复：普通SKU官方立减沿用既有 `campaign_service` 实测规则，日常价≥100元时向上取整到元，低于100元时精确到分；单品金额反算为日常价−该官方立减−目标。此前新工具误用所有价位到分的结果被更正，尚未上传的升降桌旧文件不可继续使用。不得借修复自动重传或改动已成功中促/秋季优惠。此为生成器内部公式修复，不新增平台步骤。

## 一次只读取数

在 ERP 权威目录运行（输出路径必须是不存在的新文件）：

```powershell
python scripts/campaign_price_snapshot.py --output <本次价格快照.json> --rotation-receipt docs/receipts/campaign-four38-single-upload-20260906.json
```

- 一次 NAS `REPEATABLE READ READ ONLY` 查询，45 秒总超时，没有重试；不调用旧活动准备服务。
- `all_erp_rows` 是完整定价行；`daily` 为普通报名价；`medium_target` 来自 `pricing_sku_promo.mid_buyer_price`，`big_target` 来自 `big_buyer_price`。不使用旧字段注释推断“小促”。
- `current_sellable_item_ids` 按 ERP 产品状态“在售”汇总主/备用商品 ID，不按销量、历史失败或历史无动销过滤；未知产品状态和缺映射单独列明，没有静默删除原始行。ERP 状态不冒充实时淘宝在售状态。
- 两张表都使用同一份快照及 `resolved_price_version_sha256`，不能各查一次数据库。
- 今天 7 个新 SKU 由官方成功回执作带来源文件映射，不写数据库；价格保留当前数据库值，不拿历史回执价格覆盖当前价。数据库已含新 ID 时也保留，冲突/多义映射明确报出。
- 2026-09-06 09:26 已实际取数一次；快照在 `D:\AI\畔色ERP系统\outputs\01a03341-b2cd-7810-92f3-66fad189521d\current-campaign-price-snapshot-resolved-20260906.json`，ERP 价格版本 `2ecbb6577a04d06560b2734ace325395fc64096c3508a27365c9b4aba8bf20ba`。同次生成直接复用，不重复抓取。

## 两表填写函数

### 01 最短可运行入口

在 ERP 权威目录使用 `scripts/campaign_generate_current_files.py`。一次运行仅本地计算/填表；没有网络。本次直接复用已下载模板和已取得的价格快照，输出目录必须全新。默认范围完整；已知缺映射会一次列出，不擅自排除。

```powershell
python scripts/campaign_generate_current_files.py --snapshot "D:\AI\畔色ERP系统\outputs\01a03341-b2cd-7810-92f3-66fad189521d\current-campaign-price-snapshot-resolved-20260906.json" --activity-template "C:\Users\lzdwy\Desktop\「超级立减长期活动」商品导入模版20260906092948.xlsx" --discount-template "C:\Users\lzdwy\Desktop\1788658932873.xlsx" --official-rate "10%" --target medium --start "2026-09-14 00:00:00" --end "2026-09-16 19:59:59" --output-dir "D:\AI\畔色ERP系统\outputs\01-super-reduce-gap-20260906"
```

秋季复用同一命令和快照，只替换为：

- `--activity-template "C:\Users\lzdwy\Desktop\「2026年淘宝秋季家装节秋季家装节现货」商品导入模版20260906093633.xlsx"`
- `--official-rate "12%" --target big`
- `--start "2026-09-16 20:00:00" --end "2026-09-27 23:59:59"`
- `--output-dir "D:\AI\畔色ERP系统\outputs\01-autumn-20260906"`

正常行强制使用快照 daily，不采纳淘宝建议价或旧 ERP 导出过滤；单品减钱精确计算到分。定制首轮保持当前 ERP daily，不调低价格，因此不把全店原始保护价调查设为前置门。只有失败后确需降低定制价时，才核对该 SKU 已有首次原价与固定20%底价；可选 `--custom-basis-receipt` 保留来源，不得以当前价重新计算保护基准。

成功生成 `活动报名.xlsx`、`单品立减.xlsx` 和 `receipt.json`。没有需要重报的商品时不生成活动文件；没有正额单品减钱时不生成空优惠文件。若任何输入未知或公式不成立，只写明确的问题回执、退出码2，**不生成部分商品上传文件**，不重查ERP、不扫描平台、不重试；把问题直接交代，不继续长调查。生成结果不是平台成功。

上传前由01按既有记录确认新窗口不与同商品其他单品立减重叠；本地文件工具不查平台、不自动建立优惠，也不把未知重叠状态当“无”。本 CLI 是固定规则下的文件计算入口，不是替代01业务状态管理的新prepare链。

01 先下载当场最新官方模板，再在自己的单次文件生成脚本调用：

```python
from pathlib import Path
import sys
sys.path.insert(0, str(Path('D:/AI/畔色ERP系统/ERP程序/scripts')))
from campaign_official_template import fill_selected_rows, fill_single_discount_rows

# rows 是本场完整未成功商品的全部启用 SKU；不得只保留普通 SKU。
# 普通行 activity_price 必须由同一快照 daily 填入，不让淘宝建议价覆盖。
activity_bytes = fill_selected_rows(
    current_activity_template.read_bytes(),
    rows,  # [{item: '...', sku: '...', activity_price: '8070.00'}, ...]
    official_rate='12%',  # 必传本场确认值；支持 '10%'/'15%'/'0.10'，无默认
)
discount_bytes = fill_single_discount_rows(
    current_single_discount_template.read_bytes(),
    discounts,  # [{item: '...', sku: '...', deduct: '1744.46'}, ...]
)
# 使用新文件路径，以 xb 保存 bytes；保存两份模板/输出/价格版本指纹和精确窗口。
```

文件工具只负责忠实填写已计算行，不擅自决策价格、SKU 或重复报名。01 的生成脚本仍保留既有瞬时公式断言：

- 普通报名价 = `daily`；官方立减金额按既有金额口径计算，`单品立减金额 = daily − 官方立减金额 − target`；超级立减 target=medium_target，88VIP/15% 大促 target=big_target。
- 使用 Decimal 到分计算，最终价不得低于 big_target；不能靠改日常价、擅加容差或增大优惠消除不成立的公式。
- 定制首轮保持当前 ERP daily、不主动降价、不生成单品立减；之后需要降低时才核对首次原价20%保护基准，不拿当前 daily 冒充首次原价。
- 同商品同窗口不得叠加重复单品立减；单品表的精确起止窗口由 01 在该场操作中设置并记录，不由 5 列文件推测。
- 当前成功范围不重传，未知结果不重传。已发布设定会被活动文件工具拒绝；“一键选择成功但仍草稿”不算已报名成功。
- 单品立减金额为 0 的行不需要创建优惠，空单品文件不上传；如同窗口相同优惠已经明确成功，按冻结规则复用，不重复生成上传事务。

## 模板支持边界

- `fill_selected_rows` 支持实际字段为 A商品ID/E SKUID/P活动价/S官方立减报名折扣/T官方立减金额的 20 列官方 SKU 模板。L 参考力度可空，但调用方必须明确传入当前力度；低于模板默认力度或与预填 S/T 冲突会在本地报错。自定义属性、其他 ZIP 部件、表头、原合并继承值逐项保留和回读。
- `fill_single_discount_rows` 支持实际 A商品id/B SKU_ID/C优惠值/D取值方式/E提醒的当前官方 5 列模板，保留表头与全部非数据 ZIP 部件，移除示例数据、文本保存 ID、金额不写公式。
- 01 的本场真实超级立减模板 `「超级立减长期活动」商品导入模版20260906092948.xlsx` 是 **25 列**：N 活动价/Q让利比例/R补贴金额。仍调用 `fill_selected_rows(..., official_rate='10%')`，工具按已识别表头写 Q 为数值 **10**（不是10%或0.1），R保持空，包邮和素材保留；阻止重传“活动中/进行中/已生效/已发布设定”。历史14列模板未适配，不自动套用。
- 本场25列模板读取为60商品/575SKU，商品状态28活动中、21异常、2草稿、9未报名。活动报名文件仅处理未完成商品；**单品立减的新窗口范围独立计算，不能沿用活动报名的成功排除集合而漏掉28个长期已报商品**。这是两表业务范围的区别，不增加平台扫描。
- 原 `/pricing-formula/*signup*.xlsx` 和 `/single-item-discount.xlsx` 仍可能使用历史模板/过滤服务，不能拿它们绕过当场模板或当作本次已修好的正式上传文件。本次不调用、不部署、不替换这些生产接口。

## 本次活动事实（来自 01，不是 02 平台核验）

- 秋季 campaignId=49557 / unitedActivityId=49560 / signRecordId=3538210379，力度 12%，窗口 2026-09-16 20:00:00 至 2026-09-27 23:59:59。
- 批 797261540 的 7 success 仅为入草稿；01 刷新后全部7/草稿7/已发布0，7个都属于未完成范围，不排除。
- 超级立减 signRecordId=3172207691，力度10%；01回读28活动中/21异常/2草稿。保留已成功28，不重传。此次衔接单品立减窗口由01提供为2026-09-14 00:00:00至2026-09-16 19:59:59，不改变平台长期报名到2028的期限。

## 本次已核实映射和独立范围

- 复用09:26同一ERP价格版本；既有成功回执又恢复14条精确编号对应，未改任何价格或数据库。映射后版本为 `582939924e096f99400bba74499fc314f4dca0d72eb6aa2d90e826f46ede4674`。
- 剩余31条无可证明映射，分属1038064128030（16）、724042164333（10）、919649052479（5）。逐SKU见 `docs/receipts/campaign-verified-mapping-recovery-20260906.json`；不得按名称猜配，不得默默丢弃。
- 如01已明确决定本次先处理哪些完整商品，可传 `--signup-items "商品ID,商品ID"` 和独立的 `--discount-items "商品ID,商品ID"`。省略表示模板全范围；参数不是自动筛选规则，也不新增平台步骤。
- 01本次已明确暂缓717418169535的新窗口中促单品优惠，因为其17条已报普通SKU价格高于ERP daily 0.2–1元；不继承上场2元例外、不重传已成功报名。此暂缓不自动延伸到秋季活动。
- 用户再次确认：零碎兼容性修复不得改变冻结短流程，不增加预检、候选扫描、价格证据循环、自动轮换或重试。任何业务规则改动仍需用户直接同意。
