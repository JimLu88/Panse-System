# -*- coding: utf-8 -*-
"""Web-Agent 下载产物 → ERP 自动导入 (2026-06-12 用户拍板"全部一次性打通")。

职责:
- run_ingest: 扫共享 output 目录, 按子目录分类 → 调既有导入器入库;
  防重靠 imported_files.file_hash (同文件只导一次); 加密发货报表标「待口令」;
  余额截图归档标「截图待读数」(待企业号 API 上线/OCR 确认队列)。
- orchestrate: 按更新间隔触发 Web-Agent 任务(串行, 等 job 完成) → 收尾 run_ingest
  → 飞书汇总。登录态缺失/超时的任务标「待人工」, 绝不无限重试 (交接方案 §7.6)。

更新间隔 (用户拍板): 订单默认 1 天, 余额/流水默认 3 天, settings 可改。
"""
from __future__ import annotations

import hashlib
import io
import json
import logging
import os
import threading
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.import_file import ImportedFile
from app.models.marketing import PromotionFlow
from app.services import import_storage, settings_service, web_agent_service

_log = logging.getLogger("panse.agent_ingest")

OUTPUT_DIR = Path(os.environ.get("AGENT_OUTPUT_DIR", "/app/agent_output"))

# settings 键
KEY_INTERVAL_ORDERS = "web_agent_interval_orders"      # 天, 默认 1
KEY_INTERVAL_BALANCE = "web_agent_interval_balance"    # 天, 默认 3 (余额+流水)
KEY_STATE = "web_agent_state"                          # 各类别最近成功时间 JSON
KEY_LAST_INGEST = "web_agent_last_ingest"              # 最近一次扫描报告 JSON
KEY_ORCH_STATE = "web_agent_orch_state"                # 编排进行中状态 JSON

_OOXML_ENCRYPTED_MAGIC = bytes.fromhex("d0cf11e0a1b11ae1")

# 编排任务分组 (Web-Agent 任务 id, 见 Panse-Web-Agent app/tasks/definitions.py)
ORDERS_TASKS = ["taobao_orders"]
BALANCE_FLOW_TASKS = [
    "wechat_bill", "wanxiangtai", "wanshifu",
    "bal_taobao_aggregate", "bal_ads", "bal_wanshifu",
    # 支付宝不进自动编排 (2026-06-12 修复误发二维码):
    #   · 企业号(9a) 余额走官方 API, 每次编排已用 refresh_alipay_balances 精确刷, 永不浏览器扫码;
    #   · 主力号(8812/main) 是个人号无 API, 改成"只在用户专门触发时才扫", 不自动排队弹码。
]
# 暂不编排: 支付宝流水 (企业号待官方 API 上线; 主力号需每次扫码, 待飞书推码方案)
SKIPPED_TASKS = {
    "alipay_9a": "支付宝企业号流水: 等官方 API 审核上线后走 API, 不走浏览器",
    "alipay_main": "支付宝主力号流水: 每次需本人扫码, 待飞书推二维码方案",
    "logistics_bill": "物流账单: 待提供承运商入口",
}

_orch_lock = threading.Lock()
_scan_lock = threading.Lock()

KEY_PENDING_SCAN = "web_agent_pending_scan"   # 待扫码任务 id 列表 (JSON)

# Web-Agent 支付宝账号名 → ERP 账户余额表 account_name (企业号走 API 精确取数)
ALIPAY_ACCT_MAP = {"企业号": "支付宝-企业账号"}


def get_pending_scans(db: Session) -> list:
    try:
        lst = json.loads(settings_service.get(db, KEY_PENDING_SCAN) or "[]")
        return lst if isinstance(lst, list) else []
    except json.JSONDecodeError:
        return []


def _add_pending_scan(db: Session, task_id: str) -> None:
    lst = get_pending_scans(db)
    if task_id not in lst:
        lst.append(task_id)
        settings_service.set_value(db, KEY_PENDING_SCAN, json.dumps(lst),
                                   description="自动取数: 待用户扫码的任务")
        db.commit()


