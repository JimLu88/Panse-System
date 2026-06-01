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
import threading
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Optional

from sqlalchemy import Boolean, Date, DateTime, Integer, Numeric, func, select
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
        "wanshifu_bills": SyncEntity(WanshifuBill, "sync_key"),
        "logistics_bills": SyncEntity(LogisticsBill, "sync_key"),
        "refill_records": SyncEntity(RefillRecord, "sync_key"),
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
    # bool 必须在 int 之前判断, 因为 bool 是 int 的子类
    # 飞书文本字段不接受 JSON true/false, 统一转成 "是"/"否"
    if isinstance(v, bool):
        return "是" if v else "否"
    if isinstance(v, Decimal):
        f = float(v)
        return int(f) if f == int(f) else f
    # 飞书日期字段(type 5)要求毫秒时间戳(整数), 不接受 ISO 字符串
    if isinstance(v, datetime):
        ts = v.timestamp() if v.tzinfo else v.replace(tzinfo=timezone.utc).timestamp()
        return int(ts * 1000)
    if isinstance(v, date):
        return int(datetime(v.year, v.month, v.day, tzinfo=timezone.utc).timestamp() * 1000)
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


def _primary_field_name(db: Session, binding) -> Optional[str]:
    """取飞书表主字段名 (第一列, 永远文本)。取不到时返回 None, 不影响主流程。"""
    try:
        fields = feishu_client.list_table_fields(
            db, binding.feishu_app_token, binding.feishu_table_id)
    except feishu_client.FeishuError:
        return None
    return next((f.get("field_name") for f in fields if f.get("is_primary")), None)


def _to_feishu_fields(row, fm: dict[str, str], primary_fe: Optional[str] = None) -> dict:
    """系统行 → 飞书 fields ({飞书字段名: JSON 标量})。

    飞书每张表的主字段 (第一列) 永远是文本类型且不可改。若某个日期/数字系统字段
    恰好映射到主字段, 直接发数字/时间戳会 TextFieldConvFail。这里对落到主字段的值
    强制转字符串兜底 (对常规 String 主键是无影响的 no-op)。
    """
    out = {}
    for sys_f, fe_f in fm.items():
        val = _normalize(getattr(row, sys_f, None))
        if primary_fe is not None and fe_f == primary_fe and val is not None \
                and not isinstance(val, str):
            val = str(val)
        out[fe_f] = val
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
        if isinstance(t, Boolean):
            if isinstance(value, bool):
                return value
            return str(value).strip().lower() in ("是", "true", "1", "yes", "t")
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


def _feishu_field_type(model, attr: str) -> int:
    """按系统模型列类型推断飞书字段类型: 2=数字 5=日期 1=文本(默认)。"""
    col = model.__table__.columns.get(attr)
    if col is not None:
        t = col.type
        if isinstance(t, (Numeric, Integer)):
            return 2
        if isinstance(t, (Date, DateTime)):
            return 5
    return 1


def _ensure_feishu_fields(db: Session, binding, ent, fm: dict[str, str],
                          first_sync: bool, res: "SyncResult") -> Optional[str]:
    """字段对齐 (以系统映射为准):
    - 映射里有、飞书没有的列 → 按系统字段类型直接在飞书新建。
    - 飞书多出来、映射里没有的列:
        · 首次同步 → 直接删除 (该列飞书数据一并丢失)。
        · 之后    → 记一条「飞书多余字段」冲突, 等用户裁决, 不自动删。
      飞书的主字段 (is_primary) 不能删, 跳过。

    返回飞书主字段名 (供推送时把落到主字段的值强制转文本)。
    """
    try:
        fields = feishu_client.list_table_fields(
            db, binding.feishu_app_token, binding.feishu_table_id)
    except feishu_client.FeishuError as e:
        res.errors.append(f"读取飞书字段失败: {e}")
        _logger.error("飞书同步[%s] 读取字段失败: %s", binding.system_table, e)
        return None

    primary_fe = next((f.get("field_name") for f in fields if f.get("is_primary")), None)
    existing = {f.get("field_name") for f in fields}
    mapped = set(fm.values())

    # 1) 补建缺失的映射字段
    for sys_f, fe_f in fm.items():
        if fe_f in existing:
            continue
        ftype = _feishu_field_type(ent.model, sys_f)
        try:
            feishu_client.create_field(
                db, binding.feishu_app_token, binding.feishu_table_id, fe_f, ftype)
            existing.add(fe_f)
            _logger.info("飞书同步[%s] 自动新建飞书字段「%s」(type=%d)",
                         binding.system_table, fe_f, ftype)
        except feishu_client.FeishuError as e:
            res.errors.append(f"新建飞书字段「{fe_f}」失败: {e}")
            _logger.error("飞书同步[%s] 新建字段「%s」失败: %s",
                          binding.system_table, fe_f, e)

    # 2) 处理飞书多余字段
    extras = [f for f in fields
              if f.get("field_name") not in mapped and not f.get("is_primary")]
    if not extras:
        return primary_fe
    extra_names = [f.get("field_name") for f in extras]
    if first_sync:
        for f in extras:
            try:
                feishu_client.delete_field(
                    db, binding.feishu_app_token, binding.feishu_table_id, f.get("field_id"))
                _logger.info("飞书同步[%s] 首次同步删除飞书多余字段「%s」",
                             binding.system_table, f.get("field_name"))
            except feishu_client.FeishuError as e:
                res.errors.append(f"删除飞书多余字段「{f.get('field_name')}」失败: {e}")
                _logger.error("飞书同步[%s] 删除字段「%s」失败: %s",
                              binding.system_table, f.get("field_name"), e)
    else:
        _record_field_conflict(db, binding, extra_names)
        _logger.warning("飞书同步[%s] 飞书表多出 %d 个字段, 已记冲突待裁决: %s",
                        binding.system_table, len(extra_names), extra_names)
    return primary_fe


