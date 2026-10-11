"""Bounded public projection. Import/help never connects to ERP; no ORM SELECT *."""
from __future__ import annotations
import argparse
import datetime as dt
import hashlib
import json
from decimal import Decimal
from pathlib import Path
import re
import sys
from sqlalchemy import text

CONTRACT = 'erp.aeo-bounded-public.v1'


def scope_pairs(scope):
    if not isinstance(scope, dict):
        raise ValueError('scope_required')
    products = scope.get('requestedProductCodes')
    skus = scope.get('requestedSkuCodes')
    mappings = list(scope.get('acceptedProductSkuMappings') or [])
    extra = scope.get('narrowAdditionalReadScope')
    if extra:
        mappings.append(extra)
    for values, limit in ((products, 20), (skus, 100)):
        if (not isinstance(values, list) or not 1 <= len(values) <= limit
                or any(not isinstance(v, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,32}', v) for v in values)
                or len(set(values)) != len(values)):
            raise ValueError('nonempty_unique_bounded_scope_required')
    pairs = [(m.get('productCode'), m.get('skuCode')) for m in mappings]
    if (len(pairs) != len(skus) or {s for _, s in pairs} != set(skus)
            or {p for p, _ in pairs} != set(products) or len(set(pairs)) != len(pairs)):
        raise ValueError('exact_product_sku_mapping_required')
    return sorted(pairs)


def stamp(value):
    if value is None:
        raise ValueError('source_timestamp_missing')
    result = value.isoformat() if hasattr(value, 'isoformat') else str(value)
    dt.datetime.fromisoformat(result.replace('Z', '+00:00'))
    return result


def amount(value):
    if value is None:
        return None  # Persisted unknown, never daily-price fallback or zero.
    value = Decimal(str(value))
    if not value.is_finite() or value < 0 or value != value.quantize(Decimal('.01')):
        raise ValueError('persisted_price_invalid')
    return format(value, '.2f')


def build_snapshot(connection, scope):
    pairs = scope_pairs(scope)  # Must fail before the first DB statement.
    dialect = connection.dialect.name
    if dialect == 'postgresql':
        connection.execute(text('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY'))
        connection.execute(text("SET LOCAL statement_timeout = '10s'"))
    elif dialect == 'sqlite':
        connection.execute(text('PRAGMA query_only = ON'))
    else:
        raise ValueError('unsupported_read_only_backend')
    products = sorted({p for p, _ in pairs})
    params = {f'p{i}': p for i, p in enumerate(products)}
    slots = ','.join(':'+k for k in params)
    product_rows = list(connection.execute(text(f'''
        SELECT code,name,sub_name,brand,category,image_url,main_material,
               aux_material,size_detail,size_value,size_confirmed,listing_status,updated_at
        FROM products WHERE code IN ({slots}) ORDER BY code
    '''), params).mappings())
    if len(product_rows) != len(products) or {r['code'] for r in product_rows} != set(products):
        raise ValueError('requested_product_missing_or_duplicate')
    params = {k: value for i, pair in enumerate(pairs)
              for k, value in ((f'p{i}', pair[0]), (f's{i}', pair[1]))}
    selected = ','.join(f'(:p{i},:s{i})' for i in range(len(pairs)))
    rows = list(connection.execute(text(f'''
        WITH requested(product_code,sku_code) AS (VALUES {selected})
        SELECT r.product_code AS requested_product,r.sku_code AS requested_sku,
               p.product_code,p.sku_code,p.sku,p.size_info,p.image_url,p.updated_at,
               promo.mid_buyer_price,promo.updated_at AS promo_updated_at
        FROM requested r
        LEFT JOIN pricing_sku p ON p.sku_code=r.sku_code
          AND p.product_code=r.product_code AND p.is_custom_placeholder=FALSE
        LEFT JOIN pricing_sku_promo promo ON promo.sku_code=p.sku_code
        ORDER BY r.product_code,r.sku_code
    '''), params).mappings())
    if (len(rows) != len(pairs) or {(r['product_code'],r['sku_code']) for r in rows} != set(pairs)):
        raise ValueError('requested_sku_missing_duplicate_or_mapping_mismatch')
    generated = dt.datetime.now(dt.timezone.utc).isoformat()
    public_products = [{
        'code': r['code'], 'name': r['name'], 'subName': r['sub_name'],
        'brand': r['brand'], 'category': r['category'], 'imageUrl': r['image_url'],
        'mainMaterial': r['main_material'], 'auxMaterial': r['aux_material'],
        'sizeDetail': r['size_detail'], 'sizeValue': r['size_value'],
        'sizeConfirmed': r['size_confirmed'], 'listingStatus': r['listing_status'],
        'updatedAt': stamp(r['updated_at'])} for r in product_rows]
    pricing = [{
        'productCode': r['product_code'], 'skuCode': r['sku_code'], 'sku': r['sku'],
        'sizeInfo': r['size_info'], 'imageUrl': r['image_url'],
        'updatedAt': stamp(r['updated_at']), 'midBuyerPrice': amount(r['mid_buyer_price']),
        'currency': 'CNY', 'priceBasis': '中促到手价',
        'sourceField': 'PricingSkuPromo.mid_buyer_price',
        'sourceUpdatedAt': stamp(r['promo_updated_at']), 'observedAt': generated} for r in rows]
    return dict(schemaVersion=1, contractVersion=CONTRACT, generatedAt=generated,
        mode='read-only-public-product-snapshot',
        source=dict(system='Panse ERP', databaseBackend=dialect,
                    priceField='PricingSkuPromo.mid_buyer_price', recalculated=False),
        scope=dict(coverage='selected-product-sku-only',
            requestedProductCodes=products, requestedSkuCodes=sorted(s for _, s in pairs),
            productSkuMappings=[dict(productCode=p,skuCode=s) for p,s in pairs],
            scopeSha256=hashlib.sha256(json.dumps(pairs,separators=(',',':')).encode()).hexdigest(),
            included=['product identity','public materials','public dimensions',
                      'persisted mid buyer price','public image candidates','listing status'],
            excluded=['orders','customers','costs','suppliers','factory records','private reviews'],
            dimensionAssets='not_read_no_exact_asset_to_requested_sku_contract',
            imageEvidence='candidate_only_not_license_or_exact_sku_proof'),
        products=public_products, pricing=pricing, dimensions=[],
        counts=dict(products=len(public_products),pricing=len(pricing),dimensions=0))


def validate_snapshot(snapshot, scope):
    """Consumer-usable strict projection/scope check; no DB access."""
    pairs = scope_pairs(scope)
    if (set(snapshot) != {'schemaVersion','contractVersion','generatedAt','mode','source','scope','products','pricing','dimensions','counts'}
            or snapshot['schemaVersion'] != 1 or snapshot['contractVersion'] != CONTRACT
            or snapshot['mode'] != 'read-only-public-product-snapshot'):
        raise ValueError('snapshot_contract_mismatch')
    stamp(snapshot['generatedAt'])
    if (set(snapshot['source']) != {'system','databaseBackend','priceField','recalculated'}
            or snapshot['source']['system'] != 'Panse ERP'
            or snapshot['source']['databaseBackend'] not in ('postgresql','sqlite')
            or snapshot['source']['priceField'] != 'PricingSkuPromo.mid_buyer_price'
            or snapshot['source']['recalculated'] is not False):
        raise ValueError('source_contract_mismatch')
    expected_scope = dict(coverage='selected-product-sku-only',
        requestedProductCodes=sorted({p for p,s in pairs}), requestedSkuCodes=sorted(s for p,s in pairs),
        productSkuMappings=[dict(productCode=p,skuCode=s) for p,s in pairs],
        scopeSha256=hashlib.sha256(json.dumps(pairs,separators=(',',':')).encode()).hexdigest(),
        included=['product identity','public materials','public dimensions','persisted mid buyer price',
                  'public image candidates','listing status'],
        excluded=['orders','customers','costs','suppliers','factory records','private reviews'],
        dimensionAssets='not_read_no_exact_asset_to_requested_sku_contract',
        imageEvidence='candidate_only_not_license_or_exact_sku_proof')
    if snapshot['scope'] != expected_scope:
        raise ValueError('scope_metadata_mismatch')
    product_keys = {'code','name','subName','brand','category','imageUrl','mainMaterial','auxMaterial',
                    'sizeDetail','sizeValue','sizeConfirmed','listingStatus','updatedAt'}
    price_keys = {'productCode','skuCode','sku','sizeInfo','imageUrl','updatedAt','midBuyerPrice',
                  'currency','priceBasis','sourceField','sourceUpdatedAt','observedAt'}
    for rows, keys in ((snapshot['products'],product_keys),(snapshot['pricing'],price_keys)):
        for row in rows:
            if set(row) != keys or any(isinstance(v,(dict,list)) for v in row.values()):
                raise ValueError('non_public_or_nested_projection_field')
            stamp(row['updatedAt'])
    if (len(snapshot['products']) != len({p for p,s in pairs})
            or {r['code'] for r in snapshot['products']} != {p for p,s in pairs}
            or len(snapshot['pricing']) != len(pairs)
            or {(r['productCode'],r['skuCode']) for r in snapshot['pricing']} != set(pairs)
            or snapshot['dimensions'] != []):
        raise ValueError('snapshot_outside_exact_scope')
    for row in snapshot['pricing']:
        stamp(row['sourceUpdatedAt'])
        if (row['sourceField'] != 'PricingSkuPromo.mid_buyer_price' or row['currency'] != 'CNY'
                or row['priceBasis'] != '中促到手价' or row['observedAt'] != snapshot['generatedAt']
                or row['midBuyerPrice'] != amount(row['midBuyerPrice'])):
            raise ValueError('price_semantics_changed')
    expected = dict(products=len(snapshot['products']),pricing=len(pairs),dimensions=0)
    if snapshot['counts'] != expected or snapshot['scope']['coverage'] != 'selected-product-sku-only':
        raise ValueError('partial_scope_counts_mismatch')
    return True


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--scope-json', type=Path)
    source.add_argument('--scope-inline', help='Same scope JSON; for the existing controlled SSH bridge')
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument('--out', type=Path)
    target.add_argument('--stdout', action='store_true')
    args = parser.parse_args(argv)
    try:
        scope = json.loads(args.scope_inline if args.scope_inline is not None else args.scope_json.read_text(encoding='utf-8-sig'))
        scope_pairs(scope)
        if args.out and args.out.exists():
            raise ValueError('output_exists_no_overwrite')
        # Lazy import: no config or engine access during fixture tests or help.
        sys.path.insert(0, str(Path.cwd()) if str(globals().get('__file__','')).startswith('<')
                        else str(Path(__file__).resolve().parents[1]))
        from app.database import engine
        with engine.connect() as connection:
            try:
                snapshot = build_snapshot(connection, scope)
                validate_snapshot(snapshot, scope)
            finally:
                connection.rollback()
        payload = json.dumps(snapshot, ensure_ascii=False, indent=2)
        if args.stdout:
            print(payload)
        else:
            args.out.parent.mkdir(parents=True, exist_ok=True)
            with args.out.open('x', encoding='utf-8') as stream:
                stream.write(payload)
        return 0
    except Exception as exc:
        # Never print SQL, credentials, connection strings or private rows.
        print('bounded_public_snapshot_failed:'+type(exc).__name__, file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
