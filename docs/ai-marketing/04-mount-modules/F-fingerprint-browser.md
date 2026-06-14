# 挂载项 F · 指纹浏览器集成（AdsPower）

> 状态：📝 设计稿 / **默认关闭**

## 概述

集成 AdsPower（或类似产品）作为挂载项 E 的应用层补充。

提供更细颗粒度的浏览器指纹独立化：
- WebGL 指纹
- Canvas 指纹
- 字体指纹
- AudioContext 指纹
- 时区伪装

---

## 默认状态

❌ **关闭**

---

## 启用前提

- 已启用挂载项 D（国内自动操作）
- AdsPower 或同类商业产品已采购
- 每号一独立浏览器实例的成本可承受

---

## 依赖的其他挂载项

- **必须** + 挂载项 E（代理）：网络层先隔离才有意义
- **服务于** 挂载项 D（自动操作）

---

## 与 AdsPower 的对接

AdsPower 提供本地 API：

```
GET /api/v1/browser/start?user_id=xxx
{
  "code": 0,
  "data": {
    "webdriver": "...",
    "puppeteer": "..."
  }
}
```

主系统通过 driver 接入：

```
   挂载项 D driver
        ↓
   AdsPower API：启动账号 X 的浏览器实例
        ↓
   返回 webdriver endpoint
        ↓
   driver 用 Selenium/Playwright 连接该实例
        ↓
   操作目标平台
```

---

## 关键设计决策

### 1. 一号一浏览器配置文件

AdsPower 中每个账号一个独立 profile：
- 持久化的 cookie / localStorage
- 独立的指纹组合
- 独立的代理设置

### 2. 浏览器配置文件持续性

- **不要**频繁删除 / 重建 profile（行为不一致 = 异常）
- 每号 profile 持续使用 ≥ 30 天才稳定

### 3. 与挂载项 E 的协作

```
   挂载项 E：分配代理 IP 给账号 X
        ↓
   挂载项 F：把代理 IP 写入 AdsPower profile X
        ↓
   AdsPower 启动 profile X → 用该代理打开浏览器
        ↓
   driver 连接，开始操作
```

---

## 替代产品

- AdsPower（主流）
- BitBrowser
- Multilogin
- Kameleo

选型考虑：
- 价格（按账号数 vs 按月）
- 自动化 API 完备度
- 指纹库丰富度
- 本地化（中文支持 / 中国客服）

---

## 维护成本评估

**中**：
- AdsPower 商业产品费用（每号 $0.5–2/月）
- profile 配置工作量（初次创建较繁琐）
- 系统集成的 API 适配

---

## 商业 ROI 判断点

- 不启用 D 时，无 ROI
- 启用 D 但不启用 F 时，账号被关联风险高
- 启用 D + F 后，账号存活率显著提升

---

## 风险点

| 风险 | 缓解 |
|---|---|
| AdsPower 服务断 → 操作停摆 | 备用产品（Multilogin） |
| Profile 被批量同步删除（如 AdsPower 整改） | 备份导出 |
| 指纹库与平台学习速度赛跑 | 持续更新 + 多产品备份 |

---

## 待决策项

- ⚠️ AdsPower vs 其他指纹浏览器的选型
- ⚠️ 是否自建（成本极高，通常不推荐）
- ⚠️ Profile 与账号档案的同步机制
