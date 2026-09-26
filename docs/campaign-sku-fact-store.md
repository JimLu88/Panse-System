# 商品 SKU 事实固定入口

## 本次实施计划与边界

1. 原始 XLSX 按 SHA256 留存，另记导出、文件修改、接收和索引更新时间。
2. 按真实 XML 行读取，不信任 A1 dimension。无 SKU 的商品行、空编码和重复保留。
3. 版本索引按导出时间选最新；同时间不同文件有歧义时停止，不按接收顺序覆盖。
4. 新价格快照自动固定索引版本；旧价格快照可生成绑定副本。制表按精确 item+sku
   查询，只用唯一 ERP code 和已证明的商品主/别名关系；不向 ERP 回写别名。
5. 空值不抹已有对应，冲突不自动覆盖。定制材质/用途冲突隔离。全店导出不是全店在售证明。
6. 回归完整行、版本更新/重入、篡改、重复/空值、跨商品和语义冲突；真实表独立交叉计数。

本功能仅本机活动证据缓存和只读制表。不会上传平台、报名、恢复旧任务、改变成功/未知
回执，也不会改 ERP 价格、主映射、订单、财务、库存或飞书内容。无需重启生产容器。

## 使用

程序：`scripts/campaign_sku_fact_store.py`。默认数据目录：
`D:/AI/畔色ERP系统/活动准备/商品SKU事实`（原始业务文件不入 Git）。

```text
python scripts/campaign_sku_fact_store.py register --source <用户原表> --exported-at 2026-09-26T23:05:36+08:00
python scripts/campaign_sku_fact_store.py query
python scripts/campaign_sku_fact_store.py query --item 1038064128030 --sku 6070329937248
python scripts/campaign_sku_fact_store.py bind --snapshot <既有ERP价格快照> --output <新绑定副本.json>
```

`query` 默认显示最新来源日期/hash/真实计数；指定 item/sku 返回原始编码、行号、属性及问题。
重复登记相同文件/日期返回原索引，不刷新导出时间；相同字节却给不同导出时间拒绝。
读取每次校验原件和索引元数据，损坏不静默退回旧文件。旧版本可按 `--version <SHA256>`读。

`campaign_price_snapshot.py` 新建快照时自动绑定最新可用版本；
`campaign_generate_current_files.build_rows` 已消费绑定版本，不需要新会话记住文件路径。
旧快照和旧业务批次不自动升级；已有 bound 副本仍固定旧版，即使新增了更新的文件。
生成文件不表示平台已接受；未知/重复编码只隔离相应 SKU，整个商品是否可上传仍由原
制表完整性规则判断。不要只上传半件商品，也不要把未绑定新快照的老 controller 自动恢复。

## 关系与核查

原导出 → 字节不变的版本证据 → 新价格快照的 `sku_fact_source` → 精确制表行证据。
价格仍从 ERP 价格快照读取，平台导出价格仅作原始事实。商品主/别名关系是补足文件对应
的前提，不自动产生或写入商品别名。下游制单、工厂表、财务和大盘不使用此缓存改写业务。