def _record_field_conflict(db, binding, extra_names: list[str]) -> None:
    """飞书多出列 → 记/更新一条「飞书多余字段」冲突 (整表级, source_pk=__schema__)。"""
    pk = "__schema__"
    ctx = {
        "system_table": binding.system_table,
        "feishu_app_token": binding.feishu_app_token,
        "feishu_table_id": binding.feishu_table_id,
        "extra_fields": extra_names,
    }
    desc = f"飞书表比系统多出 {len(extra_names)} 个字段: {', '.join(extra_names)}; 请裁决保留或删除"
    existing = db.execute(
        select(DataException).where(
            DataException.source_table == binding.system_table,
            DataException.source_pk == pk,
            DataException.exception_type == "feishu_extra_field",
            DataException.status == "open",
        )
    ).scalar_one_or_none()
    if existing is not None:
        existing.context = ctx
        existing.description = desc
        db.flush()
        return
    exception_service.record(
        db, source_table=binding.system_table, source_pk=pk,
        exception_type="feishu_extra_field", severity="warning",
        description=desc, suggestion_action="manual_fix", context=ctx,
    )


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


def sync_binding(db: Session, binding: FeishuTableBinding,
                 *, progress_cb=None) -> SyncResult:
    res = SyncResult(system_table=binding.system_table)
    ents = _entities()
    ent = ents.get(binding.system_table)
    if ent is None:
        res.errors.append(f"不支持同步的表: {binding.system_table}")
        _logger.error("飞书同步[%s] 配置错误: %s", binding.system_table, res.errors[-1])
        return res
    try:
        fm = json.loads(binding.field_mapping) if binding.field_mapping else {}
    except json.JSONDecodeError:
        res.errors.append("field_mapping 不是合法 JSON")
        _logger.error("飞书同步[%s] 配置错误: %s", binding.system_table, res.errors[-1])
        return res
    if ent.pk_attr not in fm:
        res.errors.append(f"field_mapping 必须包含主键字段 {ent.pk_attr}")
        _logger.error("飞书同步[%s] 配置错误: %s", binding.system_table, res.errors[-1])
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
        _logger.error("飞书同步[%s] 拉取飞书记录失败: %s", binding.system_table, e)
        return res
    fe_by_pk = {}
    for rec in fe_list:
        pk_val = rec["fields"].get(pk_feishu)
        if pk_val is not None and pk_val != "":
            fe_by_pk[str(pk_val)] = rec

    # 是否「首次同步」: 这张绑定 (系统表+飞书表) 还没有任何同步映射。
    # 首次同步以系统为准 (新配对差异直接推系统值), 并删除飞书多余列。
    first_sync = db.execute(
        select(func.count(FeishuSyncMap.id)).where(
            FeishuSyncMap.system_table == binding.system_table,
            FeishuSyncMap.feishu_table_id == binding.feishu_table_id,
        )
    ).scalar_one() == 0

    # 字段对齐: 缺失列补建; 飞书多余列首次删、之后报冲突 (避免 FieldNameNotFound 刷屏)。
    # 同时拿到飞书主字段名, 推送时把落到主字段的值强制转文本 (主字段永远是文本类型)。
    primary_fe = None
    if can_push:
        primary_fe = _ensure_feishu_fields(db, binding, ent, fm, first_sync, res)

    # 已有映射
    maps = {
        m.system_pk: m
        for m in db.execute(
            select(FeishuSyncMap).where(FeishuSyncMap.system_table == binding.system_table)
        ).scalars()
    }

    all_pks = set(sys_rows) | set(fe_by_pk)
    total = len(all_pks)
    if progress_cb:
        progress_cb(0, total)
    for i, pk in enumerate(all_pks, 1):
        try:
            _sync_one(db, binding, ent, fm, fields, pk, sys_rows.get(pk),
                      fe_by_pk.get(pk), maps.get(pk), can_push, can_pull, first_sync, res,
                      primary_fe)
        except feishu_client.FeishuError as e:
            res.errors.append(f"{pk}: {e}")
            _logger.error("飞书同步[%s] 记录 %s 失败: %s", binding.system_table, pk, e)
        except Exception as e:  # pragma: no cover
            res.errors.append(f"{pk}: {type(e).__name__}: {e}")
            _logger.exception("飞书同步[%s] 记录 %s 异常", binding.system_table, pk)
        # 每 25 条 (或最后一条) 更新一次进度 + 记日志, 让大表也能看到在走
        if progress_cb and (i % 25 == 0 or i == total):
            progress_cb(i, total)
        if i % 50 == 0 or i == total:
            _logger.info("飞书同步[%s] 进度 %d/%d (推%d 拉%d 新建飞书%d 错误%d)",
                         binding.system_table, i, total,
                         res.pushed, res.pulled, res.created_feishu, len(res.errors))
    db.flush()
    if res.errors:
        _logger.warning("飞书同步[%s] 完成但有 %d 个错误: %s",
                        binding.system_table, len(res.errors), "; ".join(res.errors[:10]))
    else:
        _logger.info("飞书同步[%s] 完成: 推%d 拉%d 新建飞书%d 新建系统%d 冲突%d",
                     binding.system_table, res.pushed, res.pulled,
                     res.created_feishu, res.created_system, res.conflicts)
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
              can_push, can_pull, first_sync, res: SyncResult,
              primary_fe: Optional[str] = None) -> None:
    pk_feishu = fm[ent.pk_attr]

    # a) 仅系统有 → push 新建到飞书
    if sys_row is not None and fe_rec is None:
        if not can_push:
            return
        rec_id = feishu_client.create_record(
            db, binding.feishu_app_token, binding.feishu_table_id,
            _to_feishu_fields(sys_row, fm, primary_fe))
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
        # 首次配对: 一致则直接建映射
        if sys_hash == fe_hash:
            db.add(_new_map(binding, pk, sys_hash, fe_hash, fe_rec["record_id"]))
        elif first_sync and can_push:
            # 首次同步以系统为准: 系统值直接覆盖飞书, 不报冲突
            feishu_client.update_record(
                db, binding.feishu_app_token, binding.feishu_table_id,
                fe_rec["record_id"], _to_feishu_fields(sys_row, fm, primary_fe))
            db.add(_new_map(binding, pk, sys_hash, sys_hash, fe_rec["record_id"]))
            res.pushed += 1
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
                                    m.feishu_record_id, _to_feishu_fields(sys_row, fm, primary_fe))
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


