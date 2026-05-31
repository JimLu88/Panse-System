"""飞书双向同步引擎 (用户需求 #11).

每条 FeishuTableBinding 定义一张系统表 ↔ 飞书多维表 的绑定 + field_mapping
(系统字段名 → 飞书字段名, 必须含主键字段)。

同步逻辑 (sync_binding):
    1. 拉系统侧记录 + 飞书侧记录, 按主键值配对 (无映射则新建 FeishuSyncMap)。
    2. 用 hash 判断各侧自上次同步后是否变化:
        - 仅系统变 → push 到飞书
        - 仅飞书变 → pull 到系统
        - 两侧都变 → 冲突: 写 DataException(feishu_conflict), 默认不自动覆盖,
          带上两侧值 + 两侧 updated_at 时间, 等用户在前端按时间裁决保留哪端。
    3. 同步成功后更新 map 的 hash + last_sync_at。

resolve_conflict: 用户选 system / feishu 后执行覆盖并清异常。
"""
from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Optional

from sqlalchemy import Date, DateTime, Integer, Numeric, func, select
from sqlalchemy.orm import Session

from app.models.exception import DataException
from app.models.feishu_sync import FeishuSyncMap, FeishuTableBinding
from app.services import exception_service, feishu_client

_logger = logging.getLogger("panse.feishu_sync")


# ----------------------------- 可同步实体注册表 -------------------- #


@dataclass
class SyncEntity:
    model: type
    pk_attr: str   # 业务主键 (稳定, 用来配对系统记录 ↔ 飞书行)


def _entities() -> dict[str, SyncEntity]:
    from app.models.customer import Customer
    from app.models.material import Material
    from app.models.order import Order, FactoryOrder, OrderDetail
    from app.models.pricing import PricingSku
    from app.models.product import Product
    from app.models.marketing import Sample, BrandMarketing, AfterSales, PromotionFlow, OutsourcingExpense, WoodLoss, DailyOperation
    from app.models.finance import AlipayFlow, FactoryReconciliation, AccountBalance, WanshifuBill, LogisticsBill, RefillRecord
    from app.models.bom import BomLine
    from app.models.inventory import ProductInventory, PartInventory
    from app.models.supplier import Supplier
    return {
        "products": SyncEntity(Product, "code"),
        "materials": SyncEntity(Material, "code"),
        "orders": SyncEntity(Order, "order_no"),
        "pricing_sku": SyncEntity(PricingSku, "sku_code"),
        "customers": SyncEntity(Customer, "matching_key"),
        "factory_orders": SyncEntity(FactoryOrder, "factory_order_no"),
        "alipay_flows": SyncEntity(AlipayFlow, "transaction_no"),
        "samples": SyncEntity(Sample, "sample_no"),
        "after_sales": SyncEntity(AfterSales, "platform_order_no"),
        "bom_lines": SyncEntity(BomLine, "sync_key"),
        "brand_marketing": SyncEntity(BrandMarketing, "sync_key"),
        "promotion_flows": SyncEntity(PromotionFlow, "sync_key"),
        "outsourcing_expenses": SyncEntity(OutsourcingExpense, "sync_key"),
        "wood_losses": SyncEntity(WoodLoss, "sync_key"),
        "factory_reconciliations": SyncEntity(FactoryReconciliation, "sync_key"),
        "account_balances": SyncEntity(AccountBalance, "sync_key"),
        "product_inventory": SyncEntity(ProductInventory, "sync_key"),
        "part_inventory": SyncEntity(PartInventory, "sync_key"),
        "daily_operations": SyncEntity(DailyOperation, "sync_key"),
        "order_details": SyncEntity(OrderDetail, "sync_key"),
        "wanshifu_bills": SyncEntity(WanshifuBill, "id"),
        "logistics_bills": SyncEntity(LogisticsBill, "id"),
        "refill_records": SyncEntity(RefillRecord, "id"),
        "suppliers": SyncEntity(Supplier, "name"),
    }


SUPPORTED_TABLES = [
    "products", "materials", "orders", "pricing_sku", "customers",
    "factory_orders", "alipay_flows", "samples", "after_sales",
    "bom_lines", "brand_marketing", "promotion_flows", "outsourcing_expenses",
    "wood_losses", "factory_reconciliations", "account_balances",
    "product_inventory", "part_inventory", "daily_operations", "order_details",
    "wanshifu_bills", "logistics_bills", "refill_records", "suppliers",
]


