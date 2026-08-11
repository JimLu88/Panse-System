# Panse-Web-Agent 交接 & 并入主系统(Panse-System)总方案

> 这是 **Panse-Web-Agent** 的全量交接文档,给 Panse-System 会话照着并入。
> 配套细节文档:同目录 `发货报表密码解密_对接方案.md`(发货报表解密、扫码强提醒、支付宝 API 接口名,本文引用它,不重复)。
> 更新:2026-06-12。

---

## 0. 这是什么 / 在哪

- **Panse-Web-Agent**(`D:\AI\Panse-Web-Agent`):独立的**浏览器自动化取数系统**,FastAPI 跑在 **:8500**。
- 职责:登录淘宝/支付宝/万师傅等平台 → 把数据**导出成文件或截图** → 喂给 ERP(Panse-System)对账/入库。
- **隔离**:独立进程/端口/浏览器档案,**不碰** AI 蜂群(`D:\AI`)、不碰 ERP 进程。
- 启动:`pythonw -m uvicorn app.main:app --host 127.0.0.1 --port 8500`(用 `pythonw` 无控制台窗口)。

---

## 1. 当前取数能力 · 逐项状态(2026-06-12)

| 流程 | 取法 | 状态 | 人工? |
|---|---|---|---|
| 淘宝 订单报表 | 人录→脚本回放(¥0) | ✅ 通过 | 否(登录态在即可) |
| 淘宝 宝贝销售明细 | 同上 | ✅ 通过 | 否 |
| 淘宝 发货报表 | 同上;文件**加密**(口令走飞书,见 §5) | ✅ 下载通过,解密待 Panse | 口令转发飞书一次 |
| 余额·淘宝聚合 | 直连 URL 整页截图 | ✅ 真数字 | 否 |
| 余额·推广(万相台) | 同上(同页) | ✅ 真数字 | 否 |
| 余额·万师傅 | 同上(走淘宝 SSO) | ✅ 真数字 | 首次授权一次 |
| 余额·支付宝企业号 | 截图(cookie注入+小眼睛解码) | ✅ 真数字 | **支付宝会话短命,需勤刷新** |
| 余额·支付宝主力号 | 同上 | ✅ 真数字 | 同上 |
| 导出·聚合账户账单 | 回放下载(直连资金页) | ✅ 通过 | 否 |
| 导出·推广(万相台) | 回放下载(直连 `one.alimama.com/#!/account/detail`) | ✅ 通过(CSV) | 否 |
| 导出·万师傅安装账单 | 回放下载 | ✅ 通过(xlsx) | 否 |
| 导出·物流账单 | 未配置 | ⬜ 待定承运商入口 | — |
| **支付宝企业号 流水/余额** | **官方 API**(见 §4) | ⏳ 应用审核中(约1天)+待上线 | 否(API 无需扫码) |
| **支付宝主力号(个人号)流水** | **截图 + 扫码**(API 开不了) | ⚠️ 见 §6 | **每次/每天要扫码** |

> 淘宝侧 **一个登录覆盖 8 个任务**(三报表 + 三余额 + 聚合账单/推广/万师傅导出)。

---

## 2. 技术架构要点(并入前要懂)

- **录一次→回放(record-replay)**:`data/workflows/*.json` 是录好的工作流(多候选选择器:`#id`/`[name]`/`text="文字"`/css路径)。`deterministic.py` 回放,**¥0、无大模型**。
- **三层 tier**:`deterministic`(回放)→ `cloud`/`local`(LLM 驱动,目前**云端关、本地关**=纯回放,失败快速失败)。
- **持久 Chrome profile**(`data/profiles/<login_key>`):真实 Chrome(`channel=chrome`)+ 持久档案,登录态落盘可复用、反爬友好。
- **截图取数**(`capture=screenshot` 任务):直连 URL 整页截图;含 **session cookie 注入**(支付宝商家这类 session-only 站才不掉登录)、**小眼睛解码**(点开打码余额)、**关通知弹窗**。
- **反风控/仿人类**:真实 Chrome(非 headless)、`--disable-blink-features=AutomationControlled`、**stealth 注入**(抹 `navigator.webdriver`)、**步间随机停顿 0.7–2.3s**、低频、**同 profile 占用锁**(防一堆 about:blank)。
- **下载**:`context` 级捕获(新标签里触发的下载也能抓,如万相台);下载等待下限 120s。
- **异步+限流编排**(`orchestrator.py`,淘宝报表):触发导出 → 等生成 → 去已生成列表收取;两次导出间隔 600s(10分钟)。

