"""Exact user-approved custom identity overlay; never global SKU aliases."""
import hashlib
import json

KEY = 'campaign_custom_correspondence_724042164333_20260927'
PRODUCT = 'PPS23250050202'
ITEM = '724042164333'
SOURCE_SHA = 'bd323236c68538710cb609e6c6a3f24d2466183652ad2da66d717a9ed06c64e0'
ROWS = [
    dict(item=ITEM, sku='5215853299378', raw_code=PRODUCT+'96', code=PRODUCT+'98', attributes='颜色分类:樱桃木其他尺寸定制咨询;'),
    dict(item=ITEM, sku='5215853299403', raw_code=PRODUCT+'99', code=PRODUCT+'95', attributes='颜色分类:黑胡桃木材质定制咨询;'),
    dict(item=ITEM, sku='5069394321177', raw_code=PRODUCT+'98', code=PRODUCT+'97', attributes='颜色分类:白色岩板定制咨询;'),
]
APPROVED = dict(schema='campaign_exact_custom_correspondence_v1',
    authorization='user_20260927_exact_three_custom_links_via_03',
    source_export_sha256=SOURCE_SHA, product_code=PRODUCT, rows=ROWS,
    scope='campaign_file_preparation_only_preserve_primary_and_alt_bindings',
    price_change=False, fixed_original_rebase=False, platform_write=False)


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,default=str).encode()).hexdigest()


def approved_rows(document):
    if document is None:
        return {}
    if document != APPROVED:
        raise ValueError('custom_correspondence_not_exact_authorized_record')
    return {(r['item'],r['sku']):dict(r) for r in document['rows']}


def validate_target(row, target):
    items={str(row.get('item')),str(row.get('product_item_id')),*map(str,row.get('product_alt_item_ids') or [])}
    if (row.get('product_code')!=PRODUCT or row.get('code')!=target['code']
            or row.get('custom') is not True or target['item'] not in items):
        raise ValueError('custom_correspondence_target_drift')
