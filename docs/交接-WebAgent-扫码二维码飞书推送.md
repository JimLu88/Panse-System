# 交接：Web-Agent 暴露支付宝登录二维码 → ERP 飞书推送（主力号流水恢复）

> 目标读者：**Web-Agent**（浏览器自动取数进程，跑在 `192.168.31.91:8500` 那台 Windows 机）的维护者。
> 本文只描述 **Web-Agent 侧要新增什么**；ERP 侧对接由畔色 ERP 仓库负责（见文末"ERP 侧契约"，等你这边接口就绪即可打通）。
> 背景日期：2026-07-03。

---

## 1. 为什么要做

- **企业号（9a）**走支付宝官方 API，永不扫码，一直自动最新。
- **主力号**走浏览器自动化，**每次拉流水都要本人扫码登录**。目前 ERP 编排里 `alipay_main` 任务被**主动跳过**，原因记为：*"支付宝主力号流水：每次需本人扫码，**待飞书推二维码方案**"*。
- 结果：**主力号流水停在 2026-05-31，6 月起再没拉过**。这直接导致 ERP 里找不到 6 月的工厂货款流水（例：4/5 月工厂账单那笔 ¥92,016 回款、以及后续销账都卡在这）。
- 用户拍板方案：**二维码由系统（ERP）推送给用户扫**，不用用户守在 Agent 那台机屏幕前。但 ERP 要能推，**前提是 Web-Agent 把二维码图片吐出来**——目前没有这个能力（`/api/qrcode`、`/api/tasks/alipay_main/qrcode`、`/api/screenshot` 均 404）。

**所以本次要你做的就一件事：让 Web-Agent 在跑主力号登录时，把二维码图片通过接口暴露出来，并能查询"扫码是否成功"。**

---

## 2. 现状（Web-Agent 已有的，可复用）

从 ERP 侧探测到的 `alipay_main` 任务定义：

```json
{
  "id": "alipay_main",
  "platform": "alipay",
  "title": "导入支付宝流水(主力号)",
  "cadence": "monthly",
  "login_url": "https://b.alipay.com/page/home",
  "success_url_contains": "b.alipay.com",
  "inputs": {"date_from": "月初 yyyy-mm-01", "date_to": "月末 yyyy-mm-dd"},
  "output_subdir": "alipay/主力",
  "account": "alipay_main",
  "note": "主力支付宝账户当月流水，需重新扫码登录"
}
```

已有的 HTTP 接口（ERP 已在用）：
- `GET  /api/tasks` — 任务清单（含 `has_session` 登录态）
- `POST /api/tasks/{id}/run` — 触发任务，返回 `{job: <id>}`
- `GET  /api/jobs/{job_id}` — 查 job 状态
- `GET  /api/settings` — 配置（含 `headless`、`agent_use_vision` 等；说明 Agent **本来就会截图**——给视觉模型用——所以截二维码不是新能力，只是没暴露）

登录页 `b.alipay.com` 未登录时会出**扫码登录二维码**（通常是 `<img>` 或 `<canvas>`）。

---

## 3. 要新增的接口（二选一，推荐方案 A）

### 方案 A（推荐）：复用 run/job，让 job 状态带二维码

**改动最小**，不加新路由，只扩展现有 run/job 语义：

1. `POST /api/tasks/alipay_main/run`，body 支持 `{"variables": {...}, "wait_scan": true}`。
   收到 `wait_scan=true` 时：打开浏览器 → 导航 `login_url` → **若命中二维码登录页**，不阻塞返回，job 进入 `awaiting_scan` 状态并把二维码截出来。
2. `GET /api/jobs/{job_id}` 在 `awaiting_scan` 阶段，返回体新增字段：

```jsonc
{
  "job": "job_xxx",
  "status": "awaiting_scan",          // 新增状态: 正在等你扫码
  "qr_image_base64": "iVBORw0KGgo...", // 新增: 二维码 PNG 的 base64 (不含 data: 前缀)
  "qr_expires_at": "2026-07-03T15:40:00+08:00", // 可选: 二维码过期时间, 便于 ERP 判断要不要重取
  "message": "请用支付宝App扫码登录主力号"
}
```
3. 用户扫码 + App 确认后，浏览器自动跳转（URL 命中 `success_url_contains`），job 从 `awaiting_scan` → 继续跑导流水 → 最终 `status: "done"`（现有逻辑不变，产物照旧落 `output/alipay/主力`）。
4. 若二维码过期/超时未扫，job 置 `status: "error"`（或 `expired`），ERP 会据此决定重取二维码。

> ERP 侧只需：`run(alipay_main, {wait_scan:true})` → 轮询 `get_job` → 见到 `qr_image_base64` 就推飞书 → 继续轮询直到 `done`。

### 方案 B（备选）：独立二维码接口

