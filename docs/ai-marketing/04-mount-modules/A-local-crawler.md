# 挂载项 A · 本地爬虫接口

> 状态：📝 设计稿 / **已确定接入**

## 概述

你已有的**本地爬虫程序**，通过标准接口接入 ② 采集分析层。

输入是 URL / 博主 ID 列表，输出是清洗后的原始内容传给分析器。

爬虫和分析器解耦：**爬虫坏了不影响主系统**。

---

## 默认状态

✅ **启用**（已确定接入）

---

## 启用前提

- 本地爬虫程序可访问
- 爬虫支持标准 HTTP / 文件 / gRPC 接口
- 爬虫输出符合本系统数据契约

---

## 依赖的其他挂载项

无。完全独立。

---

## 接口契约

```
POST /crawl
{
  "task_id": "...",
  "platform": "xhs | zhihu | dy | ks | ins | tt | xhh",
  "sources": ["url_or_blogger_id_1", "..."],
  "depth": 1,
  "options": {
    "include_images": true,
    "include_comments": false
  }
}

Response:
{
  "task_id": "...",
  "status": "queued | running | done | failed",
  "result_url": "..."
}
```

输出契约（爬虫返回）：

```json
{
  "items": [
    {
      "source_url": "...",
      "platform": "...",
      "fetched_at": "ISO-8601",
      "raw_text": "...",
      "images": [...],
      "metadata": {
        "author": "...",
        "publish_at": "ISO-8601",
        "stats": {"likes": 0, "comments": 0}
      }
    }
  ]
}
```

---

## 解耦设计

```
┌──────────────────┐                ┌──────────────────┐
│ 本地爬虫程序       │  ◀── HTTP ──── │ ② 采集分析层      │
│ (D:\AI\AI 数据爬虫)│  ──── JSON ──▶ │                  │
└──────────────────┘                └──────────────────┘
   独立部署 / 独立失败                    主系统
```

- 爬虫挂了 → ② 走降级模式（少样本 / 缓存）
- 主系统挂了 → 爬虫继续跑，结果落盘等接
- 双方协议是文件 / 队列，不是函数调用

---

## 维护成本评估

**低**：
- 爬虫本身已经有了
- 主系统只维护接口契约
- 平台改版的痛苦留在爬虫侧

---

## 商业 ROI 判断点

无需评估——已有资产，零边际成本。

---

## 与 D:\AI\AI 数据爬虫 的对接

- 爬虫项目路径：`D:\AI\AI 数据爬虫\`
- 包含 backend + frontend
- 通过 plugin-manifest.json 注册到本系统
- 详细对接点见 [integration-points.md](../07-references/integration-points.md)

---

## 风险点

| 风险 | 缓解 |
|---|---|
| 爬虫被平台封 IP | 挂载项 E 环境胶囊 |
| 爬虫返回格式漂移 | 接口契约 + schema 校验 + 版本协商 |
| 爬虫数据陈旧 | 主系统加 `fetched_at` 阈值过滤 |

---

## 待决策项

- ⚠️ 接口协议：HTTP REST vs gRPC vs 消息队列
- ⚠️ 爬虫与主系统的部署关系：同机房 vs 独立网络
- ⚠️ 失败时主系统的降级策略细节
