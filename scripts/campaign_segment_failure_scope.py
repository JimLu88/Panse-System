"""Separate expired price-segment failures from current signup failures.

Successful and unknown attempts are never relaxed here. This only classifies
the latest proven failed attempt for a new, disjoint daily price segment.
The caller still applies the persistent success/unknown protection first.
"""
from datetime import datetime
import json


def current_window(payload):
    segment = (payload.get('time_binding') or {}).get('segment') or {}
    if segment.get('kind') != 'daily':
        return None
    identity = payload['identity']
    campaign = '/'.join(str(identity[k]) for k in ('campaign_id', 'phase_id', 'sign_record_id'))
    if segment.get('campaign') != campaign:
        raise ValueError('failure_segment_campaign_mismatch')
    window = segment['price_window']
    start, end = (datetime.strptime(window[k], '%Y-%m-%d %H:%M:%S') for k in ('start', 'end'))
    if start >= end:
        raise ValueError('failure_segment_window_invalid')
    return campaign, start, end


def partition_failures(authority, payload, protected):
    identity = payload['identity']
    campaign = '/'.join(str(identity[k]) for k in ('campaign_id', 'phase_id', 'sign_record_id'))
    window = current_window(payload)
    latest = {}
    for row in authority.db.execute(
            'SELECT item,status,bundle_id,id,evidence FROM attempts '
            'WHERE campaign=? AND phase=? ORDER BY rowid DESC', (campaign, 'signup')):
        latest.setdefault(row['item'], dict(row))
    current, historical = {}, {}
    for item, row in latest.items():
        if item in protected or row['status'] != 'failed':
            continue
        # Unknown legacy metadata remains held; it is never fresh work.
        body = authority.get_bundle(row['bundle_id'])
        if body.get('campaign') != campaign:
            raise ValueError('failure_bundle_campaign_mismatch')
        if window is not None and row.get('evidence'):
            old_start, old_end = (datetime.strptime(body[k], '%Y-%m-%d %H:%M:%S') for k in ('start', 'end'))
            # Only a finished older segment qualifies. Future or overlapping
            # constraints remain current until independently resolved.
            if old_start < old_end < window[1]:
                evidence = json.loads(row['evidence'])
                if evidence.get('path') and evidence.get('sha256') and evidence.get('batch'):
                    from campaign_entry_authority import file_sha, load
                    if file_sha(evidence['path']) != evidence['sha256']:
                        raise ValueError('historical_failure_terminal_changed')
                    terminal = load(evidence['path'])
                    outcomes = [r for r in terminal.get('items', []) if r.get('item') == item]
                    if (terminal.get('phase') != 'signup' or terminal.get('terminal') is not True
                            or terminal.get('campaign') != campaign
                            or str(terminal.get('batch_id')) != str(evidence['batch'])
                            or terminal.get('claim_id') != row['id'].split(':')[0]
                            or len(outcomes) != 1 or outcomes[0].get('status') != 'failed'):
                        raise ValueError('historical_failure_terminal_not_bound')
                    historical[item] = dict(attempt_id=row['id'], bundle_id=row['bundle_id'],
                        prior_window={k:body[k] for k in ('start', 'end')}, evidence=evidence,
                        reason='prior_disjoint_daily_segment_not_current_verdict')
                    continue
        current[item] = 'failed'
    return current, historical


def plan_checkpoint(authority, store, root, run, state):
    """Read-only plan for one idle run; retain all original receipts/actions."""
    from campaign_entry_authority import load, file_sha
    from campaign_continuous_policy import fingerprint
    if state.get('status') != 'complete' or state.get('pending'):
        raise ValueError('segment_recovery_requires_completed_round')
    actions = list(store.db.execute(
        "SELECT id,payload_sha FROM continuous_campaign_actions WHERE run_id=? AND step='scope' AND status='done'",
        (run,)))
    if len(actions) != 1:
        raise ValueError('segment_recovery_scope_not_unique')
    action, expected_sha = actions[0]
    paths = list(root.glob('segments/*/actions/' + action + '/request.json'))
    if len(paths) != 1:
        raise ValueError('segment_recovery_scope_file_not_unique')
    saved = load(paths[0]); payload = saved['payload']
    if saved.get('step') != 'scope' or fingerprint(payload) != expected_sha:
        raise ValueError('segment_recovery_original_scope_changed')
    window = current_window(payload)
    if window is None:
        return {}
    identity = payload['identity']
    protected = authority.blocked(window[0], 'signup', identity['start'], identity['end'])
    _, historical = partition_failures(authority, payload, protected)
    allowed = (set(state.get('exceptions', {})) & set(state.get('initial_scope', [])))
    allowed -= set(state.get('success', {})) | set(state.get('previously_enrolled', [])) | set(protected)
    selected = {item: proof for item, proof in historical.items() if item in allowed}
    return dict(pending=sorted(selected), historical=selected,
                scope_source=dict(path=str(paths[0]), sha256=file_sha(paths[0])),
                price_window=payload['time_binding']['segment']['price_window'], platform_write=False)


def apply_checkpoint_plan(state, plan):
    """Apply a freshly revalidated plan to an in-memory state, never a claim."""
    pending = set(plan.get('pending', []))
    if (pending - set(state.get('exceptions', {})) or pending & set(state.get('success', {}))
            or pending & set(state.get('previously_enrolled', []))):
        raise ValueError('segment_recovery_scope_changed')
    for item in pending:
        state['exceptions'].pop(item)
        state.get('corrections', {}).pop(item, None)
        state.get('legacy_failures', {}).pop(item, None)
    state['pending'] = sorted(set(state.get('pending') or []) | pending)
    if pending:
        state['round'] += 1
    return state
