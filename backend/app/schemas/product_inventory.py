from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class ProductInventoryCreate(BaseModel):
    warehouse: str = Field(..., max_length=64)
    product_code: str = Field(..., max_length=32)
    sku: Optional[str] = None
    spec: Optional[str] = None
    unit: Optional[str] = "个"
    physical_qty: Decimal = Decimal("0")
    locked_qty: Decimal = Decimal("0")
    safety_stock: Optional[Decimal] = None
    lead_time_days: Optional[int] = None
    slow_moving_days: Optional[int] = 60
    reorder_point: Optional[Decimal] = None
    remark: Optional[str] = None


class ProductInventoryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    warehouse: str
    product_code: str
    sku: Optional[str]
    spec: Optional[str]
    unit: Optional[str]
    physical_qty: Decimal
    locked_qty: Decimal
    safety_stock: Optional[Decimal]
    lead_time_days: Optional[int]
    slow_moving_days: Optional[int]
    reorder_point: Optional[Decimal]
    remark: Optional[str]


class ProductInventoryWithStats(ProductInventoryOut):
    """ProductInventoryOut + 实时推算字段（不存库，每次请求计算）。"""
    id: Optional[int] = None        # 无库存产品(虚拟行)无 id
    product_name: Optional[str] = None
    has_inventory: bool = True      # False = 该产品还没建库存行(前端折叠到"无库存")
    available_qty: float                              # 展示可用=现货−已付未发(可负, 用户口径)
    physical_available: Optional[float] = None        # 物理可用=现货−locked(备货推荐口径)
    unshipped_demand: Optional[float] = None          # 已付未发单占用(可用的扣减项)
    daily_sales_30d: float
    lead_time_days_computed: Optional[int]
    safety_stock_computed: float
    reorder_point_computed: float
    days_of_stock: Optional[float]
    warning_status: str          # ok / warning / danger / critical / excess / mto(按需生产)
    auto_reorder_qty: float
    slow_moving_days: Optional[int]
    abc_class: Optional[str] = None   # A=畅销自动备货 / B / C=按需生产(MTO); 只有 A 类给备货建议
    in_production_free: Optional[float] = None       # R1 备货在产(会入库, 已从推荐备货扣掉)
    in_production_allocated: Optional[float] = None   # R1 客户单在产(发给下单客户, 不抵, 仅展示)
    season_target_month: Optional[int] = None         # 重点备货月: 备货瞄准的月(今天+提前期)
    season_multiplier: Optional[float] = None         # 季节倍数(目标月系数÷最近窗口均值)
    restock_policy: Optional[str] = None               # 唯一备货引擎策略
    target_stock: Optional[float] = None               # 唯一备货引擎目标成品库存
    qualified_hot: Optional[bool] = None               # 90天清洗销量是否达到热销门槛
    forecast_30d: Optional[int] = None                 # 同订单备货页的未来30天预测
    product_restock_total: Optional[float] = None       # 同产品各库存行建议之和=订单备货页产品数
