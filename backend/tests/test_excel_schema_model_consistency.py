"""导入 schema ↔ ORM 模型一致性回归测试.

根因防护: excel_schemas.py 是手工维护的导入字段字典, 历史上多次与 ORM 模型脱节
(例: part_inventory.material_name / refill_record.remark 在 schema 里有, 模型却无对应列,
导致 Model(**payload) 逐行抛 TypeError, 整表静默失败被跳过)。

本测试断言: 凡是走 `_commit_generic` → `Model(**payload)` 入库的实体, 其 schema 的每个
字段名都必须是对应模型的真实列 (或在已知豁免名单里, 那些字段仅供 handler 反查/翻译,
handler 会在构造前剔除)。新增字段若忘了建列/迁移, 这里会立刻红。
"""
from __future__ import annotations

import pytest

from app.services.excel_schemas import ENTITY_SCHEMAS

from app.models.product import Product
from app.models.material import Material
from app.models.bom import BomLine
from app.models.inventory import ProductInventory, PartInventory
from app.models.order import Order, OrderDetail, FactoryOrder, PartPurchase
from app.models.finance import (
    AccountBalance, RefillRecord, FactoryReconciliation, AlipayFlow,
)
from app.models.pricing import PricingSku
from app.models.pricing_ext import PricingSkuCosts, PricingSkuPromo
from app.models.marketing import (
    OutsourcingExpense, AfterSales, DailyOperation, WoodLoss, Sample,
    PromotionFlow, BrandMarketing,
)
from app.models.competitor import CompetitorPrice
from app.models.supplier import DeliveryNote


def _cols(model) -> set[str]:
    return {c.key for c in model.__table__.columns}


# entity_type → 入库模型 (走 Model(**payload) 的通用 handler)
_ENTITY_MODEL = {
    "product": Product,
    "material": Material,
    "bom_line": BomLine,
    "product_inventory": ProductInventory,
    "part_inventory": PartInventory,
    "order": Order,
    "pricing_sku": PricingSku,
    "refill_record": RefillRecord,
    "factory_reconciliation": FactoryReconciliation,
    "outsourcing_expense": OutsourcingExpense,
    "aftersales": AfterSales,
    "competitor_price": CompetitorPrice,
    "daily_operations": DailyOperation,
    "order_details": OrderDetail,
    "wood_loss": WoodLoss,
    "sample": Sample,
    "promotion_flow": PromotionFlow,
    "part_purchase": PartPurchase,
    "brand_marketing": BrandMarketing,
}

# pricing_sku 的字段拆到三张表入库 → 允许子表列
_EXTRA_COLS = {
    "pricing_sku": _cols(PricingSkuCosts) | _cols(PricingSkuPromo),
}

# 已知豁免: 这些 schema 字段仅供 handler 反查/翻译, 入库前会被剔除, 不要求是模型列。
_ALLOWED_NON_COLUMN = {
    "part_inventory": {"material_name"},   # 配件编码为空时按名称反查/生成临时编码, _h_part_inv 已过滤
}


@pytest.mark.parametrize("entity, model", sorted(_ENTITY_MODEL.items()))
def test_schema_fields_are_real_model_columns(entity, model):
    cols = _cols(model) | _EXTRA_COLS.get(entity, set())
    allowed = _ALLOWED_NON_COLUMN.get(entity, set())
    schema = ENTITY_SCHEMAS[entity]
    bad = [fn for fn in schema["fields"] if fn not in cols and fn not in allowed]
    assert not bad, (
        f"实体 {entity} 的 schema 字段不是模型 {model.__name__} 的真实列: {bad}。"
        f" 请为这些字段建列+迁移, 或从 schema 移除, 否则导入时 Model(**payload) 会逐行崩溃。"
    )


def test_every_schema_entity_is_importable_or_special():
    """每个 schema 实体要么有通用入库映射, 要么是已知特殊 handler (显式 payload)。

    防止新增实体时忘了在 importer 里挂 handler → 导入时报 '暂不支持'。
    """
    special = {"delivery_note", "alipay_flow", "factory_order", "account_balance"}
    known = set(_ENTITY_MODEL) | special
    missing = [e for e in ENTITY_SCHEMAS if e not in known]
    assert not missing, f"这些 schema 实体没有对应入库 handler 映射, 会导入失败: {missing}"
