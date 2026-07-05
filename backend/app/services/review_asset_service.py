"""评价资产台账 service (Plan1 v2): 折叠日计算 / 状态流转 / 覆盖聚合 / 导入 / from-order。

补单=刷单口径: source 从 orders.is_refill 推导。本模块只管评价资产, 不产生经营/财务数字。
3 个设置键存 system_settings; 多级提醒阈值(30·14·7)是代码常量, 不做设置。
"""
from __future__ import annotations

import io
from dataclasses import dataclass, field
from datetime import date, datetime, time as dtime, timedelta
from typing import Optional

import sqlalchemy as sa
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.order import Order
from app.models.review_asset import (
    REVIEW_ACTIVE,
    REVIEW_STATUS,
    REVIEW_TERMINAL,
    ReviewAsset,
)
from app.services import settings_service

# ---- settings 键 + 缺省 ----
KEY_FOLD_DAYS = "review_fold_days"
KEY_PENDING_TIMEOUT = "review_pending_timeout_days"
KEY_COVERAGE_MIN = "review_coverage_min"
DEFAULT_FOLD_DAYS = 180  # 评价约180天后退出首屏默认排序 (官方规则)
DEFAULT_PENDING_TIMEOUT = 10  # 刷单发货后超N天没评价则催办 (15天窗口留5天缓冲)
DEFAULT_COVERAGE_MIN = 2  # 产品活跃带图评价数低于此值预警

REVIEW_WINDOW_DAYS = 15  # 淘宝: 交易成功后可评价窗口 (固定平台规则)

# 多级折叠提醒: (剩余天数上限, 级别); 从严到宽, 剩余 ≤ 阈值 归该级
REVIEW_REMIND_LEVELS: tuple[tuple[int, str], ...] = ((7, "error"), (14, "warn"), (30, "info"))
_LEVEL_RANK = {"info": 1, "warn": 2, "error": 3}


# ============ settings ============

def _int_setting(db: Session, key: str, default: int) -> int:
    raw = settings_service.get(db, key, env_fallback=False)
    try:
        return int(raw) if raw not in (None, "") else default
    except (TypeError, ValueError):
        return default


def get_settings(db: Session) -> dict:
    return {
        "fold_days": _int_setting(db, KEY_FOLD_DAYS, DEFAULT_FOLD_DAYS),
        "pending_timeout_days": _int_setting(db, KEY_PENDING_TIMEOUT, DEFAULT_PENDING_TIMEOUT),
        "coverage_min": _int_setting(db, KEY_COVERAGE_MIN, DEFAULT_COVERAGE_MIN),
    }


def update_settings(
    db: Session,
    *,
    fold_days: Optional[int] = None,
    pending_timeout_days: Optional[int] = None,
    coverage_min: Optional[int] = None,
) -> dict:
    """更新设置。改 fold_days 时对所有非终态且有 review_date 的行重算 fold_due_date (防口径漂移)。"""
    if fold_days is not None:
        if fold_days < 1 or fold_days > 730:
            raise ValueError("review_fold_days 须在 1~730 之间")
        settings_service.set_value(db, KEY_FOLD_DAYS, str(int(fold_days)))
        rows = db.execute(
            select(ReviewAsset).where(
                ReviewAsset.status.notin_(REVIEW_TERMINAL),
                ReviewAsset.review_date.isnot(None),
            )
        ).scalars().all()
        for r in rows:
            r.fold_due_date = compute_fold_due(r.review_date, int(fold_days))
    if pending_timeout_days is not None:
        if pending_timeout_days < 0 or pending_timeout_days > 60:
            raise ValueError("review_pending_timeout_days 须在 0~60 之间")
        settings_service.set_value(db, KEY_PENDING_TIMEOUT, str(int(pending_timeout_days)))
    if coverage_min is not None:
        if coverage_min < 0 or coverage_min > 100:
            raise ValueError("review_coverage_min 须在 0~100 之间")
        settings_service.set_value(db, KEY_COVERAGE_MIN, str(int(coverage_min)))
    db.flush()
    return get_settings(db)


# ============ 折叠日计算 ============

def compute_fold_due(review_date: Optional[date], fold_days: int) -> Optional[date]:
    return review_date + timedelta(days=fold_days) if review_date else None


# ============ 创建 / from-order / 编辑 ============

