"""订单总表 / 工厂下单 / 配件采购 (Excel 表 5/6/7 → plan §3)."""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import JSON, Boolean, Date, DateTime, ForeignKey, Index, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin

# 订单状态机（plan §3 表 5）
ORDER_STATUS = ("pending_payment", "paid", "shipped", "signed", "aftersales", "cancelled")

# 合法状态迁移图：from -> {to, ...}
ORDER_STATUS_TRANSITIONS: dict[str, set[str]] = {
    "pending_payment": {"paid", "cancelled"},
    "paid": {"shipped", "aftersales", "cancelled"},
    "shipped": {"signed", "aftersales"},
    "signed": {"aftersales"},
    "aftersales": {"signed"},  # 售后结束回签收
    "cancelled": set(),
}


class Order(Base, TimestampMixin):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(primary_key=True)
    platform: Mapped[str] = mapped_column(String(32), nullable=False, index=True)  # 淘宝 / 抖音 / 直营
    shop: Mapped[Optional[str]] = mapped_column(String(32), index=True)  # 店铺(畔色店/孚格店) — 分店统计 (migration 0052)
    order_no: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    is_refill: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)  # 是否补单
    # 工厂制单编号 (用户拍板 2026-06-19: 工厂按"畔色 X 单"下单; 历史读ZIP回填, 新单按下单序顺排)
    factory_no: Mapped[Optional[int]] = mapped_column(Integer, index=True)

    order_date: Mapped[Optional[date]] = mapped_column(Date, index=True)
    ship_date: Mapped[Optional[date]] = mapped_column(Date)

    customer_name: Mapped[Optional[str]] = mapped_column(String(64))
    customer_phone: Mapped[Optional[str]] = mapped_column(String(32))
    customer_address: Mapped[Optional[str]] = mapped_column(String(255))

    product_code: Mapped[Optional[str]] = mapped_column(String(32), index=True)
    product_name: Mapped[Optional[str]] = mapped_column(String(255))
    sku: Mapped[Optional[str]] = mapped_column(String(255))
    sku_code: Mapped[Optional[str]] = mapped_column(String(32), index=True)
    is_custom: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    qty: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="pending_payment", nullable=False, index=True)

    carrier: Mapped[Optional[str]] = mapped_column(String(64))
    tracking_no: Mapped[Optional[str]] = mapped_column(String(128))
    install_ticket_no: Mapped[Optional[str]] = mapped_column(String(64))

    # 成本/费用
    theoretical_cost: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2))
    actual_cost: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2))
    # 木作估算 (migration 0085): 工厂账单只含木作, actual_cost 是木作实报; wood_cost_est =
    # 该单匹配 SKU 的定价表 wood_cost(多产品单=各商品行之和)。physical_cost 用它补回非木作
    # 成本(打包/配件/物流/安装)= actual_cost + max(0, theoretical_cost − wood_cost_est)。
    wood_cost_est: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2), nullable=True)
    # 定制加价 (方案B): is_custom 单的理论成本 = 基础BOM成本 + 此加价; 可由定制报价单回填或手填 (migration 0053)
    custom_surcharge: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2))
    actual_freight: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2))
    upstairs_fee: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2))
    install_fee: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2))
    # 实际账单覆盖预估 (migration 0089, 用户 2026-06-21): 预估来自定价表(packaging_cost/
    # logistics_cost × qty); 精确配到逐单账单时 actual_* 填实际值, physical_cost 用
    # 成本 = 原成本 − 预估 + 实际 替换(只换配到的, 未配/月结汇总保持预估)。
    est_packing: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2))
    est_logistics: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2))
    actual_packing: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2))
    actual_logistics: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2))
    # 安装 (migration 0090): est=定价表 install_cost×qty; actual=install_fee+upstairs_fee(已在订单上)
    est_install: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2))
    actual_install: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2))
    # 逐单配件真实成本 (migration 0094, 用户 2026-06-26): 配件(外采)真实值; 来源=配件采购单 related_order_no
    # 汇总(能逐单的料) 或 大宗材料差异逐单建议值人工回填。非空 → physical_cost 改逐项真实计价(不估不floor)。
    actual_parts: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2))
    compensation_fee: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2))  # 订单赔付费
    paid_amount: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2))
    discount: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2))
    platform_fee: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2))

    # 订单 P&L 扩展 (Excel 表 5-订单总表 的财务列, migration 0046)
    buyer_payable_amount: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2))   # 买家应付金额
    # 买家应付邮费 (migration 0093): 买家额外付的运费=代收, 不进货款/实付列。营收对账基准要加它
    # (支付宝该单收入含此运费), 否则被误报"正差"; 营收/利润口径不含(运费≈代收代付, 对利润中性)。
    buyer_freight: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2))           # 买家应付邮费
    shop_received_amount: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2))   # 店铺实收金额
    tax: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2))                    # 税费
    other_fee: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2))              # 其它费用
    total_cost: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2))             # 总成本
    # 售后相关费用 (订单总表内冗余展示; 权威数据见 after_sales 表)
    good_review_refund: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2))     # 好评/差价返
    second_visit_fee: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2))       # 二次上门维修费
    return_pack_freight: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2))    # 返厂打包运费
    factory_compensation: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2))   # 工厂补偿
    logistics_compensation: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2)) # 物流补偿
    compensation_total: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2))     # 补偿总金额
    # 退款
    refund_status: Mapped[Optional[str]] = mapped_column(String(32))                  # 退款状态
    refund_amount: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2))          # 退款金额
    refund_date: Mapped[Optional[date]] = mapped_column(Date)                         # 退款日期

    # 支付宝流水号 (由 alipay_backfill_service 从流水反向匹配回填, 订单表 5 表 AM 列)
    alipay_flow_no: Mapped[Optional[str]] = mapped_column(String(64), index=True)

    remark: Mapped[Optional[str]] = mapped_column(Text)

    # 平台备注 (用户拍板 2026-06-11): 买家留言/商家备注随淘宝重导覆盖更新;
    # remark 保留为 ERP 人工备注, 重导永不碰。
    buyer_message: Mapped[Optional[str]] = mapped_column(Text)   # 买家留言 (平台)
    seller_memo: Mapped[Optional[str]] = mapped_column(Text)     # 商家备注/卖家备注 (平台)

    # 发货仓库 — 默认江西仓库; 样块 / 补单订单统一杭州 (导入时由 default_warehouse_for 自动判定)
    warehouse: Mapped[Optional[str]] = mapped_column(String(32))
    # 表 5-订单总表 字段补全 (Excel 导入)
    order_profit: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2))   # 订单利润(导入快照)
    lock_status: Mapped[Optional[str]] = mapped_column(String(32))            # 锁定状态

    # Phase 1 扩展
    is_historical: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # 历史水位线之前的订单, 不参与库存 / 财务核对
    activate_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    # 远期订单激活时间; 不为空时, 调度器到点改 status=paid 并锁库存
    last_outbound_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    # 最近一次出货时间, 滞销 / 复购分析用

    # 双核对签收 (Phase 13)
    tracking_confirmed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    manual_confirmed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    signoff_questioned: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # 看板人工拖拽"确定"标记 (区分人工已确定 vs 导入/同步自动归类)
    kanban_confirmed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # 工厂制作单视图: 手动发货截止(覆盖默认30天倒扣) + 卡片备注(红色醒目)
    ship_deadline: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    production_note: Mapped[Optional[str]] = mapped_column(Text)
    # 远期单: 等客户通知再发货, 工厂制作单里单独归类(不按30天倒扣紧急度)
    is_remote_ship: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # 导入批次追踪 (C2)
    import_job_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("import_jobs.id", ondelete="SET NULL"), nullable=True, index=True)

    __table_args__ = (
        Index("ix_orders_platform_date", "platform", "order_date"),
        # qty>=0 完整性约束改由迁移 0049 在 Postgres 层用 NOT VALID 施加 (生产/CI-PG);
        # 不放模型层, 避免影响测试用的 create_all 共享内存库 (历史用例会插各种 qty)。
    )

    @property
    def cost_diff(self) -> Optional[Decimal]:
        """实际成本 − 理论成本; 任一缺失则 None (供前端差异列)."""
        if self.actual_cost is None or self.theoretical_cost is None:
            return None
        return self.actual_cost - self.theoretical_cost