关键文件:`app/engine/{deterministic,orchestrator,runner,alipay_api}.py`、`app/browser/session.py`、`app/recorder/recorder.py`、`app/tasks/{registry,definitions}.py`、`app/api/routes.py`、`app/web/templates/index.html`。

---

## 3. 凭据与安全

- **DPAPI 文件加密**(按当前 Windows 用户):登录态(`data/sessions/*.bin`)、API token、所有小机密(`data/vault/secret_*.bin`)。
- **支付宝 API 多账号**:`settings.json` 存 `alipay_accounts=[{id,name,app_id,bill_user_id,public_key}]`;**每账号应用私钥单独存 DPAPI(`alipay_priv_<id>`),永不入 settings 明文、永不回显**。
- 旧用 keyring(Windows 凭据管理器),因其 ~2560 字节上限放不下 RSA2 私钥,已全改 DPAPI。

---

## 4. 支付宝企业号 · 官方 API(优先,免扫码免风控)

详细接口/开通步骤见 `发货报表密码解密_对接方案.md` §6。要点:
- 余额:`alipay.data.bill.balance.query`(biz 传 `bill_user_id`=2088 UID;返回 `available_amount/total_amount/freeze_amount`)。
- 流水:`alipay.data.dataservice.bill.downloadurl.query`(`bill_type`=`signcustomer`资金账单/`trade`交易账单 + `bill_date`;返回下载 URL,30s 内下)。
- Web-Agent 已实现:`app/engine/alipay_api.py`(RSA2 签名、多账号 `call/query_balance/query_bill_download_url/download_bill`)+ 设置页多账号录入 + `/api/alipay/test`、`/api/alipay/test-bill` 自测端点。
- **状态**:企业号应用**审核中(约1天)**,审核通过 + **上线**后即可调通(此前调用返回「应用未上线」属正常)。
- **`spi.alipay.commerce.billdetail.query` 不要用**——那是线下收银终端(POS)的 SPI 回调接口,不是查流水的。

---

## 5. 发货报表密码解密(飞书抓口令 + 解密)

见 `发货报表密码解密_对接方案.md` §1–2。一句话:发货报表加密(OOXML,前8字节 `D0CF11E0`),口令经微信强提醒发用户 → 用户转发到飞书 → Panse webhook 抓口令存下 → 导入时 `msoffcrypto` 解密。

---

## 6. 个人账号(支付宝主力号)现状 · 必须接受的人工环节 ★用户重点★

主力号是**个人支付宝账号**,**用不了**商家级 API(余额/账单都是商家产品),所以:

1. **只能走浏览器**:登录 + 导出流水都在浏览器里做。
2. **每次(可能每天)都要扫码**:支付宝流水导出点「下载」会弹「扫描二维码验证身份」,**每次会话都要本人扫**,cookie 替不了(验证态短、跨会话失效)。**这是平台反欺诈,消灭不了。**
3. **用飞书提示扫码**(降低打扰):自动化跑到扫码弹窗时 → **截取二维码图片发飞书 + 加急提醒** → 用户在手机上对着飞书的图扫 → 流程继续。实现见 `发货报表密码解密_对接方案.md` §5(含 `im/v1/images`+`im/v1/messages`+`urgent_app/urgent_phone`)。
4. **余额**:主力号余额可用现有**截图方案**(已能出真数字),但**支付宝商家会话很短命**,要"用前刷新/扫完马上截"。

> 结论:个人号这条**做不到完全无人值守**,目标是"把人工降到最小 + 该扫码时第一时间飞书喊用户"。

---

## 7. 并入主系统(Panse-System)的规划 ★用户重点:接入不出问题★

