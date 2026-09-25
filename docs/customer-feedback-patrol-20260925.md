# 店铺口碑巡检

用户于2026-09-25要求每周两次检查千牛买家中差评和问大家新增回答，负面通过ERP飞书提醒群通知。

## 业务边界

- 固定来源：千牛 `/comment-manage/list/rateWait4PC`、`/comment-manage/ask-all`。
- 中差评使用官方 `rate=0/-1` 筛选和全分页。信用分不计分不等于中评，不按信用分猜等级。
- 问大家扫描所有问题，不以提问日期截断旧问题的新回答；逐问题读取“已展示回答”及其全部分页。卖家、折叠、投诉专用标签不在公开回答范围内。
- Web-Agent完成读取及持久化，AI只对新增/修改回答做一次批量语义判读；不逐页指挥浏览器。保留原文、问题、商品和稳定回答ID；否定、讽刺、混合反馈要结合上下文，无法可靠判断标“待人工判断”，不编造事实。
- 初次扫描的已有负面明确标历史基线，不称为新差评。主评和追评一起展示；已回复不等于问题已解决。
- 固定程序保存内容修订、基线、待发/已发/未知回执。无变化静默。通知最多一天一份、每份20条、24KB正文预算；溢出保留。23:00—07:00不发普通通知。
- 本功能不触发订单、库存、财务、工厂图表、退款、客服回复或评价素材生产；不属于独立评价素材项目。

## 部署及巡检入口

Web-Agent `scripts/feedback_patrol.py capture` → 程序输出新增回答待判读文件 →
以同一 snapshot_sha256 提交 `{answers:[{revision,sentiment,reason}]}` →
`scripts/feedback_patrol.py finish --capture ... --assessment ...`。
无新增回答时不传assessment。state保存在权威Web-Agent `data/feedback_patrol/state.sqlite3`。

ERP提供`GET /api/web-agent/feedback-notify-capabilities`和`POST /api/web-agent/feedback-notify`，
严格要求飞书双群模式的提醒群，发送前持久占位。相同内容不可重复发送；未知结果人工核对，禁止自动重试。
真实飞书message_id与本机运行结果分别保存。实际验收写入项目outputs，不在技术文档中假定发送成功。

默认每周二、周五北京时间10:00，由当前任务唯一heartbeat执行；不得另建重复OS/NAS调度。
登录失效、平台结构变更、分页不完整时不写完整检查点，不出“无负面”的结论。
同一阻塞只提醒一次，定时下次仅有限重读，禁止登录绕过、循环重启或自动改造业务。
