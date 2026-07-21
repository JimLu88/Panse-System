from app.models.base import Base
from app.models.material import Material, MaterialPriceHistory  # noqa: F401 - 物料价格历史(版本化)
from app.models.pricing_formula import PricingFormulaRule  # noqa: F401 - 注册进 metadata, SQLite 测试库才建表 (Plan C2)
from app.models.campaign_signup import CampaignSignupPrice  # noqa: F401 - 活动报名价 (Plan F1)
from app.models.campaign import (  # noqa: F401 - 活动生命周期 (2026-07-17 spec P1)
    CampaignCalendar,
    CampaignPlan,
    CampaignReconReport,
)
from app.models.field_change import FieldChange  # noqa: F401 - 人工编辑历史档案 (方向2+4)
from app.models.disassembly_log import DisassemblyLog  # noqa: F401 - 拆BOM历史+回撤
from app.models.inventory import PartInventory, ProductInventory
from app.models.product import Product
from app.models.bom import BomLine
from app.models.exception import DataException
from app.models.feishu_sync import FeishuSyncMap, FeishuTableBinding
from app.models.finance import (
    AccountBalance,
    AlipayFlow,
    FactoryReconciliation,
    LogisticsBill,
    PackingBill,
    PackingPaymentAllocation,
    RefillRecord,
    StaffSalary,
    WanshifuBill,
    WanshifuOrder,
)
from app.models.order import FactoryOrder, Order, PartPurchase, PartsMonthlyRecon
from app.models.shipment import Shipment
from app.models.ai import AiChatLog, AiCodePatch
from app.models.auth import AuditLog, User
from app.models.competitor import CompetitorPrice
from app.models.custom_variant import CustomVariant
from app.models.knowledge import AiKnowledge
from app.models.settings import SystemSetting
from app.models.system_event import SystemEvent
from app.models.chatbi_query import ChatbiQuery  # noqa: F401 - ChatBI 问答审计
from app.models.review_asset import ReviewAsset  # noqa: F401 - 评价资产台账 (Plan1 v2)
from app.models.system_health import SystemHealthLog
from app.models.import_job import ImportJob
from app.models.import_file import ImportedFile
from app.models.prepay_ledger import PrepayLedger
from app.models.settlement import OrderSettlement
from app.models.factory_recon_item import FactoryReconItem
from app.models.factory_settlement import FactorySettlementPayment, FactorySupplierAlias
from app.models.shop_deposit import ShopDeposit
from app.models.scheduled_job import ScheduledJobRun
from app.models.alert import Alert
from app.models.inventory_lock import InventoryLockLedger
from app.models.accounting_period import AccountingPeriod
from app.models.order_event import OrderEvent
from app.models.supplier_score import SupplierScore
from app.models.daily_briefing import DailyBriefing
from app.models.approval import ApprovalRequest
from app.models.customer import Customer
from app.models.sales_rollup import SalesDailyRollup
from app.models.supplier import (
    DeliveryFile,
    DeliveryNote,
    DeliveryNoteLine,
    Supplier,
)
from app.models.marketing import (
    AfterSales,
    BrandMarketing,
    OutsourcingExpense,
    PromotionFlow,
    Sample,
    WoodLoss,
)
from app.models.pricing import PricingSku
from app.models.pricing_ext import PricingSkuCosts, PricingSkuPromo
from app.models.pricing_custom import PricingCustomField, PricingCustomValue
from app.models.price_change import PriceChangeLog
from app.models.pricing_version import PricingSkuVersion  # noqa: F401 - 工厂调价历史(有效期定价)
from app.models.taobao_listing import TaobaoListing
from app.models.npd import (
    NpdProject, NpdStage, NpdStageInstance, NpdStageTaskTemplate, NpdTask,
    NpdInspectionTemplate, NpdInspectionItem,
    NpdCostGate, NpdCraftIssue, NpdSupplierCandidate, NpdBomLine, NpdKnowledgeNote,
)

__all__ = [
    "NpdStage",
    "NpdProject",
    "NpdStageInstance",
    "NpdStageTaskTemplate",
    "NpdTask",
    "NpdInspectionTemplate",
    "NpdInspectionItem",
    "NpdCostGate",
    "NpdCraftIssue",
    "NpdSupplierCandidate",
    "NpdBomLine",
    "NpdKnowledgeNote",
    "Base",
    "TaobaoListing",
    "Material",
    "PartInventory",
    "ProductInventory",
    "Product",
    "BomLine",
    "DataException",
    "FeishuSyncMap",
    "FeishuTableBinding",
    "PricingSku",
    "PricingSkuCosts",
    "PricingSkuPromo",
    "PricingCustomField",
    "PricingCustomValue",
    "Order",
    "FactoryOrder",
    "PartPurchase",
    "PartsMonthlyRecon",
    "AlipayFlow",
    "AccountBalance",
    "RefillRecord",
    "FactoryReconciliation",
    "WanshifuBill",
    "WanshifuOrder",
    "LogisticsBill",
    "PackingBill",
    "PackingPaymentAllocation",
    "StaffSalary",
    "AiChatLog",
    "AiCodePatch",
    "Sample",
    "BrandMarketing",
    "PromotionFlow",
    "OutsourcingExpense",
    "AfterSales",
    "WoodLoss",
    "User",
    "AuditLog",
    "AiKnowledge",
    "CustomVariant",
    "CompetitorPrice",
    "Supplier",
    "DeliveryNote",
    "DeliveryNoteLine",
    "DeliveryFile",
    "SystemSetting",
    "SystemEvent",
    "SystemHealthLog",
    "ImportJob",
    "ImportedFile",
    "PrepayLedger",
    "OrderSettlement",
    "FactoryReconItem",
    "FactorySettlementPayment",
    "FactorySupplierAlias",
    "ShopDeposit",
]
