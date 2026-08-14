import logging
import os
import time
import uuid
from contextlib import asynccontextmanager
from contextvars import ContextVar

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

# 每个请求一个 trace id, 贯穿日志与错误响应, 方便「按 id 串起一次请求」排查
request_id_ctx: ContextVar[str] = ContextVar("request_id", default="-")

# 全局日志: 时间戳统一用北京时间 (东八区), 不受容器时区影响, 输出到 stdout → docker logs api 可见
# 注意: converter 必须用 staticmethod 包一层。直接赋 lambda/函数会被当成绑定方法, 调用时多塞个 self,
# 触发 TypeError 让所有格式化崩溃 (日志缓冲会全空、控制台刷 "Logging error")。
def _beijing_time(secs):
    return time.gmtime((secs if secs is not None else time.time()) + 8 * 3600)
logging.Formatter.converter = staticmethod(_beijing_time)
logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
# 同时把最近日志留在内存, 供 /api/logs/recent 在界面上查看
from app.log_buffer import install_ring_buffer  # noqa: E402
install_ring_buffer()
# apscheduler 启动时每个 job 刷一行 "Added job"(~90 行)会顶掉 /api/logs/recent 的有效日志 → 降噪到 WARNING
logging.getLogger("apscheduler").setLevel(logging.WARNING)
_req_logger = logging.getLogger("panse.request")
# 高频轻量请求降到 DEBUG → 不写 INFO 访问日志, 减少磁盘琐碎写入(让 NAS 盘能休眠, 2026-06-23 降负载):
# /api/health(healthcheck)、/api/alerts/stream(SSE保活流)、/api/logs/*(日志查看器自身轮询)。
_QUIET_LOG_PATHS = frozenset({"/api/health", "/api/alerts/stream"})

from app.api import accounting as accounting_api
from app.api import admin as admin_api
from app.api import aftersales as aftersales_api
from app.api import ops_checklist as ops_checklist_api
from app.api import settlements as settlements_api
from app.api import factory_recon as factory_recon_api
from app.api import factory_orders as factory_orders_api
from app.api import factory_statement as factory_statement_api
from app.api import cs_integration as cs_integration_api
from app.api import competitor as competitor_api
from app.api import shop_deposits as shop_deposits_api
from app.api import staff_salary as staff_salary_api
from app.api import imports as imports_api
from app.api import ai as ai_api
from app.api import alerts as alerts_api
from app.api import approvals as approvals_api
from app.api import audit as audit_api
from app.api import auth as auth_api
from app.api import briefings as briefings_api
from app.api import web_agent as web_agent_api
from app.api import wechat_callback as wechat_callback_api
from app.api import customers as customers_api
from app.api import pricing_diagnosis as pricing_api
from app.api import pricing as pricing_list_api
from app.api import table_explorer as table_explorer_api
from app.api import dashboard as dashboard_api
from app.api import search as search_api
from app.api import bom as bom_api
from app.api import customization as customization_api
from app.api import exceptions as exceptions_api
from app.api import feishu as feishu_api
from app.api import finance as finance_api
from app.api import inventory as inventory_api
from app.api import part_returns as part_returns_api
from app.api import marketing as marketing_api
from app.api import match as match_api
from app.api import materials as materials_api
from app.api import orders as orders_api
from app.api import shipments as shipments_api
from app.api import producibility as producibility_api
from app.api import product_inventory as product_inventory_api
from app.api import products as products_api
from app.api import quotes as quotes_api
from app.api import reports as reports_api
from app.api import importer as importer_api
from app.api import logs as logs_api
from app.api import scanners as scanners_api
from app.api import scheduler as scheduler_api
from app.api import screenshots as screenshots_api
from app.api import suppliers as suppliers_api
from app.api import purchases as purchases_api
from app.api import procurement as procurement_api
from app.api import procurement_agent as procurement_agent_api
from app.api import monthly_settlement as monthly_settlement_api
from app.api import field_changes as field_changes_api
from app.api import exports as exports_api
from app.api import gallery as gallery_api
from app.api import manuals as manuals_api
from app.api import taobao_listings as taobao_listings_api
from app.api import product_composer as product_composer_api
from app.api import taobao_export as taobao_export_api
from app.api import npd as npd_api
from app.api import factory_settlement as factory_settlement_api
from app.api import chatbi as chatbi_api
from app.api import review_assets as review_assets_api
from app.api import campaigns as campaigns_api
from app.api import refill_sync as refill_sync_api
from app.config import get_settings
from app.dependencies import enforce_page_permission, require_authenticated
from app.middleware import AuditMiddleware

