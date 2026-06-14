# 挂载项 I · 内容血缘追踪

> 状态：📝 设计稿 / **推荐启用**

## 概述

贯穿 ②③④ 三个子系统，给每条内容打上**血缘追踪**字段，用于：

- **防止矩阵号同质化**（核心目标）
- 效果归因
- 同质化检测
- 反洗稿溯源

---

## 默认状态

✅ **推荐启用**

---

## 启用前提

无。直接挂。

---

## 依赖的其他挂载项

无。但与 ② ③ ④ 子系统深度结合。

---

## 血缘字段（详见 [unified-content-model.md](../03-data-model/unified-content-model.md)）

每条内容记录：

| 字段 | 来源 | 用途 |
|---|---|---|
| `references` | ② 采集 | 同质化检测、版权追溯 |
| `prompt_template` | ③ 生成 | 模板效果归因 |
| `style_dna_id` | ② 抽取 | 风格效果归因 |
| `parent_content_id` | ④ 变体 | 变体族系追踪 |
| `account_id` | ⑥ 调度 | 账号 ↔ 内容关联 |
| `generated_by_model` | LLM 路由 | 成本与质量归因 |

---

## 关键设计决策

### 1. 全链路血缘，不漏单元

每个**叙事单元**都有自己的血缘：

```json
{
  "narrative_units": [
    {
      "type": "hook",
      "content": "...",
      "lineage_unit": {
        "reference": "ref_001:hook",
        "prompt_template": "tpl_hook_v3",
        "model": "claude-opus-4-7"
      }
    }
  ]
}
```

不同单元可来自不同 reference / template，分别追踪。

### 2. 同质化检测

矩阵号防止两个号在 24h 内发血缘高度重叠的内容：

```
   号 A 即将发布的 content c_001
        ↓
   查询：是否有其他号最近 24h 发了 references / prompt_template 重叠率 >50% 的内容？
        ↓
   是 → 警告 / 推迟 / 改用其他变体
   否 → 通过
```

### 3. 效果归因

⑦ 分析时：
- 同 prompt_template 的内容平均真实感 → 模板好坏
- 同 style_dna_id 的内容平均真实感 → 风格好坏
- 不同模型生成的内容 → 模型 ROI

### 4. 反洗稿溯源

如果有人盗了你的内容，你能通过血缘字段证明：
- 这是你原创（特定 reference + 模板组合）
- 时间戳证明在前
- 同矩阵号有更多同血缘内容

---

## 存储

```
content_lineage_db:
  content_id → lineage 对象
  
indices:
  by_reference     → reference_id 反查内容
  by_template      → template_id 反查内容
  by_style_dna     → style_id 反查内容
  by_account       → account_id 反查内容
```

---

## 维护成本评估

**低**：
- 纯数据字段，无外部依赖
- 主要工作量在初次嵌入到 ②③④ 流程

---

## 商业 ROI 判断点

**ROI 几乎确定为正**：

- 同质化防护：直接降低矩阵号被识别风险
- 效果归因：让数据分析更有可操作性
- 内容资产沉淀：随时间积累的归因数据 = 壁垒

---

## 风险点

| 风险 | 缓解 |
|---|---|
| 血缘字段膨胀（每篇都几 KB） | 压缩 + 归档老数据 |
| 血缘字段泄漏到对外接口 | 输出层过滤 + 仅内部可见 |
| 误用血缘判定（重复但不同质化） | 阈值调优 + 人工抽检 |

---

## 待决策项

- ⚠️ 血缘数据库选型 — 图库（Neo4j）vs 关系库
- ⚠️ 血缘字段的可视化（运营员能看到内容的"族系树"）
- ⚠️ 同质化阈值 — 50% / 60% / 70%
