"""An explicit current-user scope, not a historical-sales prefilter.

Default recurring runs still attempt ERP-sellable intersect platform-on-sale.
This one authorization narrows submission only; original exports stay complete.
"""
from copy import deepcopy
import hashlib
import json
import re
from pathlib import Path

AUTHORIZATION='user-20260915-furniture-cabinet-lift'
ITEMS={'1001358847694','793052650673','793202812082'}
WINDOWS={('legacy/itemApply/3172207691','2026-09-28 00:00:00','2026-09-30 23:59:59'),
         ('49557/49560/3538210379','2026-09-16 20:00:00','2026-09-27 23:59:59')}


def validate(value,plan=None):
    if value is None:return None
    if isinstance(value,dict) and value.get('schema')=='campaign-item-scope-v2':
        return validate_v2(value,plan)
    if (not isinstance(value,dict) or set(value)!={'authorization','items'}
            or value['authorization']!=AUTHORIZATION or not isinstance(value['items'],list)
            or not value['items'] or not all(isinstance(i,str) for i in value['items'])
            or len(value['items'])!=len(set(value['items']))
            or not set(value['items']).issubset(ITEMS)):
        raise ValueError('exact_user_authorized_item_scope_required')
    if plan is not None:
        segments=plan.get('segments') or []
        windows={(s['campaign'],s['price_window']['start'],s['price_window']['end']) for s in segments}
        if not windows or not windows.issubset(WINDOWS):
            raise ValueError('authorized_item_campaign_window_changed')
    return deepcopy(value)


def validate_v2(value,plan=None):
    """Current user scope supplied once, not another hard-coded product list.

    The receipt records the user's instruction; it is not permission to change
    prices, withdraw, rotate, or waive a successful/unknown attempt.
    """
    if set(value)!={'schema','authorization','items','windows','shop_id','source'}:
        raise ValueError('exact_current_scope_fields_required')
    items=value['items'];windows=value['windows'];ref=value['source']
    if (not isinstance(items,list) or not items or any(not isinstance(i,str) or not i.isdigit() for i in items)
            or len(items)!=len(set(items)) or not isinstance(value['authorization'],str)
            or not value['authorization'].strip() or not isinstance(value['shop_id'],str) or not value['shop_id'].strip()
            or not isinstance(windows,list) or not windows or not isinstance(ref,dict) or set(ref)!={'path','sha256'}):
        raise ValueError('current_user_scope_incomplete')
    from datetime import datetime
    frozen=set()
    for w in windows:
        if not isinstance(w,dict) or set(w)!={'campaign','start','end'}:
            raise ValueError('scope_window_fields_invalid')
        if not re.fullmatch(r'(?:\d+/\d+/\d+|legacy/itemApply/\d+)',w['campaign']):
            raise ValueError('scope_campaign_invalid')
        start,end=[datetime.strptime(w[k],'%Y-%m-%d %H:%M:%S') for k in ('start','end')]
        if start>=end:raise ValueError('scope_window_invalid')
        frozen.add((w['campaign'],w['start'],w['end']))
    if len(frozen)!=len(windows):raise ValueError('duplicate_scope_window')
    raw=Path(ref['path']).read_bytes()
    if hashlib.sha256(raw).hexdigest()!=ref['sha256']:raise ValueError('user_scope_source_changed')
    source=json.loads(raw.decode('utf-8-sig'))
    expected={k:value[k] for k in ('authorization','items','windows','shop_id')}
    if source!={'schema':'campaign-user-scope-receipt-v1','operation':'signup_only',**expected}:
        raise ValueError('current_user_scope_source_mismatch')
    if plan is not None:
        segments=plan.get('segments') or []
        actual={(s['campaign'],s['price_window']['start'],s['price_window']['end']) for s in segments}
        if actual!=frozen or any(s.get('shop_id')!=value['shop_id'] for s in segments):
            raise ValueError('current_scope_campaign_window_or_shop_changed')
    return deepcopy(value)


def apply(scope,authorization):
    checked=validate(authorization)
    if checked is None:return scope
    available=set(scope['erp_sellable']) & {r['item'] for r in scope['platform_rows'] if r['on_sale'] is True}
    if not set(checked['items']).issubset(available):
        raise ValueError('authorized_item_not_in_sellable_onsale_scope')
    # Keep all evidence rows and prices intact. The persisted scope action is
    # what both submission and the mandatory final audit use as their scope.
    return dict(scope,erp_sellable=sorted(set(checked['items'])),
                authorized_item_scope=checked,full_export_preserved=True)