def _derive_source(order: Optional[Order]) -> str:
    """补单=刷单口径: 关联到订单按 is_refill; 无订单默认 refill (台账主要记刷单)。"""
    if order is None:
        return "refill"
    return "refill" if order.is_refill else "natural"


def create_manual(
    db: Session,
    *,
    order_no: str,
    review_date: Optional[date] = None,
    image_count: int = 0,
    rating: Optional[int] = None,
    review_text: Optional[str] = None,
    product_code: Optional[str] = None,
    sku_name: Optional[str] = None,
    shop: Optional[str] = None,
    source: Optional[str] = None,
    remark: Optional[str] = None,
) -> ReviewAsset:
    if not order_no:
        raise ValueError("order_no 必填")
    fold_days = get_settings(db)["fold_days"]
    order = db.execute(select(Order).where(Order.order_no == order_no)).scalars().first()
    ra = ReviewAsset(
        order_id=order.id if order else None,
        order_no=order_no,
        shop=shop or (order.shop if order else None),
        product_code=product_code or (order.product_code if order else None),
        sku_name=sku_name or (order.sku_code if order else None),
        review_date=review_date,
        image_count=image_count or 0,
        rating=rating,
        review_text=review_text,
        source=source or _derive_source(order),
        remark=remark,
    )
    if review_date:
        ra.fold_due_date = compute_fold_due(review_date, fold_days)
        ra.status = "reviewed"
    else:
        ra.status = "pending_review"
    db.add(ra)
    db.flush()
    return ra


def from_order(db: Session, order_id: int) -> tuple[ReviewAsset, bool]:
    """从订单一键生成 (幂等: 已有该 order_no 条目则返回已有)。返回 (asset, created)。"""
    order = db.get(Order, order_id)
    if not order:
        raise ValueError("订单不存在")
    existing = db.execute(
        select(ReviewAsset).where(ReviewAsset.order_no == order.order_no)
    ).scalars().first()
    if existing:
        return existing, False
    ra = ReviewAsset(
        order_id=order.id,
        order_no=order.order_no,
        shop=order.shop,
        product_code=order.product_code,
        sku_name=order.sku_code,
        source=_derive_source(order),
        status="pending_review",
    )
    db.add(ra)
    db.flush()
    return ra, True


_EDITABLE_FIELDS = ("image_count", "rating", "review_text", "product_code", "sku_name", "shop", "remark")


def update_asset(db: Session, ra: ReviewAsset, patch: dict) -> ReviewAsset:
    """编辑 + 状态流转。补录 review_date 会自动置 reviewed 并算 fold_due_date。"""
    fold_days = get_settings(db)["fold_days"]
    if "review_date" in patch:
        rd = patch["review_date"]
        ra.review_date = rd
        ra.fold_due_date = compute_fold_due(rd, fold_days) if rd else None
        if rd and ra.status == "pending_review":
            ra.status = "reviewed"
            ra.status_changed_at = datetime.now()
    for f in _EDITABLE_FIELDS:
        if f in patch:
            setattr(ra, f, patch[f])
    if "status" in patch and patch["status"] and patch["status"] != ra.status:
        new = patch["status"]
        if new not in REVIEW_STATUS:
            raise ValueError(f"非法状态 {new}")
        ra.status = new
        ra.status_changed_at = datetime.now()
    db.flush()
    return ra


# ============ 查询 / 统计 / 覆盖 ============

def list_assets(
    db: Session,
    *,
    status: Optional[str] = None,
    product_code: Optional[str] = None,
    shop: Optional[str] = None,
    source: Optional[str] = None,
    due_in_days: Optional[int] = None,
    keyword: Optional[str] = None,
    limit: int = 200,
    offset: int = 0,
) -> list[ReviewAsset]:
    q = select(ReviewAsset)
    if status:
        q = q.where(ReviewAsset.status == status)
    if product_code:
        q = q.where(ReviewAsset.product_code == product_code)
    if shop:
        q = q.where(ReviewAsset.shop == shop)
    if source:
        q = q.where(ReviewAsset.source == source)
    if due_in_days is not None:
        cutoff = date.today() + timedelta(days=due_in_days)
        q = q.where(
            ReviewAsset.fold_due_date.isnot(None),
            ReviewAsset.fold_due_date <= cutoff,
            ReviewAsset.status.notin_(REVIEW_TERMINAL),
        )
    if keyword:
        like = f"%{keyword}%"
        q = q.where(
            sa.or_(
                ReviewAsset.order_no.ilike(like),
                ReviewAsset.product_code.ilike(like),
                ReviewAsset.sku_name.ilike(like),
            )
        )
    # 临近折叠优先: 有 fold_due_date 的按日升序, 无的沉底
    q = q.order_by(
        ReviewAsset.fold_due_date.is_(None),
        ReviewAsset.fold_due_date.asc(),
        ReviewAsset.id.desc(),
    )
    return list(db.execute(q.limit(limit).offset(offset)).scalars().all())