settings = get_settings()


@asynccontextmanager
async def _lifespan(app: FastAPI):
    """Phase 6: 替代 @app.on_event (FastAPI 已废弃).

    启动: 抢 PID 文件 → 写 process_started → 起看门狗 + 调度器
    关停: 停调度器 → 停看门狗 → 释放 PID + executor
    """
    # Tachikoma 连接模式只暴露稳定身份和少量只读合同，不执行任何 ERP
    # 启动恢复、数据种入、后台调度、看门狗、机器人或业务任务。
    from app.tachikoma_connection import connection_only
    if connection_only():
        yield
        return

    # 启动即打印当前运行版本, 方便对照「拉的代码是否最新」
    try:
        from app.version import get_version
        v = get_version()
        logging.getLogger("panse.startup").info(
            "启动版本: commit=%s branch=%s 部署于=%s (%s)",
            v.get("commit"), v.get("branch") or "?",
            v.get("deployed_at") or "未知", v.get("commit_message", "")[:60],
        )
    except Exception:
        pass

    # 启动恢复: 上次进程被杀时残留的导入作业标记为 failed (否则永远卡 running)
    try:
        from app.services import import_job_service
        n = import_job_service.recover_orphaned_jobs()
        if n:
            logging.getLogger("panse.startup").warning("恢复 %d 个中断的导入作业为 failed", n)
    except Exception:  # pragma: no cover
        pass

    # 安全: 把仍在用默认弱密码 'admin' 的账号标记为必须改密 (外网 DDNS 暴露前主动保护)
    try:
        from app.database import SessionLocal as _SL
        from app.services import auth_service
        with _SL() as _s:
            k = auth_service.flag_weak_default_passwords(_s)
            if k:
                logging.getLogger("panse.startup").warning(
                    "%d 个账号仍在用默认密码 admin, 已标记必须改密", k)
    except Exception:  # pragma: no cover
        pass

    # 公式规则兜底种入 (Plan C2): 新库/测试库启动即有内置定价规则; 双重幂等
    try:
        from app.database import SessionLocal as _SL_F
        from app.services import formula_engine_service
        with _SL_F() as _s:
            inserted = formula_engine_service.seed_builtin_rules(_s)
            formula_engine_service.align_rules_to_builtin(_s)
            _s.commit()
            if inserted:
                logging.getLogger("panse.startup").info("公式规则种入 %d 条", inserted)
    except Exception:  # pragma: no cover - 种入失败不阻断启动
        logging.getLogger("panse.startup").warning("公式规则种入失败", exc_info=True)

    # 新品开发(NPD)阶段定义种入 (P0): 新库/升级后启动即有 24阶段+5门, 幂等
    try:
        from app.database import SessionLocal as _SL_N
        from app.services import npd_service
        with _SL_N() as _s:
            n = npd_service.seed_stages(_s)
            nt = npd_service.seed_task_templates(_s)
            ni = npd_service.seed_inspection_templates(_s)
            if n or nt or ni:
                logging.getLogger("panse.startup").info(
                    "NPD 种入: 阶段 %d / 任务模板 %d / 验收模板 %d 条", n, nt, ni)
    except Exception:  # pragma: no cover - 种入失败不阻断启动
        logging.getLogger("panse.startup").warning("NPD 种入失败", exc_info=True)

    try:
        from app.database import SessionLocal as _SL_FS
        from app.services import factory_settlement_service as _fss
        with _SL_FS() as _s:
            na = _fss.seed_default_aliases(_s)
            if na:
                _s.commit()
                logging.getLogger("panse.startup").info("木作月结供应商别名种入 %d 条", na)
    except Exception:  # pragma: no cover - 种入失败不阻断启动
        logging.getLogger("panse.startup").warning("木作月结别名种入失败", exc_info=True)

    # 看门狗 (Phase 1+5: PID 文件 / 60s 健康检查)
    if os.environ.get("DISABLE_WATCHDOG") != "1":
        from app.database import SessionLocal
        from app.services import system_monitor
        db = SessionLocal()
        try:
            killed = system_monitor.claim_pid_file(db)
            if killed:
                db.commit()
            system_monitor.log_process_started(db)
            db.commit()
        except Exception:  # pragma: no cover
            db.rollback()
        finally:
            db.close()
        system_monitor.start_background(interval_sec=60)

    # #15 启动补拉: 容器一起来就扫共享目录, 把 PC 自跑下载的报表导进来(群晖挂过/刚重启 → 上线即自拉)。
    # 后台线程跑, 不阻塞启动; run_ingest 不开浏览器、幂等(file_hash 防重)。
    if os.environ.get("DISABLE_SCHEDULER") != "1":
        import threading as _th

        def _startup_ingest() -> None:
            try:
                from app.database import SessionLocal as _SLI
                from app.services import agent_ingest_service as _ai
                with _SLI() as _s:
                    r = _ai.run_ingest(_s)
                logging.getLogger("panse.startup").info("启动补拉 run_ingest: %s", r)
            except Exception:  # pragma: no cover
                logging.getLogger("panse.startup").warning("启动补拉失败", exc_info=True)

        _th.Thread(target=_startup_ingest, daemon=True).start()

    # Phase 1A: 调度器独立开关, 不绑死在 watchdog 上
    if os.environ.get("DISABLE_SCHEDULER") != "1":
        from app.services import scheduler as scheduler_service
        scheduler_service.start()

    # 飞书机器人长连接 (opt-in): 配了 app_id/secret + 环境变量 ENABLE_FEISHU_BOT=1 才起,
    # 默认不动现有部署 (飞书表同步用户不会被动开机器人)。
    if os.environ.get("ENABLE_FEISHU_BOT") == "1":
        import threading as _feishu_threading

        def _start_feishu_bot() -> None:
            try:
                from app.services import feishu_ws_service
                feishu_ws_service.start()
            except Exception:  # pragma: no cover
                logging.getLogger("panse.startup").warning(
                    "飞书机器人长连接启动失败", exc_info=True
                )

        # 飞书 SDK 及其模型较大；NAS 磁盘繁忙时同步导入会阻塞整个 API 启动。
        # 长连接本来就是附属能力，改为后台加载，ERP 健康检查不再受其影响。
        _feishu_threading.Thread(
            target=_start_feishu_bot,
            name="feishu-bot-startup",
            daemon=True,
        ).start()

    yield   # 应用运行期

    # ----- 关停 -----
    if os.environ.get("DISABLE_SCHEDULER") != "1":
        from app.services import scheduler as scheduler_service
        scheduler_service.shutdown()
    if os.environ.get("DISABLE_WATCHDOG") != "1":
        from app.services import system_monitor
        system_monitor.stop_background()
        system_monitor.release_pid_file()
    # Phase 6: shutdown import job executor
    try:
        from app.services import import_job_service
        import_job_service.shutdown_executor()
    except Exception:  # pragma: no cover
        pass


