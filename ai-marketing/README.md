# AI Marketing System · Phase-1 MVP

> 畔色/孚格 家具品牌内容矩阵——内容生产流水线 + ASSIST 半自动分发 + 评论引流/养号/线索闭环。
> 设计稿见 [`../docs/ai-marketing/`](../docs/ai-marketing)。本目录是**可运行的 MVP 实现**。

## 这是什么

把设计稿的 Phase-1 跑通：

```
①选题 → ③生成(四层流水线) → ③.5审核(30秒工位) → ⑥ASSIST发布
                                          ⑤账号 ⑨养号 ⑧评论引流 ⑩线索 ⑦数据回收
```

- **零配置即跑**：默认 SQLite + 内置 mock LLM（生成家具风格文案），不用任何 API key 就能端到端演示。
- **可插拔真实模型**：配 `LLM_PROVIDER=anthropic` + `ANTHROPIC_API_KEY` 即用真模型。
- **人在环内**：评论引流和养号永不全自动（见设计稿铁律1）——系统找机会/排清单，发布由人点。
- **接 ERP**：线索成交回写 Panse-System 订单号的预留接口。

## 快速开始

```bash
cd ai-marketing
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python -m app.seed          # 初始化家具品牌种子数据(账号矩阵/违禁词/产品关键词)
uvicorn app.main:app --reload
```

打开 http://127.0.0.1:8000 看工作台，http://127.0.0.1:8000/docs 看 API。

**Docker 方式**（与 ERP 同习惯）：

```bash
cd ai-marketing
docker compose up -d --build    # 端口 8001，数据持久化在 ./data/
```

**测试**：`python -m pytest tests/ -q`（端到端 + 逻辑回归共 12 例）。

**鉴权**：`.env` 配 `API_TOKEN=xxx` 后所有 `/api/*` 需 `Authorization: Bearer xxx`（工作台会弹框让你输一次，存浏览器）。不配则免鉴权（内网）。

**看门狗**（与 ERP system_monitor 同模式）：每 60s 体检 DB/磁盘/内存/调度器心跳，写 `system_health_logs`；连续 3 次失败 → SIGTERM 自救 → Docker `restart: unless-stopped` 自动拉起（10 分钟冷却防重启风暴）。状态看工作台顶栏 🐶 或「数据大盘」页，API：`GET /api/watchdog`。环境变量 `WATCHDOG_ENABLED=0` 可关。

## 端到端走一遍（命令行）

```bash
# 1. 生成选题
curl -X POST http://127.0.0.1:8000/api/topics/generate -H 'content-type: application/json' \
  -d '{"category":"餐桌","count":3}'
# 2. 用选题生成草稿(四层流水线)
curl -X POST http://127.0.0.1:8000/api/drafts/generate -H 'content-type: application/json' \
  -d '{"topic_id":1,"account_id":1}'
# 3. 审核工位看体检报告
curl http://127.0.0.1:8000/api/review/1
# 4. 通过并排发布(ASSIST + 反共振)
curl -X POST http://127.0.0.1:8000/api/review/1/approve
curl -X POST http://127.0.0.1:8000/api/dispatch/schedule -H 'content-type: application/json' \
  -d '{"content_id":1,"account_ids":[1,2]}'
# 5. 养号清单
curl http://127.0.0.1:8000/api/nurture/1/today
# 6. 评论引流机会
curl -X POST http://127.0.0.1:8000/api/comments/scan
```

## 模块对应设计稿

| 代码 | 子系统 | 设计稿 |
|---|---|---|
| `services/topic_engine.py` | ① 选题(含长尾搜索词) | 01-topic-engine |
| `services/generator.py` | ③ 四层生成流水线 | 03-generator |
| `services/review.py` | ③.5 审核工位 | 03.5-review-station |
| `services/dispatcher.py` | ⑥ ASSIST + 反共振 | 06-dispatcher |
| `services/account_service.py` | ⑤ 账号+健康心跳 | 05-account-manager |
| `services/nurture.py` | ⑨ 养号 SOP | 09-account-nurturing |
| `services/comment_engine.py` | ⑧ 评论引流站 | 08-comment-engine |
| `services/lead_inbox.py` | ⑩ 线索收件箱 | 10-lead-inbox |
| `services/analytics.py` | ⑦ 真实感归因 | 07-analytics |
| `services/compliance.py` | 敏感词分级 | config-rule-center |
| `services/llm_router.py` | 多 LLM 路由 | llm-router |

## 状态

📝 设计稿 → 🚧 **MVP 开发中**（本目录）。当前为 Phase-1 单平台(小红书)+ASSIST 闭环骨架，逻辑可跑通，待接真实平台数据源与爬虫(挂载项 A)。
