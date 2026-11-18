# 物流发货登记与紧急订单

订单看板的“紧急订单”显示内部目标、原交期及原因。每天北京时间 09:10 将当前待跟进单合并到 ERP 通知中心。近期交期沿用 11 天；不修改平台或订单原截止日期。备注需有明确起算依据，送达承诺需要核对运输时间。渠道清单讨论期间不向飞书群广播这份汇总。

同事在登记群 @Panse System，发一条图文，附工厂下单原图，正文为 `发货登记 整单单包已发 物流公司 运单号`。物流公司填写淘宝承运商名称；该指令代表同事已确认整单单包实际交运。正常回执回复原消息。“订单看板 → 发货登记”可查最近 200 条和待核原因。转发压缩图可用 OCR 提取工厂号与子单号供核对；原图才进入自动提交。多包、多件、有发货条件、退款、图片不清等在 ERP 交给同事处理。

登记群读取 ERP 设置 `feishu_shipping_intake_chat_id`，未单独配置时用明确设置的 `feishu_alert_chat_id`。只接受经过飞书验证的用户事件。测试和未启用发货期间仅登记。真正向淘宝提交时要求 `feishu_shipping_operator_ids` 中有当前发送人 open_id，以逗号分隔。

淘宝适配器使用卖家授权的 TOP 应用。启用前在受控运行环境配置 PANSE_TOP_APP_KEY、PANSE_TOP_APP_SECRET、PANSE_TOP_SELLER_SESSION、PANSE_TOP_SELLER_NICK、PANSE_TOP_ERP_SHOP，并核实实际店铺对 trade.fullinfo.get、logistics.companies.get、logistics.offline.send、logistics.orders.detail.get 的权限。PANSE_TOP_SHIPPING_ENABLED=1 启用；默认关闭。凭据由运维通过原有密钥通道配置。关闭期间收到的登记不会事后批量补发。

仅整单单件单包执行一次。先核对本地单、真实平台卖家与子单、退款状态和承运商，再持久占用主单和运单并提交。平台未返回或状态未知时只读核对；仅匹配实际平台物流记录才显示已确认。ERP订单状态仍按官方报表更新。