如果不想动 run/job，加一个专用端点：
- `POST /api/tasks/alipay_main/qrcode` → 启动登录、截二维码，返回 `{ok, qr_image_base64, scan_token, expires_at}`。
- `GET  /api/tasks/alipay_main/scan-status?scan_token=xxx` → 返回 `{ok, status: "awaiting_scan"|"scanned"|"logged_in"|"expired"}`。
- 登录成功后（`logged_in`），再由 ERP 正常 `run(alipay_main)` 拉流水（此时 session 已就绪）。

任选其一，**契约字段名保持和上面一致**即可，ERP 侧照着接。

---

## 4. 二维码怎么截（实现提示）

Agent 已有浏览器（Playwright/Selenium 之类）+ 截图能力，按你现有栈实现：

1. 导航到 `https://b.alipay.com/page/home`（或它跳转到的登录页）。
2. **等二维码元素出现**。支付宝登录二维码常见形态：
   - `<img>` 二维码：直接 `element.screenshot()` 或读 `src`（可能是 `data:image/...` 直接就是 base64）。
   - `<canvas>` 二维码：`canvas.toDataURL()` 拿 base64，或对该元素截图。
   - 兜底：截**二维码所在区域**（定位到二维码容器 div 再 element screenshot）。选择器需你在真实页面上取（登录页可能在 iframe 里，注意切 frame）。
3. 把截到的 PNG 转 base64（不带 `data:image/png;base64,` 前缀，或带也行，ERP 会兼容处理），放进 job 状态 / 接口返回。
4. **判断"扫码成功"**：轮询当前 URL 是否命中 `success_url_contains`，或关键 cookie/登录态出现。成功即推进任务。
5. 二维码有时效（支付宝一般几分钟刷新），建议：过期后自动重新截图并更新 `qr_image_base64` + `qr_expires_at`，让 ERP 能推新码。

**注意**：headless 模式下支付宝有时对无头浏览器有风控。若扫码页在 headless 下不稳定，可给此任务单独用 headful（有头）或加反检测；这属于你这边浏览器栈的调优。

---

## 5. ERP 侧契约（我们这边会怎么用，供你对齐字段）

等你上面接口就绪，ERP 会新增（**畔色 ERP 仓库，我负责**）：

1. `web_agent_service` 加 `alipay_main_qr(db)`：调你的接口，拿 `qr_image_base64`。
2. 编排 `agent_ingest_service`：检测到 `web_agent_pending_scan` 含 `bal_alipay_main`/`alipay_main` 时 →
   触发 `run(alipay_main, {wait_scan:true})` → 轮询拿二维码 →
   用 `feishu_client.upload_image` + `send_image` **把二维码推到用户飞书** + 一条文字"请扫码登录主力号，X 分钟内有效" →
   继续轮询 job 到 `done` → 落库 6 月起主力号流水。
3. 二维码过期就重取重推；`done` 后发一条"主力号流水已更新到 X 月"。

**你只要保证返回里有 `qr_image_base64`（PNG base64）+ 一个能查"扫没扫成功/最终 done"的状态**，其余 ERP 全包。

---

## 6. 联调 / 验收清单

- [ ] `run(alipay_main, {wait_scan:true})` 后，`get_job` 能在几秒内返回 `status=awaiting_scan` + 非空 `qr_image_base64`。
- [ ] 把 `qr_image_base64` 存成 png 打开，**能被支付宝 App 扫**（清晰、完整、非整页截图）。
- [ ] 手机扫 + 确认后，job 自动从 `awaiting_scan` → `done`，产物落 `output/alipay/主力/`。
- [ ] 二维码过期场景：超时 job 置 `error`/`expired`，或自动刷新 `qr_image_base64`。
- [ ] 全程 Web-Agent 在线时 ERP 从 `http://192.168.31.91:8500` 调得通（token 已配）。

---

## 7. 临时替代（这套没上线前）

在你实现二维码接口前，ERP 可以**直接触发** `run(alipay_main)`，二维码会弹在 **192.168.31.91 那台机的浏览器/屏幕上**，人到那台机扫一次即可把当月流水拉进来（应急用，尤其先把 6 月主力号流水弄进来解 ¥92,016 那笔）。

---

## 8. 相关位置（ERP 侧，供参考对齐）

- `backend/app/services/web_agent_service.py` — ERP 调 Web-Agent 的 HTTP 客户端（`run_task`/`get_job`/`wait_job`）。
- `backend/app/services/agent_ingest_service.py` — 编排；`web_agent_pending_scan`、`web_agent_orch_state.skipped` 里记着 `alipay_main` 被跳过的原因。
- `backend/app/services/feishu_client.py` — `upload_image` / `send_image`（ERP 推二维码用）。
- `backend/app/services/feishu_bot_service.py` — 飞书机器人（已能推卡片/图片）。

有字段/契约要调整，改这份文档同步即可。
