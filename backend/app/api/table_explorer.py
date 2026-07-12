"""通用「全列数据浏览」API。

让所有业务表都能在系统里看到**全部列**（不只接口精选的几列），
统一支持：核心列默认显示 + 一键展开全部列 + 中文表头。

设计：
- 用 SQLAlchemy 模型反射，自动列出每张表的所有真实字段，无需逐表写 Out 模型。
- 中文表头从 excel_schemas 的字段定义复用（aliases[0] / desc），缺省回退到字段名。
- pricing_sku 特殊处理：合并 pricing_sku / pricing_sku_costs / pricing_sku_promo 三张表。
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import String, cast, func, or_, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_current_user
from app.models.auth import User
from app.services.excel_schemas import ENTITY_SCHEMAS

# ── 模型注册表 ─────────────────────────────────────────────────────────────
from app.models.product import Product
from app.models.material import Material
from app.models.bom import BomLine
from app.models.inventory import ProductInventory, PartInventory
from app.models.order import Order, OrderDetail, FactoryOrder
from app.models.finance import (
    AccountBalance,
    RefillRecord,
    FactoryReconciliation,
    AlipayFlow,
)
from app.models.pricing import PricingSku
from app.models.pricing_ext import PricingSkuCosts, PricingSkuPromo
from app.models.marketing import (
    OutsourcingExpense,
    AfterSales,
    DailyOperation,
    WoodLoss,
    Sample,
    PromotionFlow,
)
from app.models.competitor import CompetitorPrice
from app.models.supplier import DeliveryNote, Supplier
from app.models.marketing import BrandMarketing
from app.models.customer import Customer
from app.models.taobao_listing import TaobaoListing
from app.models.finance import WanshifuBill, LogisticsBill
from app.models.order import PartPurchase

router = APIRouter(prefix="/api/table-explorer", tags=["table-explorer"])


# entity_type → (Model, label, [核心列(默认显示)], [搜索列])
ENTITY_MODELS: dict[str, dict[str, Any]] = {
    "product": {"model": Product, "label": "产品总表",
                "core": ["code", "name", "category", "brand", "listing_status"],
                "search": ["code", "name"]},
    "material": {"model": Material, "label": "物料 / 配件价格",
                 "core": ["code", "name", "unit", "price"],
                 "search": ["code", "name"]},
    "bom_line": {"model": BomLine, "label": "BOM 物料分解",
                 "core": ["product_code", "sku_code", "material_code", "qty_per_product", "unit"],
                 "search": ["product_code", "material_code"]},
    "product_inventory": {"model": ProductInventory, "label": "成品库存",
                          "core": ["warehouse", "product_code", "sku", "physical_qty", "locked_qty"],
                          "search": ["product_code", "sku"]},
    "part_inventory": {"model": PartInventory, "label": "配件库存",
                       "core": ["warehouse", "material_code", "spec", "physical_qty", "locked_qty"],
                       "search": ["material_code", "spec"]},
    "order": {"model": Order, "label": "订单总表",
              "core": ["platform", "order_no", "order_date", "customer_name", "product_name", "qty", "status"],
              "search": ["order_no", "customer_name", "product_name"]},
    "order_details": {"model": OrderDetail, "label": "订单细节",
                      "core": ["order_no", "factory_order_no", "product_code", "sku_code", "material_name"],
                      "search": ["order_no", "product_code"]},
    "factory_order": {"model": FactoryOrder, "label": "工厂下单",
                      "core": ["factory_order_no", "platform_order_no", "factory_name", "order_date", "payment_status"],
                      "search": ["factory_order_no", "platform_order_no", "factory_name"]},
    "account_balance": {"model": AccountBalance, "label": "账户月度余额",
                        "core": ["account_name", "period_year", "period_month", "closing_balance"],
                        "search": ["account_name"]},
    "pricing_sku": {"model": PricingSku, "label": "定价总表 (全列)",
                    "core": ["product_code", "sku_code", "sku", "size_category",
                             "list_price", "daily_price", "small_promo", "big_promo", "gross_margin_rate"],
                    "search": ["product_code", "sku_code", "sku"]},
    "refill_record": {"model": RefillRecord, "label": "补单记录",
                      "core": ["order_no", "refill_date", "product_name", "qty", "order_amount"],
                      "search": ["order_no", "product_name"]},
    "factory_reconciliation": {"model": FactoryReconciliation, "label": "工厂对账",
                               "core": ["factory_name", "period_end", "bill_amount", "paid_amount", "status"],
                               "search": ["factory_name"]},
    "outsourcing_expense": {"model": OutsourcingExpense, "label": "人员外包费用",
                            "core": ["payee", "amount", "project", "payment_date"],
                            "search": ["payee", "project"]},
    "aftersales": {"model": AfterSales, "label": "售后表",
                   "core": ["platform_order_no", "reason", "compensation_fee", "status", "processed_at"],
                   "search": ["platform_order_no", "reason"]},
    "competitor_price": {"model": CompetitorPrice, "label": "竞品价目",
                         "core": ["store", "category", "product", "sku_name", "daily_price"],
                         "search": ["store", "product", "sku_name"]},
    "daily_operations": {"model": DailyOperation, "label": "日常经营",
                         "core": ["record_date", "category", "item", "amount", "expense_type"],
                         "search": ["item", "category", "recipient"]},
    "wood_loss": {"model": WoodLoss, "label": "木材损耗",
                  "core": ["purchase_date", "wood_type", "spec", "inbound_qty", "loss_qty", "loss_rate_pct"],
                  "search": ["wood_type"]},
    "sample": {"model": Sample, "label": "样品表",
               "core": ["sample_no", "product_name", "sku", "sample_type", "status", "location"],
               "search": ["sample_no", "product_name"]},
    "promotion_flow": {"model": PromotionFlow, "label": "推广记录",
                       "core": ["transaction_date", "flow_type", "amount", "remark"],
                       "search": ["remark"]},
    "delivery_note": {"model": DeliveryNote, "label": "供应商送货单",
                      "core": ["note_no", "delivery_date", "total_amount", "status"],
                      "search": ["note_no"]},
    "alipay_flow": {"model": AlipayFlow, "label": "支付宝流水",
                    "core": ["account", "transaction_time", "transaction_no", "counterparty", "amount", "balance"],
                    "search": ["transaction_no", "counterparty", "related_order_no"]},
    "brand_marketing": {"model": BrandMarketing, "label": "品牌营销",
                        "core": ["project_name", "project_type", "partner", "budget", "actual_spend", "status"],
                        "search": ["project_name", "partner"]},
    "customer": {"model": Customer, "label": "客户管理",
                 "core": ["name", "phone", "tier", "total_orders", "total_revenue"],
                 "search": ["name", "phone"]},
    "supplier": {"model": Supplier, "label": "供应商",
                 "core": ["name", "contact", "phone", "supplier_type"],
                 "search": ["name", "contact"]},
    "taobao_listing": {"model": TaobaoListing, "label": "淘宝橱窗",
                       "core": ["taobao_item_id", "title", "sku_spec", "sku_price", "sku_code"],
                       "search": ["taobao_item_id", "title", "sku_spec"]},
    "wanshifu_bill": {"model": WanshifuBill, "label": "万事付账单",
                      "core": ["bill_date", "order_no", "service_type", "amount", "status"],
                      "search": ["order_no", "service_type"]},
    "logistics_bill": {"model": LogisticsBill, "label": "物流账单",
                       "core": ["bill_date", "carrier", "tracking_no", "order_no", "freight_amount"],
                       "search": ["tracking_no", "order_no", "carrier"]},
    "part_purchase": {"model": PartPurchase, "label": "配件采购",
                      "core": ["purchase_date", "supplier", "material_code", "material_name", "qty", "unit_price", "total_amount"],
                      "search": ["supplier", "material_code", "material_name"]},
}

# 永远隐藏的辅助列 (用户明确不需要「导入校验」「问题标注」, 以及内部审计列默认折叠到「全列」)
_ALWAYS_HIDE = {"import_validation", "problem_note"}


# 公共字段中文名兜底: 让无 excel_schemas 定义的"仅浏览"表(客户/供应商/淘宝橱窗/
# 万师傅/物流账单)在「全部列」里也显示中文表头, 而不是英文字段名。
_COMMON_LABELS: dict[str, str] = {
    "id": "ID", "created_at": "创建时间", "updated_at": "更新时间",
    "name": "名称", "code": "编码", "remark": "备注", "note": "备注",
    "status": "状态", "amount": "金额", "phone": "电话", "address": "地址",
    "date": "日期", "order_no": "订单号", "platform_order_no": "平台订单号",
    "supplier_name": "供应商", "factory_name": "工厂", "qty": "数量",
    "unit_price": "单价", "total_amount": "总金额", "bill_date": "账单日期",
    "carrier": "承运商", "tracking_no": "物流单号", "is_active": "是否启用",
    "sync_key": "同步键", "import_job_id": "导入批次", "service_type": "服务类型",
    "freight_amount": "运费", "weight_kg": "重量(kg)",
    # 产品总表常见英文列 → 中文(全部列视图用)
    "alt_taobao_ids": "备用淘宝ID", "taobao_id": "淘宝ID", "taobao_sku_id": "淘宝SKU-ID",
    "sub_name": "副名称", "brand": "品牌", "category": "类目", "priority": "重要程度",
    "image_url": "图片链接", "sku": "SKU", "sku_code": "SKU编码",
    "custom_scope": "定制范围", "size_detail": "尺寸明细", "aux_material": "辅材介绍",
    "description": "产品文案", "main_material": "主材介绍", "listing_status": "上架状态",
    "accessory_desc": "外配件说明", "accessory_remark": "配件备注",
    "size_value": "尺寸值(mm)", "size_confirmed": "尺寸是否确定",
    # 物料单价库常见英文列 → 中文
    "is_custom": "是否定制", "is_discontinued": "是否停用",
    "primary_supplier_id": "主供应商", "alt_supplier_ids": "备选供应商",
    "area": "面积", "width_mm": "宽(mm)", "height_mm": "高(mm)",
    "length_mm": "长(mm)", "depth_mm": "深(mm)", "thickness_mm": "厚(mm)",
    "size_type": "尺寸类型", "calc_price": "计算价格", "unit": "单位",
    "replenish_cycle": "补货周期", "lead_time_days": "补货周期(天)",
    "material_code": "物料编码", "material_name": "物料名称", "spec": "规格",
    "physical_qty": "实物数量", "locked_qty": "锁定数量", "warehouse": "仓库",
}


# 实体专属补充中文名 (用户 2026-07-11: 订单总表表头全中文, 包括系统计算/内部列)。
# excel_schemas(导入字段)仍优先; 此处只补 schema 没有的列(如 est_/actual_ 费用分量、
# 配件覆盖、签收确认等系统列), _build_label_map 里 setdefault 不覆盖 schema 定义。
_ENTITY_EXTRA_LABELS: dict[str, dict[str, str]] = {
    # ⚠ 键必须用 ENTITY_MODELS 的实体名 "order"(单数) —— 2026-07-11 首版误挂 "orders" 致订单总表
    # 页面表头仍英文(该页 FullColumnView 走 /api/table-explorer/order)。
    "order": {
        "platform": "平台", "shop": "店铺", "order_no": "订单号",
        "is_refill": "是否补单", "factory_no": "工厂制单号", "remote_seq": "远期单序号",
        "order_date": "下单日期", "ship_date": "发货日期",
        "customer_name": "客户姓名", "customer_phone": "客户电话", "customer_address": "客户地址",
        "product_code": "产品编码", "product_name": "产品名称", "sku": "SKU描述", "sku_code": "SKU编码",
        "is_custom": "是否定制", "qty": "数量", "status": "订单状态",
        "carrier": "承运商", "tracking_no": "物流单号", "install_ticket_no": "安装工单号",
        "theoretical_cost": "理论成本", "actual_cost": "工厂账单(木作实报)",
        "wood_cost_est": "木作估算(定价表)", "custom_surcharge": "定制加价",
        "actual_freight": "实际运费", "upstairs_fee": "上楼费", "install_fee": "安装费",
        "est_packing": "预估打包费", "est_logistics": "预估物流费",
        "actual_packing": "实际打包费", "actual_logistics": "实际物流费",
        "est_install": "预估安装费", "actual_install": "实际安装费",
        "actual_parts": "实际配件成本", "est_parts": "预估配件成本(定价表)",
        "parts_override": "配件覆盖(逐单指定)",
        "compensation_fee": "订单赔付费", "paid_amount": "实付金额", "discount": "优惠金额",
        "platform_fee": "平台服务费",
        "buyer_payable_amount": "买家应付金额", "buyer_freight": "买家应付邮费",
        "shop_received_amount": "店铺实收金额", "tax": "税费", "other_fee": "其它费用",
        "total_cost": "总成本",
        "good_review_refund": "好评/差价返现", "second_visit_fee": "二次上门维修费",
        "return_pack_freight": "返厂打包运费", "factory_compensation": "工厂补偿",
        "logistics_compensation": "物流补偿", "compensation_total": "补偿总金额",
        "refund_status": "退款状态", "refund_amount": "退款金额", "refund_date": "退款日期",
        "alipay_flow_no": "支付宝流水号", "remark": "ERP备注",
        "buyer_message": "买家留言", "seller_memo": "商家备注",
        "warehouse": "发货仓库", "order_profit": "订单利润(导入快照)", "lock_status": "锁定状态",
        "is_historical": "是否历史单", "activate_at": "远期激活时间", "last_outbound_at": "最近出货时间",
        "tracking_confirmed": "物流签收确认", "manual_confirmed": "人工签收确认",
        "signoff_questioned": "签收存疑", "kanban_confirmed": "看板人工确认",
        "ship_deadline": "发货截止", "production_note": "制作单备注", "is_remote_ship": "是否远期单",
        "import_job_id": "导入批次", "id": "ID", "created_at": "创建时间", "updated_at": "更新时间",
    },
}


def _build_label_map(entity: str) -> dict[str, str]:
    """字段英文名 → 中文表头。优先复用 excel_schemas 定义, 再实体专属补充, 最后公共兜底。"""
    labels: dict[str, str] = {}
    schema = ENTITY_SCHEMAS.get(entity)
    if schema:
        for fn, fdef in schema["fields"].items():
            aliases = fdef.get("aliases") or []
            labels[fn] = aliases[0] if aliases else fdef.get("desc", fn) or fn
    for k, v in _ENTITY_EXTRA_LABELS.get(entity, {}).items():
        labels.setdefault(k, v)
    for k, v in _COMMON_LABELS.items():
        labels.setdefault(k, v)
    return labels


def _serialize_value(v: Any) -> Any:
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    return v


def _model_columns(model) -> list[str]:
    return [c.key for c in model.__table__.columns]


# ── 接口 ───────────────────────────────────────────────────────────────────

class EntityMetaOut(BaseModel):
    value: str
    label: str
    row_count: int


class ColumnMeta(BaseModel):
    key: str
    label: str
    type: str
    is_core: bool


class TableDataOut(BaseModel):
    entity: str
    label: str
    columns: list[ColumnMeta]
    total: int
    rows: list[dict[str, Any]]


@router.get("/entities", response_model=list[EntityMetaOut])
def list_entities(
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """列出所有可浏览的业务表 + 行数。"""
    out: list[EntityMetaOut] = []
    for entity, cfg in ENTITY_MODELS.items():
        model = cfg["model"]
        try:
            count = db.execute(select(func.count()).select_from(model)).scalar() or 0
        except Exception:
            count = 0
        out.append(EntityMetaOut(value=entity, label=cfg["label"], row_count=count))
    return out


def _column_type(col) -> str:
    t = str(col.type).lower()
    if "int" in t:
        return "int"
    if "numeric" in t or "float" in t or "decimal" in t:
        return "decimal"
    if "date" in t and "time" in t:
        return "datetime"
    if "date" in t:
        return "date"
    if "bool" in t:
        return "bool"
    return "str"


@router.get("/{entity}", response_model=TableDataOut)
def get_table_data(
    entity: str,
    q: Optional[str] = Query(None, description="模糊搜索"),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """返回某张表的**全部列** + 数据 (分页)。"""
    cfg = ENTITY_MODELS.get(entity)
    if not cfg:
        raise HTTPException(404, f"未知表: {entity}")
    model = cfg["model"]
    label_map = _build_label_map(entity)
    core_set = set(cfg.get("core", []))

    # 列元数据 (全部真实列, 排除永久隐藏)
    cols = [c for c in model.__table__.columns if c.key not in _ALWAYS_HIDE]
    columns = [
        ColumnMeta(
            key=c.key,
            label=label_map.get(c.key, c.key),
            type=_column_type(c),
            is_core=(c.key in core_set),
        )
        for c in cols
    ]

    # 查询
    stmt = select(model)
    if q and cfg.get("search"):
        conds = []
        for fld in cfg["search"]:
            attr = getattr(model, fld, None)
            if attr is not None:
                conds.append(attr.ilike(f"%{q}%"))
        if entity == "pricing_sku":
            # 淘宝/小红书 ID 也能搜 (用户需求 2026-07-10): ID 在 promo 子表 → IN 子查询挂回主表;
            # alt_taobao_sku_ids 是 JSON 列表(一码多SKU), cast 成文本做包含匹配。
            tq = q.strip()
            conds.append(PricingSku.sku_code.in_(
                select(PricingSkuPromo.sku_code).where(or_(
                    PricingSkuPromo.taobao_item_id.like(f"%{tq}%"),
                    PricingSkuPromo.taobao_sku_id.like(f"%{tq}%"),
                    cast(PricingSkuPromo.alt_taobao_sku_ids, String).like(f"%{tq}%"),
                    PricingSkuPromo.xhs_item_id.like(f"%{tq}%"),
                    PricingSkuPromo.xhs_sku_id.like(f"%{tq}%"),
                ))
            ))
        if conds:
            stmt = stmt.where(or_(*conds))

    total = db.execute(
        select(func.count()).select_from(stmt.subquery())
    ).scalar() or 0

    # 默认按 id 倒序 (若有)
    if hasattr(model, "id"):
        stmt = stmt.order_by(model.id.desc())
    stmt = stmt.limit(limit).offset(offset)

    records = db.execute(stmt).scalars().all()

    col_keys = [c.key for c in cols]

    # pricing_sku 合并三表 —— 列定义无论有无数据都要生成
    costs_map: dict[str, Any] = {}
    promo_map: dict[str, Any] = {}
    if entity == "pricing_sku":
        # 先把 costs/promo 的列合并进列定义（不依赖 records）
        extra_cols = []
        for ext_model in (PricingSkuCosts, PricingSkuPromo):
            ext_labels = _build_label_map("pricing_sku")
            for c in ext_model.__table__.columns:
                if c.key in ("id", "sku_code", "created_at", "updated_at"):
                    continue
                if c.key in col_keys:
                    continue
                col_keys.append(c.key)
                extra_cols.append(ColumnMeta(
                    key=c.key, label=ext_labels.get(c.key, c.key),
                    type=_column_type(c), is_core=False,
                ))
        columns.extend(extra_cols)
        # 有数据时再批量查 costs/promo
        if records:
            sku_codes = [r.sku_code for r in records if r.sku_code]
            if sku_codes:
                for cr in db.execute(
                    select(PricingSkuCosts).where(PricingSkuCosts.sku_code.in_(sku_codes))
                ).scalars():
                    costs_map[cr.sku_code] = cr
                for pr in db.execute(
                    select(PricingSkuPromo).where(PricingSkuPromo.sku_code.in_(sku_codes))
                ).scalars():
                    promo_map[pr.sku_code] = pr

    rows: list[dict[str, Any]] = []
    for r in records:
        row: dict[str, Any] = {}
        for k in _model_columns(model):
            if k in _ALWAYS_HIDE:
                continue
            row[k] = _serialize_value(getattr(r, k, None))
        if entity == "pricing_sku":
            cr = costs_map.get(r.sku_code)
            pr = promo_map.get(r.sku_code)
            for ext_obj in (cr, pr):
                if ext_obj is not None:
                    for c in ext_obj.__table__.columns:
                        if c.key in ("id", "sku_code", "created_at", "updated_at"):
                            continue
                        row[c.key] = _serialize_value(getattr(ext_obj, c.key, None))
        rows.append(row)

    return TableDataOut(
        entity=entity, label=cfg["label"], columns=columns, total=total, rows=rows
    )
