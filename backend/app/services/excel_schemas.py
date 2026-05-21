"""Excel importer 的目标 schema 定义.

每个 entity 描述: 目标字段集 + 字段类型 + 是否必填 + 别名提示 (给 AI 推断用)。
新增 entity 只需在这里加一项, importer / API / 前端会自动支持。
"""
from __future__ import annotations

from typing import Literal, TypedDict


FieldType = Literal["str", "int", "decimal", "date", "datetime", "bool"]


class FieldDef(TypedDict, total=False):
    type: FieldType
    required: bool
    desc: str
    aliases: list[str]  # 给 AI 提示: "这个字段在 Excel 里常见的列名"


class EntitySchema(TypedDict):
    label: str
    description: str
    fields: dict[str, FieldDef]
    group_by: list[str]   # 同一组的行合并为一个父对象 (如 同 note_no → 一张 DeliveryNote)
    parent_fields: list[str]  # 这些字段属于父对象, 其他属于明细行


ENTITY_SCHEMAS: dict[str, EntitySchema] = {
    "delivery_note": {
        "label": "供应商送货单",
        "description": "每行是一行明细; 同一 supplier_name + note_no 的行合并为一张送货单。",
        "fields": {
            "supplier_name": {
                "type": "str", "required": True,
                "desc": "供应商名称 (没注册过会自动创建)",
                "aliases": ["供应商", "厂家", "工厂", "供货商", "Supplier"],
            },
            "note_no": {
                "type": "str", "required": False,
                "desc": "送货单号",
                "aliases": ["单号", "送货单号", "单据号", "票号", "NoteNo", "DN号"],
            },
            "delivery_date": {
                "type": "date", "required": True,
                "desc": "送货/到货日期",
                "aliases": ["日期", "送货日期", "到货日期", "下单日期", "出库日期", "Date"],
            },
            "item_name": {
                "type": "str", "required": True,
                "desc": "品名/商品名",
                "aliases": ["品名", "商品", "商品名", "货品", "名称", "Item"],
            },
            "spec": {
                "type": "str", "required": False,
                "desc": "规格尺寸 (如 1800×850)",
                "aliases": ["规格", "尺寸", "型号", "Spec", "Size"],
            },
            "unit": {
                "type": "str", "required": False,
                "desc": "单位 (件/张/块/片/套)",
                "aliases": ["单位", "Unit"],
            },
            "qty": {
                "type": "decimal", "required": True,
                "desc": "数量",
                "aliases": ["数量", "件数", "Qty", "Quantity"],
            },
            "unit_price": {
                "type": "decimal", "required": False,
                "desc": "单价 (元)",
                "aliases": ["单价", "单价(元)", "Price", "UnitPrice"],
            },
            "amount": {
                "type": "decimal", "required": False,
                "desc": "金额 (元, 缺省会用 单价×数量 自动填)",
                "aliases": ["金额", "合计", "小计", "总额", "Amount", "Total"],
            },
            "total_amount": {
                "type": "decimal", "required": False,
                "desc": "整张单总金额 (如果 Excel 单独列了一张单的合计, 写这里)",
                "aliases": ["单据合计", "票面金额", "总计"],
            },
            "status": {
                "type": "str", "required": False,
                "desc": "状态: pending_review/confirmed/billed/paid/disputed",
                "aliases": ["状态", "对账状态", "付款状态"],
            },
            "remark": {
                "type": "str", "required": False,
                "desc": "备注",
                "aliases": ["备注", "说明", "Note", "Remark"],
            },
        },
        "group_by": ["supplier_name", "note_no"],
        "parent_fields": ["supplier_name", "note_no", "delivery_date",
                          "total_amount", "status", "remark"],
    },
    "alipay_flow": {
        "label": "支付宝流水",
        "description": "导入支付宝导出的 Excel/CSV (任意列结构)。一行 = 一条 AlipayFlow。",
        "fields": {
            "account": {
                "type": "str", "required": True,
                "desc": "支付宝账户 (企业号 / 个体户私账 / 爱群号 / 佳宝号 / 主力号)",
                "aliases": ["账户", "账号", "支付宝账号", "Account"],
            },
            "transaction_no": {
                "type": "str", "required": True,
                "desc": "交易流水号 (同一账户内全局唯一)",
                "aliases": ["流水号", "交易号", "订单号", "TransactionNo"],
            },
            "transaction_time": {
                "type": "datetime", "required": False,
                "desc": "交易时间",
                "aliases": ["时间", "交易时间", "Time"],
            },
            "transaction_type": {
                "type": "str", "required": False,
                "desc": "交易类型 (在线支付 / 转账 / 分账 / ...)",
                "aliases": ["交易类型", "类型", "Type"],
            },
            "counterparty": {
                "type": "str", "required": False,
                "desc": "对手方姓名/公司",
                "aliases": ["对方", "对手方", "对方姓名", "收款方"],
            },
            "counterparty_account": {
                "type": "str", "required": False,
                "desc": "对手方账号",
                "aliases": ["对方账号", "收款账号"],
            },
            "amount": {
                "type": "decimal", "required": True,
                "desc": "金额 (正=收入, 负=支出; 如果 Excel 是 收入/支出 分开两列, 请只选其一并加负号)",
                "aliases": ["金额", "Amount"],
            },
            "balance": {
                "type": "decimal", "required": False,
                "desc": "交易后余额",
                "aliases": ["余额", "账户余额", "Balance"],
            },
            "related_order_no": {
                "type": "str", "required": False,
                "desc": "关联的平台订单号",
                "aliases": ["商户订单号", "订单号", "OrderNo"],
            },
            "remark": {
                "type": "str", "required": False,
                "desc": "备注",
                "aliases": ["备注", "摘要", "Note"],
            },
        },
        "group_by": ["account", "transaction_no"],
        "parent_fields": list(),
    },
    "factory_order": {
        "label": "工厂下单",
        "description": "每行 = 一张工厂订单 (FactoryOrder)。",
        "fields": {
            "factory_order_no": {
                "type": "str", "required": True,
                "desc": "工厂订单号",
                "aliases": ["工厂订单号", "下单号", "厂单号"],
            },
            "platform_order_no": {
                "type": "str", "required": False,
                "desc": "对应的平台订单号 (淘宝/抖音)",
                "aliases": ["平台订单号", "淘宝单号", "订单号"],
            },
            "factory_name": {
                "type": "str", "required": False,
                "desc": "工厂名",
                "aliases": ["工厂", "厂家", "供应商"],
            },
            "order_date": {
                "type": "date", "required": False,
                "desc": "下单日期",
                "aliases": ["下单日期", "日期"],
            },
            "expected_delivery": {
                "type": "date", "required": False,
                "desc": "预计交付日期",
                "aliases": ["预计交付", "交期", "预计到货"],
            },
            "actual_delivery": {
                "type": "date", "required": False,
                "desc": "实际交付日期",
                "aliases": ["实际交付", "实际到货", "到货日期"],
            },
            "product_code": {
                "type": "str", "required": False,
                "desc": "产品编码",
                "aliases": ["产品编码", "货号", "型号"],
            },
            "sku": {
                "type": "str", "required": False,
                "desc": "SKU/规格描述",
                "aliases": ["SKU", "规格", "款式"],
            },
            "qty": {
                "type": "int", "required": False,
                "desc": "数量",
                "aliases": ["数量", "件数"],
            },
            "unit_price": {
                "type": "decimal", "required": False,
                "desc": "单价",
                "aliases": ["单价", "Price"],
            },
            "factory_bill_amount": {
                "type": "decimal", "required": False,
                "desc": "工厂账单金额",
                "aliases": ["工厂账单", "账单金额", "金额"],
            },
            "payment_method": {
                "type": "str", "required": False,
                "desc": "付款方式 (月结/现付/预付)",
                "aliases": ["付款方式", "结算"],
            },
            "payment_status": {
                "type": "str", "required": False,
                "desc": "付款状态",
                "aliases": ["付款状态", "对账"],
            },
            "remark": {
                "type": "str", "required": False,
                "desc": "备注",
                "aliases": ["备注", "说明"],
            },
        },
        "group_by": ["factory_order_no"],
        "parent_fields": list(),  # factory_order 整体是一行, 无父子
    },
}


def get_schema(entity_type: str) -> EntitySchema:
    if entity_type not in ENTITY_SCHEMAS:
        raise ValueError(f"未知实体类型: {entity_type}. 支持: {list(ENTITY_SCHEMAS)}")
    return ENTITY_SCHEMAS[entity_type]


def list_entity_types() -> list[dict]:
    return [
        {"value": k, "label": v["label"], "description": v["description"]}
        for k, v in ENTITY_SCHEMAS.items()
    ]
