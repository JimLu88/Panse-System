"""畔色 ERP 桌面看门狗 (Windows 系统托盘).

功能:
    - 30s 检查一次 Docker Desktop / 4 个容器 / /api/health
    - 托盘图标颜色实时反映状态:
        绿: 全部健康
        黄: API 慢 / backup 容器有问题 / 部分容器在重启
        红: API 挂了 / 容器没起
        灰: Docker Desktop 没运行
    - 右键菜单: 打开 ERP / 立即重启容器 / 看容器状态 / 拉取最新代码 / 退出
    - 异常时弹 Windows Toast 通知
    - 异常时自动尝试 `docker compose up -d` 拉起

依赖:
    pip install pystray pillow requests winotify

打包成单 exe (在仓库根目录 deploy/windows 下跑):
    pyinstaller --noconsole --onefile --icon=icon.ico --name=PanseTray panse_tray.py

打包后 dist/PanseTray.exe 双击即可在托盘运行.
"""
from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path
from datetime import datetime

import requests
from PIL import Image, ImageDraw
from pystray import Icon, Menu, MenuItem

try:
    from winotify import Notification, audio
    HAS_NOTIFY = True
except ImportError:
    HAS_NOTIFY = False

# ----------------------------- 配置 ---------------------------------- #


def _find_project_root() -> Path:
    """找项目根. 兼容 PyInstaller frozen exe + 直接 python panse_tray.py.

    PyInstaller 打包后:
        - __file__ 指向 _MEI 临时解压目录 (没用)
        - sys.executable 指向 PanseTray.exe (有用)
    直接跑:
        - __file__ 指向 panse_tray.py
    """
    # ENV 优先
    env_root = os.environ.get("PANSE_ROOT")
    if env_root and (Path(env_root) / "docker-compose.yml").exists():
        return Path(env_root).resolve()

    if getattr(sys, "frozen", False):
        # PyInstaller bundle, exe 路径
        start = Path(sys.executable).parent
    else:
        start = Path(__file__).parent

    # 从 start 往上找含 docker-compose.yml 的目录, 最多 5 层
    cur = start.resolve()
    for _ in range(6):
        if (cur / "docker-compose.yml").exists():
            return cur
        if cur.parent == cur:
            break
        cur = cur.parent

    return start.resolve()


PROJECT_ROOT = _find_project_root()

API_URL = "http://localhost:8000/api/health"
WEB_URL = "http://localhost:5173"
# 部署分支: 看门狗永远同步到这个分支 (可用环境变量 PANSE_BRANCH 覆盖).
# 所有改动合并进 main 后, 点一下"拉最新代码"就能拿到,
# 不受当前本地 checkout 在哪个分支的影响.
DEPLOY_BRANCH = os.environ.get("PANSE_BRANCH", "main")
CHECK_INTERVAL = 30   # 秒
AUTO_RECOVER = True    # 挂了自动 docker compose up -d
FAIL_THRESHOLD = 3     # 连续 N 次 FAIL 才触发自动恢复 (防止 AI 请求期间误报)
CONTAINERS = ["panse-system-db-1", "panse-system-api-1",
              "panse-system-web-1", "panse-system-backup-1"]
LOG_FILE = PROJECT_ROOT / "logs" / "watchdog.log"
MAX_LOG_BYTES = 2 * 1024 * 1024  # 日志超 2MB 时归档

_fail_streak = 0  # 当前连续 FAIL 次数


# ----------------------------- 日志 --------------------------------- #


def _write_log(line: str) -> None:
    """追加一行到 watchdog.log, 超 2MB 时轮转."""
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        if LOG_FILE.exists() and LOG_FILE.stat().st_size > MAX_LOG_BYTES:
            LOG_FILE.rename(LOG_FILE.with_suffix(".log.1"))
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"[{ts}] {line}\n")
    except Exception:
        pass


