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
- ERP 租约不能单独保证平台不重复发送；租约过期不是“未发送”证据。
- 平台回执用 `external_message_id` 幂等，重复回调不会重复记账。
- review/live 必须配置仓库外的固定绝对路径 `send_journal_path`；缺失即拒绝运行。
  同一桌面的所有配置/进程必须共用一个账本，不按 agent_id 或新租约换账本。
- 调用发送驱动前先持久记录任务、商家和消息轮次；数据库写入失败时不调用驱动。
  不存正文、Cookie、令牌或完整 URL。OS 文件锁覆盖搜索、会话轮转及发送。
- 发送异常、超时、确认回调丢失、未知输出一律禁止自动重发；即使驱动声称
  `retryable=true` 也不接受。未决记录同时暂停后续发送、搜索与会话切换。
- 验证码、账号异常、加微信应由驱动返回 `outcome=manual`；该轮次仍保留并停机待核。
  搜索发现阶段与发送不同，既有搜索失败重试规则暂不改动。
- 每个渠道按 ERP 任务的每日上限领取，不提供绕过平台限制的功能。

这是 0.1.1 的本机保守止损层，不是完整自主询价系统。尚未实现 ERP 原子发送意图、
账号级全局名额、一次只读核对和安全解除隔离、跨机接管或 48 小时截止。旧客户端、
不同账本路径或人工操作不在此锁的保护内。不要删除账本/换路径/换 agent_id 来恢复，
不要将 SQLite 文件放网络盘；未决结果应保留到后续正式核对流程处理。当前不开放真实
供应商联调，P6 的同一批 10 家才做真实收发验收。

## 启动

先把 `config.example.json` 复制到仓库外的本机配置目录，保持 `mode=dry_run`，
再运行：

`dry_run` 可保持 `send_journal_path=null`。未来启用 review/live 前，应将它设为本机
私有目录下的绝对路径（例如 `C:/Users/Jane/Desktop/AI/procurement-agent/send-journal.sqlite3`），
并验证同桌面所有执行器共用同一路径。本次改造没有自动创建常驻进程或打开 live 开关。

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

0.1.1 不再把人工按钮确认伪装成平台回读，也不生成 `human-review-*` 平台消息 ID。
人工点“已发送”后转入待核对；超时同样不能说未发送。旧人工流程因此会保守停在待核对，
不再自动推进到追问；补齐真实回读前不将其宣传为可连续使用的采购流程。

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

发送结果不确定（禁止自动重发）：

```json
{"outcome": "unknown", "reason": "发送或回读超时", "retryable": false}
```

轮询回复时输入的 `operation` 为 `poll_replies`，返回
`{"replies": [...]}`。每条回复必须包含 `inquiry_id`、
`external_message_id`、`content`；报价字段可选。

完整落地范围与仍缺能力见 `docs/procurement-send-safety-20260926.md`。离线测试使用临时
SQLite 和 MockDriver，不代表平台权限、真实收件或平台消息回读已经验收。

## 0.1.2：48 小时批次持久协议（尚非可用自动采购）

`bounded_runtime.BoundedDispatcher` 复用同一 `SendJournal` 文件和跨进程锁，
通过独立 dispatch-v1 机器接口完成预留、短期许可、落盘、单次发送、回执补记、一次只读核对。
默认主循环没有启用它：现有 `ExternalCommandDriver` 和人工 review 驱动不能满足最终点击检查，
不允许用于新协议。不把“兼容方法存在”当作真实平台适配已完成。

- 发送许可用 Windows 当前用户 DPAPI 加密后落盘；没有明文回退。ERP 令牌仍只在环境变量中。
- API 要求既有采购专用令牌，并将 `X-Procurement-Executor` 与服务端明确登记的执行器匹配。
- `PROCUREMENT_DISPATCH_EXECUTOR_ID` 为空时拒绝整个新协议。
- `PROCUREMENT_DISPATCH_ENABLED` 默认不设；只有值为 `1` 才能申请预留/新许可。
  停新发送后仍允许原回执补记、未知状态归档与一次只读核对。**当前不要开启该开关**。
- 本机停止标志在准备前及最后点击前检查。服务端停止后最多已有 5 秒在途许可，
  不能宣称撤销能追回已交给平台的输入。
- 服务器确认丢失时保存平台回执，重启只重试补记；不再次调用发送或申请许可。
- 许可响应丢失/落盘失败时不点击，保守保留待人工核对；不自动释放未知名额。
- 原始聊天正文不进本机恢复表，只有标识摘要、加密短许可和最小回执；私有账本放仓库外。

详情：`docs/procurement-local-dispatch-20260926.md`。协议合同联调仅使用合成商家、
临时数据库及内存 HTTP 测试客户端；真实三平台驱动、自动收件、附件子意图、报价前五和部署仍待完成。
