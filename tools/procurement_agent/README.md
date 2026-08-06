# ERP 采购执行器

这是运行在 Windows 采购电脑上的独立 sidecar。它从 Panse ERP 领取询价任务，
调用本机平台驱动，并把“发送成功、商家回复、转人工、失败”回写 ERP。

## 三种本机模式

- `dry_run`：默认。只预览任务，不加租约、不调用平台驱动、不发消息。
- `review`：领取任务并调用驱动；驱动必须在真正发送前展示内容并让人工确认。
- `live`：允许驱动直接发送。除配置文件外，还必须在本机设置
  `PROCUREMENT_AGENT_LIVE_ACK=I_UNDERSTAND_MESSAGES_WILL_BE_SENT`。

ERP 不能远程把执行器从 `dry_run` 切到 `live`。

## 安全约束

- token 只放环境变量 `PROCUREMENT_AGENT_TOKEN`，不写配置文件、不提交 Git。
- sidecar 不接收或保存淘宝、1688、小红书的密码和 cookie。
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
python -m tools.procurement_agent --config 'D:\本机配置\procurement-agent.json' --once
```

生产常驻时去掉 `--once`。真实驱动联调前，先连续运行 dry-run，确认 ERP 页面
显示的商家、渠道、话术与计划一致。

## 平台驱动 JSON 协议

sidecar 不经 shell 调用驱动。驱动从 stdin 读取一个 JSON 对象，向 stdout 输出一个
JSON 对象。

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