def _collect_api_logs(lines: int = 30) -> str:
    """从 api 容器拿最后 N 行日志, 用于诊断 API 无响应原因."""
    try:
        code, out = _run(
            ["docker", "compose", "logs", "--tail", str(lines), "--no-log-prefix", "api"],
            timeout=10,
        )
        return out.strip() if code == 0 and out else ""
    except Exception:
        return ""


def _stamp_version() -> tuple[str, dict]:
    """算出当前 HEAD 的 commit 信息, 用于「构建时注入镜像」+「写文件兜底」.

    容器内没有 .git, 后端只能靠宿主机在 build 时把版本信息传进去。
    返回 (commit 短哈希, build_args 环境变量 dict)。
    同时写一份 backend/build_version.json 作为双保险。
    """
    import json as _json

    def _g(*args):
        c, o = _run(["git", *args], timeout=15)
        return o.strip() if c == 0 else ""

    full = _g("rev-parse", "HEAD")
    short = full[:7] if full else "?"
    deployed = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    msg = _g("show", "-s", "--format=%s", "HEAD")
    cdate = _g("show", "-s", "--format=%ci", "HEAD")
    branch = _g("rev-parse", "--abbrev-ref", "HEAD")

    # 1) 构建时注入镜像的环境变量 (最可靠, 不受 Docker COPY 缓存影响)
    build_env = {
        "GIT_COMMIT": full or "unknown",
        "GIT_COMMIT_MSG": msg,
        "GIT_COMMIT_DATE": cdate,
        "GIT_BRANCH": branch,
        "BUILD_TIME": deployed,
    }
    # 2) 写文件兜底
    info = {
        "commit": short, "commit_full": full, "commit_date": cdate,
        "commit_message": msg, "branch": branch, "deployed_at": deployed,
    }
    try:
        target = PROJECT_ROOT / "backend" / "build_version.json"
        target.write_text(_json.dumps(info, ensure_ascii=False, indent=2),
                          encoding="utf-8")
        _write_log(f"标记版本: commit={short} ({msg[:50]})")
    except Exception as e:
        _write_log(f"写版本文件失败: {e}")
    return short, build_env


_SELF = Path(__file__).resolve()
# 同步时若拉到的新代码改了看门狗自己, 用这个标志让重启后的新进程接着跑构建
_RESUME_FLAG = "--resume-build"


def _self_signature() -> str:
    """看门狗自身源码的哈希, 用于检测 git pull 后自己有没有被更新."""
    try:
        return hashlib.sha256(_SELF.read_bytes()).hexdigest()
    except Exception:
        return ""


def _restart_self() -> bool:
    """用最新源码替换当前进程 (os.execv). 返回 True 表示已重启 (调用方应 return).

    仅在「从源码 python panse_tray.py 运行」时可行; 打包 exe 无法自更新,
    此时提示用户改用 start_watchdog.bat 从源码启动 (一次), 之后即可自动更新.
    """
    if getattr(sys, "frozen", False):
        notify("畔色 ERP",
               "⚠️ 看门狗自身已更新, 但你跑的是打包 exe, 无法自动生效。\n"
               "请改用 deploy\\windows\\start_watchdog.bat 从源码启动 (仅需一次),\n"
               "之后看门狗即可随代码自动更新。",
               level="warn", force=True)
        _write_log("看门狗自身已更新, 但 frozen exe 无法自更新 — 提示改用源码启动")
        return False
    _write_log("看门狗自身代码已更新, 重启加载新版本 (重启后接着重建)...")
    notify("畔色 ERP", "看门狗自身已更新, 正在重启加载新版本...",
           level="info", force=True)
    try:
        os.execv(sys.executable, [sys.executable, str(_SELF), _RESUME_FLAG])
    except Exception as e:
        _write_log(f"自重启失败: {e}")
        return False
    return True


