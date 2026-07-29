"""工厂系统下单表 → 飞书多维表格。

这张表是给内部运营与木作工厂共看的只读业务投影：
- 一行对应一个平台订单，主键为订单号；
- 价格只同步 SKU 木作成本单价，不同步总出厂价、订单金额或其它成本；
- 每次系统订单更新、下单图推送完成后立即增量覆盖；
- 保留飞书模板原有的表格 / 状态看板 / 甘特视图所依赖字段。
"""
from __future__ import annotations

import io
import json
import logging
import re
import time
from datetime import date, datetime, time as dt_time, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

from PIL import Image
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models.import_file import ImportedFile
from app.models.order import Order
from app.models.pricing import PricingSku
from app.models.product import Product
from app.services import (
    feishu_client,
    gallery_lookup,
    order_flags,
    order_sheet_archive_service,
    settings_service,
)

_logger = logging.getLogger("panse.factory_dispatch_feishu")

SYSTEM_TABLE = "factory_dispatch"
DEFAULT_APP_TOKEN = "TaOebxGaTaU9WVsfA8Pc5vZgnEb"
DEFAULT_TABLE_ID = "tblqn9PDPO0S69wZ"
EXPECTED_VIEWS = {
    "vewaQAjTUH": ("订单管理表", "grid"),
    "vewsN92OdL": ("订单状态看板", "kanban"),
    "vewrj6iAGf": ("订单下单&发货时间", "gantt"),
}
APP_TOKEN_KEY = "factory_dispatch_feishu_app_token"
TABLE_ID_KEY = "factory_dispatch_feishu_table_id"
IMAGE_CACHE_KEY = "factory_dispatch_feishu_image_tokens"
_CN_TZ = ZoneInfo("Asia/Shanghai")

# 字段 type 见飞书 Bitable：1文本、2数字、3单选、5日期、7复选框、13电话、17附件。
FIELD_SPECS: tuple[tuple[str, int], ...] = (
    ("订单号", 1),
    ("工厂下单号", 1),
    ("商品名称", 1),
    ("产品编码", 1),
    ("SKU编码", 1),
    ("SKU规格", 1),
    ("产品图", 17),
    ("尺寸", 1),
    ("订购数量", 2),
    ("木作成本价", 2),
    ("下单日期", 5),
    ("预计发货日期", 5),
    ("订单状态", 3),
    ("发货安排", 3),
    ("客户延期单", 7),
    ("客户通知拍照", 7),
    ("验货图片数", 2),
    ("订单备注", 1),
    ("客户名称", 1),
    ("客户联系方式", 13),
    ("客户地址", 1),
    ("物流单号", 1),
    ("系统更新时间", 5),
)

# 用户给的是飞书订单模板。沿用原字段 ID 改名，可保留三个视图的列位置和显示配置。
LEGACY_RENAMES: dict[str, tuple[str, int]] = {
    "订单金额": ("木作成本价", 2),
    "销售负责人": ("客户联系方式", 13),
    "库存情况": ("产品编码", 1),
    "负责人销售记录": ("工厂下单号", 1),
}

_PHOTO_KW = (
    "通知拍照", "拍照通知", "发货前拍照", "出厂前拍照", "做好拍照",
    "拍照确认", "拍照发", "发图", "照片确认", "图片确认", "出厂图",
)
_NON_FACTORY_NAME_KW = (
    "补差", "差价", "补拍", "补邮费", "邮费补拍", "补运费", "补款",
    "尾款", "专拍", "专链", "加价链接",
)


def _target(db: Session) -> tuple[str, str]:
    return (
        settings_service.get(db, APP_TOKEN_KEY, env_fallback=False) or DEFAULT_APP_TOKEN,
        settings_service.get(db, TABLE_ID_KEY, env_fallback=False) or DEFAULT_TABLE_ID,
    )


def _date_ms(value: Optional[date]) -> Optional[int]:
    if value is None:
        return None
    return int(datetime.combine(value, dt_time.min, tzinfo=_CN_TZ).timestamp() * 1000)


def _now_ms() -> int:
    return int(datetime.now(_CN_TZ).timestamp() * 1000)


