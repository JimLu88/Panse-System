# 与同级 AI 项目的对接点

> 状态：📝 设计稿
> 本系统位于 `D:\AI\AI Marketing Syeyem\`，下面是与 `D:\AI\` 下其他 AI 子项目的可能集成关系。

---

## 同级目录全景

```
D:\AI\
├── AI Marketing Syeyem        ← 本系统（内容生产 + 分发）
├── AI Pic Matching System     ← 图片匹配系统
├── AI 代码手脚                  ← 代码辅助工具
├── AI 客服系统                  ← 客服系统
├── AI 数据爬虫                  ← 爬虫（本系统挂载项 A 的源头）
├── AI 蜂群系统 (h-semas)        ← 多 agent 协同系统
├── AI 视觉中心                  ← 视觉资产 / 图像处理
├── AI 记忆中心                  ← 跨系统记忆 / RAG
├── AI 账本中心                  ← 账务 / 财务
├── AI 轻执行                    ← 轻量执行框架
├── observability               ← 可观测性栈（Grafana/Loki/Tempo/Prometheus）
└── scripts                     ← 通用脚本（备份等）
```

---

## 关键对接关系

### 1. AI 数据爬虫（挂载项 A 源头）

**关系**：强依赖。本系统 ② 采集分析层通过挂载项 A 接入。

**对接点**：
- 项目路径：`D:\AI\AI 数据爬虫\`
- 接入文档：`D:\AI\AI 数据爬虫\plugin-manifest.json`
- 调用方式：HTTP REST / 文件队列（接口待定）
- 主要 URL：⚠️ 待补充

**注意**：
- 爬虫独立部署，本系统不依赖爬虫所在进程
- 爬虫接口契约定义在挂载项 A 文档

详见 [04-mount-modules/A-local-crawler](../04-mount-modules/A-local-crawler.md)。

---

### 2. AI 视觉中心（潜在交集）

**关系**：可能集成。本系统 ③ 生成引擎的"视觉锚定"能力（ComfyUI 工作流）可能复用视觉中心的资产 / 能力。

**对接点**：
- 项目路径：`D:\AI\AI 视觉中心\`
- 可能能力：
  - ComfyUI 工作流模板库
  - Lora / 风格图库
  - 配图渲染服务

**对接时间**：Phase 3（视觉锚定上线时）

---

### 3. AI 记忆中心（潜在交集）

**关系**：可能集成。本系统 ③ 事实核查层可调用记忆中心的企业知识库。

**对接点**：
- 项目路径：`D:\AI\AI 记忆中心\`
- 后端：`D:\AI\AI 记忆中心\backend\app\memory.py`
- 可能能力：
  - 事实存储 + RAG 检索
  - 跨系统经验沉淀

**对接时间**：Phase 3（事实核查升级时）

---

### 4. observability（强烈推荐集成）

**关系**：所有 AI 项目共用同一可观测栈。

**对接点**：
- 项目路径：`D:\AI\observability\`
- 栈：Grafana + Loki + Tempo + Prometheus
- 配置：`D:\AI\observability\docker-compose.yml`
- 配套：`D:\AI\observability\bee_otel.py`（OpenTelemetry helper）

**对接时间**：Phase 1（从一开始就接入）

**接入工作**：
- 业务日志走 Loki
- Trace 走 Tempo
- Metrics 走 Prometheus
- Dashboards 引用 `D:\AI\observability\grafana\dashboards\*.json` 模板

---

### 5. AI 蜂群系统 (h-semas)（远期可能性）

**关系**：本系统可作为蜂群中的一个 agent，承担"内容生产 / 分发"职责。

**对接点**：
- 项目路径：`D:\AI\AI 蜂群系统\h-semas\`
- 演进协调器：`D:\AI\AI 蜂群系统\h-semas\backend\app\evolution_coordinator\`

**对接时间**：长期（不在 4 阶段路线图内）

---

### 6. AI 账本中心（弱关联）

**关系**：本系统的 LLM 调用成本可上报到账本中心，统一财务记账。

**对接点**：
- 项目路径：`D:\AI\AI 账本中心\`
- 后端：`D:\AI\AI 账本中心\backend\app\main.py`

**对接时间**：Phase 4（商业化阶段）

---

### 7. scripts（运维工具复用）

**关系**：本系统的备份、部署脚本复用通用脚本。

**对接点**：
- 备份：`D:\AI\scripts\restic_backup.ps1`
- 任务注册：`D:\AI\scripts\register_task.ps1`
- 恢复演练：`D:\AI\scripts\restore_drill.md`
- 蓝绿部署：`D:\AI\scripts\blue_green_deploy.ps1`

**对接时间**：Phase 2（部署上线时）

---

## 弱 / 无关联

以下项目目前与本系统无直接对接关系：

- `AI Pic Matching System` — 图片匹配（领域不同）
- `AI 代码手脚` — 代码辅助（开发工具）
- `AI 客服系统` — 客服（业务领域不同）
- `AI 轻执行` — 执行框架（可能未来作为底座）

---

## 对接原则

### 1. 接口契约先行

任何跨项目调用，先写接口契约文档，再实现。

### 2. 故障隔离

依赖项目挂了，本系统能降级运行而不是 crash。

### 3. 版本协商

跨项目接口要支持版本协商，避免上下游同步发版。

### 4. 监控统一

所有跨项目调用走 observability 栈，便于全链路追踪。

---

## 待决策项

- ⚠️ 是否所有 AI 项目共用同一套用户 / 权限体系
- ⚠️ 跨项目数据共享的数据契约
- ⚠️ AI 蜂群系统对本系统的"接管"边界