# 默认可扫码刷新的余额任务 (淘宝聚合一扫覆盖淘宝SSO的推广/万师傅)。
# 不含支付宝: 企业号走 API、主力号(8812)需专门触发, 避免裸『扫码』误弹支付宝二维码 (2026-06-12)。
DEFAULT_SCAN_TASKS = ["bal_taobao_aggregate", "bal_ads", "bal_wanshifu"]


def start_pending_scans(db: Session) -> dict:
    """用户在飞书回复『扫码』后调用: 后台依次跑待扫任务 (wait_scan=True) —
    发大二维码到飞书、保持浏览器开等扫 ≤10分钟; 扫成功的从待扫清单移除并扫描导入。
    没有记录的待扫任务时, 默认跑全部余额截图任务 (主动刷新登录态)。"""
    tasks = get_pending_scans(db) or list(DEFAULT_SCAN_TASKS)
    # 企业号支付宝永远只走官方 API, 滤掉任何残留的企业号扫码任务, 杜绝误发二维码 (2026-06-12)。
    tasks = [t for t in tasks if "alipay_9a" not in t]
    if not tasks:
        return {"started": False, "reason": "无待扫码任务 (支付宝企业号走 API, 不扫码)"}
    if not _scan_lock.acquire(blocking=False):
        return {"started": False, "reason": "已有扫码流程在进行中"}

    def _run() -> None:
        from app.database import SessionLocal
        d = SessionLocal()
        try:
            done = []
            for tid in list(tasks):
                r = web_agent_service.run_task(d, tid, {"wait_scan": True})
                if r.get("job"):
                    final = web_agent_service.wait_job(d, r["job"], timeout_s=720, poll_s=8)
                    if (final.get("status") or "").lower() in ("done", "ok", "success"):
                        done.append(tid)
            remain = [t for t in get_pending_scans(d) if t not in done]
            settings_service.set_value(d, KEY_PENDING_SCAN, json.dumps(remain))
            d.commit()
            run_ingest(d)   # 扫到的余额截图一并导入
        except Exception:  # noqa: BLE001
            _log.exception("扫码流程线程异常")
            d.rollback()
        finally:
            d.close()
            _scan_lock.release()

    threading.Thread(target=_run, name="web-agent-scan", daemon=True).start()
    return {"started": True, "tasks": tasks}


# ----------------------------- 工具 ----------------------------- #

def _get_int(db: Session, key: str, default: int) -> int:
    try:
        return int(settings_service.get(db, key) or default)
    except (TypeError, ValueError):
        return default


def _load_json(db: Session, key: str) -> dict:
    try:
        return json.loads(settings_service.get(db, key) or "{}")
    except json.JSONDecodeError:
        return {}


def _save_json(db: Session, key: str, data: dict) -> None:
    settings_service.set_value(db, key, json.dumps(data, ensure_ascii=False))


def _hash_exists(db: Session, file_hash: str) -> Optional[ImportedFile]:
    return db.execute(
        select(ImportedFile).where(ImportedFile.file_hash == file_hash)
        .order_by(ImportedFile.id.desc())
    ).scalars().first()


def _fresh_shipping_password(db: Session, *, max_age_min: int = 60) -> Optional[str]:
    """飞书最近收到的发货报表口令 (一次一密)。超过 max_age_min 分钟视为过期不用。"""
    pwd = settings_service.get(db, "taobao_shipping_pwd_latest", env_fallback=False)
    if not pwd:
        return None
    at = settings_service.get(db, "taobao_shipping_pwd_at", env_fallback=False)
    if at:
        try:
            ts = datetime.fromisoformat(at)
            now = datetime.now(ts.tzinfo) if ts.tzinfo else datetime.now()
            if now - ts > timedelta(minutes=max_age_min):
                return None
        except ValueError:
            pass
    return pwd


def _report_to_dict(rep) -> dict:
    """导入器报告 (dataclass/dict) → 可 JSON 的摘要 (只留标量与短列表)。"""
    src = rep if isinstance(rep, dict) else dict(vars(rep))
    out = {}
    for k, v in src.items():
        if isinstance(v, (int, float, str, bool)) or v is None:
            out[k] = v
        elif isinstance(v, list):
            out[k] = [str(x)[:120] for x in v[:5]]
    return out


