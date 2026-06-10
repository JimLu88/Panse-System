# 核心 JSON Schema

> 状态：📝 设计稿
> 这里集中放各核心对象的 JSON 形态，便于跨子系统对齐字段。

---

## 1. 风格 DNA

来自 ② 采集分析层的强制 JSON 输出：

```json
{
  "style_dna_id": "style_xxx",
  "extracted_from": ["ref_001", "ref_007", "ref_023"],
  "version": "v1.0",
  "features": {
    "sentence_length_avg": 15,
    "sentence_length_p90": 28,
    "emoji_frequency": 0.12,
    "emoji_palette": ["🌟", "💧", "✨"],
    "question_ratio": 0.25,
    "first_person_ratio": 0.6,
    "hook_type": "pain_point",
    "structure_pattern": "problem-solution-case",
    "vocabulary_level": "middle",
    "emotion_tone": "friendly",
    "transition_words": ["但是", "其实", "没想到"],
    "ending_type": "call_to_action",
    "tag_count": 8,
    "paragraph_length_avg": 3
  },
  "embedding": [/* 768-dim vector */]
}
```

---

## 2. 选题对象

来自 ① 选题引擎：

```json
{
  "topic_id": "t_xxx",
  "created_at": "ISO-8601",
  "title": "...",
  "category": "skincare",
  "platform_targets": ["xhs", "ins"],
  "heat_score": 87,
  "heat_status": "peak | safe | decay",
  "safe_window": {
    "start": "ISO-8601",
    "end": "ISO-8601"
  },
  "references": ["ref_001", "ref_007"],
  "recommended_style_dna_id": "style_xxx",
  "lineage_from": ["metric_001"]
}
```

---

## 3. 草稿对象

来自 ③ 生成引擎：

```json
{
  "content_id": "c_xxx",
  "topic_id": "t_xxx",
  "draft_version": 1,
  "narrative_units": [/* 见 unified-content-model.md */],
  "fact_check": {
    "passed": ["claim_1", "claim_3"],
    "pending_human": ["claim_2"]
  },
  "compliance": {
    "level_s_hits": [],
    "level_a_hits": ["最低价"],
    "level_b_hits": ["超好用"]
  },
  "scores": {
    "ai_likeness": 42,
    "info_density": 7.8
  },
  "must_fix_markers": {
    "cover_text_density": 0.4,
    "para2_marketing": "需软化",
    "personal_anchor_count": 3
  },
  "lineage": {
    "references": ["ref_001"],
    "prompt_template": "tpl_v2.3",
    "style_dna_id": "style_xxx",
    "generated_by_model": "claude-opus-4-7"
  }
}
```

---

## 4. 账号档案

来自 ⑤ 账号管理：

```json
{
  "account_id": "xhs_007",
  "platform": "xhs",
  "credentials_ref": "vault://account/xhs_007",
  "voice_persona": {
    "catchphrases": ["真的会谢", "建议人手一个"],
    "emoji_preference": ["🌟", "💧"],
    "sentence_length_avg": 12,
    "first_person_ratio": 0.7,
    "visual_style": "morandi_warm_tone"
  },
  "negative_words_from_review_diff": ["全网最低", "强烈推荐"],
  "topic_affinity": {
    "skincare": 0.9,
    "makeup": 0.6
  },
  "audience_profile": {
    "follower_count": 12300,
    "fan_tier": "thousand",
    "demographics": "..."
  },
  "health": {
    "post_alive_rate_24h": 0.92,
    "real_comment_rate": 0.034,
    "restricted_flags": [],
    "last_check_at": "ISO-8601",
    "health_score": 87
  },
  "driver_mode": "assist"
}
```

---

## 5. 发布事件

来自 ⑥ 分发调度：

```json
{
  "event_id": "e_xxx",
  "content_id": "c_xxx",
  "account_id": "xhs_007",
  "platform": "xhs",
  "scheduled_at": "ISO-8601",
  "published_at": "ISO-8601",
  "driver_used": "assist | api | automation",
  "result": "success | failed | degraded",
  "platform_post_id": "...",
  "anti_resonance": {
    "offset_minutes": 23,
    "tag_set_variant": "B"
  }
}
```

---

## 6. 指标对象

来自 ⑦ 数据回收：

```json
{
  "content_id": "c_xxx",
  "account_id": "xhs_007",
  "platform_post_id": "...",
  "checkpoints": [
    {
      "at": "T+1h",
      "views": 234,
      "likes": 12,
      "comments": 3,
      "collects": 5
    },
    {
      "at": "T+24h",
      "views": 4521,
      "likes": 312,
      "comments": 47,
      "collects": 189
    }
  ],
  "realness_score": 0.74,
  "attribution_features": {
    "topic": "skincare/oily-acne",
    "format": "diary",
    "cover_ratio": "3:4",
    "publish_hour": 7
  },
  "weight_factor": 1.0
}
```

---

## 7. 风格 DNA 库 索引

```json
{
  "index_id": "idx_style_v1",
  "indexed_fields": ["features.hook_type", "features.structure_pattern", "embedding"],
  "size": 1247,
  "category_partitions": ["skincare", "makeup", "lifestyle", "tech"]
}
```

---

## 8. 平台人格词典 schema

```json
{
  "platform": "xhs",
  "version": "v1.0",
  "tone": "girlfriend_chat",
  "forbidden_words": [
    {"level": "S", "words": ["..."]},
    {"level": "A", "words": ["..."]},
    {"level": "B", "words": ["..."]}
  ],
  "required_elements": ["emoji", "question_hook", "3:4_cover"],
  "fold_line": {
    "title_chars": 20,
    "body_first_chars": 50
  },
  "tag_count_range": [5, 10]
}
```

---

## 字段命名约定

- 所有 ID：`<prefix>_<random>` 形式，prefix 三位以内
- 时间戳：ISO-8601 字符串
- 评分：0–1 浮点（除非显式标注 0–100）
- 权重：0–10 浮点
- 平台代号：xhs / zhihu / dy / ks / ins / tt / xhh
