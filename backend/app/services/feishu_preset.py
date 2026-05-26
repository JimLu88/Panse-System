"""飞书绑定预设 (用户需求: 一键导入全部 23 张表的绑定 + field_mapping)。

每条预设 = (system_table, feishu_table_id, direction, label, field_mapping)。
field_mapping: 系统字段名 -> 飞书列名 (必须含该实体的业务主键字段)。

飞书列名为中文最佳猜测, 用户导入后可在「查询飞书字段」UI 逐表核对、修正再启用。
本模块同时充当这份映射的"留存副本", 详见 docs/feishu-sync-mapping.md。

注意: 5 张支付宝流水表都映射到同一个 system_table=alipay_flows
(方向仅入, 靠 account / 交易号区分), 这正是放宽绑定唯一性的原因。
"""
from __future__ import annotations

WIKI_TOKEN = "NpWzwIcLBilnIlk0B2sc5ETInZc"


# (system_table, feishu_table_id, direction, label, field_mapping)
PRESETS: list[tuple[str, str, str, str, dict[str, str]]] = [
    (
        "products", "tbleu3HqLCFXMnYw", "bidirectional", "产品表",
        {
            "code": "产品编码", "name": "产品名称", "brand": "品牌", "category": "类目",
            "remark": "备注", "image_url": "图片", "custom_scope": "定制范围",
            "size_detail": "尺寸明细", "aux_material": "辅材介绍", "description": "产品文案",
        },
    ),
    (
        "pricing_sku", "tbl7IjyyxTTmDKJz", "bidirectional", "定价表",
        {
            "sku_code": "SKU编码", "product_code": "产品编码", "sku": "SKU",
            "size_category": "尺寸分类", "daily_price": "日常价", "small_promo": "小促价",
            "mid_promo": "中促价", "big_promo": "大促价", "gross_margin_rate": "毛利率",
            "image_url": "图片",
        },
    ),
    (
        "bom_lines", "tblOtUUUOT8PsuP9", "in", "BOM表",
        {
            "sync_key": "唯一键", "product_code": "产品编码", "sku": "SKU",
            "sku_code": "SKU编码", "material_code": "物料编码", "unit": "单位",
            "qty_per_product": "单件用量", "size_type": "尺寸类型", "remark": "备注",
        },
    ),
    (
        "materials", "tbl2p6mBkalDg70O", "bidirectional", "物料价格",
        {
            "code": "物料编码", "name": "物料名称", "size_type": "尺寸类型", "unit": "单位",
            "price": "单价", "is_custom": "是否定制", "lead_time_days": "交期天数",
            "remark": "备注",
        },
    ),
    (
        "product_inventory", "tblRsLFDXvuKE2CB", "in", "成品库存",
        {
            "sync_key": "唯一键", "warehouse": "仓库", "product_code": "产品编码",
            "sku": "SKU", "spec": "规格", "unit": "单位", "physical_qty": "实物数量",
            "locked_qty": "锁定数量", "remark": "备注",
        },
    ),
    (
        "part_inventory", "tblwQ2yL1rzGzDQh", "in", "配件库存",
        {
            "sync_key": "唯一键", "warehouse": "仓库", "material_code": "物料编码",
            "spec": "规格", "unit": "单位", "physical_qty": "实物数量",
            "locked_qty": "锁定数量", "safety_stock": "安全库存", "remark": "备注",
        },
    ),
    (
        "orders", "tblEue0AXVJLPda4", "bidirectional", "销售订单",
        {
            "order_no": "订单号", "platform": "平台", "order_date": "下单日期",
            "ship_date": "发货日期", "customer_name": "客户姓名", "customer_phone": "客户电话",
            "customer_address": "收货地址", "product_code": "产品编码", "product_name": "产品名称",
            "sku": "SKU", "qty": "数量", "status": "状态", "paid_amount": "实付金额",
            "carrier": "快递公司", "tracking_no": "快递单号", "remark": "备注",
        },
    ),
    (
        "factory_orders", "tblTn8Kb8yQCT39U", "in", "工厂下单",
        {
            "factory_order_no": "工厂订单号", "platform_order_no": "平台订单号",
            "factory_name": "工厂名称", "order_date": "下单日期", "expected_delivery": "预计交期",
            "product_code": "产品编码", "sku": "SKU", "qty": "数量", "unit_price": "单价",
            "factory_bill_amount": "工厂账单金额", "payment_method": "付款方式",
            "payment_status": "付款状态", "carrier": "快递公司", "tracking_no": "快递单号",
            "remark": "备注",
        },
    ),
    (
        "factory_reconciliations", "tblVDWBjF6WZiPKy", "in", "工厂对账",
        {
            "sync_key": "唯一键", "period_start": "对账起", "period_end": "对账止",
            "factory_name": "工厂名称", "order_amount": "下单金额", "bill_amount": "账单金额",
            "paid_amount": "已付金额", "status": "状态", "diff_amount": "差异金额",
            "diff_reason": "差异原因", "remark": "备注",
        },
    ),
    (
        "alipay_flows", "tblIJO5UipqPnpmK", "in", "支付宝流水-企业号",
        {
            "transaction_no": "交易号", "account": "账户", "transaction_time": "交易时间",
            "transaction_type": "交易类型", "counterparty": "对方", "amount": "金额",
            "related_order_no": "关联订单号", "balance": "余额", "remark": "备注",
        },
    ),
    (
        "alipay_flows", "tbl79NjIFcayl4eN", "in", "支付宝流水-个体户私账",
        {
            "transaction_no": "交易号", "account": "账户", "transaction_time": "交易时间",
            "transaction_type": "交易类型", "counterparty": "对方", "amount": "金额",
            "related_order_no": "关联订单号", "balance": "余额", "remark": "备注",
        },
    ),
    (
        "alipay_flows", "tblUPYpeREl93yIz", "in", "支付宝流水-爱群",
        {
            "transaction_no": "交易号", "account": "账户", "transaction_time": "交易时间",
            "transaction_type": "交易类型", "counterparty": "对方", "amount": "金额",
            "related_order_no": "关联订单号", "balance": "余额", "remark": "备注",
        },
    ),
    (
        "alipay_flows", "tblIFStV63UPmFAl", "in", "支付宝流水-佳宝",
        {
            "transaction_no": "交易号", "account": "账户", "transaction_time": "交易时间",
            "transaction_type": "交易类型", "counterparty": "对方", "amount": "金额",
            "related_order_no": "关联订单号", "balance": "余额", "remark": "备注",
        },
    ),
    (
        "alipay_flows", "tbleXlRHNqHVqtI4", "in", "支付宝流水-主力",
        {
            "transaction_no": "交易号", "account": "账户", "transaction_time": "交易时间",
            "transaction_type": "交易类型", "counterparty": "对方", "amount": "金额",
            "related_order_no": "关联订单号", "balance": "余额", "remark": "备注",
        },
    ),
    (
        "account_balances", "tblrUiLJOc5d3Wm0", "in", "账户余额",
        {
            "sync_key": "唯一键", "account_name": "账户名称", "account_no": "账号",
            "period_year": "年", "period_month": "月", "opening_balance": "期初余额",
            "income": "收入", "expense": "支出", "closing_balance": "期末余额",
            "remark": "备注",
        },
    ),
    (
        "wood_losses", "tblvLARSHPdlmpOV", "in", "木材损耗",
        {
            "sync_key": "唯一键", "purchase_date": "采购日期", "wood_type": "木材类型",
            "spec": "规格", "unit": "单位", "inbound_qty": "入库量", "used_qty": "使用量",
            "loss_qty": "损耗量", "loss_rate_pct": "损耗率", "reason": "原因",
            "remark": "备注",
        },
    ),
    (
        "samples", "tbl0jwfGypXEi2xR", "in", "样品",
        {
            "sample_no": "样品编号", "product_code": "产品编码", "product_name": "产品名称",
            "sku": "SKU", "sample_type": "样品类型", "qty": "数量", "made_at": "制作日期",
            "cost": "成本", "location": "位置", "status": "状态", "usage": "用途",
            "remark": "备注",
        },
    ),
    (
        "brand_marketing", "tblraKjamWiLubQx", "in", "品牌营销",
        {
            "sync_key": "唯一键", "project_name": "项目名称", "project_type": "项目类型",
            "partner": "合作方", "start_date": "开始日期", "end_date": "结束日期",
            "budget": "预算", "actual_spend": "实际支出", "payment_date": "付款日期",
            "status": "状态", "remark": "备注",
        },
    ),
    (
        "promotion_flows", "tblJ1sgVxmk5JjBZ", "in", "推广记录",
        {
            "sync_key": "唯一键", "transaction_date": "交易日期", "flow_type": "类型",
            "amount": "金额", "balance_after": "余额", "remark": "备注",
        },
    ),
    (
        "daily_operations", "tblvyqyNBj1er26J", "in", "日常经营",
        {
            "sync_key": "唯一键", "record_date": "日期", "item": "项目", "amount": "金额",
            "payment_account": "支付账户", "category": "分类", "expense_type": "支出类型",
            "recipient": "支付对象", "payment_method": "支付方式",
            "alipay_flow_no": "支付宝流水号", "invoice_status": "发票状态", "remark": "备注",
        },
    ),
    (
        "order_details", "tblYLdjivHwpu5ea", "in", "订单细节",
        {
            "sync_key": "唯一键", "order_no": "订单编号", "factory_order_no": "工厂订单号",
            "product_code": "产品编码", "product_name": "产品名称",
            "sku_code": "SKU编码", "sku_name": "SKU名称",
            "bom_material_code": "关联BOM物料编码", "material_name": "关联物料名称",
        },
    ),
    (
        "outsourcing_expenses", "tblmmRAfnySumzq0", "in", "人员外包",
        {
            "sync_key": "唯一键", "payee": "收款方", "amount": "金额", "project": "项目",
            "related_order_no": "关联订单号", "cost_category": "成本类别",
            "payment_date": "付款日期", "remark": "备注",
        },
    ),
    (
        "after_sales", "tbldwJIwYhXBPmWW", "in", "售后",
        {
            "platform_order_no": "平台订单号", "reason": "原因", "compensation_fee": "赔付费",
            "status": "状态", "processed_at": "处理日期", "remark": "备注",
        },
    ),
    (
        "customers", "tblP0NKUeoQR8Se9", "bidirectional", "客户",
        {
            "matching_key": "匹配键", "name": "客户姓名", "phone": "手机号", "address": "地址",
            "tier": "客户等级", "total_orders": "总订单数", "total_revenue": "总消费金额",
            "note": "备注",
        },
    ),
]


def get_presets() -> list[dict]:
    """返回预设列表 (每项为 dict, field_mapping 保持为 Python dict)。"""
    return [
        {
            "system_table": system_table,
            "feishu_table_id": feishu_table_id,
            "direction": direction,
            "label": label,
            "field_mapping": field_mapping,
        }
        for (system_table, feishu_table_id, direction, label, field_mapping) in PRESETS
    ]
