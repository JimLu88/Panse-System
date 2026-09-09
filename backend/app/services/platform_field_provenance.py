"""Presence, consensus, observed source version and compare-and-set ownership.

No metadata is backfilled by migration. Unknown/old/manual values are not a
clear instruction. Hashes carry no customer text. Imported snapshots must come
from a trusted capture boundary; upload/parse time alone is not capture time.
"""
from datetime import datetime, timezone, timedelta
import hashlib

ALIASES = {
    'buyer_message': ('买家留言', '主订单买家留言', '买家留言备注', '买家备注'),
    'seller_memo': ('卖家备注', '商家备注', '卖家留言', '常用备注'),
    'tracking_no': ('物流单号', '运单号'),
}

def normalized(value):
    return str(value).strip() if value is not None else ''

def digest(value):
    return hashlib.sha256(normalized(value).encode()).hexdigest()

def observe(parsed, row, headers, raw):
    """Keep every row/alias observation: partial or conflicting input cannot clear."""
    parsed.source_sha256 = hashlib.sha256(raw).hexdigest()
    for field, names in ALIASES.items():
        selected = [name for name in names if name in headers]
        if not selected:
            continue
        entry = parsed.source_fields.setdefault(field, {'values': [], 'complete': True})
        for name in selected:
            if name not in row:
                entry['complete'] = False
            elif normalized(row[name]) not in entry['values']:
                entry['values'].append(normalized(row[name]))

def _aware(value):
    try:
        result = datetime.fromisoformat(value) if isinstance(value, str) else value
        return result.astimezone(timezone.utc) if result and result.tzinfo else None
    except (TypeError, ValueError, AttributeError):
        return None

def apply(order, parsed, warnings, *, new=False):
    """Ordinary import path; no historical sweeps, no separately invoked cleanup."""
    state = dict(order.platform_field_state or {})
    at = _aware(parsed.source_observed_at)
    now = datetime.now(timezone.utc)
    trusted_clock = bool(at and at <= now + timedelta(minutes=5)
                         and parsed.source_sha256 and parsed.source_kind == 'agent_download')
    fresh_clear = bool(trusted_clock and at >= now - timedelta(hours=48))

    def legacy_nonempty(field, value, current, prior):
        # Preserve pre-existing nonempty-import behavior, but an unversioned
        # change cannot inherit the previous snapshot's permission to clear.
        if (value and (field != 'tracking_no' or not current)
                and (not prior or digest(current) == prior.get('value_sha256'))):
            setattr(order, field, value)
            if prior and value != current:
                state[field] = {**prior, 'source_kind': 'unverified_nonempty',
                                'value_sha256': digest(value), 'clear_intent': False,
                                'unverified_after': now.isoformat()}
    for field in ALIASES:
        if field in parsed.protected_fields:
            warnings.append(f'{field}: explicit_human_owner_preserved')
            continue
        current = normalized(getattr(order, field, None))
        prior = state.get(field) or {}
        obs = parsed.source_fields.get(field)
        if not obs:
            # Compatibility fill/update only for unowned fields. Absence never clears.
            value = normalized(getattr(parsed, field, None))
            legacy_nonempty(field, value, current, prior)
            continue
        if not obs['complete'] or len(obs['values']) != 1:
            warnings.append(f'{field}: conflicting_or_incomplete_source_preserved')
            continue
        value = obs['values'][0]
        if not trusted_clock:
            legacy_nonempty(field, value, current, prior)
            if current != value:
                warnings.append(f'{field}: source_time_or_authority_missing_preserved')
            continue
        if prior:
            old_at = _aware(prior.get('observed_at'))
            unverified_after = _aware(prior.get('unverified_after'))
            if unverified_after and at <= unverified_after:
                continue
            if not old_at or at <= old_at or parsed.source_sha256 == prior.get('source_sha256'):
                continue
            if digest(current) != prior.get('value_sha256'):
                warnings.append(f'{field}: local_manual_change_preserved')
                continue
        elif current and (not value or (field == 'tracking_no' and current != value)):
            warnings.append(f'{field}: unowned_existing_value_preserved')
            continue
        clear = bool(current and not value)
        if clear and (not fresh_clear or prior.get('source_kind') != 'agent_download'):
            warnings.append(f'{field}: clear_requires_fresh_owned_snapshot')
            continue
        if clear and not parsed.source_scope_complete:
            warnings.append(f'{field}: incomplete_order_scope_preserved')
            continue
        if clear and field == 'tracking_no':
            # Require a trusted status reversal as well as a present blank column.
            from app.services.taobao_order_import import _resolve_status
            status, recognized = _resolve_status(parsed.status_text)
            if not (parsed.status_trusted and recognized and status == 'paid'
                    and prior.get('shipment_status') in ('shipped', 'signed')):
                warnings.append('tracking_no: withdrawal_evidence_missing_preserved')
                continue
        previous = digest(current)
        # Preserve pending clear evidence over later empty snapshots until a new
        # nonempty tracking value appears; its CAS preimage remains unchanged.
        keep_clear = not value and prior.get('clear_intent')
        from app.services.taobao_order_import import _resolve_status
        source_status, _ = _resolve_status(parsed.status_text)
        state[field] = {
            'schema': 1, 'source_kind': parsed.source_kind,
            'source_sha256': parsed.source_sha256, 'observed_at': at.isoformat(),
            'value_sha256': digest(value),
            'previous_sha256': prior.get('previous_sha256') if keep_clear else previous,
            'clear_intent': bool(clear or keep_clear),
            'shipment_status': source_status,
            'ship_date_sha256': digest(order.ship_date),
        }
        setattr(order, field, value)
        if clear and field == 'tracking_no' and digest(order.ship_date) == prior.get('ship_date_sha256'):
            order.ship_date = None
    order.platform_field_state = state or None

def tracking_clear(order):
    proof = (order.platform_field_state or {}).get('tracking_no') or {}
    if (proof.get('schema') == 1 and proof.get('clear_intent') and proof.get('source_kind') == 'agent_download'
            and proof.get('value_sha256') == digest('') and not normalized(order.tracking_no)
            and proof.get('previous_sha256') and proof.get('source_sha256') and _aware(proof.get('observed_at'))):
        return proof
    return None