def _build_and_up(prefix: str = "代码更新完成") -> None:
    """标记版本 → docker compose build + up. update_code / force_sync / 自重启后共用."""
    commit, build_env = _stamp_version()
    _write_log("开始 docker compose build + up...")
    code, out = _run(
        ["docker", "compose", "up", "-d", "--build", "--renew-anon-volumes"],
        timeout=300, env=build_env,
    )
    if code == 0:
        _write_log(f"{prefix}, 已同步到 {DEPLOY_BRANCH} 最新 (commit={commit})")
        notify("畔色 ERP",
               f"✅ {prefix}！已同步到 {DEPLOY_BRANCH} 最新 (版本 {commit})",
               level="info", force=True)
    else:
        _write_log(f"build 失败 (完整输出):\n{out}")
        tail = out.strip()[-300:] if out.strip() else "(无输出)"
        notify("畔色 ERP", f"❌ build 失败:\n{tail}\n\n详细日志: {LOG_FILE}",
               level="error", force=True)


def open_log(icon=None, item=None):
    """弹出带滚动条的日志窗口 (显示最近 100 行), 方便与 AI 核对问题."""
    import tkinter as tk
    from tkinter import scrolledtext

    def _show():
        lines: list[str] = []
        if LOG_FILE.exists():
            try:
                with open(LOG_FILE, "r", encoding="utf-8") as f:
                    lines = f.readlines()
            except Exception:
                pass

        root = tk.Tk()
        root.title("畔色 ERP — 看门狗日志")
        root.geometry("920x500")
        root.resizable(True, True)

        txt = scrolledtext.ScrolledText(root, font=("Consolas", 9), wrap=tk.WORD,
                                        bg="#1e1e1e", fg="#d4d4d4",
                                        insertbackground="white")
        txt.pack(fill=tk.BOTH, expand=True, padx=6, pady=6)

        content = "".join(lines[-100:]) if lines else "(日志为空)"
        txt.insert(tk.END, content)
        txt.see(tk.END)
        txt.configure(state=tk.DISABLED)

        bar = tk.Frame(root)
        bar.pack(fill=tk.X, padx=6, pady=(0, 6))
        tk.Button(bar, text="📂 打开完整日志文件",
                  command=lambda: os.startfile(str(LOG_FILE))).pack(side=tk.LEFT)
        tk.Button(bar, text="关闭", command=root.destroy).pack(side=tk.RIGHT)

        root.mainloop()

    threading.Thread(target=_show, daemon=True).start()


# ----------------------------- 状态判定 ---------------------------- #


class Status:
    OK = "ok"
    WARN = "warn"
    FAIL = "fail"
    NO_DOCKER = "no_docker"


_HIDE_WINDOW_KW = {}
if sys.platform == "win32":
    # Phase: Windows 上 subprocess 不弹 cmd 黑窗 (30s 一次太烦)
    _CREATE_NO_WINDOW = 0x08000000
    _HIDE_WINDOW_KW = {"creationflags": _CREATE_NO_WINDOW}


def _run(cmd: list[str], timeout: int = 10, env: dict | None = None) -> tuple[int, str]:
    full_env = None
    if env:
        full_env = {**os.environ, **env}
    try:
        r = subprocess.run(cmd, capture_output=True, text=True,
                            timeout=timeout, cwd=str(PROJECT_ROOT),
                            encoding="utf-8", errors="replace",
                            env=full_env,
                            **_HIDE_WINDOW_KW)
        return r.returncode, (r.stdout + r.stderr)
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        return -1, str(e)


def check_docker() -> bool:
    code, _ = _run(["docker", "info"], timeout=5)
    return code == 0


def check_containers() -> dict:
    """返回 {container_name: state}, state = running / restarting / exited / missing"""
    out = {c: "missing" for c in CONTAINERS}
    code, output = _run([
        "docker", "ps", "-a",
        "--format", "{{.Names}}\t{{.State}}",
    ], timeout=10)
    if code != 0:
        return out
    for line in output.splitlines():
        parts = line.split("\t")
        if len(parts) >= 2:
            name, state = parts[0], parts[1]
            if name in out:
                out[name] = state
    return out


