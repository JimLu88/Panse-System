from datetime import date
from decimal import Decimal
from typing import Optional

from sqlalchemy import Date, ForeignKey, Integer, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class PartInventory(Base, TimestampMixin):
    """配件库存 (Excel 表 4b-配件库存)。

    每条记录 = (仓库, 物料编码) 维度的库存快照。
    入库行 add_part_row 在物料缺失时会自动触发 Material 的「定制」建档。

    Phase 6: 数量改用 Decimal(14,3) — 之前 Integer 在 BOM 小数 qty 时会向上取整, 多锁/多扣库存.
    """

    __tablename__ = "part_inventory"

    id: Mapped[int] = mapped_column(primary_key=True)
    sync_key: Mapped[Optional[str]] = mapped_column(String(128), unique=True, nullable=True)
    warehouse: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    material_code: Mapped[str] = mapped_column(
        ForeignKey("materials.code", ondelete="RESTRICT"), nullable=False, index=True
    )
    spec: Mapped[Optional[str]] = mapped_column(String(255))
    unit: Mapped[Optional[str]] = mapped_column(String(16))
    physical_qty: Mapped[Decimal] = mapped_column(Numeric(14, 3), default=Decimal("0"), nullable=False)
    locked_qty: Mapped[Decimal] = mapped_column(Numeric(14, 3), default=Decimal("0"), nullable=False)
    # 待返厂/维修中 (坏件): 已从良品库移出, 不计入可用; 修好移回良品, 报废/退货则核销。
    # server_default 让裸 SQL / 旧插入路径也拿到 0 (与 migration 0055 一致), 不触发 NOT NULL。
    defective_qty: Mapped[Decimal] = mapped_column(
        Numeric(14, 3), default=Decimal("0"), server_default="0", nullable=False)
    last_inbound_at: Mapped[Optional[date]] = mapped_column(Date)
    last_outbound_at: Mapped[Optional[date]] = mapped_column(Date)
    safety_stock: Mapped[Optional[Decimal]] = mapped_column(Numeric(14, 3))
    # 表 4b 字段补全 (Excel 导入需要; 全部 nullable 向后兼容)
    lead_time_days: Mapped[Optional[int]] = mapped_column(Integer)          # 提前期(天)
    slow_moving_days: Mapped[Optional[int]] = mapped_column(Integer)        # 滞销预警天数
    avg_daily_sales: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 3))  # 日均销量
    stock_status: Mapped[Optional[str]] = mapped_column(String(32))         # 库存状态(导入快照)
    stock_alert_status: Mapped[Optional[str]] = mapped_column(String(32))   # 库存预警状态(导入快照)
    slow_moving_status: Mapped[Optional[str]] = mapped_column(String(32))   # 滞销状态(导入快照)
    auto_restock_qty: Mapped[Optional[Decimal]] = mapped_column(Numeric(14, 3))  # 自动计算备货量(导入快照)
    remark: Mapped[Optional[str]] = mapped_column(String(255))

    @property
    def available_qty(self) -> Decimal:
        return Decimal(self.physical_qty or 0) - Decimal(self.locked_qty or 0)


