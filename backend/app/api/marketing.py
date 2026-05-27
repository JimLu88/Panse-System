"""营销与售后表的通用 CRUD + ROI 计算。"""
from datetime import date
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.marketing import (
    AfterSales,
    BrandMarketing,
    OutsourcingExpense,
    PromotionFlow,
    Sample,
    WoodLoss,
)
from app.services import roi_service

router = APIRouter(prefix="/api/marketing", tags=["marketing"])


# -------- Samples (13) --------

class SampleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    sample_no: str
    product_code: Optional[str]
    product_name: Optional[str]
    sku: Optional[str]
    sample_type: Optional[str]
    qty: int
    made_at: Optional[date]
    cost: Optional[Decimal]
    location: Optional[str]
    status: Optional[str]
    usage: Optional[str]
    remark: Optional[str]


@router.get("/samples", response_model=list[SampleOut])
def list_samples(
    status: Optional[str] = None, limit: int = Query(200, le=1000), db: Session = Depends(get_db)
):
    stmt = select(Sample)
    if status:
        stmt = stmt.where(Sample.status == status)
    stmt = stmt.order_by(Sample.sample_no).limit(limit)
    return db.execute(stmt).scalars().all()


class SampleUpdate(BaseModel):
    status: Optional[str] = None
    location: Optional[str] = None
    usage: Optional[str] = None
    remark: Optional[str] = None


@router.patch("/samples/{sample_id}", response_model=SampleOut)
def update_sample(sample_id: int, payload: SampleUpdate, db: Session = Depends(get_db)):
    sample = db.get(Sample, sample_id)
    if sample is None:
        raise HTTPException(status_code=404, detail="样品不存在")
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(sample, field, value)
    db.commit()
    db.refresh(sample)
    return sample


# -------- Brand Marketing (14) --------

class BrandMarketingOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    project_name: str
    project_type: Optional[str]
    partner: Optional[str]
    start_date: Optional[date]
    end_date: Optional[date]
    budget: Optional[Decimal]
    actual_spend: Optional[Decimal]
    status: Optional[str]
    effect_eval: Optional[str]


class BrandMarketingCreate(BaseModel):
    project_name: str
    project_type: Optional[str] = None
    partner: Optional[str] = None
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    budget: Optional[Decimal] = None
    actual_spend: Optional[Decimal] = None
    status: Optional[str] = "进行中"
    effect_eval: Optional[str] = None


@router.get("/brand", response_model=list[BrandMarketingOut])
def list_brand(db: Session = Depends(get_db)):
    return db.execute(select(BrandMarketing).order_by(BrandMarketing.id.desc())).scalars().all()


@router.post("/brand", response_model=BrandMarketingOut, status_code=201)
def create_brand(payload: BrandMarketingCreate, db: Session = Depends(get_db)):
    b = BrandMarketing(**payload.model_dump())
    db.add(b)
    db.commit()
    db.refresh(b)
    return b


# -------- Promotion Flows (15) --------

class PromotionFlowOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    transaction_date: Optional[date]
    flow_type: Optional[str]
    amount: Decimal
    alipay_flow_no: Optional[str]
    remark: Optional[str]


@router.get("/promotion", response_model=list[PromotionFlowOut])
def list_promotion(limit: int = Query(200, le=1000), db: Session = Depends(get_db)):
    rows = db.execute(
        select(PromotionFlow).order_by(PromotionFlow.transaction_date.desc().nulls_last()).limit(limit)
    ).scalars().all()
    return rows


# -------- Outsourcing (17) --------

class OutsourcingOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    alipay_flow_no: Optional[str]
    payee: str
    amount: Decimal
    project: Optional[str]
    cost_category: Optional[str]
    payment_date: Optional[date]
    remark: Optional[str]


@router.get("/outsourcing", response_model=list[OutsourcingOut])
def list_outsourcing(limit: int = Query(200, le=1000), db: Session = Depends(get_db)):
    return db.execute(
        select(OutsourcingExpense).order_by(OutsourcingExpense.payment_date.desc().nulls_last()).limit(limit)
    ).scalars().all()


# -------- After Sales (18) --------

class AfterSalesOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    platform_order_no: str
    reason: Optional[str]
    in_platform_total: Optional[Decimal]
    out_platform_total: Optional[Decimal]
    refill_sku: Optional[str]
    status: Optional[str]
    customer_satisfaction: Optional[str]
    processed_at: Optional[date]


@router.get("/after-sales", response_model=list[AfterSalesOut])
def list_after_sales(
    status: Optional[str] = None, limit: int = Query(200, le=1000), db: Session = Depends(get_db)
):
    stmt = select(AfterSales)
    if status:
        stmt = stmt.where(AfterSales.status == status)
    stmt = stmt.order_by(AfterSales.processed_at.desc().nulls_last()).limit(limit)
    return db.execute(stmt).scalars().all()


# -------- Wood Loss (12) --------

class WoodLossOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    purchase_date: Optional[date]
    wood_type: Optional[str]
    spec: Optional[str]
    unit: Optional[str]
    inbound_qty: Optional[Decimal]
    used_qty: Optional[Decimal]
    loss_qty: Optional[Decimal]
    loss_rate_pct: Optional[Decimal]
    related_product_qty: Optional[Decimal] = None
    reason: Optional[str] = None
    disposition: Optional[str] = None
    remark: Optional[str] = None


@router.get("/wood-loss", response_model=list[WoodLossOut])
def list_wood_loss(db: Session = Depends(get_db)):
    return db.execute(select(WoodLoss).order_by(WoodLoss.id.desc())).scalars().all()


# -------- ROI --------

class RoiOut(BaseModel):
    period_start: Optional[date]
    period_end: Optional[date]
    promotion_spend: Decimal
    promotion_recharge: Decimal
    order_count: int
    order_revenue: Decimal
    avg_order_value: Decimal
    roi: Optional[Decimal]


@router.get("/roi", response_model=RoiOut)
def get_roi(
    period_start: Optional[date] = Query(None),
    period_end: Optional[date] = Query(None),
    db: Session = Depends(get_db),
):
    r = roi_service.compute(db, period_start=period_start, period_end=period_end)
    return RoiOut(**r.__dict__)