### 7.1 集成形态
- Web-Agent 作为 **独立子服务**(:8500)运行,Panse-System 通过其 **HTTP API** 触发任务、取结果。**先不强行合进同一进程**(浏览器自动化重、易拖垮 ERP)。
- 未来若要合并:把 `app/engine`、`app/browser`、`app/tasks` 作为 Panse 的一个模块挂载,共享 Panse 的 secret/scheduler/通知通道。

### 7.2 数据交付(关键)
- 所有导出文件落 `D:\AI\Panse-Web-Agent\data\output\<日期>\<子目录>\`。
- **约定一个共享目录**(或让 Panse 导入服务直接读 Web-Agent 的 output),ERP 现有 `taobao_order_import.py` 等导入器**已能解析**淘宝多表/聚合账单等;新增的需补解析(如万相台 CSV、支付宝 API 账单)。
- 余额:截图 PNG / API 数字 → 写入 ERP 的账户余额表(账户名、金额、统计日期=录入当天)。

### 7.3 通知通道(复用 Panse 飞书机器人)
- 发货报表口令、个人号扫码二维码、人工卡点提醒 → **统一走 Panse 已有的飞书 bot**(`feishu_bot_service`/`feishu_webhook_service`)。Web-Agent 侧只产生"事件 + 图/文",发送在 Panse。

### 7.4 凭据统一
- 现在 Web-Agent 用自己的 DPAPI vault(独立)。并入后**可保持独立**(隔离更安全),或迁到 Panse 的 secret 管理;**私钥/登录态务必继续加密、不入库明文**。

### 7.5 调度
- Panse scheduler 按日触发 Web-Agent 的 `POST /api/tasks/{id}/run`(或一个"跑全部"编排)。注意淘宝报表**10 分钟限流**、支付宝**短会话**、个人号**要人工扫码**——调度要容忍"待人工/稍后重试"。

### 7.6 不冲突清单(接入不出问题的硬要求)
- **端口/进程隔离**:Web-Agent 独占 :8500,独立 `pythonw` 进程;不与 ERP/AI 蜂群抢资源。
- **浏览器档案独占锁**:同一登录档案同时只允许一个浏览器(已实现 `ProfileBusyError`);Panse 调度别并发触发同平台任务。
- **不碰 AI 蜂群**(`D:\AI`)。
- **大文件/缓存只放 D 盘**(用户硬规:C 盘禁写)。
- **失败要快速失败 + 标"待人工"**,不要无限重试卡死调度。
- **窗口**:服务用 `pythonw` 无窗口;但**截图/导出任务会弹真实 Chrome 几秒**(反风控故意),并入后若不想打扰,可评估无头(会增加被风控概率)。

### 7.7 人工 vs 自动 总览(让主系统知道哪些要人)
| 环节 | 自动? | 人工内容 |
|---|---|---|
| 淘宝各任务(报表/余额/导出) | ✅ 基本无人 | 登录态偶尔过期 → 重扫一次 |
| 发货报表解密 | 半自动 | 把微信口令转发飞书一次 |
| 支付宝企业号(API) | ✅ 无人(审核+上线后) | 一次性:建应用/配密钥/上线 |
| **支付宝主力号(个人号)流水** | ❌ 要人 | **每次/每天扫码**(飞书提示) |
| 支付宝余额(截图) | 半自动 | 会话短 → 用前刷新 |
| 物流账单 | ⬜ | 待定承运商入口 |

---

## 8. 待办(交接时未完成项)
1. **企业号 API**:等审核+上线 → 用 `/api/alipay/test`、`/test-bill` 验通 → 接入每日取数。
2. **支付宝流水(个人号)**:Web-Agent 侧补"检测扫码弹窗→截二维码→发飞书"逻辑;Panse 侧补飞书发图+加急。
3. **发货报表解密**:Panse 侧补 §5 的飞书抓口令 + msoffcrypto 解密。
4. **物流账单**:待用户给承运商入口 URL。
5. **万相台 CSV / 支付宝 API 账单** 的 ERP 导入解析(若 ERP 还没有)。
