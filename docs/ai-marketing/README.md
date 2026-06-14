# AI Marketing Syeyem · 全平台内容生产与分发系统

> 🚧 **已实现 Phase-1/2/3**（小红书+知乎）· 可运行代码见 [`../../ai-marketing/`](../../ai-marketing)，交接说明见 [`../../ai-marketing/HANDOFF.md`](../../ai-marketing/HANDOFF.md)。
> 本目录为原始设计稿；落地实现以 `ai-marketing/` 为准（养号/内容制作/分发/评论 + 看门狗 + 运营台账已建成，49 测试全绿）。
> 服务于小红书、知乎、抖音、快手、Instagram、TikTok、小黑盒 等多平台的 AI 内容生产 + 多模式分发。

---

## 一句话定位

**AI 驱动的内容生产流水线 + 多模式可插拔分发引擎。**

三条核心原则：
1. **生产与对抗解耦** — 内容生产高价值零风险先做，分发对抗按需挂载
2. **默认半自动，对抗能力可插拔** — 海外走 API，国内走 ASSIST，挂载项默认关闭
3. **数据驱动，越用越聪明** — Prompt / 模板 / 风格全部版本化，能 A/B、能回滚、能反哺

详见 [00-overview/positioning.md](00-overview/positioning.md)。

---

## 一张图看懂

```
┌────────────────────────────────────────────────────────────┐
│  横向支撑层                                                   │
│  ├─ 配置与规则中心（热更新 + 沙盒灰度 + 模板社区）              │
│  ├─ 风控事件总线（独立订阅，跨系统聚合异常信号）                  │
│  └─ 多 LLM 路由（顶级模型/廉价模型分层调用）                    │
└────────────────────────────────────────────────────────────┘
        │                                              │
   ┌────▼──────────────── 内容生产主线 ──────────────▼──────┐
   │  ①选题引擎 → ②采集分析 → ③生成引擎 → ④平台适配         │
   │  挂载项：[A 本地爬虫接口] ──→ ②                          │
   └─────────────────────────────────────────────────────┘
         │
   ┌─────▼──────────────── 运营与分发主线 ─────────────────┐
   │  ⑤账号管理 → ⑥分发调度 → ⑦数据回收                    │
   │  挂载项：[C 海外 API] [D 国内自动] [E 代理环境] [B/F/G/H/I]  │
   └─────────────────────────────────────────────────────┘

   部署隔离：国内业务实例 ⫻ 海外业务实例（共享内容核心层）
```

---

## 我现在该看哪份文档（按角色）

### 产品 / 决策者

| 想了解什么 | 看哪 |
|---|---|
| 这系统是干什么的 | [00-overview/positioning.md](00-overview/positioning.md) |
| 关键决策有哪些（贴墙） | [00-overview/key-decisions.md](00-overview/key-decisions.md) |
| 4 阶段路线图 | [06-roadmap/](06-roadmap/) |
| 风险最高的部分 | [04-mount-modules/D-domestic-automation.md](04-mount-modules/D-domestic-automation.md) · [G-batch-registration.md](04-mount-modules/G-batch-registration.md) |
| 国内 vs 海外怎么分 | [05-deployment/domestic-overseas-isolation.md](05-deployment/domestic-overseas-isolation.md) |

### 架构师

| 想了解什么 | 看哪 |
|---|---|
| 整体架构 + 子系统职责 | [00-overview/architecture.md](00-overview/architecture.md) |
| 数据模型（事件流 / 叙事单元 / 血缘） | [03-data-model/unified-content-model.md](03-data-model/unified-content-model.md) |
| 核心 JSON Schema | [03-data-model/schemas.md](03-data-model/schemas.md) |
| 横向支撑（配置 / 风控 / LLM 路由）| [02-cross-cutting/](02-cross-cutting/) |
| 挂载项依赖关系 | [04-mount-modules/README.md](04-mount-modules/README.md) |

### 开发

| 想了解什么 | 看哪 |
|---|---|
| 子系统①–⑦ 详细职责 | [01-subsystems/](01-subsystems/) |
| 与 AI 数据爬虫 / AI 视觉中心 / observability 的对接 | [07-references/integration-points.md](07-references/integration-points.md) |
| 各平台 API / 规则速查 | [07-references/platforms.md](07-references/platforms.md) |
| **外部开源工具选型（给①–⑦配现成轮子）** | [07-references/external-tools.md](07-references/external-tools.md) |
| 术语表 | [00-overview/glossary.md](00-overview/glossary.md) |

### 运营

| 想了解什么 | 看哪 |
|---|---|
| ASSIST 模式怎么工作 | [01-subsystems/06-dispatcher.md](01-subsystems/06-dispatcher.md) |
| 审核工位的设计 | [01-subsystems/03.5-review-station.md](01-subsystems/03.5-review-station.md) |
| 账号性格档案 / 健康心跳 | [01-subsystems/05-account-manager.md](01-subsystems/05-account-manager.md) |
| 真实感归因 | [01-subsystems/07-analytics.md](01-subsystems/07-analytics.md) |

---

## 完整目录树