class PartReturn(Base, TimestampMixin):
    """配件返厂/退货单 (方案C — 坏件财务闭环).

    「处理待返厂坏件」时同步生成, 记录这次处置的钱:
      disposition=returned → amount = 应收供应商退款 (status=open 待收, 收到后 settle)
      disposition=repaired → amount = 返厂维修费 (确认即 settled)
      disposition=scrapped → amount = 报废损失 (= 采购成本, 确认即 settled)
    可关联 支付宝流水号 与 原采购单, 供应商对账用。库存动作仍由 part_defect_service 处理。
    """

    __tablename__ = "part_returns"

    id: Mapped[int] = mapped_column(primary_key=True)
    material_code: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    material_name: Mapped[Optional[str]] = mapped_column(String(255))
    warehouse: Mapped[str] = mapped_column(String(64), default="default", nullable=False)
    qty: Mapped[Decimal] = mapped_column(Numeric(14, 3), default=Decimal("0"), nullable=False)
    disposition: Mapped[str] = mapped_column(String(16), nullable=False)   # returned/repaired/scrapped
    amount_kind: Mapped[str] = mapped_column(String(16), nullable=False)   # refund/repair_fee/scrap_loss
    amount: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2))
    reason: Mapped[Optional[str]] = mapped_column(String(255))
    supplier: Mapped[Optional[str]] = mapped_column(String(128))
    related_purchase_no: Mapped[Optional[str]] = mapped_column(String(32))
    alipay_flow_no: Mapped[Optional[str]] = mapped_column(String(64))
    tracking_no: Mapped[Optional[str]] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(16), default="open", nullable=False, index=True)  # open/settled
    actor: Mapped[Optional[str]] = mapped_column(String(64))
    processed_at: Mapped[Optional[date]] = mapped_column(Date)
    remark: Mapped[Optional[str]] = mapped_column(String(500))


class ProductInventory(Base, TimestampMixin):
    """成品库存 (Excel 表 4a-成品库存)。

    高级字段说明:
      safety_stock      — 手动设置或系统推算的安全库存量
      lead_time_days    — 工厂平均交货天数 (由 FactoryOrder 历史推算, 可手动覆盖)
      slow_moving_days  — 滞销预警阈值：超过此天数未出货 → 触发滞销警告 (默认 60)
      reorder_point     — 预警线：当 available_qty <= reorder_point 时触发补货预警
                          系统自动推算 = safety_stock + lead_time_days × daily_sales_30d
    """

    __tablename__ = "product_inventory"

    id: Mapped[int] = mapped_column(primary_key=True)
    sync_key: Mapped[Optional[str]] = mapped_column(String(128), unique=True, nullable=True)
    warehouse: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    product_code: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    sku: Mapped[Optional[str]] = mapped_column(String(255))
    spec: Mapped[Optional[str]] = mapped_column(String(255))
    unit: Mapped[Optional[str]] = mapped_column(String(16))
    physical_qty: Mapped[Decimal] = mapped_column(Numeric(14, 3), default=Decimal("0"), nullable=False)
    locked_qty: Mapped[Decimal] = mapped_column(Numeric(14, 3), default=Decimal("0"), nullable=False)
    safety_stock: Mapped[Optional[Decimal]] = mapped_column(Numeric(14, 3), nullable=True)
    lead_time_days: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    slow_moving_days: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, default=60)
    reorder_point: Mapped[Optional[Decimal]] = mapped_column(Numeric(14, 3), nullable=True)
    # 表 4a 字段补全 (Excel 导入需要; 全部 nullable 向后兼容)
    product_name: Mapped[Optional[str]] = mapped_column(String(255))        # 产品名称
    last_inbound_at: Mapped[Optional[date]] = mapped_column(Date)           # 最后入库日期
    last_outbound_at: Mapped[Optional[date]] = mapped_column(Date)          # 最后出库日期
    avg_daily_sales: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 3))  # 日均销量
    stock_status: Mapped[Optional[str]] = mapped_column(String(32))         # 库存状态(导入快照)
    stock_alert_status: Mapped[Optional[str]] = mapped_column(String(32))   # 库存预警状态(导入快照)
    slow_moving_status: Mapped[Optional[str]] = mapped_column(String(32))   # 滞销状态(导入快照)
    auto_restock_qty: Mapped[Optional[Decimal]] = mapped_column(Numeric(14, 3))  # 自动计算备货量(导入快照)
    remark: Mapped[Optional[str]] = mapped_column(String(255))

    @property
    def available_qty(self) -> Decimal:
        return Decimal(self.physical_qty or 0) - Decimal(self.locked_qty or 0)