# ----------------------------- 分类导入 ----------------------------- #

def _classify(rel: Path) -> str:
    """相对路径 → 类别。子目录约定见 Web-Agent definitions.py output_subdir。"""
    parts = [p.lower() for p in rel.parts]
    if "balance" in parts:
        return "balance"
    if "taobao" in parts:
        return "taobao_report"
    if "聚合账单" in rel.parts:
        return "settlement"
    if "wanxiangtai" in parts or "ads" in parts:
        return "promotion"
    if "wanshifu" in parts:
        return "wanshifu"
    if any("alipay" in p for p in parts):
        return "alipay"
    return "other"


def _import_wanxiangtai_csv(db: Session, raw: bytes) -> dict:
    """万相台无界 CSV → PromotionFlow。
    列: 记账时间,交易日期,收支类型,交易类型,操作金额(元),操作后余额(元),备注
    sync_key = wxt:<记账时间>:<金额> 幂等; 收支类型 收入→充值 / 支出→支出。
    """
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("gbk", errors="replace")
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if not lines:
        return {"inserted": 0, "error": "空文件"}
    header = [h.strip() for h in lines[0].split(",")]

    def col(name_part: str) -> Optional[int]:
        for i, h in enumerate(header):
            if name_part in h:
                return i
        return None

    i_time, i_date = col("记账时间"), col("交易日期")
    i_io, i_kind = col("收支类型"), col("交易类型")
    i_amt, i_remark = col("操作金额"), col("备注")
    if i_time is None or i_amt is None:
        return {"inserted": 0, "error": f"表头不识别: {header}"}
    inserted = skipped = 0
    for ln in lines[1:]:
        cells = [c.strip() for c in ln.split(",")]
        if len(cells) <= max(i_time, i_amt) or not cells[i_amt]:
            continue
        sync_key = f"wxt:{cells[i_time]}:{cells[i_amt]}"
        if db.execute(select(PromotionFlow).where(
                PromotionFlow.sync_key == sync_key)).scalar_one_or_none():
            skipped += 1
            continue
        io_type = cells[i_io] if i_io is not None and i_io < len(cells) else ""
        kind = cells[i_kind] if i_kind is not None and i_kind < len(cells) else ""
        tdate = None
        if i_date is not None and i_date < len(cells) and cells[i_date]:
            try:
                tdate = date.fromisoformat(cells[i_date][:10])
            except ValueError:
                pass
        remark_parts = [kind]
        if i_remark is not None and i_remark < len(cells):
            remark_parts += [c for c in cells[i_remark:] if c]
        db.add(PromotionFlow(
            sync_key=sync_key,
            transaction_date=tdate,
            flow_type="充值" if io_type == "收入" else "支出",
            amount=Decimal(cells[i_amt].replace(",", "") or "0"),
            remark=" ".join(p for p in remark_parts if p)[:500] or None,
        ))
        inserted += 1
    return {"inserted": inserted, "skipped_duplicate": skipped}


# 余额截图文件夹名 → ERP AccountBalance.account_name (企业号走 API 不在此, 跳过)
_BAL_OCR_MAP = {
    "淘宝聚合": "淘宝聚合账户",
    "推广": "淘宝推广账户",
    "万师傅": "万师傅",
    "支付宝主力号": "支付宝-15824198812",
}
_BAL_MAX = Decimal("5000000")   # 合理上限, 超过视为读错不写


