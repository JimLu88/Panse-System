# 本场失败普通SKU：既有单品立减原位修正

## 范围与实际接入边界

用户本轮“好的，你继续”由03转交，承接31件官方失败报告的普通小额修正。只允许秋季 `49557/49560/3538210379`、9月16日20:00至27日23:59:59、既有优惠 `144956016253`、失败报名claim `f0b16a1280f64514ba8d64dd64cc53db` 内有具体券后价失败原文的普通SKU。授权及报告文件hash固定在 `receipts/campaign-autumn-discount-amend-authority-20260911.json`。

单条真实立减金额变化绝对值不超过2元；新最终价仍须满足本场大促目标正负2元容差。报名价保持ERPdaily，定制不进入本入口。不得新建优惠、重新上传整批成功记录、撤回/取消或修改无关行。

旧 `campaign_discount_correction_service` 是计划7另一活动固定4条SKU补入，非本场可用原位入口，不能改它的硬编码去重放。新增的是**共享本地状态与证据入口**，不提供/虚构淘宝接口或自动浏览器选择器。03必须先确认现有平台支持仅修改指定行、原优惠和窗口不变的入口；如果只能整批替换/撤回新建，不使用本入口冒充完成。

## 最短操作

1. 正常把本轮报名官方失败终态record入共享库；不能猜批号。03已规范化的失败SKU与旧/新金额转成下面两个JSON。它们是当前报告的本地格式转换，不是再下载/预检。
2. 03在原优惠编辑界面一次读取精确目标SKU旧金额，保存带原始页面/返回证据的 `before.json`，立即claim：

   `python scripts/campaign_discount_amend.py claim --request request.json --before before.json`

   返回精确原优惠、窗口、旧/新金额payload。此时已持久记录unknown；同一失败claim/SKU只能占位一次，重复请求、不同文件名或并发任务不能解锁。成功/未知报名商品不能修改。
3. 03只用当前原位编辑能力执行一次payload，在同一编辑现场再次确认旧值未变。不能转成新建/整批导入。写后读取实际新金额与明确官方保存结果，保存 `after.json` 并record：

   `python scripts/campaign_discount_amend.py record --claim <返回ID> --receipt after.json`

   仅确认success且读到精确新金额的行进入金额覆盖层；failed且确认旧金额保留旧值；缺行、超时或异常状态保持unknown，不自动重试。部分成功可分回执补齐，但不能把已终态改成另一状态。
4. 使用原报名生成器重新生成未成功范围（不下载新模板）。生成、validated_body、claim自动读同一覆盖层，计算真实新金额；旧包因金额证据变化拒绝继续。原历史成功文件不修改、不伪造新成功批号。

`run_once(authority, request_path, read_before, edit_once, read_after)` 可供03现有运输能力接入；3个回调分别返回before文件、执行一次原位编辑、返回after文件。异常保留unknown，函数不连接浏览器、不自动重试。CAS是本地锁+刚读取的旧值，不声称淘宝具有原子CAS；单浏览器写入所有者和现场旧值比较仍必需。

## JSON合同

request.json：

```json
{
  "schema":"campaign_discount_amend_request_v1",
  "authorization_sha256":"3287fe2ff60c90d98c0597f14060e67cbfe0aa67556eb7c682f1c831f444bfb0",
  "campaign":"49557/49560/3538210379",
  "start":"2026-09-16 20:00:00",
  "end":"2026-09-27 23:59:59",
  "offer_id":"144956016253",
  "failed_signup_claim":"f0b16a1280f64514ba8d64dd64cc53db",
  "failure_rows":{"path":"<规范化报告JSON绝对路径>","sha256":"<该JSON实际hash>"},
  "rows":[{"item":"<精确商品>","sku":"<精确SKU>","old_deduct":"<已成功旧金额>","new_deduct":"<授权修正金额>"}]
}
```

规范化报告JSON包含 `schema=campaign_discount_failure_rows_v1`、`campaign`、`claim_id`、`source={path,sha256}`（原官方xlsx）和 `rows`。每行精确 `item,sku,status="failed",signup_price,issue_kind="coupon_final_price",platform_error`。`signup_price`须与失败上传包对应行一致。保留官方原文和真实行号可加字段；报告截断尾段不能编造SKU。程序校验报告指纹和失败登记，但不把任意人编写的JSON当独立平台事实；真实报告提取与物理SKU对应由03证据承担。

before/after通用结构：

```json
{
  "schema":"campaign_discount_amount_readback_v1",
  "campaign":"49557/49560/3538210379",
  "offer_id":"144956016253",
  "start":"2026-09-16 20:00:00",
  "end":"2026-09-27 23:59:59",
  "observed_at":"<实际观测ISO时间，带时区>",
  "source":{"path":"<原始页面或返回证据文件绝对路径>","sha256":"<实际hash>"},
  "rows":[{"item":"<商品>","sku":"<SKU>","deduct":"<实际读到的金额>"}]
}
```

before须精确覆盖本次修改行，5分钟内且非未来；这是写入CAS当次读值，不是报名预检。after另加 `claim_id,terminal=true,operation_reference`（真实保存回执/操作记录引用），每行加实际 `status=success/failed`，观测时间不得早于claim。不要求编造平台不存在的批号；原始证据要证明对应保存结果与实际金额。未确认的行不填成功。只有真实平台证据可以填充，不得用本地计划/测试输出代替。

覆盖层保存原回执、新回执及金额链；每次读取验证回执文件hash，未知行阻挡该商品继续报名，未修改行保持原金额。旧证据被修改、链断、未知或重叠优惠均拒绝。不声称对外部页面未涉及行做了全量平台回读。

## 验证与交付状态

`python -m unittest discover -s scripts/tests -p 'test_campaign*.py'`。覆盖旧值本地/平台不符、正负2/2.01、非法金额、失败/未知/成功保护、缺失/旧/未来回读、异常后不重放、部分成功、只覆盖精确行以及正式生成/提交消费新金额。测试使用临时SQLite与合成证据，不是实际平台修改。

本模块无需NAS部署；实际启用仍以03找到原位编辑能力、取得缺失报名批号和真实前后回读为准。02不接管浏览器。