# 全局依赖 enforce_page_permission: 子账号页面权限的后端纵深防御 (受限子账号绕过前端直调 API 也拦 403)。
# 挂全局 → 覆盖所有路由, 无需逐个 router 改; 放行路径/未登录/admin/不受限账号在依赖内快速短路。
app = FastAPI(
    title="Panse ERP", version="0.1.0", lifespan=_lifespan,
    # authn 先于 authz: require_authenticated 堵匿名访问 (影子模式默认只记不拦),
    # enforce_page_permission 再管子账号页面权限。
    dependencies=[Depends(require_authenticated), Depends(enforce_page_permission)],
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.cors_origins.split(",") if o.strip()],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
# 外网/群晖访问优化: JSON 响应 gzip 压缩 (大列表体积降 ~80%, 1KB 以下不压)
# 图片(/api/gallery/)走原始字节、不二次 gzip — 已压缩内容再压白耗 CPU 且破坏流式 (评审#10)。
from starlette.middleware.gzip import GZipMiddleware  # noqa: E402
_GZIP_SKIP_PREFIXES = ("/api/gallery/",)


class _PathAwareGZip:
    """图片等已压缩路径跳过 gzip, 其余仍走 GZipMiddleware。"""
    def __init__(self, app, minimum_size: int = 1024):
        self._plain = app
        self._gzip = GZipMiddleware(app, minimum_size=minimum_size)

    async def __call__(self, scope, receive, send):
        if scope.get("type") == "http" and any(
                scope.get("path", "").startswith(p) for p in _GZIP_SKIP_PREFIXES):
            return await self._plain(scope, receive, send)
        return await self._gzip(scope, receive, send)