class FactoryOrder(Base, TimestampMixin):
    __tablename__ = "factory_orders"

    id: Mapped[int] = mapped_column(primary_key=True)
    factory_order_no: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    internal_order_no: Mapped[Optional[str]] = mapped_column(String(32), unique=True, nullable=True, index=True)
    platform_order_no: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    factory_name: Mapped[Optional[str]] = mapped_column(String(128))
    order_date: Mapped[Optional[date]] = mapped_column(Date)
    expected_delivery: Mapped[Optional[date]] = mapped_column(Date)
    actual_delivery: Mapped[Optional[date]] = mapped_column(Date)
    product_code: Mapped[Optional[str]] = mapped_column(String(32), index=True)
    product_name: Mapped[Optional[str]] = mapped_column(String(255))   # 产品名称 (表 6 导入)
    sku: Mapped[Optional[str]] = mapped_column(String(255))
    qty: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    unit_price: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2))
    factory_bill_amount: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2))
    expected_amount: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2))
    payment_method: Mapped[Optional[str]] = mapped_column(String(32))  # 月结 / 现付 / 预付
    payment_status: Mapped[str] = mapped_column(String(32), default="unpaid", nullable=False)
    payment_date: Mapped[Optional[date]] = mapped_column(Date)
    alipay_flow_no: Mapped[Optional[str]] = mapped_column(String(64))
    carrier: Mapped[Optional[str]] = mapped_column(String(64))
    tracking_no: Mapped[Optional[str]] = mapped_column(String(128))
    remark: Mapped[Optional[str]] = mapped_column(Text)

    # Phase 2 扩展
    voided_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    voided_reason: Mapped[Optional[str]] = mapped_column(Text)
    # 17:00 退款检查 → 作废工厂单时填
    source_order_id: Mapped[Optional[int]] = mapped_column(Integer)
    # 关联回 platform Order.id, 库存释放时用

    # 导入批次追踪 (C2)
    import_job_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("import_jobs.id", ondelete="SET NULL"), nullable=True, index=True)


