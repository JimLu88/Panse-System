"""导入/改数据后的实时同步 (用户拍板 2026-06-17)。

痛点: 对账异常池只在每日 09:50 cron 才重写, 用户导入补单/流水、清理异常后, 待办里的
异常清单要等第二天才跟上 —— "感觉和清理后的数据不挂钩"。

本服务在关键写操作后**后台立即重算**: 成本兜底(缺成本异常) + 14 条对账规则(写异常池),
让待办/异常清单几秒内跟上最新数据, 而不是等到点。

设计:
- 防抖合并: 同一时刻只允许一个同步线程在跑; 期间来的新请求置 pending, 跑完再补一轮
  (把一批连续导入合并成一次重算, 避免每行都触发)。
- 独立 Session: 后台线程用自己的 SessionLocal, 不碰请求线程的 Session (SQLAlchemy 非线程安全)。
- 永不抛出: 同步失败只记日志, 不影响触发它的导入请求。

用法: 任何写完数据的地方调一句 `realtime_sync_service.trigger("import:refill")` 即可,
立即返回, 实际重算在后台。
"""
from __future__ import annotations

import logging
import threading

_logger = logging.getLogger("panse.realtime_sync")

_lock = threading.Lock()
_running = False
_pending = False
_last_result: dict = {}


def _do_resync() -> dict:
    """跑一轮全量自动对账流水线 (用独立 Session)。

    用户拍板 2026-06-17: 财务对账面板右侧那一排按钮(归类流水/退款识别/工厂流水匹配/重新核销/
    经营支出配流水/立即对账)全部改成自动 —— 导入后/每日由本流水线跑, 用户不用再点。
    每步独立 try+commit, 一步失败不连累后面。AI走查(花钱)与工厂别名(配置)仍手动, 不进自动。
    """
    from app.database import SessionLocal

    out: dict = {}
    db = SessionLocal()

    def _step(name: str, fn) -> None:
        try:
            r = fn(db)
            db.commit()
            out[name] = r if isinstance(r, (dict, int)) else "ok"
        except Exception as e:  # noqa: BLE001
            try:
                db.rollback()
            except Exception:
                pass
            out[name] = f"err: {type(e).__name__}"
            _logger.exception("实时同步-%s 失败: %s", name, e)

    try:
        from app.services import (
            alipay_amount_match_service, alipay_flow_router_service,
            custom_order_reconcile_service, data_quality_service,
            exception_recheck_service, expense_flow_match_service, flow_refund_service,
            factory_reconciliation_service, order_cost_service, order_sync_service,
            reconciliation_service, scanner_service, smart_matching_service,
        )
        # ── 0) 订单缺 product_code 经 sku_code 回填 (导入丢编码补救; 排行/汇总短名都靠它) ──
        _step("backfill_product_code", lambda d: order_sync_service.backfill_product_code(d))
        # ── 1) 支付宝流水归类/核销/配单 (原「重新核销」「归类流水」「自动配流水」按钮) ──
        _step("smart_match", lambda d: smart_matching_service.run(d) and None)
        _step("route_flows", lambda d: alipay_flow_router_service.run_all(d) and None)
        _step("amount_match", lambda d: alipay_amount_match_service.match(d) and None)
        # ── 2) 退款对识别 (原「退款对识别」按钮) ──
        _step("detect_refunds", lambda d: flow_refund_service.detect_refunds(d))
        # ── 3) 工厂流水匹配 (原「工厂流水匹配」按钮); 导入工厂对账单后自动配支付宝付款 ──
        _step("factory_match", lambda d: factory_reconciliation_service.match_factory_alipay_by_bill_amount(d))
        # ── 4) 经营支出配流水 (原「自动配流水」按钮) ──
        _step("expense_match", lambda d: expense_flow_match_service.match_expense_flows(d) and None)
        # ── 5) 成本兜底 + 缺成本异常 (#30) ──
        _step("cost", lambda d: order_cost_service.auto_cost_backfill(d))
        # ── 5a) 定制单推演成本: 缺成本依据的定制单自动算成本(规则→85%兜底)写回 theoretical_cost,
        #        喂给全系统会计成本计算; 清「定制订单缺成本依据」异常 (用户拍板 2026-06-17) ──
        _step("custom_cost", lambda d: custom_order_reconcile_service.auto_backfill_custom_costs(d))
        # ── 5b) 数据质量扫描写异常池 (含错配单 cost_exceeds_paid 等 23 项) ──
        _step("data_quality", lambda d: data_quality_service.run_all(d))
        # ── 5c) 旧版完整性扫描 (负库存/发货早于下单/非正价/定制缺价/悬空编码/重复流水 6 项) ──
        #        并入实时, 这样「全量扫描」按钮可撤掉 —— 所有问题都实时进异常 (用户拍板 2026-06-17)
        _step("scanners", lambda d: scanner_service.run_all(d))
        # ── 6) 14 条对账规则写异常池 (原「立即对账」按钮) ──
        _step("reconcile", lambda d: reconciliation_service.run_all(d, record_exceptions=True) and "ok")
        # ── 7) 批量销账: 数据已修的异常自动关闭 ──
        _step("closed", lambda d: exception_recheck_service.bulk_close_resolved(d))
    finally:
        db.close()
    return out


def _worker() -> None:
    global _running, _pending, _last_result
    while True:
        _last_result = _do_resync()
        with _lock:
            if not _pending:
                _running = False
                return
            _pending = False  # 期间有新触发 → 再补一轮 (合并连续导入)


def trigger(reason: str = "") -> dict:
    """请求一次后台实时同步 (防抖合并)。立即返回, 不阻塞调用方。"""
    global _running, _pending
    _logger.info("实时同步触发: %s", reason or "(unspecified)")
    with _lock:
        if _running:
            _pending = True
            return {"scheduled": True, "merged": True, "reason": reason}
        _running = True
    threading.Thread(target=_worker, name="realtime-sync", daemon=True).start()
    return {"scheduled": True, "merged": False, "reason": reason}


def run_sync_blocking(reason: str = "") -> dict:
    """同步(阻塞)跑一轮 —— 供手动「立即同步」端点用, 跑完返回结果。"""
    _logger.info("实时同步(阻塞)触发: %s", reason or "(unspecified)")
    return _do_resync()


def status() -> dict:
    with _lock:
        return {"running": _running, "pending": _pending, "last_result": _last_result}
