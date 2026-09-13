# 长期报名失败与精确优惠时段接续

本入口修复时间上下文缺口，不新增价格规则。长期超级立减报名失败可以关联用户指定的日常优惠段，但不得重写旧 controller、bundle、报告、无效果修正或成功/未知占位。

`scripts/campaign_segment_repair.py --request <JSON> --output <新目录>` 仅准备；加 `--claim` 生成一次性凭证，由03调用原固定 `discount_amend` Web Agent 任务。维护测试不等于实际保存/报名。

请求字段：`schema=campaign_segment_repair_v1`、精确 `campaign/shop_name/start/end/offer_id`、`readback={path,sha256}`、`failures=[{claim_id,report:{path,sha256}}]`。请求不接受新金额。程序使用原失败 claim、原快照价格版本、报告原始约束和本段已读回金额重新计算，保持 ERP 日常报名价与固定中促目标累计±2元。只支持10%中促普通 SKU，小额无实际改变、定制、未知/成功商品及其他场次拒绝。

原优惠须已有真实导入文件/官方回执登记，读回金额和精确时段一致。不得将五条读回当全量当前优惠状态。新 claim 在写前重新校验来源、原失败登记及同段金额；已派发/未知不能重建或解锁。同失败/时段/修改即使换输出文件名也不生成第二次派发。

9月14日至16日案例本地核验：鎏金6228006543290立减3101.83→3101.93；样块四普通SKU5.98→5.99。只代表本地可行修正，正式保存、回读、失败商品完整SKU补报、淘宝终态另记。9月28日至30日和暂缓升降桌不在本次范围。

测试：`python -m pytest scripts/tests/test_campaign_segment_repair.py scripts/tests/test_campaign_continuous_transport.py -q`。覆盖金额重算、窗口/店铺/优惠错配、源文件变更、成功未知保护、普通日常价、定制拒绝、2元边界、原报告不变、幂等及重放保护。
