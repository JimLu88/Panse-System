# -*- mode: python ; coding: utf-8 -*-
#
# 不使用 collect_all(PyQt6)：Analysis 阶段会极慢，易被误判为卡死；
# PyInstaller 内置 hook-PyQt6 会从入口脚本静态分析拉取依赖。
#
import os
from pathlib import Path
from PyInstaller.utils.hooks import collect_all

block_cipher = None

# v1.6.14：litellm 内部大量动态 import，静态分析收不全，导致 frozen exe 里
# litellm/__init__.py 执行不完整 → `module 'lite' has no attribute 'custom_provider_map'`
# 运行时崩溃（LLM 全挂）。用 collect_all 完整收集其 datas/binaries/hiddenimports。
_litellm_datas, _litellm_binaries, _litellm_hidden = collect_all("litellm")

try:
    _spec_dir = os.path.dirname(os.path.abspath(SPEC))
except NameError:
    _spec_dir = os.getcwd()

# 遍历 apps/ 目录，把所有 .py 文件转成模块名加入 hiddenimports
_extra_hidden_apps = []
for _root, _dirs, _files in os.walk(os.path.join(_spec_dir, "apps")):
    for _f in _files:
        if _f.endswith(".py"):
            _rel = os.path.relpath(os.path.join(_root, _f), _spec_dir)
            _mod = _rel.replace(os.sep, ".")[:-3]   # 去掉 .py
            if _mod.endswith(".__init__"):
                _mod = _mod[:-9]                    # apps.foo.__init__ → apps.foo
            _extra_hidden_apps.append(_mod)

_cfgs = Path(_spec_dir) / "configs"
_scripts = Path(_spec_dir) / "scripts"
_datas = []
if _cfgs.is_dir():
    _datas.append((str(_cfgs), "configs"))
if _scripts.is_dir():
    _datas.append((str(_scripts), "scripts"))

# LiteLLM 用 importlib.resources 读取包内数据文件；onefile exe 需显式打入，否则会报
# No such file ... litellm/model_prices_and_context_window_backup.json
try:
    import litellm

    _litellm_root = Path(litellm.__file__).resolve().parent
    for _fname in (
        "model_prices_and_context_window_backup.json",
        "anthropic_beta_headers_config.json",
        "blog_posts.json",
        "provider_endpoints_support_backup.json",
    ):
        _fp = _litellm_root / _fname
        if _fp.is_file():
            _datas.append((str(_fp), "litellm"))
    _anth_tok = (
        _litellm_root / "litellm_core_utils" / "tokenizers" / "anthropic_tokenizer.json"
    )
    if _anth_tok.is_file():
        _datas.append(
            (str(_anth_tok), "litellm/litellm_core_utils/tokenizers")
        )
    # litellm.containers 等模块通过 files("litellm") / 路径读取 endpoints.json
    _cont_dir = _litellm_root / "containers"
    if _cont_dir.is_dir():
        for _fp in _cont_dir.iterdir():
            if _fp.is_file():
                _datas.append((str(_fp), "litellm/containers"))
except Exception:
    pass

# rapidocr_onnxruntime 的 ONNX 模型文件和配置（frozen 下无法通过 importlib.resources 自动找到）
try:
    import rapidocr_onnxruntime as _rocr
    import os as _os
    _rocr_root = _os.path.dirname(_rocr.__file__)
    _datas.append((_rocr_root, "rapidocr_onnxruntime"))
except Exception:
    pass

