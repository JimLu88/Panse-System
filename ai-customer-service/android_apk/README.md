# 千牛接待助手 APK（Android）

AIWorkbench v1.5.0 配套 Android 端，运行在卖家手机上，通过 AccessibilityService 读取/操控千牛 App。
PC 端 Python 主程序通过 HTTP API（阶段 2 引入）控制本 APK。

---

## 当前进度（阶段 1.1 ~ 1.3 骨架）

✅ Gradle 项目结构
✅ AccessibilityService 声明 + 配置
✅ MainActivity（授权状态显示 + 跳转设置）
✅ Locators 字典（千牛 resource-id，**待真机校准**）
✅ logcat dump：监听千牛页面切换 + 打印关键节点

🔲 阶段 1.4 SessionListReader（读会话列表）
🔲 阶段 1.5 MessageReader（读消息 + 气泡侧别）
🔲 阶段 1.6 MessageSender（发消息）
🔲 阶段 2.x HTTP 服务 + 配对
🔲 阶段 5.x 端到端验证

---

## 环境要求

- **Android Studio Hedgehog (2023.1.1) 或更新版本**
- **JDK 17**（Android Studio 自带）
- **Android SDK Platform 34**（首次打开会自动提示安装）
- **真机：** Android 7.0+（API 24+）红米 / 荣耀 / OPPO / vivo 均可

> ⚠️ **不要在雷电模拟器上测试。** 本方案的目的就是替代雷电方案。

---

## 构建步骤

### 方式 A：Android Studio（推荐）

1. 打开 Android Studio → File → Open → 选择 `D:\AI\AI 客服系统\android_apk` 文件夹
2. 等待 Gradle Sync（首次约 5-10 分钟，下载依赖）
3. 顶部菜单：Build → Make Project（或 Ctrl+F9）
4. 构建产物：`android_apk/app/build/outputs/apk/debug/app-debug.apk`

### 方式 B：命令行（如果熟悉 Gradle）

```powershell
# 需要先在 Android Studio 里至少打开一次本项目以生成 gradlew 脚本
cd "D:\AI\AI 客服系统\android_apk"
.\gradlew assembleDebug
```

---

## 真机安装 + 授权

### 1. 启用 USB 调试（仅安装阶段需要，**装完就关**）

手机：设置 → 关于本机 → 连续点击「版本号」7 次 → 开启「开发者选项」→ 启用「USB 调试」

### 2. 安装 APK

```powershell
adb install -r android_apk\app\build\outputs\apk\debug\app-debug.apk
```

或：把 `app-debug.apk` 拷到手机本地，用文件管理器点击安装（允许未知来源）。

### 3. 授权无障碍服务

1. 手机：打开「千牛接待助手」App
2. 点击「打开无障碍设置」按钮
3. 在列表中找到「千牛接待助手」→ 启用 → 同意警告
4. 返回 APP，状态应显示「已授权」

### 4. 安装完成后请**关闭 USB 调试**

设置 → 系统 → 开发者选项 → 关闭 USB 调试。
本 APK 后续运行**完全不需要 ADB**，关掉可减少千牛风控信号。

---

## 阶段 1 验证

### 验证目标：千牛 view tree 能被读取

1. 千牛 App 中打开「消息」页面
2. PC 上执行（需要重新临时打开 USB 调试 + 连接）：

```powershell
adb logcat -s QianniuAgent
```

### 预期输出

```
═══ AccessibilityService 已连接 ═══
→ 千牛页面切换: com.taobao.qianniu.MessageCenterActivity
─── dump 开始 reason=page_change ───
  [TAB_MESSAGE] id=com.taobao.qianniu:id/tab_message 命中 1 个节点
  [SESSION_LIST] id=com.taobao.qianniu:id/recycler_view 命中 1 个节点
  [SESSION_ITEM_NAME] id=com.taobao.qianniu:id/tv_name 命中 5 个节点
    #0 text=买家昵称A desc=null
    #1 text=买家昵称B desc=null
─── dump 结束 ───
```

### 如果 Locators 全部未命中

logcat 会输出：

```
⚠️ Locators 字典全部未命中，dump 整树前 80 个节点：
[android.widget.FrameLayout] id=android:id/content text= desc=
  [androidx.recyclerview.widget.RecyclerView] id=com.taobao.qianniu:id/xxxxx ...
```

把这段完整 logcat 贴回给 Claude，会用真实 ID 更新 `Locators.kt`。

### 验证安全性：千牛是否检测到我们

授权无障碍服务后，**正常使用千牛 24 小时**（手动收发消息，**不要让脚本动**）：
- 账号无风控告警 → 无障碍方案可行 ✓
- 账号被风控 → 需要 fallback 方案

---

## 故障排查

| 现象 | 排查方向 |
|---|---|
| Gradle Sync 失败 / 下载超时 | 网络问题，可能需要 VPN 或换镜像 |
| MainActivity 启动崩溃 | 看 logcat: `adb logcat AndroidRuntime:E *:S` |
| 已授权但 logcat 无输出 | 重启千牛 App；确认 AccessibilityService 列表里本服务仍勾选 |
| 显示「已授权」但点千牛无反应 | 千牛包名可能不是 `com.taobao.qianniu`，确认：`adb shell pm list packages \| grep qianniu` |

---

## 后续阶段路线

| 阶段 | 内容 | 预计 |
|---|---|---|
| 1.4-1.6 | SessionListReader / MessageReader / MessageSender | 2-3 天 |
| 2.x | Ktor HTTP 服务 + 配对二维码 + 前台服务 | 3 天 |
| 3.x | PC Python 端切换到 HTTP Adapter | 3 天 |
| 4.x | PC UI 改造（添加设备对话框） | 2 天 |
| 5.x | 端到端验证（24h 影子模式 → 7 天真实接待） | 7 天 |

详见 `C:\Users\lzdwy\.claude\plans\d-cursor-ai-dist-aiworkbench-v1-3-68-ex-mellow-bear.md`（v1.5.0 plan）。
