# 飞书双向同步 — 表绑定映射留存

所有飞书表都在同一个多维表格 (Bitable) 内。

- **Wiki 节点 token**: `NpWzwIcLBilnIlk0B2sc5ETInZc`
- 配好凭证后, 用此 token 在「飞书双向同步」页一键导入即可解析为 App Token。

权威映射源码: `backend/app/services/feishu_preset.py` (本文档为其人类可读副本)。

## 表清单 (24 张)

| 飞书表名 | Table ID | ERP system_table | 主键字段 | 同步方向 | 状态 |
| --- | --- | --- | --- | --- | --- |
| 产品表 | tbleu3HqLCFXMnYw | products | code | 双向 | 已支持 |
| 定价表 | tbl7IjyyxTTmDKJz | pricing_sku | sku_code | 双向 | 已支持 |
| BOM表 | tblOtUUUOT8PsuP9 | bom_lines | sync_key | 仅入 | 已支持 |
| 物料价格 | tbl2p6mBkalDg70O | materials | code | 双向 | 已支持 |
| 成品库存 | tblRsLFDXvuKE2CB | product_inventory | sync_key | 仅入 | 已支持 |
| 配件库存 | tblwQ2yL1rzGzDQh | part_inventory | sync_key | 仅入 | 已支持 |
| 销售订单 | tblEue0AXVJLPda4 | orders | order_no | 双向 | 已支持 |
| 工厂下单 | tblTn8Kb8yQCT39U | factory_orders | factory_order_no | 仅入 | 已支持 |
| 工厂对账 | tblVDWBjF6WZiPKy | factory_reconciliations | sync_key | 仅入 | 已支持 |
| 支付宝流水-企业号 | tblIJO5UipqPnpmK | alipay_flows | transaction_no | 仅入 | 已支持 |
| 支付宝流水-个体户私账 | tbl79NjIFcayl4eN | alipay_flows | transaction_no | 仅入 | 已支持 |
| 支付宝流水-爱群 | tblUPYpeREl93yIz | alipay_flows | transaction_no | 仅入 | 已支持 |
| 支付宝流水-佳宝 | tblIFStV63UPmFAl | alipay_flows | transaction_no | 仅入 | 已支持 |
| 支付宝流水-主力 | tbleXlRHNqHVqtI4 | alipay_flows | transaction_no | 仅入 | 已支持 |
| 账户余额 | tblrUiLJOc5d3Wm0 | account_balances | sync_key | 仅入 | 已支持 |
| 木材损耗 | tblvLARSHPdlmpOV | wood_losses | sync_key | 仅入 | 已支持 |
| 样品 | tbl0jwfGypXEi2xR | samples | sample_no | 仅入 | 已支持 |
| 品牌营销 | tblraKjamWiLubQx | brand_marketing | sync_key | 仅入 | 已支持 |
| 推广记录 | tblJ1sgVxmk5JjBZ | promotion_flows | sync_key | 仅入 | 已支持 |
| 日常经营 | tblvyqyNBj1er26J | daily_operations | sync_key | 仅入 | 已支持 |
| 人员外包 | tblmmRAfnySumzq0 | outsourcing_expenses | sync_key | 仅入 | 已支持 |
| 售后 | tbldwJIwYhXBPmWW | after_sales | platform_order_no | 仅入 | 已支持 |
| 客户 | tblP0NKUeoQR8Se9 | customers | matching_key | 双向 | 已支持 |
| 订单细节 | tblYLdjivHwpu5ea | — | — | — | 暂未支持-缺模型 |

> 5 个支付宝流水表都同步到 `alipay_flows` (靠 `account` / 交易号区分, 方向仅入)。
> 这正是放宽 `FeishuTableBinding` 唯一性 (改为 `(system_table, feishu_table_id)` 复合唯一) 的原因。

## 如何使用

1. 在「飞书双向同步」页配好飞书应用凭证 (App ID / App Secret) 并测试连接通过。
2. 点「一键导入预设(23表)」, 填入 wiki token (默认已填 `NpWzwIcLBilnIlk0B2sc5ETInZc`)。
   - 默认 **不启用**, 先建好全部绑定。勾选「立即启用」可在导入时即开启 (不建议, 应先核对字段)。
3. 系统自动解析 wiki token 为 App Token, 并创建全部绑定 (含 field_mapping)。
4. 逐个绑定用「查询飞书字段」核对实际列名, 修正各表的 `field_mapping` (系统字段 → 飞书列名),
   确认无误后再把对应绑定「启用」。
5. 启用后系统每 30 分钟自动同步; 也可手动「立即同步」。两端都改同一条记录会进入冲突待裁决。

> 注: `field_mapping` 中的飞书列名为中文最佳猜测, 必须与飞书实际列名完全一致才能同步成功,
> 因此「核对字段」这一步不能省。
