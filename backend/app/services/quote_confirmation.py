"""Request-bound explicit quote confirmation. No prices or catalog writes."""
from typing import Literal
from uuid import UUID
from pydantic import BaseModel, StrictBool, model_validator, field_validator
from fastapi import HTTPException


class QuoteConfirmation(BaseModel):
    request_id: UUID
    price_confirmed: StrictBool
    material_confirmed: StrictBool
    identity_confirmed: StrictBool
    inputs: dict


class ConfirmedQuoteIn(BaseModel):
    confirmation: QuoteConfirmation

    @field_validator('target_material','main_material','base_sku_code','base_product_code',check_fields=False)
    @classmethod
    def reject_blank_choice(cls, value):
        if not isinstance(value,str) or not value.strip():
            raise ValueError('本次明确选择不能为空')
        return value

    @model_validator(mode='before')
    @classmethod
    def require_current_confirmation(cls, value):
        if not isinstance(value, dict):
            return value
        c = value.get('confirmation')
        if isinstance(c, QuoteConfirmation):
            c = c.model_dump(mode='json')
        if (not isinstance(c, dict)
                or any(c.get(k) is not True for k in ('price_confirmed','material_confirmed','identity_confirmed'))
                or c.get('inputs') != {k:v for k,v in value.items() if k != 'confirmation'}):
            raise ValueError('请重新明确确认本次价格口径、精确款式和具体主材；旧确认不适用于已修改的请求')
        return value


PriceTier = Literal['list','daily','mid','big','mid_buyer','big_buyer']


def validate_light_context(db, payload):
    from app.models.pricing import PricingSku
    from app.models.pricing_ext import PricingSkuPromo
    from app.services import custom_quote_v2_service as v2
    sku=db.query(PricingSku).filter(PricingSku.sku_code==payload.base_sku_code,
        PricingSku.product_code==payload.base_product_code).first()
    if not sku:
        raise HTTPException(422,'确认的精确SKU不属于当前产品，请重新选择款式')
    if not v2.detect_wood(payload.target_material):
        raise HTTPException(422,'请明确本次主材木种/板材；不能将台面或空值当作主材')
    field=v2._BUYER_PRICE_TIERS.get(payload.price_tier)
    source=db.query(PricingSkuPromo).filter(PricingSkuPromo.sku_code==sku.sku_code).first() if field else sku
    field=field or v2._PRICE_TIERS[payload.price_tier]
    price=getattr(source,field,None) if source else None
    if price is None or not v2._is_quoteable_sku(sku,resolved_price=price):
        raise HTTPException(422,'所选SKU缺少本次确认口径的有效价格；不回退其他价型或款式')
