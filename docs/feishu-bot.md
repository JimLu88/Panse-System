# 飞书机器人 — 发图自动识别入库

> 群里 @机器人 / 私聊发一张图 → 自动识别（订单表 / 订单图 / 供应商送货单）→ 不确定时发卡片让你点选 → 确认后入库。
> 代码已搭好（`feishu_bot_service.py`），**上线只差你的飞书自建应用凭证 + 开放平台几项配置**。

## 一、你要做的（飞书开放平台 + 本系统后台）

### 1. 建自建应用，拿凭证
- [飞书开放平台](https://open.feishu.cn/) → 创建企业自建应用 → 拿 **App ID / App Secret**。
- 本系统 `管理 → AI 集成 / 集成配置` 里填入（加密存 `system_settings`，无需重启）：
  - `feishu_app_id`、`feishu_app_secret`
  - （可选）`feishu_verification_token`、`feishu_encrypt_key`（开放平台「事件订阅」里若开了就填，校验来源/解密）

### 2. 开「机器人」能力 + 权限
- 应用功能 → 添加「机器人」。
- 权限管理开通：`im:message`（收发消息）、`im:resource`（下载消息里的图片）、`im:message:send_as_bot`。

### 3. 配「事件订阅」
- 请求地址填：`https://<你的公网域名>/api/feishu/webhook`
- 订阅事件：
  - `im.message.receive_v1`（接收消息——收图触发识别）
  - 卡片按钮回调（`card.action.trigger`，v2 卡片回传交互）会回调到同一地址，无需另配。
- 把机器人拉进要用的群（或直接私聊机器人）。

### 4. 配 OCR 视觉模型
- `管理 → AI 集成` 把 **OCR** 那档配成 vision 模型（`claude-opus` / `qwen-vl-max` 等）。
- 没配也不会崩：识别会返回"不确定"，直接发选类型卡片让你手点。

## 二、流程（已实现）

1. 收到图片消息 → `feishu_bot_service.on_message_event`：下载图 → `classify_image` 用 vision 判类型+置信度 → 暂存到 `system_settings`。
2. 置信度 ≥ 0.75 → 发**确认卡片**（识别为 X，确认入库？）；否则发**选类型卡片**（订单表/订单图/供应商送货单/取消）。
3. 点按钮 → `card.action.trigger` → `on_card_action`：重新下载原图、按选定类型解析入库 → 回结果卡片。
   - 订单表/订单图 → `vision_ocr_service.parse_qianniu_order` → 建 `orders`（新单插入、已存在跳过）。
   - 供应商送货单 → `ocr_service.parse_delivery_note` 识别行项；**完整入库目前提示走 供应商→送货单 OCR**（需归属供应商 + 原图归档，后续接上）。

## 三、已知 TODO（上线后可继续）

- **供应商送货单完整入库**：现在只识别+提示，接 `delivery_storage` + 供应商归属后可一键入库。
- **异步 ack**：webhook 目前同步处理（下载+AI 可能 >3s，飞书会重试）。生产建议先回 200、后台线程处理 + 用 `event_id` 去重。
- **卡片就地更新**：现在用「回复新消息」反馈结果；可改成更新原卡片（callback 返回 toast/card）。
- 凭证就绪后做一次端到端联调（发图 → 卡片 → 入库）。

## 四、相关代码

- `backend/app/services/feishu_bot_service.py` — 机器人主逻辑（分类/卡片/暂存/入库分发）。
- `backend/app/services/feishu_client.py` — `download_message_resource` / `reply_card` / `reply_text`。
- `backend/app/services/feishu_webhook_service.py` — `_maybe_bot` 路由消息/卡片事件。
- `backend/app/api/feishu.py` — `/api/feishu/webhook` 回调入口。
- 测试：`backend/tests/test_feishu_bot_service.py`。
