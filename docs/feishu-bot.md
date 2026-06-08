# 飞书机器人 — 发图自动识别入库（长连接 WebSocket）

> 群里 @机器人 / 私聊发一张图 → 自动识别（订单表 / 订单图 / 供应商送货单）→ 不确定时发卡片让你点选 → 确认后入库。
> 采用**长连接(WebSocket)**接入：自建应用**免公网地址、免验签**，最适合内网/家用部署。
> 代码已搭好（`feishu_ws_service.py` + `feishu_bot_service.py`），**上线只差你的飞书凭证 + 开放平台几项配置**。

## 一、你要开的权限（开发配置 → 权限管理）

> ⚠️ 飞书改过几版权限名，**以你控制台显示的中文名为准**；下面是功能 + 对应 scope。

| 功能 | 权限（scope） |
|---|---|
| 接收单聊发来的消息 | `im:message.p2p_msg:readonly`（获取用户发给机器人的单聊消息） |
| 接收群里 @机器人 的消息 | `im:message.group_at_msg:readonly`（获取群组中@机器人消息） |
| **下载消息里的图片**（关键） | `im:resource`（获取与上传图片或文件资源） |
| 发消息 / 回卡片 / 更新卡片 | `im:message:send_as_bot`（以应用身份发送消息） |

加完权限要**创建版本并发布**才生效。

## 二、步骤

1. **建应用 + 开机器人**：[飞书开放平台](https://open.feishu.cn/) → 创建企业自建应用 → 拿 **App ID / App Secret** →「应用功能」开启**机器人**。
2. **加权限**：上表 4 项 → 保存 → 创建版本发布。
3. **事件订阅（选长连接）**：开放平台「事件与回调 → 事件订阅」→ 订阅方式选 **「使用长连接接收事件」**（不用填公网回调地址）→ 添加事件 **接收消息 `im.message.receive_v1`**。
   - 卡片按钮回调 `card.action.trigger` 走同一条长连接，**无需另配**。
4. **填凭证**：本系统 `管理 → 集成配置` 填 `feishu_app_id` / `feishu_app_secret`（加密落库）。
5. **配 OCR**：本系统 `管理 → AI 集成`，OCR 那档配 vision 模型（claude-opus / qwen-vl-max）。没配也不崩，会走"选类型"卡片。
6. **开机器人开关 + 重启**：给 api 加环境变量 **`ENABLE_FEISHU_BOT=1`**（`docker-compose.override.yml` 或 compose 的 `api.environment`），`docker compose up -d`。启动日志出现「飞书机器人长连接已启动」即成功。
7. **拉机器人进群** 或私聊它，发图测试。

## 三、流程（已实现）

1. 收到图片消息（`im.message.receive_v1`）→ `feishu_bot_service.on_message_event`：下载图 → `classify_image` 用 vision 判类型+置信度 → 暂存 `system_settings`。
2. 置信度 ≥ 0.75 → 发**确认卡片**；否则发**选类型卡片**（订单表/订单图/供应商送货单/取消）。
3. 点按钮（`card.action.trigger`）→ `feishu_ws_service._on_card`：
   - **3 秒内回一个 toast**「已收到，正在识别入库…」（满足飞书回调时限）。
   - 入库放**后台线程** `process_pick`：重新下载原图 → 按类型解析入库 → **patch 更新卡片**显示结果（飞书允许 30 分钟内延时更新）。
   - 订单表/订单图 → `vision_ocr_service.parse_qianniu_order` → 建 `orders`。
   - 供应商送货单 → `ocr_service.parse_delivery_note` 识别行项（**完整入库目前提示走 供应商→送货单 OCR**，待接 `delivery_storage`）。

## 四、已知 TODO（上线后可继续）

- **供应商送货单完整入库**：现在只识别+提示，接 `delivery_storage` + 供应商归属后一键入库。
- 凭证就绪后做一次端到端联调（发图 → 卡片 → 入库）。
- 可选：卡片改 v2(JSON 2.0) 视觉更佳；目前用 v1 卡片 + toast/patch，功能完整。

## 五、相关代码

- `backend/app/services/feishu_ws_service.py` — 长连接客户端（lark-oapi WS）+ 事件/卡片 handler + 启停。
- `backend/app/services/feishu_bot_service.py` — 识别/卡片/暂存/入库分发 + 异步 `process_pick`。
- `backend/app/services/feishu_client.py` — `download_message_resource` / `reply_card` / `patch_card`。
- `backend/app/main.py` — lifespan 里 `ENABLE_FEISHU_BOT=1` 才 `feishu_ws_service.start()`。
- 测试：`tests/test_feishu_bot_service.py`、`tests/test_feishu_ws_service.py`。

> 备注：仍保留了 webhook 路径（`/api/feishu/webhook` + `feishu_webhook_service._maybe_bot`）作为备用，长连接是推荐主路径。