def pending_overdue_rows(db: Session, timeout_days: int) -> list[tuple[ReviewAsset, date, int]]:
    """待评价超时: status=pending_review 且距发货日(无则创建日)已超 timeout_days。

    返回 (asset, base_date, elapsed_days)。
    """
    today = date.today()
    rows = db.execute(
        select(ReviewAsset, Order.ship_date)
        .outerjoin(Order, ReviewAsset.order_id == Order.id)
        .where(ReviewAsset.status == "pending_review")
    ).all()
    out: list[tuple[ReviewAsset, date, int]] = []
    for ra, ship in rows:
        base = ship or (ra.created_at.date() if ra.created_at else today)
        elapsed = (today - base).days
        if elapsed > timeout_days:
            out.append((ra, base, elapsed))
    return out


def coverage(db: Session, *, coverage_min: Optional[int] = None) -> list[dict]:
    """按 product_code 聚合活跃(reviewed+folding_soon)且带图(image_count>0)评价数; 低覆盖排前。"""
    cmin = coverage_min if coverage_min is not None else get_settings(db)["coverage_min"]
    rows = db.execute(
        select(
            ReviewAsset.product_code,
            func.count(ReviewAsset.id).label("active_cnt"),
            func.max(ReviewAsset.review_date).label("last_review"),
            func.min(ReviewAsset.fold_due_date).label("next_fold"),
        )
        .where(
            ReviewAsset.status.in_(REVIEW_ACTIVE),
            ReviewAsset.image_count > 0,
            ReviewAsset.product_code.isnot(None),
        )
        .group_by(ReviewAsset.product_code)
    ).all()
    out = []
    for pc, cnt, last_r, next_f in rows:
        out.append({
            "product_code": pc,
            "active_image_reviews": int(cnt),
            "last_review_date": last_r.isoformat() if last_r else None,
            "next_fold_date": next_f.isoformat() if next_f else None,
            "below_min": int(cnt) < cmin,
            "coverage_min": cmin,
        })
    out.sort(key=lambda x: (not x["below_min"], x["active_image_reviews"]))
    return out


def stats(db: Session) -> dict:
    s = get_settings(db)
    today = date.today()
    fold_cut = today + timedelta(days=30)
    near_fold = db.execute(
        select(func.count(ReviewAsset.id)).where(
            ReviewAsset.fold_due_date.isnot(None),
            ReviewAsset.fold_due_date <= fold_cut,
            ReviewAsset.status.notin_(REVIEW_TERMINAL),
        )
    ).scalar() or 0
    pending_overdue = len(pending_overdue_rows(db, s["pending_timeout_days"]))
    month_start_dt = datetime.combine(today.replace(day=1), dtime.min)
    new_month = db.execute(
        select(func.count(ReviewAsset.id)).where(ReviewAsset.created_at >= month_start_dt)
    ).scalar() or 0
    low_cov = sum(1 for c in coverage(db) if c["below_min"])
    return {
        "near_fold": int(near_fold),
        "pending_overdue": int(pending_overdue),
        "new_this_month": int(new_month),
        "low_coverage_products": int(low_cov),
        **s,
    }


# ============ 导入 / 模板 ============

TEMPLATE_HEADERS = ["订单号", "评价日期", "评价图张数", "星级", "评价内容", "产品编码", "SKU", "备注"]


@dataclass
class ImportReport:
    inserted: int = 0
    skipped_duplicate: int = 0
    skipped_invalid: int = 0
    unlinked: int = 0  # 订单不在库仍入库的条数
    errors: list = field(default_factory=list)


def _cell_str(row: tuple, i: int) -> Optional[str]:
    if i >= len(row) or row[i] is None:
        return None
    s = str(row[i]).strip()
    return s or None


def _cell_int(row: tuple, i: int) -> Optional[int]:
    s = _cell_str(row, i)
    if s is None:
        return None
    try:
        return int(float(s))
    except ValueError:
        return None