app.add_middleware(_PathAwareGZip, minimum_size=1024)
app.add_middleware(AuditMiddleware)
# 幂等: 带 Idempotency-Key 的写请求重复到达直接 409, 防双击/重试重复创建 (优化 #3)
from app.idempotency import IdempotencyMiddleware  # noqa: E402
app.add_middleware(IdempotencyMiddleware)


@app.middleware("http")
async def _tachikoma_connection_guard(request: Request, call_next):
    from app.tachikoma_connection import connection_only, connection_path_allowed
    if connection_only() and not connection_path_allowed(request.method, request.url.path):
        return JSONResponse(
            status_code=403,
            content={
                "code": "PRODUCTION_EXECUTION_DISABLED",
                "detail": "Panse ERP is running in Tachikoma connection-only mode",
                "service": "panse-system",
            },
        )
    return await call_next(request)


@app.middleware("http")
async def _log_requests(request: Request, call_next):
    """每个请求记一行带时间戳的日志: rid 方法 路径 状态 耗时. 方便排查「卡在哪/谁报错」."""
    rid = request.headers.get("x-request-id") or uuid.uuid4().hex[:12]
    request_id_ctx.set(rid)
    request.state.request_id = rid
    start = time.monotonic()
    path = request.url.path
    try:
        response = await call_next(request)
    except Exception as e:  # 未捕获异常也要留痕
        dur = (time.monotonic() - start) * 1000
        _req_logger.error("[%s] %s %s -> EXC %s (%.0fms)", rid, request.method, path,
                          type(e).__name__, dur)
        raise
    dur = (time.monotonic() - start) * 1000
    response.headers["X-Request-ID"] = rid
    # 高频轻量请求(健康检查/SSE保活流/日志轮询)降到 debug, 不写 INFO 访问日志(减少磁盘琐碎写入);
    # 慢请求 (>3s) 升到 warning
    if path in _QUIET_LOG_PATHS or path.startswith("/api/logs/"):
        level = logging.DEBUG
    elif dur > 3000:
        level = logging.WARNING
    else:
        level = logging.INFO
    _req_logger.log(level, "[%s] %s %s -> %s (%.0fms)", rid, request.method, path,
                    response.status_code, dur)
    return response


# ---- 统一错误响应: 保留 FastAPI 原有 detail 形状 (前端读 .detail), 仅追加 request_id ----
@app.exception_handler(StarletteHTTPException)
async def _http_exc_handler(request: Request, exc: StarletteHTTPException):
    rid = getattr(request.state, "request_id", request_id_ctx.get())
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail, "request_id": rid},
        headers={"X-Request-ID": rid, **(exc.headers or {})},
    )


@app.exception_handler(RequestValidationError)
async def _validation_exc_handler(request: Request, exc: RequestValidationError):
    rid = getattr(request.state, "request_id", request_id_ctx.get())
    return JSONResponse(
        status_code=422,
        content={"detail": exc.errors(), "request_id": rid},
        headers={"X-Request-ID": rid},
    )


@app.exception_handler(Exception)
async def _unhandled_exc_handler(request: Request, exc: Exception):
    rid = getattr(request.state, "request_id", request_id_ctx.get())
    logging.getLogger("panse.error").exception("[%s] 未处理异常 %s %s", rid,
                                                request.method, request.url.path)
    return JSONResponse(
        status_code=500,
        content={"detail": "服务器内部错误，请稍后重试或联系管理员。", "request_id": rid},
        headers={"X-Request-ID": rid},
    )