# ----------------------------- 状态 (Phase 1 保留) ----------------- #


@dataclass
class SyncStatus:
    system_table: str
    feishu_table_id: str
    direction: str
    enabled: bool
    mapped_rows: int


def list_status(db: Session) -> list[SyncStatus]:
    bindings = db.execute(select(FeishuTableBinding)).scalars().all()
    out: list[SyncStatus] = []
    for b in bindings:
        count = db.execute(
            select(func.count(FeishuSyncMap.id)).where(
                FeishuSyncMap.system_table == b.system_table,
                FeishuSyncMap.feishu_table_id == b.feishu_table_id,
            )
        ).scalar_one()
        out.append(SyncStatus(
            system_table=b.system_table, feishu_table_id=b.feishu_table_id,
            direction=b.direction, enabled=b.enabled, mapped_rows=count,
        ))
    return out


# ----------------------------- 值规整 / hash ---------------------- #


def _normalize(v: Any) -> Any:
    """把系统值 / 飞书值规整成可比较、可哈希的标量。"""
    if v is None or v == "":
        return None
    if isinstance(v, Decimal):
        f = float(v)
        return int(f) if f == int(f) else f
    if isinstance(v, (date, datetime)):
        return v.isoformat()
    if isinstance(v, float):
        return int(v) if v == int(v) else v
    if isinstance(v, (list, dict)):
        # 飞书富文本/人员字段可能是结构, 统一序列化
        return json.dumps(v, ensure_ascii=False, sort_keys=True)
    return v


def _hash(values: dict) -> str:
    norm = {k: _normalize(v) for k, v in values.items()}
    blob = json.dumps(norm, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:32]


def _system_values(row, fields: list[str]) -> dict:
    return {f: getattr(row, f, None) for f in fields}


def _feishu_values(rec_fields: dict, fm: dict[str, str]) -> dict:
    """飞书记录 fields → {系统字段名: 值}。"""
    return {sys_f: rec_fields.get(fe_f) for sys_f, fe_f in fm.items()}


def _to_feishu_fields(row, fm: dict[str, str]) -> dict:
    """系统行 → 飞书 fields ({飞书字段名: JSON 标量})。"""
    out = {}
    for sys_f, fe_f in fm.items():
        out[fe_f] = _normalize(getattr(row, sys_f, None))
    return out


