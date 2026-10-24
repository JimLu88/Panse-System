"""Read-only bed calibration replay; held-out evidence is separate from node fit."""
import json
import statistics
from collections import defaultdict
from app.services.custom_quote_reference import bed_references
from app.services.custom_quote_v2_service import quote_light, _sku_variant_key
from app.services.bed_cost_curve import estimate_wood
from app.cli.custom_quote_verify import fingerprints
from app.models.pricing import PricingSku


def run(db):
    before = fingerprints(db)
    refs = bed_references(db)
    rows = {r['sku_code']: r for r in refs['items']}
    skus = {s.sku_code: s for s in db.query(PricingSku).filter(PricingSku.sku_code.in_(rows)).all()}
    cases, known, heldout, parts, groups = [], [], [], [], defaultdict(list)
    for code,r in rows.items():
        if r['error']:
            cases.append(dict(product=r['product_code'], sku=code, blocked=r['error']))
            continue
        s=skus[code]
        key=(r['product_code'],_sku_variant_key(s.sku),r['height_cm'])
        groups[key].append(dict(sku=code, area=r['length_cm']*r['width_cm']/10000,wood=float(s.wood_cost)))
        for factor in (1,.9,1.1):
            result=quote_light(db,base_product_code=r['product_code'],base_sku_code=code,
                price_tier='big_buyer',outer_dimensions=dict(length_cm=r['length_cm']*factor,width_cm=r['width_cm']*factor))
            basis=result.get('outer_dimension_basis') or {}
            cases.append(dict(product=r['product_code'],sku=code,factor=factor,price=result.get('final_price'),
                error=result.get('error'),mode=(basis.get('calibration') or {}).get('mode'),
                same_anchor=(factor!=1 or result.get('final_price')==r['buyer_price']),
                fees_unchanged=basis.get('fees')==r['fees']))
        for part in r['parts']:
            if any(k in part['name'] for k in ('木作','铺板')) or not part['price']:continue
            result=quote_light(db,base_product_code=r['product_code'],base_sku_code=code,
                price_tier='big_buyer',outer_dimensions=dict(length_cm=r['length_cm'],width_cm=r['width_cm']),
                remove_parts=[dict(material=part['name'],qty=part['qty'])])
            parts.append(dict(sku=code,part=part['name'],price=result.get('final_price'),error=result.get('error')))
    for key,pts in groups.items():
        for a in pts:
            for b in pts:
                if a['area']==b['area']:continue
                try:
                    r=estimate_wood(pts,source_area=a['area'],source_cost=a['wood'],target_area=b['area'])
                    known.append(dict(product=key[0],source=a['sku'],target=b['sku'],
                        error=round(r['wood']-b['wood'],4),old_error=round(a['wood']*b['area']/a['area']-b['wood'],4)))
                except ValueError as exc:
                    known.append(dict(product=key[0],source=a['sku'],target=b['sku'],blocked=str(exc)))
        for b in pts:
            remaining=[p for p in pts if p['area']!=b['area']]
            if len({p['area'] for p in remaining})<2:continue
            if not min(p['area'] for p in remaining)<b['area']<max(p['area'] for p in remaining):continue
            a=min(remaining,key=lambda p:abs(p['area']-b['area']))
            try:
                r=estimate_wood(remaining,source_area=a['area'],source_cost=a['wood'],target_area=b['area'])
                heldout.append(dict(product=key[0],target=b['sku'],actual=b['wood'],predicted=round(r['wood'],2),
                    absolute_error_percent=abs(r['wood']-b['wood'])/b['wood']*100,
                    old_absolute_error_percent=abs(a['wood']*b['area']/a['area']-b['wood'])/b['wood']*100))
            except ValueError as exc:
                heldout.append(dict(product=key[0],target=b['sku'],blocked=str(exc)))
    valid=[r for r in heldout if 'absolute_error_percent' in r]
    return dict(products=refs['products'],sku_count=len(rows),cases=cases,known_nodes=known,heldout=heldout,parts=parts,
        heldout_summary=dict(count=len(valid),
            median_error_percent=statistics.median(r['absolute_error_percent'] for r in valid) if valid else None,
            max_error_percent=max((r['absolute_error_percent'] for r in valid),default=None),
            old_median_error_percent=statistics.median(r['old_absolute_error_percent'] for r in valid) if valid else None),
        prices_unchanged=before==fingerprints(db),production_writes=False)


if __name__=='__main__':
    from sqlalchemy import text
    from app.database import SessionLocal
    with SessionLocal() as db:
        if db.bind.dialect.name=='postgresql':db.execute(text('SET TRANSACTION READ ONLY'))
        print(json.dumps(run(db),ensure_ascii=False))
        db.rollback()