# ----------------------------- 后台同步 --------------------------- #
# 手动「立即同步」走后台线程: 不受前端超时限制, 每张表同步完立即提交,
# 中途中断也不会丢已同步的进度。进度查 /api/feishu/sync/status 或运行日志。

_sync_lock = threading.Lock()
_sync_state: dict = {
    "running": False, "started_at": None, "finished_at": None,
    "scope": None, "summary": None, "error": None,
    "total": 0, "done": 0, "current": None, "tables": [],
    "current_done": 0, "current_total": 0,
}


def _table_label(system_table: str) -> str:
    """取系统表对应的中文名 (来自预设), 没有就回退原名."""
    from app.services.feishu_preset import PRESETS
    for st, _tid, _dir, label, _fm in PRESETS:
        if st == system_table:
            return label
    return system_table


def _run_background_sync(system_table: Optional[str]) -> None:
    from app.database import SessionLocal
    db = SessionLocal()
    agg = {"tables": 0, "pushed": 0, "pulled": 0, "created_feishu": 0,
           "created_system": 0, "conflicts": 0, "errors": 0}
    try:
        q = select(FeishuTableBinding).where(FeishuTableBinding.enabled.is_(True))
        if system_table:
            q = q.where(FeishuTableBinding.system_table == system_table)
        bindings = db.execute(q).scalars().all()
        with _sync_lock:
            _sync_state["total"] = len(bindings)
            _sync_state["done"] = 0
            _sync_state["current"] = None
            _sync_state["tables"] = []
        _logger.info("飞书同步: 后台任务开始, 共 %d 张启用表", len(bindings))
        for b in bindings:
            label = _table_label(b.system_table)
            with _sync_lock:
                _sync_state["current"] = label
                _sync_state["current_done"] = 0
                _sync_state["current_total"] = 0
            _logger.info("飞书同步[%s] 开始 (%d/%d)",
                         b.system_table, agg["tables"] + 1, len(bindings))

            def _on_progress(done, tot):
                with _sync_lock:
                    _sync_state["current_done"] = done
                    _sync_state["current_total"] = tot

            res = sync_binding(db, b, progress_cb=_on_progress)
            db.commit()   # 逐表提交, 中断不丢进度
            agg["tables"] += 1
            agg["pushed"] += res.pushed
            agg["pulled"] += res.pulled
            agg["created_feishu"] += res.created_feishu
            agg["created_system"] += res.created_system
            agg["conflicts"] += res.conflicts
            agg["errors"] += len(res.errors)
            with _sync_lock:
                _sync_state["done"] = agg["tables"]
                _sync_state["tables"].append({
                    "system_table": b.system_table, "label": label,
                    "pushed": res.pushed, "pulled": res.pulled,
                    "created_feishu": res.created_feishu,
                    "created_system": res.created_system,
                    "conflicts": res.conflicts,
                    "errors": len(res.errors),
                    "error_detail": res.errors[-1] if res.errors else None,
                })
        _logger.info("飞书同步: 后台任务完成 %s", agg)
        with _sync_lock:
            _sync_state["summary"] = agg
            _sync_state["current"] = None
    except Exception as e:  # pragma: no cover
        db.rollback()
        _sync_state["error"] = f"{type(e).__name__}: {e}"
        _logger.exception("飞书同步: 后台任务异常")
    finally:
        db.close()
        with _sync_lock:
            _sync_state["running"] = False
            _sync_state["finished_at"] = datetime.now(timezone.utc).isoformat()