def _order_notes(order: Order) -> str:
    seen: set[str] = set()
    parts: list[str] = []
    for label, value in (
        ("买家", order.buyer_message),
        ("卖家", order.seller_memo),
        ("ERP", order.remark),
        ("生产", order.production_note),
    ):
        text = str(value or "").strip()
        if text and text not in seen:
            parts.append(f"{label}：{text}")
            seen.add(text)
    return "\n".join(parts)


def _photo_requested(order: Order) -> bool:
    text = order_flags.order_text(order)
    return any(k in text for k in _PHOTO_KW)


def _is_non_factory_order(order: Order) -> bool:
    text = " ".join(str(x or "") for x in (order.product_name, order.sku, order.sku_code))
    return any(k in text for k in _NON_FACTORY_NAME_KW)


def _status(order: Order, *, remote: bool, refunded: bool) -> str:
    raw = order.status or ""
    if raw == "cancelled" or refunded:
        return "已作废"
    if raw == "signed":
        return "已签收"
    if raw == "shipped":
        return "已发货"
    if order.is_customer_delayed:
        return "客户延期"
    if remote:
        return "等客户通知"
    if raw in ("aftersales",):
        return "售后中"
    if order.factory_no:
        return "生产中"
    return "待制单"


def _ship_plan(order: Order, *, remote: bool) -> str:
    if remote:
        return "之后发货（等通知）"
    if order.is_customer_delayed:
        return "客户延期（继续生产）"
    return "做好直接发货"


def _expected_ship_date(order: Order, *, remote: bool) -> Optional[date]:
    if order.ship_date:
        return order.ship_date
    if order.is_customer_delayed:
        return order.customer_delay_deadline
    if remote:
        return None
    if order.ship_deadline:
        return order.ship_deadline
    return order.order_date + timedelta(days=30) if order.order_date else None


def _wood_unit_price(order: Order, pricing: Optional[PricingSku]) -> Optional[Decimal]:
    qty = Decimal(str(order.qty or 1))
    if order.wood_cost_est is not None and qty > 0:
        return (Decimal(str(order.wood_cost_est)) / qty).quantize(Decimal("0.01"))
    if pricing is not None and pricing.wood_cost is not None:
        return Decimal(str(pricing.wood_cost)).quantize(Decimal("0.01"))
    return None


def _inspection_counts(db: Session) -> dict[str, int]:
    counts: dict[str, int] = {}
    rows = db.execute(
        select(ImportedFile).where(ImportedFile.kind == "factory_inspection")
    ).scalars().all()
    for row in rows:
        order_no = str((row.row_summary or {}).get("order_no") or "").strip()
        if order_no:
            counts[order_no] = counts.get(order_no, 0) + 1
    return counts