def _cell_date(row: tuple, i: int) -> Optional[date]:
    if i >= len(row) or row[i] is None:
        return None
    v = row[i]
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    s = str(v).strip()
    if not s:
        return None
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d", "%m/%d/%Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def import_rows(db: Session, wb) -> ImportReport:
    """解析 xlsx 导入。行级幂等 (order_no+review_date 已存在则跳过)。列见 TEMPLATE_HEADERS。"""
    fold_days = get_settings(db)["fold_days"]
    ws = wb.active
    rep = ImportReport()
    for row in ws.iter_rows(min_row=2, values_only=True):
        if row is None or all(c is None or str(c).strip() == "" for c in row):
            continue
        order_no = _cell_str(row, 0)
        if not order_no:
            rep.skipped_invalid += 1
            continue
        review_date = _cell_date(row, 1)
        dup = db.execute(
            select(ReviewAsset.id).where(
                ReviewAsset.order_no == order_no,
                ReviewAsset.review_date == review_date,
            )
        ).scalars().first()
        if dup:
            rep.skipped_duplicate += 1
            continue
        order = db.execute(select(Order).where(Order.order_no == order_no)).scalars().first()
        if order is None:
            rep.unlinked += 1
        ra = ReviewAsset(
            order_id=order.id if order else None,
            order_no=order_no,
            shop=order.shop if order else None,
            product_code=_cell_str(row, 5) or (order.product_code if order else None),
            sku_name=_cell_str(row, 6) or (order.sku_code if order else None),
            review_date=review_date,
            image_count=_cell_int(row, 2) or 0,
            rating=_cell_int(row, 3),
            review_text=_cell_str(row, 4),
            source=_derive_source(order),
            remark=_cell_str(row, 7),
        )
        if review_date:
            ra.fold_due_date = compute_fold_due(review_date, fold_days)
            ra.status = "reviewed"
        else:
            ra.status = "pending_review"
        db.add(ra)
        rep.inserted += 1
    db.flush()
    return rep


def build_template_xlsx() -> bytes:
    import openpyxl

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "评价资产导入"
    ws.append(TEMPLATE_HEADERS)
    ws.append(["示例TB123456", "2026-05-12", 3, 5, "颜色很正实物好看", "M8812", "洞石色/1.2m", "旗舰店刷单(此示例行可删)"])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def to_dict(ra: ReviewAsset) -> dict:
    """序列化给前端。"""
    return {
        "id": ra.id,
        "order_id": ra.order_id,
        "order_no": ra.order_no,
        "shop": ra.shop,
        "product_code": ra.product_code,
        "sku_name": ra.sku_name,
        "review_date": ra.review_date.isoformat() if ra.review_date else None,
        "image_count": ra.image_count,
        "rating": ra.rating,
        "review_text": ra.review_text,
        "fold_due_date": ra.fold_due_date.isoformat() if ra.fold_due_date else None,
        "days_to_fold": (ra.fold_due_date - date.today()).days if ra.fold_due_date else None,
        "status": ra.status,
        "source": ra.source,
        "screenshot_file_id": ra.screenshot_file_id,
        "remark": ra.remark,
        "created_at": ra.created_at.isoformat() if ra.created_at else None,
    }


# ============ 每日巡检 + 提醒 (供 daily_0900_review_asset_remind job) ============

def _level_for_days(days_left: int) -> Optional[str]:
    """剩余天数 → 提醒级别 (≤7 或逾期=error, ≤14=warn, ≤30=info, 更远=None)。"""
    for thresh, lvl in REVIEW_REMIND_LEVELS:  # ((7,error),(14,warn),(30,info))
        if days_left <= thresh:
            return lvl
    return None


