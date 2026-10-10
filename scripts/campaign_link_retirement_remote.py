"""Exact user-approved ERP link exclusion; no platform or product mutation.

Runs through the local maintenance CLI inside the existing API container.
Only apply() writes, and only the one existing campaign_item_exclusions table.
"""
import hashlib
import json
from sqlalchemy import select, text
from app.models.product import Product
from app.models.pricing import PricingSku
from app.models.pricing_ext import PricingSkuPromo
from app.models.campaign import CampaignItemExclusion

OLD = '919649052479'
KEEP = '1036471324464'
PRODUCT = 'PPS25250130420'
PROOF = 'cdccb78b4e9b7c2dc3377fb44c49bf05d53bc0ff3012921ed8f7ba98c3d8c60e'
SOURCE = 'user_link_retired_20260927'
REASON = f'用户确认旧链接仓库中；仅停用本链接活动候选，保留同款{KEEP}；截图SHA256:{PROOF}'


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,default=str).encode()).hexdigest()


def row_dict(row):
    return {col.name:getattr(row,col.name) for col in row.__table__.columns}


def inspect(db, lock=False):
    def rows(stmt):
        return list(db.execute(stmt.with_for_update() if lock else stmt).scalars())
    products=rows(select(Product).where(Product.code==PRODUCT))
    if len(products)!=1:raise ValueError('exact_product_missing_or_ambiguous')
    skus=rows(select(PricingSku).where(PricingSku.product_code==PRODUCT).order_by(PricingSku.sku_code))
    if not skus:raise ValueError('product_has_no_skus')
    codes=[s.sku_code for s in skus]
    promos=rows(select(PricingSkuPromo).where(PricingSkuPromo.sku_code.in_(codes)).order_by(PricingSkuPromo.sku_code))
    product=products[0]
    item_ids={str(product.taobao_id),*map(str,product.alt_taobao_ids or []),*[str(p.taobao_item_id) for p in promos]}
    if not {OLD,KEEP}<=item_ids:raise ValueError('two_links_not_bound_to_same_product')
    # Prevent a stale confirmation from retiring a link reassigned elsewhere.
    foreign=db.execute(select(PricingSkuPromo.sku_code).where(
        PricingSkuPromo.taobao_item_id==OLD,~PricingSkuPromo.sku_code.in_(codes))).scalars().all()
    if foreign:raise ValueError('old_link_bound_to_other_product')
    if db.execute(select(Product.code).where(Product.taobao_id==OLD,Product.code!=PRODUCT)).first():
        raise ValueError('old_link_is_another_products_primary')
    markers=rows(select(CampaignItemExclusion).where(CampaignItemExclusion.taobao_item_id.in_([OLD,KEEP])))
    protected=dict(product=row_dict(product),skus=[row_dict(r) for r in skus],promos=[row_dict(r) for r in promos],
        keep_exclusion=[row_dict(r) for r in markers if r.taobao_item_id==KEEP])
    old=next((r for r in markers if r.taobao_item_id==OLD),None)
    exact=old is not None and old.active and old.source==SOURCE and old.reason==REASON
    if old is not None and not exact:
        raise ValueError('existing_old_link_marker_requires_review_do_not_overwrite')
    body=dict(old_item=OLD,keep_item=KEEP,product_code=PRODUCT,
        product_listing_status=product.listing_status,protected_sha256=digest(protected),
        old_exclusion=row_dict(old) if old else None,
        sku_bindings=[dict(code=p.sku_code,item=p.taobao_item_id,sku=p.taobao_sku_id,alt=p.alt_taobao_sku_ids) for p in promos],
        proof_sha256=PROOF,new_link_platform_status='unknown',
        only_write=dict(table='campaign_item_exclusions',item=OLD,source=SOURCE,reason=REASON),
        rollback=dict(action='deactivate_exact_marker_only_after_new_authorization',source=SOURCE,item=OLD),
        already_applied=bool(exact),platform_write=False)
    body['precondition_sha256']=digest(body)
    return body


def apply(db, expected, proof):
    if proof!=PROOF:raise ValueError('proof_mismatch')
    if db.bind.dialect.name=='postgresql':
        # A second identical invocation waits for the original transaction.
        db.execute(text('SELECT pg_advisory_xact_lock(919649052479)'))
    before=inspect(db,lock=True)
    if before['already_applied']:
        return dict(status='already_applied_no_write',before=before,database_write=False,platform_write=False)
    if before['precondition_sha256']!=expected:raise ValueError('dry_run_stale_or_wrong_scope')
    db.add(CampaignItemExclusion(taobao_item_id=OLD,reason=REASON,source=SOURCE,active=True))
    db.flush()
    after=inspect(db)
    if after['protected_sha256']!=before['protected_sha256']:raise ValueError('protected_fields_changed')
    db.commit()
    # Readback in a fresh transaction: a successful flush isn't completion.
    readback=inspect(db)
    if not readback['already_applied']:raise ValueError('committed_marker_not_read_back')
    return dict(status='applied',before=before,after=readback,database_write=True,platform_write=False)


def run(mode, expected=None, proof=None):
    from app.database import SessionLocal
    with SessionLocal() as db:
        if mode=='preview':
            if db.bind.dialect.name=='postgresql':
                db.execute(text('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY'))
            return dict(status='preview',preview=inspect(db),database_write=False,platform_write=False)
        if mode!='apply':raise ValueError('unsupported_mode')
        if db.bind.dialect.name=='postgresql':
            db.execute(text('SET TRANSACTION ISOLATION LEVEL SERIALIZABLE'))
        return apply(db,expected,proof)