def start_background_sync(system_table: Optional[str] = None) -> bool:
    """启动后台同步。已有任务在跑则返回 False。"""
    with _sync_lock:
        if _sync_state["running"]:
            return False
        _sync_state.update(running=True, started_at=datetime.now(timezone.utc).isoformat(),
                           finished_at=None, scope=system_table or "all",
                           summary=None, error=None,
                           total=0, done=0, current=None, tables=[],
                           current_done=0, current_total=0)
    threading.Thread(target=_run_background_sync, args=(system_table,),
                     daemon=True).start()
    return True


def sync_status() -> dict:
    return dict(_sync_state)


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
        primary_fe = _primary_field_name(db, binding)
        feishu_client.update_record(db, ctx["feishu_app_token"], ctx["feishu_table_id"],
                                    rec_id, _to_feishu_fields(sys_row, fm, primary_fe))
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


def resolve_extra_fields(db: Session, exception_id: int, action: str, *,
                         resolved_by: Optional[str] = None) -> None:
    """裁决「飞书多余字段」冲突。
    action='delete' 删掉飞书这些多余列 (以系统为准); 'keep' 保留 (两边都留, 仅标记已处理)。
    """
    if action not in ("delete", "keep"):
        raise ValueError("action 必须是 delete 或 keep")
    exc = db.get(DataException, exception_id)
    if exc is None or exc.exception_type != "feishu_extra_field":
        raise ValueError("找不到该飞书多余字段冲突")
    ctx = exc.context or {}
    if action == "delete":
        extras = set(ctx.get("extra_fields") or [])
        fields = feishu_client.list_table_fields(
            db, ctx["feishu_app_token"], ctx["feishu_table_id"])
        for f in fields:
            if f.get("field_name") in extras and not f.get("is_primary"):
                feishu_client.delete_field(
                    db, ctx["feishu_app_token"], ctx["feishu_table_id"], f.get("field_id"))
    exc.status = "resolved"
    exc.resolved_by = resolved_by
    exc.resolved_at = datetime.now(timezone.utc).isoformat()
    db.flush()


def push_record(*args, **kwargs):  # 兼容旧引用
    raise NotImplementedError("请改用 sync_binding / sync_all")


def pull_records(*args, **kwargs):  # 兼容旧引用
    raise NotImplementedError("请改用 sync_binding / sync_all")
