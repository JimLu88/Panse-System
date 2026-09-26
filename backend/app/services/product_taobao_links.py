"""Read-only links from current exact ERP mappings, never historical aliases."""
import re
from collections import defaultdict
from sqlalchemy import select
from app.models.product import Product
from app.models.pricing import PricingSku
from app.models.pricing_ext import PricingSkuPromo


def numeric_id(value):
    value = str(value or '').strip()
    return value if re.fullmatch(r'[1-9][0-9]{4,31}', value) else None


def missing_id(value):
    return str(value or '').strip().lower() in {'', '-', '—', '暂无', '待定', '无', 'none', 'null'}


def link(item, sku=None):
    url = 'https://item.taobao.com/item.htm?id=' + item
    return {'url': url + ('&skuId=' + sku if sku else ''),
            'item_id': item, 'sku_id': sku,
            'label': '打开此款' if sku else '打开商品'}


def build_link_maps(db):
    """Batch all mappings to catch conflicts even outside the displayed page."""
    products = db.scalars(select(Product)).all()
    rows = db.execute(select(PricingSku, PricingSkuPromo).outerjoin(
        PricingSkuPromo, PricingSkuPromo.sku_code == PricingSku.sku_code)).all()
    primary = {p.code: numeric_id(p.taobao_id) for p in products}
    product_items = defaultdict(set)
    sku_owners = defaultdict(set)
    for sku, promo in rows:
        item = numeric_id(promo.taobao_item_id) if promo else None
        tid = numeric_id(promo.taobao_sku_id) if promo else None
        if item: product_items[sku.product_code].add(item)
        if tid: sku_owners[tid].add((item, sku.sku_code))
    product_map = {}
    for p in products:
        items = product_items[p.code] or ({primary[p.code]} if primary[p.code] else set())
        product_map[p.code] = {'taobao_links': [link(i) for i in sorted(items)],
                               'taobao_link_status': '商品级链接；展开查看款式' if items else '缺淘宝商品映射'}
    sku_map = {}
    for sku, promo in rows:
        item = numeric_id(promo.taobao_item_id) if promo else None
        tid = numeric_id(promo.taobao_sku_id) if promo else None
        links, status = [], '缺淘宝商品映射'
        if promo and ((not missing_id(promo.taobao_item_id) and not item) or (not missing_id(promo.taobao_sku_id) and not tid)):
            status = '淘宝ID格式异常，待核对'
        elif tid and len(sku_owners[tid]) > 1:
            status = '淘宝SKU映射冲突，待核对'
        elif tid and not item:
            status = '缺此SKU所属商品ID，待核对'
        elif item:
            links, status = [link(item, tid)], '款式已映射' if tid else '仅商品链接，缺SKU映射'
        else:
            candidates = product_items[sku.product_code] or ({primary.get(sku.product_code)} if primary.get(sku.product_code) else set())
            if len(candidates) == 1:
                links, status = [link(next(iter(candidates)))], '仅商品链接，缺SKU映射'
            elif candidates:
                status = '多商品链接，缺此款映射'
        sku_map[sku.sku_code] = {'taobao_links': links, 'taobao_link_status': status}
    return product_map, sku_map