def check_api() -> tuple[bool, int]:
    """API health: (ok, latency_ms). 不达就 (False, -1)."""
    try:
        t0 = time.time()
        r = requests.get(API_URL, timeout=3)
        dt = int((time.time() - t0) * 1000)
        return r.status_code == 200, dt
    except requests.RequestException:
        return False, -1


def assess() -> tuple[str, str]:
    """综合判定: (status, message)."""
    if not check_docker():
        return Status.NO_DOCKER, "Docker Desktop 没运行"
    containers = check_containers()
    bad = {c: s for c, s in containers.items()
           if s not in ("running",)}
    if bad:
        if all(s in ("restarting",) for s in bad.values()):
            return Status.WARN, f"容器重启中: {list(bad.keys())}"
        return Status.FAIL, f"容器异常: {bad}"
    ok, lat = check_api()
    if not ok:
        return Status.FAIL, "API 无响应"
    if lat > 1000:
        return Status.WARN, f"API 慢 ({lat}ms)"
    return Status.OK, f"全部正常 (API {lat}ms)"


# ----------------------------- 图标生成 ---------------------------- #


def make_icon(color: str) -> Image.Image:
    """生成一个 64x64 圆形托盘图标."""
    img = Image.new("RGBA", (64, 64), (255, 255, 255, 0))
    draw = ImageDraw.Draw(img)
    colors = {
        Status.OK: "#52c41a",      # 绿
        Status.WARN: "#fa8c16",    # 橙
        Status.FAIL: "#cf1322",    # 红
        Status.NO_DOCKER: "#8c8c8c",  # 灰
    }
    fill = colors.get(color, "#1677ff")
    draw.ellipse((4, 4, 60, 60), fill=fill, outline="white", width=3)
    # 中央写 "P"
    draw.text((22, 16), "P", fill="white")
    return img


# ----------------------------- 通知 ---------------------------- #


_LAST_NOTIFIED = {"status": None, "ts": 0}


def notify(title: str, msg: str, *, level: str = "info", force: bool = False) -> None:
    """Windows Toast. 同等级 5 分钟内不重复 (force=True 直接发, 用于完成类通知)."""
    if not HAS_NOTIFY:
        print(f"[{level}] {title}: {msg}")
        return
    now = time.time()
    if not force and _LAST_NOTIFIED["status"] == level and now - _LAST_NOTIFIED["ts"] < 300:
        return
    _LAST_NOTIFIED.update(status=level, ts=now)
    try:
        n = Notification(app_id="畔色 ERP", title=title, msg=msg)
        if level == "error":
            n.set_audio(audio.LoopingAlarm, loop=False)
        n.show()
    except Exception as e:
        print(f"通知失败: {e}")


# ----------------------------- 动作 (右键菜单) ---------------------- #


def open_erp(icon=None, item=None):
    webbrowser.open(WEB_URL)


def open_api_docs(icon=None, item=None):
    webbrowser.open("http://localhost:8000/docs")


def restart_containers(icon=None, item=None):
    notify("畔色 ERP", "正在重启容器...", level="info")

    def _do():
        code, output = _run(["docker", "compose", "restart"], timeout=60)
        if code == 0:
            notify("畔色 ERP", "✅ 容器已重启", level="info", force=True)
        else:
            _write_log(f"重启失败:\n{output}")
            notify("畔色 ERP", f"重启失败:\n{output.strip()[-300:]}", level="error", force=True)
    threading.Thread(target=_do, daemon=True).start()


def start_containers(icon=None, item=None):
    notify("畔色 ERP", "正在启动容器...", level="info")

    def _do():
        code, output = _run(["docker", "compose", "up", "-d"], timeout=120)
        if code == 0:
            notify("畔色 ERP", "✅ 容器已启动", level="info", force=True)
        else:
            _write_log(f"启动失败:\n{output}")
            notify("畔色 ERP", f"启动失败:\n{output.strip()[-300:]}", level="error", force=True)
    threading.Thread(target=_do, daemon=True).start()