def run_daily_scan(db: Session, *, today: Optional[date] = None) -> dict:
    """每日巡检 (供 job + 测试; today 可注入)。会改状态+防刷屏标记, 调用方负责 commit。

    1) 状态流转: reviewed 距折叠 ≤30 → folding_soon; 逾期未释放 → folded。
    2) 多级折叠提醒清单 (error ≤7/含逾期, warn ≤14, info ≤30); info 仅首次进窗推(防刷屏)。
    3) 待评价超时清单。 4) 产品低覆盖清单。 返回结构化 dict, 不推送 (推送在 job 层)。
    """
    s = get_settings(db)
    today = today or date.today()
    rows = db.execute(
        select(ReviewAsset).where(
            ReviewAsset.status.notin_(REVIEW_TERMINAL),
            ReviewAsset.status != "pending_review",
            ReviewAsset.fold_due_date.isnot(None),
        )
    ).scalars().all()
    fold_notify: dict[str, list] = {"error": [], "warn": [], "info": []}
    newly_folded = 0
    newly_folding = 0
    for ra in rows:
        days_left = (ra.fold_due_date - today).days
        if days_left < 0 and ra.status != "folded":
            ra.status = "folded"
            ra.status_changed_at = datetime.now()
            newly_folded += 1
        elif 0 <= days_left <= 30 and ra.status == "reviewed":
            ra.status = "folding_soon"
            ra.status_changed_at = datetime.now()
            newly_folding += 1
        level = _level_for_days(days_left)
        if not level:
            continue
        # 防刷屏: info(15~30天) 仅首次进窗推; warn/error 每日推
        if level == "info" and ra.last_notified_level is not None:
            continue
        fold_notify[level].append({
            "id": ra.id, "order_no": ra.order_no, "product_code": ra.product_code,
            "sku_name": ra.sku_name,
            "review_date": ra.review_date.isoformat() if ra.review_date else None,
            "fold_due_date": ra.fold_due_date.isoformat(), "days_left": days_left,
        })
        ra.last_notified_level = level
        ra.last_notified_date = today

    pend = pending_overdue_rows(db, s["pending_timeout_days"])
    pending = [{
        "id": ra.id, "order_no": ra.order_no, "product_code": ra.product_code,
        "base_date": base.isoformat(), "elapsed_days": elapsed,
        "window_left": max(0, REVIEW_WINDOW_DAYS - elapsed),
    } for ra, base, elapsed in pend]

    low_cov = [c for c in coverage(db) if c["below_min"]]

    max_level = None
    for lvl in ("error", "warn", "info"):
        if fold_notify[lvl]:
            max_level = lvl
            break
    if max_level is None and pending:
        max_level = "warn"
    if max_level is None and low_cov:
        max_level = "info"
    db.flush()
    return {
        "fold_notify": fold_notify,
        "pending": pending,
        "low_coverage": low_cov,
        "newly_folded": newly_folded,
        "newly_folding": newly_folding,
        "max_level": max_level,
        "has_content": bool(
            fold_notify["error"] or fold_notify["warn"] or fold_notify["info"]
            or pending or low_cov
        ),
    }


def _fold_line(item: dict) -> str:
    tail = item["order_no"][-4:] if item.get("order_no") else "----"
    prod = item.get("product_code") or "?"
    sku = item.get("sku_name") or ""
    dl = item["days_left"]
    due = item.get("fold_due_date") or ""
    when = f"逾期{-dl}天" if dl < 0 else f"剩{dl}天"
    return f"· {prod} {sku} 订单…{tail} → {due}折叠({when})"


def format_reminder(res: dict) -> str:
    """把 run_daily_scan 结果组装成飞书文案 (供 job + 测试)。"""
    fn = res["fold_notify"]
    lines = [
        f"📸 评价资产日报（🔴{len(fn['error'])} ⚠{len(fn['warn'])} "
        f"⏳{len(res['pending'])} 📉{len(res['low_coverage'])}）"
    ]
    if fn["error"]:
        lines.append("🔴 7天内/已折叠:")
        lines += [_fold_line(i) for i in fn["error"]]
    if fn["warn"]:
        lines.append("⚠ 14天内折叠:")
        lines += [_fold_line(i) for i in fn["warn"]]
    if fn["info"]:
        lines.append("🟡 30天内折叠(首报):")
        lines += [_fold_line(i) for i in fn["info"]]
    if res["pending"]:
        lines.append("⏳ 待评价超时:")
        for p in res["pending"]:
            tail = p["order_no"][-4:] if p.get("order_no") else "----"
            lines.append(f"· 订单…{tail} 已{p['elapsed_days']}天未评价(窗口剩{p['window_left']}天)")
    if res["low_coverage"]:
        lines.append("📉 低覆盖产品:")
        for c in res["low_coverage"]:
            lines.append(f"· {c['product_code']} 活跃带图评价仅{c['active_image_reviews']}条")
    lines.append("→ 请安排刷单节点释放/补齐评价图")
    return "\n".join(lines)