def _ocr_balance_to_db(db: Session, path: Path, raw: bytes) -> tuple[str, str, dict]:
    """B1 (用户拍板 2026-06-12): 余额截图自动 OCR 读「可用余额」→ 置信且数字合理才写 AccountBalance;
    读不准/读不到 → 报异常(alert)、不写库(财务零损伤红线)。企业号走 API 不在此。"""
    from app.models.finance import AccountBalance
    erp_name = next((v for k, v in _BAL_OCR_MAP.items() if k in str(path)), None)
    if not erp_name:
        return ("account_balance", "pending_read",
                {"note": "余额截图已归档 — 未识别账户, 待人工确认"})

    def _flag(reason: str) -> None:
        try:
            from app.services import alert_service
            alert_service.upsert(db, kind="balance_ocr_uncertain", severity="warning",
                                 title=f"余额OCR读不准: {erp_name}",
                                 body=f"{reason} — 已归档未写库, 请人工核对截图后手填。文件: {path.name}",
                                 dedupe_key=f"balance_ocr:{erp_name}:{path.name}")
            db.commit()
        except Exception:  # noqa: BLE001
            db.rollback()

    try:
        from app.services import vision_ocr_service
        r = vision_ocr_service.parse_balance_screenshot(db, raw, account_hint=erp_name)
    except Exception as e:  # noqa: BLE001 — OCR 不可用/失败: 归档待人工, 不阻断
        _flag(f"OCR 调用失败: {type(e).__name__}")
        return ("account_balance", "pending_read", {"account": erp_name, "note": "OCR失败, 待人工"})

    avail, conf = r.get("available"), (r.get("confidence") or "").lower()
    try:
        val = Decimal(str(avail)) if avail is not None else None
    except Exception:  # noqa: BLE001
        val = None
    # 读不准/读不到/不合理 → 不写, 报异常
    if val is None or conf != "high" or val < 0 or val > _BAL_MAX:
        _flag(f"读数={avail} 置信={conf}(需 high 且 0~{_BAL_MAX})")
        return ("account_balance", "pending_read",
                {"account": erp_name, "ocr": r, "note": "读不准, 已报异常待人工"})

    today = date.today()
    prev = db.execute(
        select(AccountBalance).where(AccountBalance.account_name == erp_name)
        .order_by(AccountBalance.period_year.desc(), AccountBalance.period_month.desc())
    ).scalars().first()
    row = db.execute(select(AccountBalance).where(
        AccountBalance.account_name == erp_name,
        AccountBalance.period_year == today.year,
        AccountBalance.period_month == today.month)).scalar_one_or_none()
    if row is None:
        row = AccountBalance(account_name=erp_name, period_year=today.year,
                             period_month=today.month,
                             account_no=(prev.account_no if prev else None),
                             opening_balance=(prev.closing_balance if prev else Decimal("0")))
        db.add(row)
    row.closing_balance = val
    row.as_of_date = today
    row.remark = f"OCR自动读数(可用余额 {val}, {r.get('label_found') or ''}) {today}"
    db.commit()
    return ("account_balance", "imported",
            {"account": erp_name, "balance": str(val), "note": "OCR自动读数已写库"})


def _import_one(db: Session, category: str, path: Path, raw: bytes) -> tuple[str, str, dict]:
    """单文件导入。返回 (归档kind, 状态, 摘要)。
    状态: imported / pending_password / pending_read / unsupported"""
    if category == "taobao_report":
        from app.services import taobao_order_import
        password = None
        if raw[:8] == _OOXML_ENCRYPTED_MAGIC:
            # 加密发货报表: 取最近飞书口令(60 分钟内有效)解密; 没口令则标待口令
            password = _fresh_shipping_password(db)
            if not password:
                return ("taobao", "pending_password",
                        {"note": "加密发货报表 — 待飞书口令(转发『发货密码 xxx』到机器人)后自动解密"})
        rep = taobao_order_import.import_taobao_orders(db, path.name, raw, password=password)
        errs = getattr(rep, "errors", None)
        if errs:
            # 口令过期/错误 → 仍标待口令 (不算 error, 等用户重发新口令)
            if password and any("解密" in str(e) for e in errs):
                return ("taobao", "pending_password", {"note": str(errs[0])})
            return ("taobao", "error", _report_to_dict(rep))
        return ("taobao", "imported", _report_to_dict(rep))
    if category == "settlement":
        from app.services import settlement_import_service
        rep = settlement_import_service.import_bill(db, raw, source="agent")
        return ("settlement", "imported", _report_to_dict(rep))
    if category == "promotion":
        rep = _import_wanxiangtai_csv(db, raw)
        return ("promotion", "imported" if "error" not in rep else "unsupported", rep)
    if category == "wanshifu":
        import openpyxl
        from app.services import wanshifu_order_service
        wb = openpyxl.load_workbook(io.BytesIO(raw), data_only=True)
        rep = wanshifu_order_service.import_workbook(db, wb)
        return ("wanshifu_orders", "imported", _report_to_dict(rep))
    if category == "balance":
        return _ocr_balance_to_db(db, path, raw)
    if category == "alipay":
        return ("alipay", "pending_read",
                {"note": "支付宝账单格式待首份文件确认后接解析"})
    return ("generic", "unsupported", {"note": "未识别类别, 仅归档"})