```
D:\AI\AI Marketing Syeyem\
├── README.md                                          ← 入口（本文件）
├── 00-overview\
│   ├── positioning.md                                 定位与三条核心设计原则
│   ├── architecture.md                                整体架构图 + 子系统职责矩阵
│   ├── key-decisions.md                               11 条关键设计决策（贴墙版）
│   └── glossary.md                                    术语表
├── 01-subsystems\
│   ├── 01-topic-engine.md                             ① 选题与热点引擎
│   ├── 02-collector-analyzer.md                       ② 素材采集与分析层
│   ├── 03-generator.md                                ③ 内容生成引擎（四层流水线）
│   ├── 03.5-review-station.md                         ③.5 人工审核工位（30 秒/篇）
│   ├── 04-platform-adapter.md                         ④ 多平台适配器
│   ├── 05-account-manager.md                          ⑤ 账号与凭证管理
│   ├── 06-dispatcher.md                               ⑥ 分发调度中心（双模 driver）
│   └── 07-analytics.md                                ⑦ 数据回收与分析
├── 02-cross-cutting\
│   ├── config-rule-center.md                          横向 Ⅰ 配置与规则中心
│   ├── risk-event-bus.md                              横向 Ⅱ 风控事件总线
│   └── llm-router.md                                  横向 Ⅲ 多 LLM 路由
├── 03-data-model\
│   ├── unified-content-model.md                       事件流 + 叙事单元 + 血缘
│   └── schemas.md                                     核心 JSON schema
├── 04-mount-modules\
│   ├── README.md                                      挂载项依赖矩阵
│   ├── A-local-crawler.md                             A 本地爬虫接入（✅ 启用）
│   ├── B-image-fingerprint.md                         B 图片指纹变形（❌ 默认关）
│   ├── C-overseas-api-driver.md                       C 海外 API driver（✅ 推荐）
│   ├── D-domestic-automation.md                       D 国内自动操作（❌ 慎挂）
│   ├── E-proxy-env-isolation.md                       E 代理环境胶囊（❌ 默认关）
│   ├── F-fingerprint-browser.md                       F 指纹浏览器（❌ 默认关）
│   ├── G-batch-registration.md                        G 接码批量注册（❌ 风险最高）
│   ├── H-watermark.md                                 H 合规水印（❌ 默认关）
│   └── I-content-lineage.md                           I 内容血缘追踪（✅ 推荐）
├── 05-deployment\
│   └── domestic-overseas-isolation.md                 国内/海外双实例隔离
├── 06-roadmap\
│   ├── phase-1-mvp.md                                 1–3 月：单平台 ASSIST 闭环
│   ├── phase-2-expand.md                              4–6 月：核心平台 + AUTO
│   ├── phase-3-scale.md                               7–9 月：智能化（风格库/归因）
│   └── phase-4-evaluate.md                            10–12 月：高阶能力按需挂载
└── 07-references\
    ├── platforms.md                                   7 大平台规则/API 速查
    └── integration-points.md                          与 AI 数据爬虫等的对接
```

---

## 进度状态

整体处于 **📝 设计稿** 阶段。所有子文档都有完整设计，但尚未开始实现。

未来追踪用：

| 状态 | 含义 |
|---|---|
| 📝 设计稿 | 当前阶段：仅文档 |
| 🚧 开发中 | 已有代码实现，未完成 |
| ✅ 已上线 | 已可在生产使用 |

---

## 与同级 D:\AI\\* 项目的关系

本项目位于 `D:\AI\AI Marketing Syeyem\`，与同级 AI 项目的可能集成：

| 同级项目 | 关系 | 状态 |
|---|---|---|
| `AI 数据爬虫` | 挂载项 A 的源头（强依赖）| ✅ 已确定接入 |
| `AI 视觉中心` | ③ 视觉锚定的潜在资产源 | Phase 3 评估 |
| `AI 记忆中心` | ③ 事实核查层的潜在知识库 | Phase 3 评估 |
| `observability` | 共用可观测栈（Grafana/Loki/Tempo/Prometheus）| Phase 1 接入 |
| `AI 蜂群系统 (h-semas)` | 长期可能成为蜂群中的一个 agent | 远期 |
| `AI 账本中心` | LLM 成本上报 | Phase 4 |
| `scripts` | 备份 / 部署脚本复用 | Phase 2 |

详见 [07-references/integration-points.md](07-references/integration-points.md)。

---

## 内容约定

- **本目录不是代码仓库**，是设计文档。所有 `.md` 都是规划稿
- 所有"待定数值 / 待调研项"统一标注 ⚠️
- 用户关键判断（如"挂载项 D 维护成本无底洞"、"G 风险最高"）原样保留在对应文档显眼位置，不能软化
- 每份子系统 / 挂载项文档使用统一骨架（职责 / 核心能力 / 关键决策 / IO / 接口 / 风险 / MVP / 待决策）

---

## 下一步

1. 评估并完善文档中标 ⚠️ 的待补充项（特别是各平台精确折叠线、API 限额）
2. 进入 [Phase 1 MVP](06-roadmap/phase-1-mvp.md)：单平台 + 5 个真实自有账号 + 全 ASSIST 模式
