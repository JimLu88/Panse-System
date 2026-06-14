# 统一内容模型（地基中的地基）

> 状态：📝 设计稿
> ⚠️ 整个系统最核心的数据结构。所有子系统对话的语言。

---

## 一、事件流（Event Sourcing）

**一条内容是一串不可变事件，不是一行数据库记录。**

```yaml
content_id: c_xxxx
events:
  - topic_chosen           # 来自 ①
  - reference_collected    # 来自 ②
  - style_extracted        # ② 的 JSON 输出
  - draft_v1_generated     # ③ 第一稿
  - fact_check_passed
  - human_edit_v2          # 人工修改的 Diff
  - adapted_xhs_v1         # ④ 小红书版本
  - adapted_ins_v1         # ④ Instagram 版本
  - sandbox_passed
  - published_xhs
  - metrics_24h            # ⑦ 回收
  - ...
```

**为什么**：
- 所有中间产物留痕，便于回溯、复盘、A/B
- 子系统对话用事件流，松耦合
- 数据资产沉淀的根本

---

## 二、结构化叙事单元

**内容不是大段文本，是原子化单元组合**：

```json
{
  "narrative_units": [
    {
      "type": "hook",
      "weight": "core",
      "platforms": ["all"],
      "content": "..."
    },
    {
      "type": "pain_point",
      "weight": "core",
      "content": "..."
    },
    {
      "type": "solution",
      "weight": "core",
      "content": "..."
    },
    {
      "type": "case",
      "weight": "secondary",
      "content": "..."
    },
    {
      "type": "cta",
      "weight": "auxiliary",
      "platforms": ["xhs"],
      "content": "..."
    }
  ],
  "skeleton": "hook→pain→solution→case→cta",
  "lineage": {
    "references": ["ref_id_001", "ref_id_007"],
    "prompt_template": "tpl_v2.3",
    "style_dna_id": "style_xxx"
  }
}
```

**为什么**：
- 平台适配时可按单元重组（小红书要 cta，Ins 不要）
- 单元的权重决定能否在变体中被删
- 不同单元可独立 A/B 测试

---

## 三、血缘字段（lineage）

每条内容记录：

| 字段 | 含义 | 用途 |
|---|---|---|
| `references` | 参考样本 ID 列表 | 同质化检测、版权追溯 |
| `prompt_template` | 使用的 prompt 版本 | 模板效果归因 |
| `style_dna_id` | 使用的风格 DNA | 风格效果归因 |
| `parent_content_id` | 若是变体，父稿 ID | 变体族系追踪 |
| `account_id` | 目标账号 | 账号 ↔ 内容关联 |
| `generated_by_model` | 实际使用的 LLM 模型 | 成本与质量归因 |

**用途**：
- 同质化检测（如果两条内容血缘高度重叠，警告）
- 效果归因（哪个模板 / 风格在哪个号上效果好）
- 矩阵号防同号识别（同 references 的内容不在同一时间发）

---

## 四、单元类型枚举

| 类型 | 描述 | 典型 weight | 平台特异性 |
|---|---|---|---|
| `hook` | 钩子，开头第一句 | core | 各平台不同 |
| `pain_point` | 痛点描述 | core | 通用 |
| `solution` | 解决方案 | core | 通用 |
| `case` | 案例 / 故事 | secondary | 通用 |
| `data_point` | 数据 / 引用 | secondary | 知乎类强、小红书弱 |
| `cta` | call-to-action | auxiliary | 小红书要 / Ins 不要 |
| `tag_set` | 标签集合 | auxiliary | 每平台不同 |
| `cover_caption` | 封面文字 | core for 小红书/Ins | 视觉平台 |

---

## 五、不同子系统看到的视图

| 子系统 | 看到的内容模型 |
|---|---|
| ① 选题 | content_id + topic 元信息 + 推荐风格 |
| ② 采集分析 | 参考样本 → 风格 DNA |
| ③ 生成 | 选题 + 单元骨架 → 填充各单元 content |
| ③.5 审核 | 完整稿 + 必改 3 节点标记 + Diff 报告 |
| ④ 适配 | 单元数组 → 选择性重组 / 改写 |
| ⑥ 分发 | 平台版本 + 账号 + 时间 |
| ⑦ 分析 | content_id + lineage + metrics |

---

## 六、与外部存储的关系

事件流是**唯一真相源**。其他存储是衍生视图：

- 关系库：当前状态快照（便于查询）
- 向量库：风格 DNA / 内容指纹（便于检索）
- 时序库：metrics（便于分析）
- 对象存储：原始素材 / 配图 / 视频

所有衍生视图都可以从事件流重建。