def build_rows(db: Session) -> list[dict[str, Any]]:
    """按工厂下单图同口径构造飞书行，不改变订单或价格数据。"""
    auto_since = order_sheet_archive_service.AUTO_SINCE
    orders = db.execute(
        select(Order).where(
            Order.is_refill.is_(False),
            or_(
                Order.order_date >= auto_since,
                Order.factory_no.isnot(None),
                Order.remote_seq.isnot(None),
            ),
        ).order_by(Order.order_date.asc().nulls_last(), Order.id.asc())
    ).scalars().all()

    codes = {o.product_code for o in orders if o.product_code}
    product_names = {
        code: name
        for code, name in db.execute(
            select(Product.code, Product.name).where(Product.code.in_(codes))
        ).all()
    } if codes else {}
    sku_codes = {o.sku_code for o in orders if o.sku_code}
    pricing = {
        row.sku_code: row
        for row in db.execute(
            select(PricingSku).where(PricingSku.sku_code.in_(sku_codes))
        ).scalars().all()
    } if sku_codes else {}
    inspection_counts = _inspection_counts(db)

    now_ms = _now_ms()
    out: list[dict[str, Any]] = []
    for order in orders:
        paid = order_sheet_archive_service._is_paid(order)
        refunded = order_sheet_archive_service._is_refunded(order)
        current = (order.status or "") != "cancelled" and not refunded
        if not paid or (order.status or "") == "pending_payment":
            continue
        if order_sheet_archive_service._is_sample_order(order):
            continue
        topup, _reason = order_sheet_archive_service._is_parts_topup(db, order)
        if topup or _is_non_factory_order(order):
            continue
        if not (order.sku_code or order.sku):
            continue
        # 取消/退款但曾经拿过正式或远期编号的单保留，状态明确显示作废；从未进入工厂链路的不展示。
        if not (order.factory_no or order.remote_seq or current):
            continue

        ps = pricing.get(order.sku_code or "")
        remote = order_flags.is_remote(order)
        unit_wood = _wood_unit_price(order, ps)
        image_rel = None
        if order.product_code:
            image_rel = (
                gallery_lookup.sku_image_rel(order.product_code, order.sku_code, order.sku)
                or gallery_lookup.main_image_rel(order.product_code)
            )
        factory_label = order_flags.factory_label(order)
        if not factory_label and current:
            factory_label = "待编号"
        out.append({
            "订单号": order.order_no,
            "工厂下单号": factory_label,
            "商品名称": product_names.get(order.product_code or "") or order.product_name or "",
            "产品编码": order.product_code or "",
            "SKU编码": order.sku_code or "",
            "SKU规格": order.sku or "",
            "尺寸": (ps.size_info if ps else None) or "",
            "订购数量": int(order.qty or 1),
            "木作成本价": float(unit_wood) if unit_wood is not None else None,
            "下单日期": _date_ms(order.order_date),
            "预计发货日期": _date_ms(_expected_ship_date(order, remote=remote)),
            "订单状态": _status(order, remote=remote, refunded=refunded),
            "发货安排": _ship_plan(order, remote=remote),
            "客户延期单": bool(order.is_customer_delayed),
            "客户通知拍照": _photo_requested(order),
            "验货图片数": inspection_counts.get(order.order_no, 0),
            "订单备注": _order_notes(order),
            "客户名称": order.customer_name or "",
            "客户联系方式": order.customer_phone or "",
            "客户地址": order.customer_address or "",
            "物流单号": order.tracking_no or "",
            "系统更新时间": now_ms,
            "_order_id": order.id,
            "_image_rel": image_rel,
        })
    return out


def _ensure_schema(db: Session, app_token: str, table_id: str) -> dict[str, dict]:
    fields = feishu_client.list_table_fields(db, app_token, table_id)
    by_name = {f.get("field_name"): f for f in fields}

    # 先把模板旧列改造成业务列，保留字段 ID 与视图布局。
    for old_name, (new_name, new_type) in LEGACY_RENAMES.items():
        old = by_name.get(old_name)
        if old is None or new_name in by_name:
            continue
        try:
            feishu_client.update_field(
                db, app_token, table_id, old["field_id"],
                field_name=new_name, field_type=new_type,
            )
        except feishu_client.FeishuError:
            _logger.warning("飞书模板字段改造失败 %s→%s，改为新建字段", old_name, new_name, exc_info=True)
            feishu_client.create_field(db, app_token, table_id, new_name, new_type)
        fields = feishu_client.list_table_fields(db, app_token, table_id)
        by_name = {f.get("field_name"): f for f in fields}

    for name, field_type in FIELD_SPECS:
        field = by_name.get(name)
        if field is None:
            feishu_client.create_field(db, app_token, table_id, name, field_type)
            fields = feishu_client.list_table_fields(db, app_token, table_id)
            by_name = {f.get("field_name"): f for f in fields}
            continue
        if field.get("is_primary"):
            continue
        if int(field.get("type") or 0) != field_type:
            feishu_client.update_field(
                db, app_token, table_id, field["field_id"],
                field_name=name, field_type=field_type,
            )
            fields = feishu_client.list_table_fields(db, app_token, table_id)
            by_name = {f.get("field_name"): f for f in fields}

    # 模板原有「订单金额」不是木作成本价。若字段类型无法直接改造而走了新建兜底，
    # 只删除这一个已确认的模板旧列，避免工厂继续看到订单总金额。
    legacy_total = by_name.get("订单金额")
    if legacy_total and by_name.get("木作成本价") and not legacy_total.get("is_primary"):
        feishu_client.delete_field(db, app_token, table_id, legacy_total["field_id"])
        fields = feishu_client.list_table_fields(db, app_token, table_id)
        by_name = {f.get("field_name"): f for f in fields}

    return by_name


