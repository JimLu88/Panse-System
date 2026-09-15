"""An explicit current-user scope, not a historical-sales prefilter.

Default recurring runs still attempt ERP-sellable intersect platform-on-sale.
This one authorization narrows submission only; original exports stay complete.
"""
from copy import deepcopy

AUTHORIZATION='user-20260915-furniture-cabinet-lift'
ITEMS={'1001358847694','793052650673','793202812082'}
WINDOWS={('legacy/itemApply/3172207691','2026-09-28 00:00:00','2026-09-30 23:59:59'),
         ('49557/49560/3538210379','2026-09-16 20:00:00','2026-09-27 23:59:59')}


def validate(value,plan=None):
    if value is None:return None
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
