# Windows 桌面集成

3 个东西, 按需用:

## 1. start.bat / stop.bat — 一键启停

**双击 `start.bat`**:
- 自动检测 Docker Desktop, 没开就开
- 跑 `docker compose up -d` 起所有容器
- 等 API 健康
- 自动开浏览器到 http://localhost:5173

**双击 `stop.bat`**: 停所有容器 (数据保留)

适合: 不想用托盘, 偶尔打开 ERP 干活的场景.

---

## 2. 系统托盘看门狗 (推荐 start_watchdog.bat 源码模式)

> **强烈建议用 `start_watchdog.bat` 从源码启动, 而不是打包的 exe。**
> 源码模式下点「拉最新代码 + 重建」时, 如果新代码连看门狗自己都更新了,
> 它会**自动重启加载新版本** — 真正一键到底, 以后永远不用再手动重装看门狗。
> 打包的 exe 做不到自更新 (git pull 改不了 exe 文件本身), 每次看门狗有更新都得重新 build。
>
> **双击 `start_watchdog.bat`** 即可 (第一次会自动装依赖)。

最像你之前的"双击运行的后台看门狗程序":

- 🟢 绿色: 全部健康
- 🟡 黄色: API 慢 / 部分容器在重启
- 🔴 红色: 容器挂了
- ⚪ 灰色: Docker Desktop 没运行
- **30 秒检查一次**, 挂了**自动 `docker compose up -d`**
- 异常时**桌面右下角弹通知**
- **右键菜单**:
  - 🌐 打开 ERP
  - 📖 API 文档 Swagger
  - 📊 当前状态 (详细)
  - ▶️ 启动容器
  - 🔁 重启容器
  - ⬇️ 拉最新代码 + 重建
  - ❌ 退出

### 装一次 (打包成 .exe)

需要 Python 3.10+ 装好. 没装的话 → https://www.python.org/downloads/windows/ (装时勾 "Add Python to PATH")

```cmd
双击 build_exe.bat
```

约 1 分钟后, `dist\PanseTray.exe` 就生成了 (~15MB 单文件, 不依赖 Python).

**双击 `PanseTray.exe`** 启动. 右下角任务栏看图标.

### 开机自启 (可选)

```cmd
双击 install_autostart.bat
```

会在开机启动文件夹放快捷方式. 下次开机自动起.

**取消开机自启**: `Win+R` → `shell:startup` → 删掉 PanseTray.lnk

---

## 3. 故障处理

### "PanseTray 启动后图标灰色"

= Docker Desktop 没运行. 启动 Docker Desktop 等 30 秒, 图标会自动变色.

### "图标常红, 自动恢复也救不活"

右键托盘 → 当前状态 看具体哪个容器挂了. 或者命令行:
```cmd
docker compose logs api --tail 50
```

### "exe 报毒"

PyInstaller 打包的 exe 在某些杀毒里会被误报. 加白名单或自己看源码 (panse_tray.py) 没问题再用.

### "我不想要自动恢复"

编辑 panse_tray.py, 把 `AUTO_RECOVER = True` 改成 `False`, 重新 build_exe.bat.

---

## 关于安全 / 权限

- PanseTray.exe 只在本机执行 docker 命令, 不联网 (除了 GET localhost:8000/api/health)
- 不需要管理员权限运行 (但 Docker Desktop 本身需要)
- 不会改注册表, 开机自启只是放快捷方式到 Startup 文件夹

## 怎么卸载

1. 右键托盘 → 退出
2. 删 `dist\PanseTray.exe`
3. (如果装了自启) `Win+R` → `shell:startup` → 删 PanseTray.lnk