def _load_image_cache(db: Session) -> dict[str, str]:
    raw = settings_service.get(db, IMAGE_CACHE_KEY, env_fallback=False)
    try:
        value = json.loads(raw or "{}")
        return value if isinstance(value, dict) else {}
    except json.JSONDecodeError:
        return {}


def _image_bytes(rel: str) -> tuple[bytes, str]:
    root = gallery_lookup._root().resolve()
    path = (root / rel).resolve()
    path.relative_to(root)
    if not path.is_file():
        raise FileNotFoundError(rel)
    with Image.open(path) as image:
        image = image.convert("RGB")
        image.thumbnail((1200, 1200))
        buf = io.BytesIO()
        image.save(buf, format="JPEG", quality=86, optimize=True)
    return buf.getvalue(), path.name.rsplit(".", 1)[0][:220] + ".jpg"


def _image_signature(rel: str) -> str:
    root = gallery_lookup._root().resolve()
    path = (root / rel).resolve()
    path.relative_to(root)
    st = path.stat()
    return f"{rel}|{st.st_size}|{st.st_mtime_ns}"


def _attachment_value(
    db: Session,
    app_token: str,
    rel: Optional[str],
    cache: dict[str, str],
) -> list[dict]:
    if not rel:
        return []
    sig = _image_signature(rel)
    token = cache.get(sig)
    if not token:
        content, name = _image_bytes(rel)
        token = feishu_client.upload_bitable_image(db, app_token, content, name)
        if not token:
            raise feishu_client.FeishuError(f"产品图上传未返回 file_token: {rel}")
        cache[sig] = token
        # 飞书素材接口 5 QPS；顺序上传时留一点间隔，首轮约 60 张，之后只传新增图。
        time.sleep(0.22)
    return [{"file_token": token}]


def _norm(value: Any) -> Any:
    if value in (None, ""):
        return None
    if isinstance(value, list):
        if all(isinstance(x, dict) and x.get("file_token") for x in value):
            return sorted(x.get("file_token") for x in value)
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float, Decimal)):
        return float(value)
    return str(value)


def _same(remote: dict, expected: dict) -> bool:
    return all(_norm(remote.get(k)) == _norm(v) for k, v in expected.items())


def _is_template_demo(record: dict, expected_order_nos: set[str]) -> bool:
    fields = record.get("fields") or {}
    order_no = str(fields.get("订单号") or "")
    if not order_no or order_no in expected_order_nos:
        return False
    if re.fullmatch(r"F18\d{5}", order_no):
        return True
    raw_date = fields.get("下单日期")
    return isinstance(raw_date, (int, float)) and raw_date < 1704067200000  # 2024-01-01


