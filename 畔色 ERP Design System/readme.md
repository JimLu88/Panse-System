# 畔色 ERP 设计系统 (畔色孚格 ERP Design System)

为 **畔色孚格 ERP** —— 一套家具电商内部管理系统 —— 打造的定制设计系统。覆盖产品/物料/BOM、库存、订单、对账、报价、营销/售后、AI 助手、供应商对账 OCR 等全流程模块。设计目标：**可读性优先、Web + 移动端双适配、易用高效**，并以现代化的「畔色」水岸青为品牌主色。

> **品牌名释义**：「畔色」= 水岸之色。界面主色采用 **Google 经典蓝 `#1A73E8`**（干净、产品级、可信赖），呼应工具型 ERP 的专业气质，满足长时间注视的可读性需求。

---

## 来源 (Sources)

本设计系统基于真实代码库构建，**视觉与交互以代码为准**（非凭截图臆测）：

- **GitHub 仓库**：`https://github.com/JimLu88/Panse-System` （分支 `main`）
  - 前端：`frontend/` — React 18 + TypeScript + Vite
  - UI 框架：**Ant Design v5** (`antd ^5.16`) + `@ant-design/icons`
  - 图表：`echarts` + `echarts-for-react`
  - 数据：`@tanstack/react-query`、`axios`、`dayjs`、`react-router-dom`
  - 后端：`backend/` — FastAPI + SQLAlchemy + Alembic（Python）
- 关键参考文件：`frontend/src/main.tsx`（AntD 主题 token）、`frontend/src/styles/global.css`（字体基线）、`frontend/src/App.tsx`（顶栏 + 分组导航）、`frontend/src/pages/DashboardPage.tsx`（卡片/统计/图表风格）、`frontend/src/pages/OrdersPage.tsx`、`LoginPage.tsx`。

> 阅读者若无仓库访问权限，可凭以上信息向项目所有者申请。

### 与原系统的关系（演进，而非照搬）
原系统采用「苹果排版规范」主题：系统字体、正文 15px、主色 **苹果蓝 `#0071e3`**，dashboard 另有一套 slate 中性 + 紫/靛/天蓝强调的「Mosaic」卡片风格（圆角 16、柔和阴影）。本设计系统**保留**其干净、留白、柔和阴影的现代卡片气质与 slate 中性体系，**升级**两点（应用户要求）：
1. 主色由苹果蓝/紫，改为 **Google 经典蓝**（`#1A73E8`），干净、产品级、与 Material 体系一致。
2. 字体由系统苹方/SF，改为开源 **Noto Sans SC**（等同思源黑体），保证跨设备一致渲染。
3. 图标由 Ant Design Icons 升级为 Google **Material Symbols · Outlined**（四轴可变，选中态可切实心）。

---

## 内容基调 (Content Fundamentals)

ERP 是高频内部工具，文案以**简洁、直接、专业**为纲：

- **语言**：简体中文为主，夹带英文/数字（如 SKU、订单号、`admin`）。界面语言为中英双语可切换的设计预留（locale = `zh_CN`）。
- **人称**：对用户多用**祈使/陈述**，不刻意「您」。如「请输入」「登录后请立即修改密码」「点击表头排序」。系统对用户的提醒口吻平实，如「有补单 ¥X 未计入」「数据偏旧」。
- **大小写/格式**：金额一律 `¥` 前缀 + 千分位（`¥1,284,560`）；日期人性化（`06-22`、`2026-06-22`）；编号等宽（`PS-20260622-018`）。
- **标点**：中文全角标点；标签短句不加句号；说明性长句用句号。
- **Emoji**：原系统在数据新鲜度红绿灯（🟢🟡🔴）等少量场景使用 emoji 作状态符号。设计系统**克制使用**：正式组件用图标/色块表达状态，仅在 demo 卡片与移动端轻量场景用 emoji 占位示意。
- **语气示例**：
  - 标题：`运营大盘`、`对账中心`、`配件库存`
  - 副标题：`实时经营概览 · 每分钟自动刷新`
  - 操作：`新建订单`、`智能匹配`、`确认入库`、`生成工厂下单`
  - 状态：`已发货`、`待付款`、`已对账`、`超卖`、`缺料`、`负库存`
- **氛围 (vibe)**：可信赖的「数字管家」。冷静、精确、不花哨；每一个像素、每一个数字都为「看清、判断、操作」服务。

---

## 视觉基础 (Visual Foundations)

- **色彩**：
  - 主色 = Google 蓝 `--blue-600 #1a73e8`（按钮/链接/选中），品牌色 `--blue-500 #4285f4`，深蓝 `--blue-900 #174ea6`（顶栏底）。
  - 中性 = slate 冷灰（文字 `#1e293b` / 次要 `#64748b` / 三级 `#94a3b8`；页面底 `#f8fafc`；描边 `#e8edf2`）。
  - 语义 = 成功 emerald `#10b981` / 警告 amber `#f59e0b` / 危险 rose `#f43f5e` / 信息 sky `#0ea5e9`（沿用原系统状态色）。状态多用「软底 + 同色文字 + 细描边」的标签。