def refresh_alipay_balances(db: Session) -> list[dict]:
    """用支付宝 API 刷新企业号等账户余额 → 写 AccountBalance(本月, 统计日期=今天)。
    走官方 API (精确, 零截图/零 OCR); 个人号无 API 的不在此列。"""
    from app.models.finance import AccountBalance
    out: list[dict] = []
    accts = web_agent_service.alipay_accounts(db)
    today = date.today()
    for acc in accts:
        erp_name = ALIPAY_ACCT_MAP.get(acc.get("name") or "")
        if not erp_name:
            continue
        r = web_agent_service.alipay_balance(db, acc.get("id"))
        raw = r.get("raw") or {}
        total = raw.get("total_amount") or r.get("balance")
        if not r.get("ok") or total is None:
            out.append({"account": erp_name, "error": r.get("msg") or r.get("error") or "无余额"})
            continue
        prev = db.execute(
            select(AccountBalance).where(AccountBalance.account_name == erp_name)
            .order_by(AccountBalance.period_year.desc(), AccountBalance.period_month.desc())
        ).scalars().first()
        row = db.execute(select(AccountBalance).where(
            AccountBalance.account_name == erp_name,
            AccountBalance.period_year == today.year,
            AccountBalance.period_month == today.month)).scalar_one_or_none()
        if row is None:
            row = AccountBalance(
                account_name=erp_name, period_year=today.year, period_month=today.month,
                account_no=(prev.account_no if prev else None),
                opening_balance=(prev.closing_balance if prev else Decimal("0")))
            db.add(row)
        row.closing_balance = Decimal(str(total))
        row.as_of_date = today
        avail = raw.get("available_amount")
        frz = raw.get("freeze_amount")
        row.remark = (f"支付宝API精确取数: 可用{avail}+冻结{frz}=总{total} ({today})"
                      if avail is not None else f"支付宝API精确取数: {total} ({today})")
        out.append({"account": erp_name, "balance": str(total)})
    db.commit()
    return out


def run_ingest(db: Session) -> dict:
    """扫 output 目录全部文件, 新文件(hash 未见过)导入+归档。幂等, 可随时跑。"""
    report: dict = {"scanned": 0, "imported": 0, "skipped_known": 0,
                    "pending": 0, "errors": 0, "files": []}
    if not OUTPUT_DIR.exists():
        report["error"] = f"共享目录不存在: {OUTPUT_DIR} (检查 compose 卷挂载)"
        return report
    state = _load_json(db, KEY_STATE)
    for path in sorted(OUTPUT_DIR.rglob("*")):
        if not path.is_file() or path.name.startswith("_"):
            continue
        if path.suffix.lower() not in (".xlsx", ".xls", ".csv", ".zip", ".png"):
            continue
        rel = path.relative_to(OUTPUT_DIR)
        if rel.parts and rel.parts[0] == "agent_selftest":
            continue
        category = _classify(rel)
        report["scanned"] += 1
        raw = path.read_bytes()
        file_hash = hashlib.sha256(raw).hexdigest()
        if _hash_exists(db, file_hash) is not None:
            report["skipped_known"] += 1
            continue
        entry = {"path": str(rel), "category": category}
        try:
            kind, status, summary = _import_one(db, category, path, raw)
            summary["agent_status"] = status
            res = import_storage.archive(
                db, content=raw, original_name=path.name, kind=kind,
                source="api", row_summary=summary)
            db.commit()
            entry.update({"status": status, "summary": summary, "file_id": res.file.id})
            if status == "imported":
                report["imported"] += 1
                state[category] = datetime.now().isoformat(timespec="seconds")
            else:
                report["pending"] += 1
        except Exception as e:  # noqa: BLE001 - 单文件失败不阻断批量
            db.rollback()
            _log.warning("agent ingest 失败 %s", rel, exc_info=True)
            entry.update({"status": "error",
                          "summary": {"error": f"{type(e).__name__}: {e}"}})
            report["errors"] += 1
        report["files"].append(entry)
    state["last_ingest_at"] = datetime.now().isoformat(timespec="seconds")
    _save_json(db, KEY_STATE, state)
    _save_json(db, KEY_LAST_INGEST, report)
    db.commit()
    return report


