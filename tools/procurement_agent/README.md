# ERP 采购执行器

这是运行在 Windows 采购电脑上的独立 sidecar。它从 Panse ERP 领取询价任务，
调用本机平台驱动，并把“候选发现、发送成功、商家回复、转人工、失败”回写 ERP。

## 三种本机模式

- `dry_run`：默认。只预览搜索和待发任务，不加租约、不调用平台驱动、不发消息。
- `review`：领取任务并调用驱动；驱动必须在真正发送前展示内容并让人工确认。
- `live`：允许驱动直接发送。除配置文件外，还必须在本机设置
  `PROCUREMENT_AGENT_LIVE_ACK=I_UNDERSTAND_MESSAGES_WILL_BE_SENT`。

ERP 不能远程把执行器从 `dry_run` 切到 `live`。

## 安全约束

- token 只放环境变量 `PROCUREMENT_AGENT_TOKEN`，不写配置文件、不提交 Git。
- sidecar 不接收或保存淘宝、1688、拼多多、小红书的密码和 cookie。
- 同一家商家只有一个租约，避免两台电脑重复发送。
- 平台回执用 `external_message_id` 幂等，重复回调不会重复记账。
- 三次可重试失败后自动转人工；验证码、账号异常、加微信应由驱动立即返回
  `outcome=manual`。
- 每个渠道按 ERP 任务的每日上限领取，不提供绕过平台限制的功能。

## 启动

先把 `config.example.json` 复制到仓库外的本机配置目录，保持 `mode=dry_run`，
再运行：

```powershell
$env:PROCUREMENT_AGENT_TOKEN = '<与 ERP 一致的独立令牌>'
python -m tools.procurement_agent --config 'C:\Users\Jane\Desktop\AI\procurement-agent\config.json' --once
```

生产常驻时去掉 `--once`。真实驱动联调前，先连续运行 dry-run，确认 ERP 页面
显示的商家、渠道、话术与计划一致。

## 当前内置的 review 驱动

`browser_review_driver.mjs` 使用单独的采购 Chrome 档案，绝不读取个人 Chrome
档案。它能执行候选搜索并打开商品页面；发送阶段只展示审核页、复制 ERP 已确认话术，
由采购人员亲自在平台发送并点击“我已在平台实际发送”。它不会自动点击发送、下单或付款。

首次联调前分别人工登录（登录结果只留在本机采购 Chrome 档案）：

```powershell
node tools\procurement_agent\open_procurement_chrome.mjs taobao
node tools\procurement_agent\open_procurement_chrome.mjs 1688
node tools\procurement_agent\open_procurement_chrome.mjs pinduoduo
```

当前平台收件箱选择器尚未经过真实登录态验证，因此 `poll_replies` 保持安全空实现；
不得把“接口正常”误报为“商家回复已经自动回写”。完成小批量登录联调后才能逐个平台启用。

## 平台驱动 JSON 协议

sidecar 不经 shell 调用驱动。驱动从 stdin 读取一个 JSON 对象，向 stdout 输出一个
JSON 对象。

候选搜索时输入的 `operation` 为 `discover`。找到候选时返回：

```json
{
  "outcome": "found",
  "merchant_name": "商家名称",
  "merchant_external_id": "平台店铺ID",
  "merchant_url": "店铺链接",
  "product_url": "商品链接",
  "candidate_score": 82,
  "candidate_reason": "规格匹配且可批量定制",
  "candidate_snapshot": {
    "title": "商品标题",
    "price_text": "页面展示价",
    "location": "发货地"
  }
}
```

候选快照只允许商品展示字段；Cookie、令牌和浏览器存储即使由驱动误传也会在 API
入口被丢弃。

发送成功：

```json
{
  "outcome": "sent",
  "external_message_id": "平台消息ID",
  "external_thread_id": "会话ID",
  "sent_content": "实际发送内容"
}
```

需要人工：

```json
{"outcome": "manual", "reason": "出现验证码或商家要求加微信"}
```

可重试失败：

```json
{"outcome": "failed", "reason": "窗口暂时未找到", "retryable": true}
```

轮询回复时输入的 `operation` 为 `poll_replies`，返回
`{"replies": [...]}`。每条回复必须包含 `inquiry_id`、
`external_message_id`、`content`；报价字段可选。