class PartPurchase(Base, TimestampMixin):
    __tablename__ = "part_purchases"

    id: Mapped[int] = mapped_column(primary_key=True)
    purchase_no: Mapped[str] = mapped_column(String(32), unique=True, nullable=False, index=True)
    supplier: Mapped[Optional[str]] = mapped_column(String(128))
    purchase_date: Mapped[Optional[date]] = mapped_column(Date, index=True)
    material_code: Mapped[Optional[str]] = mapped_column(String(32), index=True)
    material_name: Mapped[Optional[str]] = mapped_column(String(255))
    spec: Mapped[Optional[str]] = mapped_column(String(255))
    qty: Mapped[Decimal] = mapped_column(Numeric(12, 4), default=Decimal("1"))
    unit_price: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2))
    amount: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2))
    tracking_no: Mapped[Optional[str]] = mapped_column(String(128))
    freight: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2))
    total_amount: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2))
    purchase_type: Mapped[Optional[str]] = mapped_column(String(32))  # 备货 / 单单采 / ...
    related_order_no: Mapped[Optional[str]] = mapped_column(String(64))
    payment_method: Mapped[Optional[str]] = mapped_column(String(32))
    payment_status: Mapped[str] = mapped_column(String(32), default="unpaid", nullable=False)
    payment_date: Mapped[Optional[date]] = mapped_column(Date)
    alipay_flow_no: Mapped[Optional[str]] = mapped_column(String(64))
    remark: Mapped[Optional[str]] = mapped_column(Text)   # 备注 (migration 0046)
    # 配件采购发票原图 (OCR 识别来源, 历史发票留存可点击查看)
    source_file_id: Mapped[Optional[int]] = mapped_column(ForeignKey("purchase_files.id"))
    ocr_warnings: Mapped[Optional[list]] = mapped_column(JSON)
    ocr_model: Mapped[Optional[str]] = mapped_column(String(64))