_extra_hidden = [
    "yaml",
    "numpy",
    "cv2",
    "PIL",
    "PIL.Image",
    "PIL._imaging",
    "mss",
    "onnxruntime",
    "rapidocr_onnxruntime",
    "apps.core.ai.llm_client",
    "apps.core.ai.rag_kb",
    "apps.core.automation.popup_dismiss",
    "apps.core.automation.popup_worker",
    "apps.core.automation.vision",
    "apps.core.crm.migrate",
    "apps.core.crm.kb_import",
    "apps.core.crm.kb_import_ai",
    "apps.core.crm.shop_delete",
    "apps.core.configs.shop_yaml_bootstrap",
    "apps.core.configs.shop_yaml_calibration",
    "apps.core.channels.qianniu",
    "apps.core.channels.qianniu.driver",
    "apps.core.channels.qianniu.session_list_unread",
    "apps.core.channels.qianniu.visual_sentry",
    "apps.core.channels.qianniu.window_ops",
    "apps.core.configs.loader",
    "apps.core.capture.screen",
    "apps.ui.dialogs.shop_calibration_dialog",
    "openpyxl",
    "openpyxl.cell",
    "openpyxl.workbook",
    "apps.core.crm.policy_repo",
    "apps.core.intent.classify",
    "apps.core.orchestrator.event_pipeline",
    "apps.core.orchestrator.health",
    "apps.core.orchestrator.companion_reports",
    "apps.core.push",
    "apps.core.push.service",
    "apps.core.runtime_paths",
    "apps.core.risk_guard",
    "apps.core.risk_guard.guard",
    # Shadow：observer 内延迟 import pynput；rules_prompt 供 llm_client 注入
    "apps.core.shadow",
    "apps.core.shadow.observer",
    "apps.core.shadow.evolve",
    "apps.core.shadow.safety_guard",
    "apps.core.shadow.win_foreground",
    "apps.core.shadow.rules_prompt",
    "pynput",
    "pynput.mouse",
    "apps.core.ocr.engine_rapid",
    "apps.core.ocr.engine_paddle_optional",
    "apps.core.strategy.copy",
    "apps.core.strategy.discount",
    "apps.core.strategy.jim_takeover_mode",
    "apps.ui.views.dashboard_view",
    "apps.ui.views.kb_view",
    "apps.ui.views.settings_view",
    "uiautomation",
    "pycaw",
    "pycaw.pycaw",
    "comtypes",
    "comtypes.client",
    "comtypes.server",
    "pythoncom",
    "win32com",
    "win32com.client",
    "psutil",
    "anthropic",
    "litellm",
    # tiktoken 通过 tiktoken_ext 插件注册 cl100k_base；frozen 下 iter_modules 为空会导致
    # 「Unknown encoding cl100k_base · Plugins found: []」
    "tiktoken_ext.openai_public",
    # v1.4.0 手机接待 — uiautomator2
    "uiautomator2",
    "adbutils",
    "whichcraft",
    # v1.5.x 手机接待 — APK + HTTP Adapter（与 u2 并存，由用户选择）
    "httpx",
    "httpx._client",
    "httpx._transports",
    "httpx._transports.default",
    "httpcore",
    "h11",
    "zeroconf",
    "zeroconf._services",
    "zeroconf._services.browser",
    "zeroconf._services.info",
    "apps.mobile.adapter.http_mobile_adapter",
    "apps.mobile.device.pairing",
    "apps.mobile.device.device_discovery",
    # v1.4.0 局域网仪表盘 — FastAPI / Uvicorn 传递依赖
    "fastapi",
    "fastapi.staticfiles",
    "fastapi.responses",
    "uvicorn",
    "uvicorn.main",
    "starlette",
    "starlette.staticfiles",
    "starlette.responses",
    "anyio",
    "anyio._backends._asyncio",
    "sniffio",
    "pydantic_core",
    "httptools",
] + _extra_hidden_apps

# 开发机若装了 sentence-transformers / torch(CUDA)，PyInstaller 会误收集 ~2GB+。
# 接待主链路不依赖这些包；图库 CLIP / 满血 RAG 稠密向量在 frozen 中走降级路径。
_PYINSTALLER_EXCLUDES = [
    "torch",
    "torchvision",
    "torchaudio",
    "torchgen",
    "functorch",
    "transformers",
    "sentence_transformers",
    "accelerate",
    "peft",
    "bitsandbytes",
    "scipy",
    "sklearn",
    "scikit-learn",
    "numba",
    "llvmlite",
    "gradio",
    "gradio_client",
    "sympy",
    "matplotlib",
    "pandas",
    "tensorboard",
    "open_clip",
    "open_clip_torch",
    "paddle",
    "paddleocr",
    "paddlex",
    "datasets",
    "triton",
]

a = Analysis(
    ["apps/ui/main.py"],
    pathex=[_spec_dir],
    binaries=list(_litellm_binaries),
    datas=_datas + list(_litellm_datas),
    hiddenimports=_extra_hidden + list(_litellm_hidden),
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[
        os.path.join(_spec_dir, "hooks", "rth_tiktoken_ext.py"),
        os.path.join(_spec_dir, "hooks", "rth_comtypes.py"),
    ],
    excludes=_PYINSTALLER_EXCLUDES,
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="AIWorkbench",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
