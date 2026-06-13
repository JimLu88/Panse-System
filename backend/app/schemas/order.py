from datetime import date
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class OrderCreate(BaseModel):
    platform: str
    order_no: str
    is_refill: bool = False
    order_date: Optional[date] = None
    customer_name: Optional[str] = None
    customer_phone: Optional[str] = None
    customer_address: Optional[str] = None
    product_code: Optional[str] = None
    product_name: Optional[str] = None
    sku: Optional[str] = None
    sku_code: Optional[str] = None
    is_custom: bool = False
    qty: int = 1
    paid_amount: Optional[Decimal] = None
    remark: Optional[str] = None


class OrderUpdate(BaseModel):
    carrier: Optional[str] = None
    tracking_no: Optional[str] = None
    install_ticket_no: Optional[str] = None
    ship_date: Optional[date] = None
    actual_cost: Optional[Decimal] = None
    actual_freight: Optional[Decimal] = None
    remark: Optional[str] = None


class OrderStatusChange(BaseModel):
    status: str
    actor: Optional[str] = None
    force: bool = False
    confirmed: bool = False   # 看板人工拖拽 → 标记该单为"已确定"(人工敲定)
    # Plan F2: 取消带活跃工厂单的订单时必须二选一 — future=转远期单 / release=纯释放库存
    disposition: Optional[str] = None          # "future" | "release"
    planned_ship_date: Optional[date] = None   # disposition=future 时必填 (预计发货日)


class OrderOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    platform: str
    order_no: str
    # 内部产品名 (产品总表), 列表端点回填; 订单自带 product_name 是淘宝标题不直观
    internal_product_name: Optional[str] = None
    is_refill: bool
    order_date: Optional[date]
    ship_date: Optional[date]
    customer_name: Optional[str]
    product_code: Optional[str]
    product_name: Optional[str]
    sku: Optional[str]
    is_custom: bool
    qty: int
    status: str
    # 派生展示状态: 有未完成售后(AfterSales 非「已完成」)的订单显示为 aftersales,
    # 不改底层 status(保留 shipped/signed 生命周期)。看板/筛选按此归"售后中"(2026-06-12)。
    display_status: Optional[str] = None
    has_active_aftersales: bool = False
    carrier: Optional[str]
    tracking_no: Optional[str]
    paid_amount: Optional[Decimal]
    theoretical_cost: Optional[Decimal] = None
    actual_cost: Optional[Decimal] = None
    actual_freight: Optional[Decimal] = None
    cost_diff: Optional[Decimal] = None
    kanban_confirmed: bool = False   # 看板里人工拖拽确定过


class CsvImportReport(BaseModel):
    inserted: int
    backfilled: int = 0
    skipped_duplicate: int
    skipped_invalid: int
    errors: list[str] = Field(default_factory=list)
    archived_file_id: Optional[int] = None   # 归档原文件 id (导入档案可回溯)
    duplicate_upload: bool = False            # 同一文件曾上传过
