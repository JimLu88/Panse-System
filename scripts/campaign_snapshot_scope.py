"""Resolve an explicit transaction without importing other products' rotations.

The pinned full snapshot remains immutable. Related row changes still change
its digest and fail the normal price/window gate; unrelated rows stay pinned.
"""
from copy import deepcopy
from campaign_price_snapshot import digest


def resolve_for_items(authority, snapshot, items):
    wanted = set(map(str, items))
    if not wanted or any(not i.isdigit() for i in wanted):
        raise ValueError('snapshot_explicit_item_scope_required')
    resolved = authority.resolve_snapshot(snapshot)
    old = snapshot['all_erp_rows']; new = resolved['all_erp_rows']
    if (len(old) != len(new) or len({r['code'] for r in old}) != len(old)
            or [r['code'] for r in old] != [r['code'] for r in new]):
        raise ValueError('snapshot_scope_row_identity_changed')
    def identities(row):
        return {str(row.get('item')), str(row.get('product_item_id')),
                *map(str, row.get('product_alt_item_ids') or [])}
    rows = []
    for before, after in zip(old, new):
        if identities(before) != identities(after):
            raise ValueError('snapshot_scope_product_identity_changed')
        rows.append(deepcopy(after if wanted & identities(before) else before))
    resolved['all_erp_rows'] = rows
    resolved['resolved_price_version_sha256'] = digest(rows)
    return resolved
