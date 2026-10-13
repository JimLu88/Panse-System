"""Final reporting boundary: historical receipts never substitute SKU/price audit.

Pure local projection of the byte-pinned official export and frozen ERP snapshot.
It cannot submit, amend prices, clear exceptions, or authorize a retry.
"""
from collections import defaultdict
from decimal import Decimal, InvalidOperation
from pathlib import Path

from campaign_entry_authority import file_sha, load

POLICY = 'official-export-price-closeout-v1'


def assess(result, root, authority):
    from campaign_final_audit import parse_inventory, ACCEPTED
    from campaign_failure_remediation import mapped_erp_rows
    audit = result.get('final_audit') or {}
    expected_segments = {s.get('segment_id') for s in result.get('segments', [])}
    actual_segments = {s.get('segment_id') for s in audit.get('segments', [])}
    gaps = []
    rows = []
    if not audit.get('verified') or not expected_segments or expected_segments != actual_segments:
        gaps.append('official_full_export_not_verified')
    for segment in audit.get('segments', []):
        sid = segment.get('segment_id')
        try:
            source = segment['source_file']
            if file_sha(source['path']) != source['sha256']:
                raise ValueError('official_export_changed')
            official = defaultdict(list)
            for row in parse_inventory(Path(source['path']).read_bytes()):
                if row['state'] not in ('撤销报名', '已撤销', '已取消'):
                    official[(row['item'], row['sku'])].append(row)
            snapshot = authority.resolve_snapshot(load(Path(root)/'segments'/sid/'resolved-snapshot.json'))
            erp = defaultdict(dict)
            for entry in mapped_erp_rows(snapshot):
                for sku in [entry.get('sku')] + list(entry.get('alt') or []):
                    if sku:
                        erp[(str(entry['item']), str(sku))][entry['code']] = entry
            for expected in segment.get('rows', []):
                pair = (expected['item'], expected['sku'])
                matching = official[pair]
                e = list(erp[pair].values())
                record = dict(item=pair[0], sku=pair[1], segment_id=sid,
                    product_name=e[0].get('product_name', '') if len(e)==1 else '',
                    registration_verified=expected.get('registered') is True,
                    signup_price_status='unknown', discount_and_target_status='not_verified',
                    price_window=segment.get('price_window'), source_sha256=source['sha256'],
                    complete=False)
                if len(matching)==1 and len(e)==1:
                    record.update(activity_price=matching[0].get('activity_price'),
                                  erp_daily=e[0].get('daily'), custom=e[0].get('custom'))
                    if e[0].get('custom') is True:
                        record['signup_price_status']='fixed_original_floor_requires_verification'
                    else:
                        try:
                            record['signup_price_status']=('matches_erp_daily' if
                                Decimal(matching[0]['activity_price'])==Decimal(str(e[0]['daily']))
                                else 'differs_from_erp_daily')
                        except (InvalidOperation, ValueError, TypeError, KeyError):
                            pass
                # The activity export has no actual discount amount/window or
                # checkout composition. Do not treat a calculated offer as saved.
                rows.append(record)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            gaps.append('segment_price_evidence_unavailable:'+str(sid)+':'+type(exc).__name__)
    return dict(policy=POLICY, complete=bool(rows) and not gaps and all(r['complete'] for r in rows),
                rows=rows, gaps=gaps, platform_write=False,
                note='Registration, signup price, actual discount/window and target price are independent checks; missing checks cannot mean complete.')


def apply(result, assessment):
    """Never change controller state, historical success or replay protection."""
    valid = assessment.get('policy') == POLICY and assessment.get('complete') is True
    return dict(result, completion_audit=assessment,
                all_registration_verified=result.get('all_signed_up') is True,
                all_completed=valid, completion_status='verified_complete' if valid else 'not_complete')