class PurchaseFile(Base, TimestampMixin):
    """上传的配件采购发票/单据原图 — 按 year/month 归档 (业务需求: 历史发票留存可查看)."""
    __tablename__ = "purchase_files"

    id: Mapped[int] = mapped_column(primary_key=True)
    year: Mapped[int] = mapped_column(Integer, nullable=False)
    month: Mapped[int] = mapped_column(Integer, nullable=False)
    file_path: Mapped[str] = mapped_column(String(512), nullable=False)
    original_name: Mapped[Optional[str]] = mapped_column(String(255))
    mime_type: Mapped[Optional[str]] = mapped_column(String(64))
    size_bytes: Mapped[Optional[int]] = mapped_column(Integer)
    uploaded_by: Mapped[Optional[str]] = mapped_column(String(64))

    __table_args__ = (
        Index("ix_purchase_files_period", "year", "month"),
    )


class OrderDetail(Base, TimestampMixin):
    """订单细节 — 飞书 tblYLdjivHwpu5ea，记录每个 SKU 行级订单与物料对应关系。"""
    __tablename__ = "order_details"

    id: Mapped[int] = mapped_column(primary_key=True)
    sync_key: Mapped[Optional[str]] = mapped_column(String(128), unique=True, nullable=True)
    order_no: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    factory_order_no: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    product_code: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    product_name: Mapped[Optional[str]] = mapped_column(String(255))
    sku_code: Mapped[Optional[str]] = mapped_column(String(64))
    sku_name: Mapped[Optional[str]] = mapped_column(String(255))
    bom_material_code: Mapped[Optional[str]] = mapped_column(String(64))
    material_name: Mapped[Optional[str]] = mapped_column(String(255))
    remark: Mapped[Optional[str]] = mapped_column(Text)
    # 行级商品列 (migration 0084): source='import' = 一单多宝贝的商品行(成本按行汇总, 杜绝塌单漏算)
    qty: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    amount: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2), nullable=True)
    source: Mapped[Optional[str]] = mapped_column(String(16), nullable=True, index=True)


# 配件状态枚举
ACCESSORY_STATUS = ("未采购", "已下单", "运输中", "已到货", "工厂提供")


class OrderAccessoryItem(Base, TimestampMixin):
    """订单配件清单行 — 每单每个 AC-* BOM 物料一行，跟踪采购与物流状态。

    MW-*/MP-* 物料 is_factory_provided=True，状态固定为「工厂提供」无需操作。
    AC-*/SP-* 物料需采购，可录入快递单号自动追踪物流。
    """
    __tablename__ = "order_accessory_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("orders.id", ondelete="CASCADE"), nullable=False, index=True
    )
    order_no: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    material_code: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    material_name: Mapped[Optional[str]] = mapped_column(String(255))
    qty_required: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False)
    unit: Mapped[Optional[str]] = mapped_column(String(16))
    is_factory_provided: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # 来源: bom = BOM 自动带出; 客户备注 = 截图 OCR 备注里识别的新增配件
    source: Mapped[str] = mapped_column(String(16), default="bom", nullable=False)
    # 未采购 / 已下单 / 运输中 / 已到货 / 工厂提供
    status: Mapped[str] = mapped_column(String(32), default="未采购", nullable=False, index=True)
    tracking_no: Mapped[Optional[str]] = mapped_column(String(128), index=True)
    carrier_code: Mapped[Optional[str]] = mapped_column(String(64))   # 快递100 承运商代码
    carrier_name: Mapped[Optional[str]] = mapped_column(String(64))   # 顺丰/中通...
    tracking_events: Mapped[Optional[list]] = mapped_column(JSON)     # 缓存物流时间线
    tracking_last_status: Mapped[Optional[str]] = mapped_column(String(255))
    tracking_updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    part_purchase_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("part_purchases.id", ondelete="SET NULL"), nullable=True
    )
    alert_level: Mapped[Optional[str]] = mapped_column(String(16))    # warn / critical
    alert_reason: Mapped[Optional[str]] = mapped_column(String(255))
    remark: Mapped[Optional[str]] = mapped_column(Text)
    # 按配件聚合采购视图: 采购单号 + 自送(工厂周边买/自己送, 免物流号)
    purchase_no: Mapped[Optional[str]] = mapped_column(String(128))
    self_delivered: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    __table_args__ = (
        UniqueConstraint("order_id", "material_code", name="uq_order_accessory_item"),
    )