# ----------------------------- 编排 ----------------------------- #

def _due(state: dict, category: str, interval_days: int, force: bool) -> bool:
    if force:
        return True
    last = state.get(category)
    if not last:
        return True
    try:
        return datetime.fromisoformat(last) <= datetime.now() - timedelta(days=interval_days)
    except ValueError:
        return True


def orchestrate(db: Session, *, force: bool = False) -> dict:
    """每日编排: 探活 → 按更新间隔触发到期任务(串行) → 扫描导入 → 汇总。"""
    out: dict = {"started_at": datetime.now().isoformat(timespec="seconds"),
                 "tasks": [], "pending_manual": [], "skipped": []}
    hb = web_agent_service.health(db)
    if not hb.get("online"):
        out["agent_offline"] = hb.get("error", "无法连接")
        out["ingest"] = run_ingest(db)   # Agent 掉线也把已有文件扫了
        _save_json(db, KEY_ORCH_STATE, {**out, "running": False})
        db.commit()
        return out

    tasks_info = {t["id"]: t for t in (web_agent_service.list_tasks(db).get("tasks") or [])}
    state = _load_json(db, KEY_STATE)
    iv_orders = _get_int(db, KEY_INTERVAL_ORDERS, 1)
    iv_balance = _get_int(db, KEY_INTERVAL_BALANCE, 3)

    plan: list[str] = []
    if _due(state, "taobao_report", iv_orders, force):
        plan += ORDERS_TASKS
    if (_due(state, "settlement", iv_balance, force)
            or _due(state, "balance", iv_balance, force)
            or _due(state, "promotion", iv_balance, force)):
        plan += BALANCE_FLOW_TASKS

    for task_id, reason in SKIPPED_TASKS.items():
        out["skipped"].append({"task": task_id, "reason": reason})

    # 支付宝企业号余额: 走官方 API 精确刷 — 每次编排都刷 (API 便宜, 无浏览器/无扫码,
    # 用户拍板 2026-06-12 企业号余额"每天刷"; 不受余额/流水的 3 天截图周期限制)。
    try:
        out["alipay_balance"] = refresh_alipay_balances(db)
        state["alipay_balance"] = datetime.now().isoformat(timespec="seconds")
        _save_json(db, KEY_STATE, state)
        db.commit()
    except Exception as e:  # noqa: BLE001
        out["alipay_balance"] = [{"error": f"{type(e).__name__}: {e}"}]

    today = date.today()
    for task_id in plan:
        info = tasks_info.get(task_id, {})
        if info and not info.get("has_session"):
            out["pending_manual"].append(
                {"task": task_id, "reason": "登录态缺失 — 请到取数控制台重新扫码"})
            continue
        # 不传日期 (用户拍板 2026-06-12): 淘宝导出走"近3个月"全量, 每次刷新所有订单状态,
        # 避免按几天导漏掉中间某天的状态变化。Web-Agent 录制工作流本就无选日期步骤。
        variables: dict = {}
        _save_json(db, KEY_ORCH_STATE, {"running": True, "current": task_id,
                                        "started_at": out["started_at"]})
        db.commit()
        r = web_agent_service.run_task(db, task_id, variables)
        if not r.get("ok", True) or not r.get("job"):
            out["tasks"].append({"task": task_id, "status": "error",
                                 "error": r.get("error", "无 job id")})
            continue
        # 淘宝3报表异步导出受淘宝"两次导出≥5分钟"限流, 整轮可达~18分钟 → 等到 30 分钟,
        # 与 Web-Agent agent_total_timeout_s(1500s) 对齐, 避免单轮假超时 (2026-06-15)。
        final = web_agent_service.wait_job(db, r["job"], timeout_s=1800)
        status = (final.get("status") or "").lower()
        item = {"task": task_id, "status": status}
        if status in ("error", "failed", "timeout"):
            err = str(final.get("error") or final.get("note") or "")
            item["error"] = err[:300]
            if "需扫码" in err or "扫码" in err:
                # 需扫码: 记入待扫清单 (Web-Agent 已飞书提示"回复扫码启动"), 不在编排里干等
                _add_pending_scan(db, task_id)
                out["pending_manual"].append(
                    {"task": task_id, "reason": "需扫码 — 方便时在飞书回复『扫码』启动"})
            else:
                out["pending_manual"].append(
                    {"task": task_id, "reason": f"任务{status}: {err[:120]}"})
        out["tasks"].append(item)

    out["ingest"] = run_ingest(db)
    out["finished_at"] = datetime.now().isoformat(timespec="seconds")
    _save_json(db, KEY_ORCH_STATE, {**out, "running": False})
    db.commit()

    # 飞书汇总 (复用机器人通道; 测试环境 PANSE_DISABLE_NOTIFY 静默)
    try:
        from app.services import notify_service
        ing = out["ingest"]
        text = (f"自动取数完成: 任务 {len(out['tasks'])} 个, "
                f"新导入 {ing.get('imported', 0)} 份, 待人工 {len(out['pending_manual'])} 项。")
        if out["pending_manual"]:
            text += "\n待人工: " + "; ".join(
                f"{p['task']}({p['reason'][:40]})" for p in out["pending_manual"][:5])
        notify_service.notify(db, text, level="info", title="畔色 ERP [自动取数日报]")
    except Exception:  # pragma: no cover
        pass
    return out