def _coerce_for_model(model, attr: str, value: Any) -> Any:
    """飞书标量 → 系统模型列类型 (pull 时用)。"""
    if value is None or value == "":
        return None
    col = model.__table__.columns.get(attr)
    if col is None:
        return value
    t = col.type
    try:
        if isinstance(t, Numeric):
            return Decimal(str(value))
        if isinstance(t, Integer):
            return int(float(value))
        if isinstance(t, Date) and not isinstance(t, DateTime):
            if isinstance(value, (int, float)):   # 飞书时间戳 (ms)
                return datetime.fromtimestamp(value / 1000, tz=timezone.utc).date()
            return date.fromisoformat(str(value)[:10])
        if isinstance(t, DateTime):
            if isinstance(value, (int, float)):
                return datetime.fromtimestamp(value / 1000, tz=timezone.utc)
            return datetime.fromisoformat(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return value
    return value


# ----------------------------- 同步主流程 ------------------------- #


@dataclass
class SyncResult:
    system_table: str
    pushed: int = 0
    pulled: int = 0
    created_feishu: int = 0
    created_system: int = 0
    conflicts: int = 0
    errors: list[str] = field(default_factory=list)


def sync_binding(db: Session, binding: FeishuTableBinding) -> SyncResult:
    res = SyncResult(system_table=binding.system_table)
    ents = _entities()
    ent = ents.get(binding.system_table)
    if ent is None:
        res.errors.append(f"不支持同步的表: {binding.system_table}")
        return res
    try:
        fm = json.loads(binding.field_mapping) if binding.field_mapping else {}
    except json.JSONDecodeError:
        res.errors.append("field_mapping 不是合法 JSON")
        return res
    if ent.pk_attr not in fm:
        res.errors.append(f"field_mapping 必须包含主键字段 {ent.pk_attr}")
        return res

    direction = binding.direction or "bidirectional"
    can_push = direction in ("out", "bidirectional")
    can_pull = direction in ("in", "bidirectional")
    fields = list(fm.keys())
    pk_attr = ent.pk_attr
    pk_feishu = fm[pk_attr]

    # 系统侧
    sys_rows = {
        str(getattr(r, pk_attr)): r
        for r in db.execute(select(ent.model)).scalars()
        if getattr(r, pk_attr) is not None
    }
    # 飞书侧
    try:
        fe_list = feishu_client.list_records(db, binding.feishu_app_token, binding.feishu_table_id)
    except feishu_client.FeishuError as e:
        res.errors.append(str(e))
        return res
    fe_by_pk = {}
    for rec in fe_list:
        pk_val = rec["fields"].get(pk_feishu)
        if pk_val is not None and pk_val != "":
            fe_by_pk[str(pk_val)] = rec

    # 已有映射
    maps = {
        m.system_pk: m
        for m in db.execute(
            select(FeishuSyncMap).where(FeishuSyncMap.system_table == binding.system_table)
        ).scalars()
    }

    all_pks = set(sys_rows) | set(fe_by_pk)
    for pk in all_pks:
        try:
            _sync_one(db, binding, ent, fm, fields, pk, sys_rows.get(pk),
                      fe_by_pk.get(pk), maps.get(pk), can_push, can_pull, res)
        except feishu_client.FeishuError as e:
            res.errors.append(f"{pk}: {e}")
        except Exception as e:  # pragma: no cover
            res.errors.append(f"{pk}: {type(e).__name__}: {e}")
    db.flush()
    return res


def _new_map(binding, pk, sys_hash, fe_hash, feishu_record_id) -> FeishuSyncMap:
    return FeishuSyncMap(
        system_table=binding.system_table, system_pk=pk,
        feishu_app_token=binding.feishu_app_token,
        feishu_table_id=binding.feishu_table_id,
        feishu_record_id=feishu_record_id,
        system_hash=sys_hash, feishu_hash=fe_hash,
        last_sync_at=datetime.now(timezone.utc),
    )


def _sync_one(db, binding, ent, fm, fields, pk, sys_row, fe_rec, m,
              can_push, can_pull, res: SyncResult) -> None:
    pk_feishu = fm[ent.pk_attr]

    # a) 仅系统有 → push 新建到飞书
    if sys_row is not None and fe_rec is None:
        if not can_push:
            return
        rec_id = feishu_client.create_record(
            db, binding.feishu_app_token, binding.feishu_table_id,
            _to_feishu_fields(sys_row, fm))
        sys_hash = _hash(_system_values(sys_row, fields))
        db.add(_new_map(binding, pk, sys_hash, sys_hash, rec_id))
        res.created_feishu += 1
        return

    # b) 仅飞书有 → pull 新建到系统
    if fe_rec is not None and sys_row is None:
        if not can_pull:
            return
        vals = _feishu_values(fe_rec["fields"], fm)
        payload = {f: _coerce_for_model(ent.model, f, v) for f, v in vals.items()}
        new_row = ent.model(**{k: v for k, v in payload.items() if v is not None})
        db.add(new_row)
        db.flush()
        fe_hash = _hash(vals)
        db.add(_new_map(binding, pk, fe_hash, fe_hash, fe_rec["record_id"]))
        res.created_system += 1
        return

    if sys_row is None or fe_rec is None:
        return

    # c) 两侧都有
    sys_vals = _system_values(sys_row, fields)
    fe_vals = _feishu_values(fe_rec["fields"], fm)
    sys_hash = _hash(sys_vals)
    fe_hash = _hash(fe_vals)

    if m is None:
        # 首次配对: 一致则建映射, 不一致按冲突处理
        if sys_hash == fe_hash:
            db.add(_new_map(binding, pk, sys_hash, fe_hash, fe_rec["record_id"]))
        else:
            _record_conflict(db, binding, ent, fm, pk, sys_row, fe_rec, sys_vals, fe_vals)
            db.add(_new_map(binding, pk, sys_hash, fe_hash, fe_rec["record_id"]))
            res.conflicts += 1
        return

    sys_changed = m.system_hash != sys_hash
    fe_changed = m.feishu_hash != fe_hash
    m.feishu_record_id = fe_rec["record_id"]

    if sys_changed and fe_changed:
        _record_conflict(db, binding, ent, fm, pk, sys_row, fe_rec, sys_vals, fe_vals)
        res.conflicts += 1
        return
    if sys_changed and can_push:
        feishu_client.update_record(db, binding.feishu_app_token, binding.feishu_table_id,
                                    m.feishu_record_id, _to_feishu_fields(sys_row, fm))
        m.system_hash = sys_hash
        m.feishu_hash = sys_hash
        m.last_sync_at = datetime.now(timezone.utc)
        res.pushed += 1
    elif fe_changed and can_pull:
        for f, v in fe_vals.items():
            setattr(sys_row, f, _coerce_for_model(ent.model, f, v))
        m.feishu_hash = fe_hash
        m.system_hash = fe_hash
        m.last_sync_at = datetime.now(timezone.utc)
        res.pulled += 1


def _record_conflict(db, binding, ent, fm, pk, sys_row, fe_rec, sys_vals, fe_vals) -> None:
    """两侧都改 → 写冲突异常 (附两侧值 + 更新时间), 不自动覆盖。"""
    sys_updated = getattr(sys_row, "updated_at", None)
    diffs = []
    for f in fm:
        sv = _normalize(sys_vals.get(f))
        fv = _normalize(fe_vals.get(f))
        if sv != fv:
            diffs.append({"field": f, "system": sv, "feishu": fv})
    # 避免重复: 同一 (table, pk) 已有未解决冲突就更新, 否则新建
    existing = db.execute(
        select(DataException).where(
            DataException.source_table == binding.system_table,
            DataException.source_pk == pk,
            DataException.exception_type == "feishu_conflict",
            DataException.status == "open",
        )
    ).scalar_one_or_none()
    ctx = {
        "system_table": binding.system_table,
        "system_pk": pk,
        "feishu_app_token": binding.feishu_app_token,
        "feishu_table_id": binding.feishu_table_id,
        "feishu_record_id": fe_rec["record_id"],
        "diffs": diffs,
        "system_updated_at": sys_updated.isoformat() if isinstance(sys_updated, datetime) else None,
        "feishu_updated_at": fe_rec.get("last_modified_time"),
    }
    if existing is not None:
        existing.context = ctx
        existing.description = f"飞书与系统数据冲突 ({len(diffs)} 字段不同), 请裁决保留哪端"
        db.flush()
        return
    exception_service.record(
        db, source_table=binding.system_table, source_pk=pk,
        exception_type="feishu_conflict", severity="warning",
        description=f"飞书与系统数据冲突 ({len(diffs)} 字段不同), 请裁决保留哪端",
        suggestion_action="manual_fix", context=ctx,
    )


def sync_all(db: Session) -> list[SyncResult]:
    out = []
    for b in db.execute(
        select(FeishuTableBinding).where(FeishuTableBinding.enabled.is_(True))
    ).scalars():
        out.append(sync_binding(db, b))
    return out


def resolve_conflict(db: Session, exception_id: int, keep: str, *,
                     resolved_by: Optional[str] = None) -> None:
    """用户裁决冲突: keep='system' 推系统值到飞书; keep='feishu' 拉飞书值到系统。"""
    if keep not in ("system", "feishu"):
        raise ValueError("keep 必须是 system 或 feishu")
    exc = db.get(DataException, exception_id)
    if exc is None or exc.exception_type != "feishu_conflict":
        raise ValueError("找不到该飞书冲突")
    ctx = exc.context or {}
    ents = _entities()
    ent = ents.get(ctx.get("system_table"))
    binding = db.execute(
        select(FeishuTableBinding).where(
            FeishuTableBinding.system_table == ctx.get("system_table"))
    ).scalar_one_or_none()
    if ent is None or binding is None:
        raise ValueError("绑定/实体已不存在")
    fm = json.loads(binding.field_mapping) if binding.field_mapping else {}
    pk = ctx.get("system_pk")
    sys_row = db.execute(
        select(ent.model).where(getattr(ent.model, ent.pk_attr) == pk)
    ).scalar_one_or_none()
    rec_id = ctx.get("feishu_record_id")

    if keep == "system" and sys_row is not None:
        feishu_client.update_record(db, ctx["feishu_app_token"], ctx["feishu_table_id"],
                                    rec_id, _to_feishu_fields(sys_row, fm))
        new_hash = _hash(_system_values(sys_row, list(fm.keys())))
    elif keep == "feishu":
        fe_list = feishu_client.list_records(db, ctx["feishu_app_token"], ctx["feishu_table_id"])
        rec = next((r for r in fe_list if r["record_id"] == rec_id), None)
        if rec is None:
            raise ValueError("飞书记录已不存在")
        fe_vals = _feishu_values(rec["fields"], fm)
        if sys_row is not None:
            for f, v in fe_vals.items():
                setattr(sys_row, f, _coerce_for_model(ent.model, f, v))
        new_hash = _hash(fe_vals)
    else:
        new_hash = None

    # 更新映射 hash, 标记冲突已解决
    m = db.execute(
        select(FeishuSyncMap).where(
            FeishuSyncMap.system_table == ctx.get("system_table"),
            FeishuSyncMap.system_pk == pk,
        )
    ).scalar_one_or_none()
    if m is not None and new_hash is not None:
        m.system_hash = new_hash
        m.feishu_hash = new_hash
        m.last_sync_at = datetime.now(timezone.utc)
    exc.status = "resolved"
    exc.resolved_by = resolved_by
    exc.resolved_at = datetime.now(timezone.utc).isoformat()
    db.flush()


def resolve_conflict_merged(db: Session, exception_id: int, field_choices: dict[str, str], *,
                            resolved_by: Optional[str] = None) -> None:
    """字段级合并裁决: 逐字段选 system / feishu, 合并后两侧写一致 (主键恒以系统为准)。"""
    exc = db.get(DataException, exception_id)
    if exc is None or exc.exception_type != "feishu_conflict":
        raise ValueError("找不到该飞书冲突")
    ctx = exc.context or {}
    ent = _entities().get(ctx.get("system_table"))
    binding = db.execute(
        select(FeishuTableBinding).where(
            FeishuTableBinding.system_table == ctx.get("system_table"))
    ).scalar_one_or_none()
    if ent is None or binding is None:
        raise ValueError("绑定/实体已不存在")
    fm = json.loads(binding.field_mapping) if binding.field_mapping else {}
    pk = ctx.get("system_pk")
    rec_id = ctx.get("feishu_record_id")
    sys_row = db.execute(
        select(ent.model).where(getattr(ent.model, ent.pk_attr) == pk)
    ).scalar_one_or_none()
    if sys_row is None:
        raise ValueError("系统记录已不存在")

    fe_list = feishu_client.list_records(db, ctx["feishu_app_token"], ctx["feishu_table_id"])
    rec = next((r for r in fe_list if r["record_id"] == rec_id), None)
    if rec is None:
        raise ValueError("飞书记录已不存在")
    fe_vals = _feishu_values(rec["fields"], fm)
    sys_vals = _system_values(sys_row, list(fm.keys()))

    merged: dict[str, Any] = {}
    for f in fm:
        if f == ent.pk_attr:
            merged[f] = sys_vals.get(f)          # 主键不允许通过合并改动
            continue
        if field_choices.get(f) == "feishu":
            merged[f] = fe_vals.get(f)
            setattr(sys_row, f, _coerce_for_model(ent.model, f, fe_vals.get(f)))
        else:
            merged[f] = sys_vals.get(f)

    feishu_client.update_record(
        db, ctx["feishu_app_token"], ctx["feishu_table_id"], rec_id,
        {fm[f]: _normalize(merged[f]) for f in fm})

    new_hash = _hash(merged)
    m = db.execute(
        select(FeishuSyncMap).where(
            FeishuSyncMap.system_table == ctx.get("system_table"),
            FeishuSyncMap.system_pk == pk,
        )
    ).scalar_one_or_none()
    if m is not None:
        m.system_hash = new_hash
        m.feishu_hash = new_hash
        m.last_sync_at = datetime.now(timezone.utc)
    exc.status = "resolved"
    exc.resolved_by = resolved_by
    exc.resolved_at = datetime.now(timezone.utc).isoformat()
    db.flush()


def push_record(*args, **kwargs):  # 兼容旧引用
    raise NotImplementedError("请改用 sync_binding / sync_all")


def pull_records(*args, **kwargs):  # 兼容旧引用
    raise NotImplementedError("请改用 sync_binding / sync_all")