- **字体**：Noto Sans SC（界面）+ JetBrains Mono（金额/编号/对账数字，`tnum` 等宽对齐、右对齐）。正文 15px、表格 14px、行高 1.47、标题字距 −0.022em。
- **间距**：4px 基准网格；内容内边距 24；表格三档行高（紧凑 40 / 默认 48 / 宽松 56）；移动端最小点击区 44px。
- **背景**：以纯净浅灰 `#f8fafc` 为主，无重纹理。少量场景用极轻的主色径向渐变（登录页、资金条 `linear-gradient(135deg,#fff,blue-50)`、移动端头图深蓝渐变）点缀，绝不喧宾夺主。
- **圆角**：标签 8 / 控件 10 / 弹窗 12 / **卡片 16**（主）/ 大容器 20 / 胶囊 999。
- **阴影**：柔和、低对比的「悬浮」体系（`--shadow-xs … xl`），卡片默认 `--shadow-xs`，hover 升 `--shadow-md` 并上移 1px。聚焦统一用主色聚焦环 `--focus-ring`（3px teal 半透明）。
- **卡片**：白底 + 1px 细描边 `--border` + `--shadow-xs` + 圆角 16；可点击卡片 hover 上浮 + 主色描边。
- **边框/分隔**：表格内用更浅的 `--border-subtle #eef2f7`，结构性分隔用 `--border`。
- **动效**：克制。进入用 `--ease-out`（120–280ms）；hover 改色/上浮，press 轻微下沉 0.5px；列表/抽屉用 translateX 滑入（**绝不**把 opacity:0 作为静息态，保证打印/降级可见）；图表用 ECharts 默认生长动画。`prefers-reduced-motion` 下全部降级。
- **悬停/按压**：hover = 颜色加深或软底浮现；press = 主色更深（active）+ 轻微位移。
- **透明/模糊**：仅在移动端头图卡片等少量场景用半透明 + `backdrop-filter: blur`。
- **图像气质**：产品多为家具，建议暖中性、自然质感的实拍图；UI 本身保持冷静中性，让内容（数据/图片）成为主角。
- **深色模式**：`[data-theme="dark"]` 全量语义覆盖，底色走「深蓝灰」（`#0d1320` / 卡片 `#161d2c`）；主色在深色下提亮一档（blue-300）保证对比。

---

## 图标 (Iconography)

- 图标体系 = Google **Material Symbols · Outlined**（线性描边、2px、光学尺寸 24；FILL/粗细/圆角/光学尺寸 四轴可变，选中态可切 FILL=1 实心）。原系统的 Ant Design Icons 可平滑迁移到此体系。
- 本设计系统的组件以 **ReactNode** 形式接收图标（`icon` / `prefix` 等 props），不内嵌图标字体，便于消费方引入 `material-symbols` 字体或导出 SVG。
- Emoji 不进入正式界面：状态用图标 + 语义色 + 色块表达。
- 未使用自绘 SVG 插画；品牌 Logo 当前为**临时字标**（见下）。

---

## 索引 / 清单 (Index)

**根目录**
- `styles.css` — 全局样式入口（仅 @import）。消费方链接此文件。
- `tokens/` — `fonts.css` `colors.css` `typography.css` `spacing.css` `dark.css` `base.css`
- `guidelines/` — 基础规范展示卡（Type / Colors / Spacing / Brand）
- `components/` — 可复用组件（见下）
- `ui_kits/` — 产品界面还原（见下）
- `SKILL.md` — Agent Skill 入口（可下载用于 Claude Code）
- `readme.md` — 本文件

**组件 (`components/`)** — 命名空间 `window.ERPDesignSystem_dc7e11`
- `core/`：`Button` · `Tag` · `Card` · `StatCard` · `Input` · `Select`
- `data/`：`DataTable`（排序 / 多选 / 固定表头 / 三档密度 / 数字右对齐）
- `navigation/`：`TopNav` · `PageHeader` · `Tabs` · `Segmented`

**UI Kits (`ui_kits/`)**
- `web/` — **Web 后台 ERP**（桌面端）：登录 → 运营大盘（KPI + ECharts + 对账健康）→ 订单（标签筛选 + 数据表 + 行抽屉）→ 配件库存（密度切换 + 水位）→ 对账中心（支付宝智能核销）。入口 `web/index.html`。
- `mobile/` — **移动端 App**：工作台 / 拍照录单（AI OCR 流程）/ 库存查询 / 我的，底部 Tab 切换。入口 `mobile/index.html`。

---

## 用法 (Usage)

消费项目链接全局样式后即可使用 token：
```html
<link rel="stylesheet" href="styles.css">
```
组件通过编译产物 `_ds_bundle.js` 暴露在 `window.ERPDesignSystem_dc7e11`：
```html
<script src="_ds_bundle.js"></script>
<script>const { Button, DataTable, StatCard } = window.ERPDesignSystem_dc7e11;</script>
```
深色模式：在根元素加 `data-theme="dark"`。

---

## 注意事项 (Caveats)
- **字体替换**：原系统用系统苹方/SF（不内嵌），本系统改用 Google 托管的 **Noto Sans SC**。若你有思源/苹方授权字体，替换 `tokens/fonts.css` 的 `@import` 即可。
- **品牌 Logo 为临时字标**：当前 Logo 是基于「畔」字 + 蓝色渐变的字标占位，待正式品牌标识。
- **UI Kit 为视觉还原**：UI Kit 屏幕为自包含的高保真演示（基于 token 重建外观），偏重视觉/交互一致性，非接入真实接口；生产代码请使用 `_ds_bundle.js` 中的组件。
- 图表（ECharts）在 UI Kit 中用 SVG 渲染器，便于截图与降级。