def show_status(icon=None, item=None):
    status, msg = assess()
    containers = check_containers()
    detail = "\n".join(f"  {c}: {s}" for c, s in containers.items())
    full = f"{msg}\n\n容器:\n{detail}\n\n路径: {PROJECT_ROOT}"
    notify(f"畔色 ERP 状态: {status.upper()}", full, level=status)


def update_code(icon=None, item=None):
    """一键同步: 切到 DEPLOY_BRANCH → 拉最新 → 重建.

    不管当前本地在哪个分支, 都会先切到 DEPLOY_BRANCH (默认 main) 再拉,
    所以只要改动合并进了该分支, 点一下就升级到最新.
    容器启动时 Dockerfile 会自动跑 alembic upgrade head, 无需手动迁移.
    """
    notify("畔色 ERP", f"同步 {DEPLOY_BRANCH} 最新代码 + 重建中...", level="info")

    def _do():
        _write_log(f"--- 开始更新代码 (分支: {DEPLOY_BRANCH}) ---")
        code, out = _run(["git", "fetch", "origin"], timeout=120)
        if code != 0:
            notify("畔色 ERP", f"❌ git fetch 失败: {out[:200]}", level="error", force=True)
            _write_log(f"git fetch 失败: {out[:300]}")
            return
        _write_log("git fetch 完成")
        code, out = _run(["git", "checkout", DEPLOY_BRANCH], timeout=30)
        if code != 0:
            notify("畔色 ERP",
                   f"❌ 切到 {DEPLOY_BRANCH} 失败 (本地可能有未提交改动): {out[:200]}",
                   level="error", force=True)
            _write_log(f"git checkout 失败: {out[:300]}")
            return
        old_sig = _self_signature()
        code, out = _run(
            ["git", "pull", "--ff-only", "origin", DEPLOY_BRANCH], timeout=120,
        )
        if code != 0:
            notify("畔色 ERP",
                   f"❌ git pull 失败 (可试「强制同步」): {out[:200]}",
                   level="error", force=True)
            _write_log(f"git pull 失败: {out[:300]}")
            return
        _write_log(f"git pull 完成: {out.strip()[:120]}")
        # 拉到的新代码若改了看门狗自己, 先用新代码重启, 重启后接着重建
        if _self_signature() != old_sig and _restart_self():
            return
        _build_and_up("代码更新完成")
    threading.Thread(target=_do, daemon=True).start()


def force_sync(icon=None, item=None):
    """强制同步: 硬重置到远端 DEPLOY_BRANCH, 丢弃本地代码改动后重建.

    用于普通「拉最新代码」因本地分叉/改动导致 ff-only 失败时.
    会丢弃本地未提交的代码改动; gitignore 的文件 (如 panse_erp.db) 不受影响.
    """
    notify("畔色 ERP", f"强制同步 {DEPLOY_BRANCH} (丢弃本地代码改动)...", level="warn")

    def _do():
        for cmd in (["git", "fetch", "origin"],
                    ["git", "checkout", DEPLOY_BRANCH]):
            code, out = _run(cmd, timeout=120)
            if code != 0:
                notify("畔色 ERP", f"{' '.join(cmd)} 失败: {out[:200]}", level="error")
                return
        old_sig = _self_signature()
        code, out = _run(
            ["git", "reset", "--hard", f"origin/{DEPLOY_BRANCH}"], timeout=60,
        )
        if code != 0:
            notify("畔色 ERP", f"reset 失败: {out[:200]}", level="error")
            return
        # reset 后看门狗自己若变了, 先用新代码重启, 重启后接着重建
        if _self_signature() != old_sig and _restart_self():
            return
        _build_and_up("强制同步完成")
    threading.Thread(target=_do, daemon=True).start()


def quit_app(icon, item):
    icon.stop()


# ----------------------------- 主循环 ---------------------------- #


