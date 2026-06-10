# 千牛聊天记录抓取（PC / uiautomation）

## 你会得到什么

- 输入：`names.txt` 或 `names.csv`（客户昵称列表）
- 输出：`chat_history.csv`（自动追加写入，含列：客户昵称/发送者/时间/内容/hash）
- 失败时：自动导出 `logs/control-tree-*.txt`（控件树，用于精确定位 AutomationId/ClassName）

## GUI 版本（推荐）

运行 GUI：

```bash
python qn_gui.py
```

GUI 里可以选择昵称文件与导出 CSV 路径，并设置 sleep/翻页等参数。

## 安装依赖

在本目录打开 PowerShell：

```bash
pip install -r requirements.txt
```

## 运行

1. 打开并登录千牛 PC 客户端，保持窗口可见（不要最小化）
2. 按需编辑 `names.txt`（或 `names.csv`）
3. 运行：

```bash
python qn_scrape.py
```

## 打包成 EXE（单文件）

在本目录 PowerShell 运行：

```bash
powershell -ExecutionPolicy Bypass -File .\\build_exe.ps1
```

生成的 EXE：

- `dist\\千牛聊天记录导出工具.exe`

## 重要说明（第一次运行大概率会失败，这是正常的）

由于千牛 UI 版本差异，脚本需要你提供以下信息之一才能稳定运行：

- 搜索框的 `AutomationId` 或 `ClassName`
- 聊天消息列表容器的 `AutomationId` 或 `ClassName`

脚本在报错时会自动生成 `logs/control-tree-*.txt`。你把里面 **搜索框附近** 和 **聊天列表附近** 的片段发我，我就能把 `qn_scrape.py` 里的 `QNSelectors(...)` 精确补齐。

## 等待时间（网络/加载延迟）

脚本已经在以下关键点强制 `sleep(2)`（可在代码中调整）：

- 输入昵称后，等待搜索结果刷新
- 触发打开会话后，等待聊天面板/弹窗加载
- 每次向上滚动后，等待历史记录加载