def start_orchestrate_async(*, force: bool = False) -> bool:
    """手动「立即取数」: 后台线程跑编排。已在跑返回 False。"""
    if not _orch_lock.acquire(blocking=False):
        return False

    def _run() -> None:
        from app.database import SessionLocal
        db = SessionLocal()
        try:
            orchestrate(db, force=force)
        except Exception:  # noqa: BLE001
            _log.exception("web-agent 编排线程异常")
            db.rollback()
        finally:
            db.close()
            _orch_lock.release()

    threading.Thread(target=_run, name="web-agent-orchestrate", daemon=True).start()
    return True


def is_running() -> bool:
    if _orch_lock.acquire(blocking=False):
        _orch_lock.release()
        return False
    return True


def pull_orders_async(db: Session) -> dict:
    """手动「更新拉取订单」(订单页按钮): 后台触发淘宝订单近3月全量下载 + 导入。
    与全量取数共用锁, 避免并发。在线检查由调用方/端点做。"""
    if not _orch_lock.acquire(blocking=False):
        return {"started": False, "reason": "已有取数/拉单在进行中, 请稍候"}

    def _run() -> None:
        from app.database import SessionLocal
        d = SessionLocal()
        try:
            _save_json(d, KEY_ORCH_STATE, {"running": True, "current": "taobao_orders(手动拉单)",
                                           "started_at": datetime.now().isoformat(timespec="seconds")})
            d.commit()
            r = web_agent_service.run_task(d, "taobao_orders", {})
            if r.get("job"):
                web_agent_service.wait_job(d, r["job"], timeout_s=1800, poll_s=10)
            rep = run_ingest(d)
            _save_json(d, KEY_ORCH_STATE, {"running": False, "manual_pull": rep,
                                           "finished_at": datetime.now().isoformat(timespec="seconds")})
            d.commit()
        except Exception:  # noqa: BLE001
            _log.exception("手动拉单线程异常")
            d.rollback()
        finally:
            d.close()
            _orch_lock.release()

    threading.Thread(target=_run, name="web-agent-pull-orders", daemon=True).start()
    return {"started": True}