def watchdog_loop(icon: Icon) -> None:
    """30s 一次的检查 + 图标更新 + 自动恢复."""
    global _fail_streak
    _write_log(f"看门狗启动 — 项目根: {PROJECT_ROOT} | 间隔: {CHECK_INTERVAL}s | 恢复阈值: 连续{FAIL_THRESHOLD}次")
    while True:
        try:
            status, msg = assess()
            _write_log(f"[{status.upper()}] {msg}")
            icon.icon = make_icon(status)
            icon.title = f"畔色 ERP - {msg}"

            if status == Status.FAIL:
                _fail_streak += 1
            else:
                _fail_streak = 0

            # 连续 FAIL_THRESHOLD 次才触发自动恢复, 避免 AI 请求期间的短暂超时误报
            if AUTO_RECOVER and status == Status.FAIL and _fail_streak >= FAIL_THRESHOLD:
                api_logs = _collect_api_logs()
                if api_logs:
                    _write_log(f"=== api 容器日志 (最后30行) ===\n{api_logs}\n=== END ===")
                # 通知里附上最后一行容器日志和日志路径
                last_log_line = api_logs.splitlines()[-1][:120] if api_logs else "无日志"
                notify(
                    "畔色 ERP",
                    f"检测到异常, 尝试自动拉起: {msg}（连续{_fail_streak}次）\n"
                    f"容器最新日志: {last_log_line}\n"
                    f"详细日志: {LOG_FILE}",
                    level="warn",
                )
                _write_log(f"触发自动恢复 (连续{_fail_streak}次FAIL), 执行 docker compose up -d")
                _fail_streak = 0
                _run(["docker", "compose", "up", "-d"], timeout=120)
        except Exception as e:
            _write_log(f"watchdog_loop 异常: {e}")
            print(f"watchdog error: {e}")
        time.sleep(CHECK_INTERVAL)


def _show_error_box(msg: str) -> None:
    """启动时无控制台 (.exe 模式), 用 Win32 弹个错误框, 不至于静默退出."""
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(0, msg, "畔色 ERP 看门狗", 0x10)
    except Exception:
        # 兜底: print + 等用户看到 (有控制台的话)
        print(msg)


def main():
    if not (PROJECT_ROOT / "docker-compose.yml").exists():
        _show_error_box(
            f"未在以下路径找到 docker-compose.yml:\n\n{PROJECT_ROOT}\n\n"
            "请把 PanseTray.exe 放到 <项目根>\\deploy\\windows\\dist\\ 下,\n"
            "或设环境变量 PANSE_ROOT 指向项目根目录."
        )
        sys.exit(1)

    icon = Icon(
        "PanseERP",
        icon=make_icon(Status.NO_DOCKER),
        title="畔色 ERP - 启动中",
        menu=Menu(
            MenuItem("🌐 打开 ERP", open_erp, default=True),
            MenuItem("📖 API 文档 (Swagger)", open_api_docs),
            Menu.SEPARATOR,
            MenuItem("📊 当前状态", show_status),
            MenuItem("▶️ 启动容器", start_containers),
            MenuItem("🔁 重启容器", restart_containers),
            Menu.SEPARATOR,
            MenuItem("⬇️ 拉最新代码 + 重建", update_code),
            MenuItem("🧹 强制同步 (丢弃本地改动)", force_sync),
            Menu.SEPARATOR,
            MenuItem("📋 查看看门狗日志", open_log),
            Menu.SEPARATOR,
            MenuItem("❌ 退出看门狗", quit_app),
        ),
    )
    threading.Thread(target=watchdog_loop, args=(icon,), daemon=True).start()
    # 自重启接力: 上一个 (旧) 进程拉完代码后用新代码重启了自己, 这里接着重建
    if _RESUME_FLAG in sys.argv:
        _write_log("检测到自重启接力标志, 用新代码继续重建...")
        threading.Thread(
            target=lambda: _build_and_up("看门狗已更新, 重建完成"),
            daemon=True,
        ).start()
    icon.run()


if __name__ == "__main__":
    main()
