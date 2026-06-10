# 挂载项 C · 海外 AUTO 模式 / 官方 API 驱动

> 状态：📝 设计稿 / **推荐启用**

## 概述

海外平台的官方 API 接入。每个平台一个 driver 插件，配置中心切换。

涵盖：
- Instagram Graph API
- TikTok Content Posting API
- Pinterest API
- YouTube Data API
- X (Twitter) API
- Facebook Pages API

---

## 默认状态

✅ **推荐启用**（在拿到 API 准入后）

---

## 启用前提

每个平台 driver 单独评估：

| Driver | 启用前提 | 优先级 |
|---|---|---|
| Instagram Graph | Business Account + App Review 通过 | 高 |
| Pinterest | Developer 申请 + App Approval | 高 |
| TikTok Content Posting | TikTok for Developers App + Audit | 中 |
| YouTube Data | Google Cloud Project + OAuth | 中 |
| X API | X Developer Portal 付费档 | 低 |
| Facebook Pages | 同 Instagram（同账号） | 高 |

---

## 依赖的其他挂载项

- **强烈建议 + 挂载项 E**（环境胶囊）：API 调用走干净 IP
- 与 ⑥ 分发调度的 driver 接口对接

---

## Driver 插件结构

```
drivers/
├── api/
│   ├── instagram_driver.py
│   ├── pinterest_driver.py
│   ├── tiktok_driver.py
│   ├── youtube_driver.py
│   ├── x_driver.py
│   └── facebook_driver.py
├── interface.py        # publish(content, account) 统一接口
└── registry.py         # driver 注册
```

每个 driver 实现：
- `publish(content, account)` — 发布
- `verify(post_id)` — 确认发布成功
- `quota_remaining()` — 配额查询
- `health_check()` — 自检

---

## 关键设计决策

### 1. 每个平台独立 driver

不要写一个"超级 driver"。每家 API 差异巨大，独立 driver 维护成本反而更低。

### 2. 配额管理

每个 driver 自带配额追踪：
- 实时记录每次调用消耗
- 接近上限时主动告警
- 触发上限自动切换到备用 key 池 / 降级 ASSIST

### 3. 版本协商

平台 API 经常升版本。Driver 必须：
- 显式声明使用的 API 版本
- 平台公告新版本时自动 PR / 告警
- 老版本下线前 30 天迁移

---

## 维护成本评估

**中**：
- 6 个平台 × 每年 1–2 次 API 改动
- OAuth 令牌刷新逻辑复杂
- 审核拒绝重申请需要时间

但相比挂载项 D（国内自动操作），这是"明面合法"的对抗成本——平台不会针对你封号。

---

## 商业 ROI 判断点

海外场景下 **ROI 几乎确定为正**：
- 自动发布省下人工成本
- 官方 API 不算"灰产"，账号安全
- 数据回收（来自同 API 的统计）一并解决

---

## 与 ⑥ 调度中心的对接

```
   ⑥ publish(content, account)
        ↓
   driver_registry.get(account.platform).publish(...)
        ↓
   具体 driver（如 instagram_driver）
        ↓
   平台 API
```

---

## 风险点

| 风险 | 缓解 |
|---|---|
| API key 泄漏 | Vault / 环境变量 + 不入 git |
| 审核拒绝 | 应用预审、备用账号、申请理由模板 |
| API 配额耗尽 | 多 key 轮换 + 实时监控 |
| 平台 API 弃用 | 版本协商 + 监听公告 |
| 海外网络不稳 | 挂载项 E（代理） |

---

## 待决策项

- ⚠️ 6 个平台的接入顺序（建议 Instagram + Pinterest 先做）
- ⚠️ 是否给每个 driver 做独立 Docker 容器（隔离故障）
- ⚠️ OAuth 令牌存储方案
