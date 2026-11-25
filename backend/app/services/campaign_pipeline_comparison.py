"""Read-only replay of two auditors; never authorizes an upload or marks a plan done."""
from collections import Counter
from hashlib import sha256
from io import BytesIO
from types import SimpleNamespace
from zipfile import ZipFile, BadZipFile

from app.services import campaign_recon_service as old
from app.services.campaign_pipeline_audit_vendor import audit_enrolled_against_erp


def compare_export(data: bytes, price_map: dict, pair_map: dict) -> dict:
    if not data or len(data) > 10 * 1024 * 1024:
        raise ValueError('请选择不超过 10 MB 的官方 XLSX 导出文件')
    try:
        with ZipFile(BytesIO(data)) as z:
            if sum(x.file_size for x in z.infolist()) > 50 * 1024 * 1024:
                raise ValueError('表格解压后过大')
    except BadZipFile as exc:
        raise ValueError('文件不是有效 XLSX') from exc
    records = old.parse_activity_floor_evidence_export(data)
    if not records or len(records) > 20000:
        raise ValueError('未识别到有效商品 SKU，或超过 20000 行')
    resolved = old.resolve_current_activity_records(records)
    # Compare only exact item+SKU mappings, even though the legacy API uses SKU keys.
    rows = [old.compare_records([r], {r['sku_id']: pair_map[(r['item_id'], r['sku_id'])]}
            if (r['item_id'], r['sku_id']) in pair_map else {})[0]
            for r in resolved['rows']]
    new_map = {key: {'custom': value.get('is_placeholder'), 'target': value.get('target')}
               for key, value in pair_map.items()}
    new_error = None
    try:
        new = audit_enrolled_against_erp(BytesIO(data), new_map,
                    SimpleNamespace(tolerance_yuan='2.00', erp_target_mode='target'))
    except (KeyError, ValueError, TypeError, IndexError, ArithmeticError) as exc:
        new = None
        new_error = f'新核对器不支持这份表格格式（{type(exc).__name__}）'
    reasons = Counter()
    for r in records:
        entry = pair_map.get((r['item_id'], r['sku_id']))
        if r['status'] != '已发布设定':
            reasons['非已发布设定状态'] += 1
        elif entry is None:
            reasons['ERP 无精确映射'] += 1
        elif entry.get('is_placeholder'):
            reasons['定制占位 SKU'] += 1
        elif not entry.get('target'):
            reasons['缺 ERP 目标价'] += 1
        elif r.get('coupon_after') is None:
            reasons['缺官方券后价'] += 1
    seen = {r['sku_id'] for r in records}
    return {'mode': 'read_only_comparison', 'file_sha256': sha256(data).hexdigest(),
            'price_source': 'current_erp', 'platform_writes_enabled': False,
            'business_complete': False,
            'scope_note': '仅比较所选导出文件；未验证活动身份、全量范围及当前平台状态，不能作为报名完成凭证。',
            'export_rows': len(records), 'legacy': {'rows': rows,
                'verdicts': dict(Counter(r['verdict'] for r in rows)),
                'ambiguities': resolved.get('ambiguities', [])},
            'pipeline': new, 'pipeline_error': new_error,
            'pipeline_skip_reasons': dict(reasons),
            'erp_skus_not_in_file': len(set(price_map) - seen)}


def current_maps(db, plan):
    from app.services import campaign_service as svc
    prices = svc.target_prices(db, plan)
    pairs = {}
    for sku, product in svc._mapped_pairs(db):
        for sid in svc._expand_sku_ids(product):
            if sid not in prices:
                continue
            key = (str(product.taobao_item_id).strip(), sid)
            if key in pairs or prices[sid].get('sku_code') != sku.sku_code:
                raise ValueError('ERP 存在重复商品 SKU 映射，请先修复映射')
            pairs[key] = prices[sid]
    return prices, pairs