def sync(db: Session, *, include_images: bool = True) -> dict:
    """增量同步系统下单表。成功不发消息；错误由调用方纳入订单自动化告警与重试。"""
    app_token, table_id = _target(db)
    result: dict[str, Any] = {
        "table_id": table_id,
        "rows": 0,
        "created": 0,
        "updated": 0,
        "deleted_demo": 0,
        "missing_wood_cost": [],
        "missing_product_image": [],
        "errors": [],
        "views": {},
    }
    try:
        _ensure_schema(db, app_token, table_id)
        views = feishu_client.list_views(db, app_token, table_id)
        view_map = {v.get("view_id"): v for v in views}
        for view_id, (name, kind) in EXPECTED_VIEWS.items():
            got = view_map.get(view_id)
            ok = bool(got and got.get("view_name") == name and got.get("view_type") == kind)
            result["views"][view_id] = {
                "ok": ok,
                "name": got.get("view_name") if got else None,
                "type": got.get("view_type") if got else None,
            }
            if not ok:
                result["errors"].append(f"飞书视图不匹配: {view_id} 预期{name}/{kind}")
        if result["errors"]:
            result["ok"] = False
            return result

        rows = build_rows(db)
        result["rows"] = len(rows)
        result["missing_wood_cost"] = [
            str(row["订单号"]) for row in rows if row.get("木作成本价") is None
        ]
        if result["missing_wood_cost"]:
            result["errors"].append(
                "木作成本价缺失 "
                f"{len(result['missing_wood_cost'])} 单: "
                + ",".join(result["missing_wood_cost"][:10])
            )
            result["ok"] = False
            return result
        cache = _load_image_cache(db)
        cache_before = dict(cache)
        remote_rows = feishu_client.list_records(db, app_token, table_id)
        remote_by_no: dict[str, dict] = {}
        for rec in remote_rows:
            order_no = str((rec.get("fields") or {}).get("订单号") or "").strip()
            if order_no and order_no not in remote_by_no:
                remote_by_no[order_no] = rec

        creates: list[dict] = []
        updates: list[dict] = []
        expected_order_nos = {str(row["订单号"]) for row in rows}
        for row in rows:
            order_no = str(row["订单号"])
            payload = {k: v for k, v in row.items() if not k.startswith("_")}
            image_rel = row.get("_image_rel")
            if not image_rel:
                result["missing_product_image"].append(order_no)
            if include_images and image_rel:
                try:
                    payload["产品图"] = _attachment_value(db, app_token, image_rel, cache)
                except Exception as e:  # noqa: BLE001 - 单张图失败不阻断其它订单
                    result["errors"].append(f"{order_no} 产品图失败: {type(e).__name__}: {e}")
                    payload["产品图"] = []

            remote = remote_by_no.get(order_no)
            if remote is None:
                creates.append(payload)
            elif not _same(remote.get("fields") or {}, payload):
                updates.append({"record_id": remote["record_id"], "fields": payload})

        if creates:
            ids = feishu_client.batch_create_records(db, app_token, table_id, creates)
            result["created"] = sum(bool(x) for x in ids)
            if result["created"] != len(creates):
                result["errors"].append(
                    f"飞书下单表新建失败 {len(creates) - result['created']}/{len(creates)} 条"
                )
        if updates:
            failed = feishu_client.batch_update_records(db, app_token, table_id, updates)
            result["updated"] = len(updates) - len(failed)
            if failed:
                result["errors"].append(f"飞书下单表更新失败 {len(failed)}/{len(updates)} 条")

        # 只清飞书模板自带的 2022 年示例行；不删除任何无法确认来源的人工行。
        demo_ids = [
            rec["record_id"] for rec in remote_rows
            if _is_template_demo(rec, expected_order_nos)
        ]
        if rows and not result["errors"] and demo_ids:
            result["deleted_demo"] = feishu_client.batch_delete_records(
                db, app_token, table_id, demo_ids
            )

        if cache != cache_before:
            # 防缓存无限增长：保留最近使用和最新插入的至多 500 个 token。
            if len(cache) > 500:
                used: set[str] = set()
                for row in rows:
                    rel = row.get("_image_rel")
                    if not rel:
                        continue
                    try:
                        used.add(_image_signature(str(rel)))
                    except (FileNotFoundError, OSError, ValueError):
                        continue
                cache = {k: v for k, v in cache.items() if k in used}
            settings_service.set_value(
                db, IMAGE_CACHE_KEY, json.dumps(cache, ensure_ascii=False),
                description="工厂系统下单表产品图飞书素材 token 缓存",
            )
        settings_service.set_value(
            db, APP_TOKEN_KEY, app_token, description="工厂系统下单表飞书 app_token"
        )
        settings_service.set_value(
            db, TABLE_ID_KEY, table_id, description="工厂系统下单表飞书 table_id"
        )
        db.commit()
    except Exception as e:  # noqa: BLE001 - 交给订单自动化告警与后续重试
        db.rollback()
        result["errors"].append(f"{type(e).__name__}: {e}")
        _logger.exception("工厂系统下单表同步失败")
    result["ok"] = not result["errors"]
    return result