# Phase 6 P0: 限速 (screenshots / importer 防刷)
from app.rate_limit import install_rate_limit  # noqa: E402
install_rate_limit(app)

app.include_router(auth_api.router)
app.include_router(audit_api.router)
app.include_router(materials_api.router)
app.include_router(products_api.router)
app.include_router(inventory_api.router)
app.include_router(part_returns_api.router)
app.include_router(product_inventory_api.router)
app.include_router(bom_api.router)
app.include_router(ops_checklist_api.router)
app.include_router(settlements_api.router)
app.include_router(factory_recon_api.router)
app.include_router(factory_orders_api.router)
app.include_router(chatbi_api.router)
app.include_router(review_assets_api.router)
app.include_router(factory_statement_api.router)
app.include_router(cs_integration_api.router)
app.include_router(competitor_api.router)
app.include_router(shop_deposits_api.router)
app.include_router(staff_salary_api.router)
app.include_router(imports_api.router)
app.include_router(exceptions_api.router)
app.include_router(feishu_api.router)
app.include_router(match_api.router)
app.include_router(quotes_api.router)
app.include_router(orders_api.router)
app.include_router(shipments_api.router)
app.include_router(producibility_api.router)
app.include_router(finance_api.router)
app.include_router(scanners_api.router)
app.include_router(ai_api.router)
app.include_router(marketing_api.router)
app.include_router(reports_api.router)
app.include_router(customization_api.router)
app.include_router(admin_api.router)
app.include_router(suppliers_api.router)
app.include_router(purchases_api.router)
app.include_router(procurement_api.router)
app.include_router(procurement_agent_api.router)
app.include_router(monthly_settlement_api.router)
app.include_router(field_changes_api.router)
app.include_router(exports_api.router)
app.include_router(gallery_api.router)
app.include_router(manuals_api.router)
app.include_router(importer_api.router)
app.include_router(logs_api.router)
app.include_router(alerts_api.router)
app.include_router(scheduler_api.router)
app.include_router(screenshots_api.router)
app.include_router(aftersales_api.router)
app.include_router(accounting_api.router)
app.include_router(briefings_api.router)
app.include_router(briefings_api.supplier_router)
app.include_router(customers_api.router)
app.include_router(pricing_api.router)
app.include_router(pricing_list_api.router)
app.include_router(pricing_list_api.formula_router)
app.include_router(table_explorer_api.router)
app.include_router(search_api.router)
app.include_router(approvals_api.router)
app.include_router(dashboard_api.router)
app.include_router(taobao_listings_api.router)
app.include_router(product_composer_api.router)
app.include_router(taobao_export_api.router)
app.include_router(web_agent_api.router)
app.include_router(wechat_callback_api.router)
app.include_router(npd_api.router)
app.include_router(factory_settlement_api.router)
app.include_router(campaigns_api.router)
app.include_router(refill_sync_api.router)
from app import tachikoma_connection as tachikoma_connection_api  # noqa: E402
app.include_router(tachikoma_connection_api.router)


@app.get("/api/health")
def health():
    from app.tachikoma_connection import identity_payload
    return identity_payload()


@app.get("/api/ready")
def ready():
    """就绪探针: 真正连一下 DB(SELECT 1)。/api/health 是浅探活只证明进程在,
    DB 断/盘满时它仍 200; /api/ready 让这类故障对部署层(健康检查/负载均衡)可见。"""
    from sqlalchemy import text
    from app.database import SessionLocal
    db = SessionLocal()
    try:
        db.execute(text("SELECT 1"))
        return {"ready": True}
    except Exception as e:  # noqa: BLE001 — 探针要把任何 DB 故障翻成 503
        return JSONResponse(status_code=503,
                            content={"ready": False, "error": type(e).__name__})
    finally:
        db.close()


@app.get("/api/version")
def version():
    """当前运行的代码版本 — 用于核对是否已同步到最新 commit. 公开 (不需登录)."""
    from app.version import get_version
    return get_version()
