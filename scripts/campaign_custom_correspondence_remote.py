"""Preview / one exact setting insert only. No SKU, price or order mutation."""
import json
from sqlalchemy import select, text
from app.models.product import Product
from app.models.pricing import PricingSku
from app.models.pricing_ext import PricingSkuPromo
from app.models.settings import SystemSetting
from campaign_custom_correspondence_policy import KEY, PRODUCT, ITEM, APPROVED, ROWS, digest


def row_dict(row):
    return {c.name:getattr(row,c.name) for c in row.__table__.columns}


def inspect(db, lock=False):
    def rows(stmt):
        return list(db.execute(stmt.with_for_update() if lock else stmt).scalars())
    products=rows(select(Product).where(Product.code==PRODUCT))
    skus=rows(select(PricingSku).where(PricingSku.product_code==PRODUCT).order_by(PricingSku.sku_code))
    if len(products)!=1 or ITEM not in {str(products[0].taobao_id),*map(str,products[0].alt_taobao_ids or [])}:
        raise ValueError('exact_product_link_not_proven')
    codes=[s.sku_code for s in skus]
    promos=rows(select(PricingSkuPromo).where(PricingSkuPromo.sku_code.in_(codes)).order_by(PricingSkuPromo.sku_code))
    by_code={s.sku_code:s for s in skus}
    all_promos=rows(select(PricingSkuPromo).order_by(PricingSkuPromo.sku_code))
    bindings=[]
    for target in ROWS:
        sku=by_code.get(target['code'])
        if sku is None or sku.is_custom_placeholder is not True:
            raise ValueError('target_not_existing_custom_sku')
        matches=[p for p in all_promos if target['sku'] in {str(p.taobao_sku_id),*map(str,p.alt_taobao_sku_ids or [])}]
        if len(matches)>1 or any(p.sku_code!=target['code'] or str(p.taobao_item_id)!=ITEM for p in matches):
            raise ValueError('physical_sku_has_other_binding_requires_review')
        bindings.append(dict(**target, existing_physical_bindings=[row_dict(p) for p in matches]))
    settings=rows(select(SystemSetting).where(SystemSetting.key==KEY))
    marker=settings[0] if settings else None
    if marker and (marker.is_secret or marker.value_encrypted is not None or json.loads(marker.value_plain)!=APPROVED):
        raise ValueError('existing_correspondence_conflict')
    protected=dict(products=[row_dict(r) for r in products],skus=[row_dict(r) for r in skus],promos=[row_dict(r) for r in promos])
    result=dict(protected_sha256=digest(protected),already_applied=marker is not None,
        bindings=bindings, preserved_primary_and_alternatives=[dict(code=p.sku_code,item=p.taobao_item_id,sku=p.taobao_sku_id,alt=p.alt_taobao_sku_ids) for p in promos if p.sku_code in {r['code'] for r in ROWS}],
        setting=row_dict(marker) if marker else None, only_write=dict(table='system_settings',key=KEY,value=APPROVED),
        platform_write=False,price_change=False,order_change=False)
    result['precondition_sha256']=digest(result)
    return result


def apply(db, expected):
    if db.bind.dialect.name=='postgresql':db.execute(text('SELECT pg_advisory_xact_lock(724042164333)'))
    before=inspect(db,lock=True)
    if before['already_applied']:
        return dict(status='already_applied_no_write',database_write=False,before=before)
    if not expected or before['precondition_sha256']!=expected:raise ValueError('preview_stale_or_wrong_scope')
    db.add(SystemSetting(key=KEY,value_plain=json.dumps(APPROVED,ensure_ascii=False),is_secret=False,
        description='2026-09-27用户确认3条定制活动对应；保留主备用绑定、价格、原价基线及历史订单'))
    db.flush()
    after=inspect(db)
    if after['protected_sha256']!=before['protected_sha256']:raise ValueError('protected_fields_changed')
    db.commit()
    readback=inspect(db)
    if not readback['already_applied'] or readback['protected_sha256']!=before['protected_sha256']:
        raise ValueError('commit_readback_mismatch_do_not_retry')
    return dict(status='applied',database_write=True,before=before,after=readback,platform_write=False)


def run(mode,expected=None):
    from app.database import SessionLocal
    with SessionLocal() as db:
        if mode=='preview':
            if db.bind.dialect.name=='postgresql':db.execute(text('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY'))
            return dict(status='preview',preview=inspect(db),database_write=False)
        if mode!='apply':raise ValueError('unsupported_mode')
        if db.bind.dialect.name=='postgresql':db.execute(text('SET TRANSACTION ISOLATION LEVEL SERIALIZABLE'))
        return apply(db,expected)
