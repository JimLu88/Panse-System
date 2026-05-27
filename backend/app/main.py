import logging
import os
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

# 全局日志: 带时间戳 (UTC→本地由容器时区决定), 输出到 stdout → docker logs api 可见
logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
# 同时把最近日志留在内存, 供 /api/logs/recent 在界面上查看
from app.log_buffer import install_ring_buffer  # noqa: E402
install_ring_buffer()
_req_logger = logging.getLogger("panse.request")

from app.api import accounting as accounting_api
from app.api import admin as admin_api
from app.api import aftersales as aftersales_api
from app.api import ai as ai_api
from app.api import alerts as alerts_api
from app.api import approvals as approvals_api
from app.api import audit as audit_api
from app.api import auth as auth_api
from app.api import briefings as briefings_api
from app.api import customers as customers_api
from app.api import pricing_diagnosis as pricing_api
from app.api import pricing as pricing_list_api
from app.api import dashboard as dashboard_api
from app.api import search as search_api
from app.api import bom as bom_api
from app.api import customization as customization_api
from app.api import exceptions as exceptions_api
from app.api import feishu as feishu_api
from app.api import finance as finance_api
from app.api import inventory as inventory_api
from app.api import marketing as marketing_api
from app.api import match as match_api
from app.api import materials as materials_api
from app.api import orders as orders_api
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
from app.config import get_settings
from app.middleware import AuditMiddleware

settings = get_settings()


@asynccontextmanager
async def _lifespan(app: FastAPI):
    """Phase 6: 替代 @app.on_event (FastAPI 已废弃).

    启动: 抢 PID 文件 → 写 process_started → 起看门狗 + 调度器
    关停: 停调度器 → 停看门狗 → 释放 PID + executor
    """
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

    # Phase 1A: 调度器独立开关, 不绑死在 watchdog 上
    if os.environ.get("DISABLE_SCHEDULER") != "1":
        from app.services import scheduler as scheduler_service
        scheduler_service.start()

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


app = FastAPI(title="Panse ERP", version="0.1.0", lifespan=_lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.cors_origins.split(",") if o.strip()],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(AuditMiddleware)


@app.middleware("http")
async def _log_requests(request: Request, call_next):
    """每个请求记一行带时间戳的日志: 方法 路径 状态 耗时. 方便排查「卡在哪/谁报错」."""
    start = time.monotonic()
    path = request.url.path
    try:
        response = await call_next(request)
    except Exception as e:  # 未捕获异常也要留痕
        dur = (time.monotonic() - start) * 1000
        _req_logger.error("%s %s -> EXC %s (%.0fms)", request.method, path,
                          type(e).__name__, dur)
        raise
    dur = (time.monotonic() - start) * 1000
    # 健康检查太频繁, 降级到 debug; 慢请求 (>3s) 升到 warning
    if path == "/api/health":
        level = logging.DEBUG
    elif dur > 3000:
        level = logging.WARNING
    else:
        level = logging.INFO
    _req_logger.log(level, "%s %s -> %s (%.0fms)", request.method, path,
                    response.status_code, dur)
    return response

# Phase 6 P0: 限速 (screenshots / importer 防刷)
from app.rate_limit import install_rate_limit  # noqa: E402
install_rate_limit(app)

app.include_router(auth_api.router)
app.include_router(audit_api.router)
app.include_router(materials_api.router)
app.include_router(products_api.router)
app.include_router(inventory_api.router)
app.include_router(product_inventory_api.router)
app.include_router(bom_api.router)
app.include_router(exceptions_api.router)
app.include_router(feishu_api.router)
app.include_router(match_api.router)
app.include_router(quotes_api.router)
app.include_router(orders_api.router)
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
app.include_router(search_api.router)
app.include_router(approvals_api.router)
app.include_router(dashboard_api.router)


@app.get("/api/health")
def health():
    return {"ok": True}


@app.get("/api/version")
def version():
    """当前运行的代码版本 — 用于核对是否已同步到最新 commit. 公开 (不需登录)."""
    from app.version import get_version
    return get_version()
