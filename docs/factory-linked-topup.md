# 实物主单与尺寸补差单

适用于唯一实物子单、购买量1，另有唯一尺寸补差子单的情况。先由业务执行者核对用户指示，在现有账务入口把费用行标为 `same_order_topup` 并关联主订单；写好主单生产备注和定制标记，保留通知后发货限制。

在正式 API 容器的现有数据库会话中调用：

```python
from app.services import factory_linked_topup_service as linked

args = dict(main_order_no=main_order_no, topup_order_no=topup_order_no,
            dimensions_mm={"length": 2000, "depth": 900, "height": 750})
checked = linked.preview(db, **args)
# 业务执行者核对 checked 的订单、SKU、数量、金额、尺寸及既有账务关系。
linked.confirm(db, **args, expected_identity_sha256=checked["identity_sha256"],
               actor=actor, evidence_ref=evidence_ref)
```

尺寸示例不能用于其他订单。`preview` 只读；`confirm` 只记录精确关联证据，不编号、不发图、不修改账务或平台原备注。之后仍走现有选单出图、看图及 `complete_selected_order_delivery`，由业务执行者发送与核对。

主单使用本次明确尺寸，标准SKU图注明仅作结构材质参考；补差单不独立生产。任一订单的SKU、数量、金额、退款、备注、客户资料、数量确认或账务关系变化，原绑定失效，不回退标准尺寸。待核对主单进入推送审计缺项。已发送/未知回执不允许重新确认以重发，旧图存档和回执保留。

本入口当前只支持一件、两笔订单，不自动猜测多订单对应关系，不修改原SKU尺寸、商品价格、采购账单、库存或发货状态。上线验收与实际发送由不同回执记录。
