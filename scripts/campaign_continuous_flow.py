"""Durable short-flow controller; all browser operations belong to one Web Agent.

The transport is an explicit program adapter, not an AI callback or a fallback
to legacy campaign_service.preflight. Integration must supply real receipts.
No real adapter is registered by this module: simulations are not readiness.
"""
from copy import deepcopy
import json
import sqlite3
import uuid

from campaign_continuous_policy import (
    activity_identity, classify_items, fingerprint, load_rules, signup_scope,
)

WRITE_STEPS = {'discount', 'signup', 'repair'}
REQUIRED_CAPABILITIES = {
    'retained_edge', 'no_ai_browser_steps', 'official_templates',
    'complete_scope', 'validated_generation', 'durable_write_receipts',
    'discount_window_readback', 'failure_reports', 'scoped_price_repair',
}


class Blocked(RuntimeError):
    def __init__(self, step, reason):
        self.step, self.reason = step, reason
        super().__init__(f'{step}: {reason}')


class Store:
    """Separate tables: never alter/reinterpret old claim or source tables."""
    def __init__(self, path):
        self.db = sqlite3.connect(str(path), timeout=10, isolation_level=None)
        self.db.executescript('''
          CREATE TABLE IF NOT EXISTS continuous_campaign_runs(
            id TEXT PRIMARY KEY, identity TEXT NOT NULL, rule_sha TEXT NOT NULL,
            body TEXT NOT NULL, owner TEXT);
          CREATE TABLE IF NOT EXISTS continuous_campaign_actions(
            id TEXT PRIMARY KEY, run_id TEXT NOT NULL, step TEXT NOT NULL,
            payload_sha TEXT NOT NULL, status TEXT NOT NULL, result TEXT,
            UNIQUE(run_id,step,payload_sha));
          CREATE TABLE IF NOT EXISTS continuous_campaign_shop_owners(
            shop TEXT PRIMARY KEY, run_id TEXT NOT NULL, owner TEXT NOT NULL);
        ''')

    def close(self):
        self.db.close()

    def start(self, identity, rule_sha):
        run_id = fingerprint({'identity': identity, 'rule_sha': rule_sha})
        self.db.execute('INSERT OR IGNORE INTO continuous_campaign_runs VALUES(?,?,?,?,NULL)',
                        (run_id, identity, rule_sha, json.dumps({
                            'pending': None, 'success': {}, 'exceptions': {},
                            'repairs_seen': {}, 'corrections': {}, 'round': 0, 'status': 'ready'})))
        return run_id

    def lock(self, run_id, shop='default'):
        owner = uuid.uuid4().hex
        self.db.execute('BEGIN IMMEDIATE')
        try:
            self.db.execute('INSERT INTO continuous_campaign_shop_owners VALUES(?,?,?)',
                            (shop, run_id, owner))
            changed = self.db.execute(
                'UPDATE continuous_campaign_runs SET owner=? WHERE id=? AND owner IS NULL',
                (owner, run_id)).rowcount
            if changed != 1:
                raise Blocked('ownership', 'another_owner_or_crash_checkpoint_needs_reconciliation')
            self.db.execute('COMMIT')
        except (sqlite3.IntegrityError, Blocked) as exc:
            self.db.execute('ROLLBACK')
            raise Blocked('ownership', 'another_owner_or_crash_checkpoint_needs_reconciliation') from exc
        return owner

    def unlock(self, run_id, owner):
        self.db.execute('UPDATE continuous_campaign_runs SET owner=NULL WHERE id=? AND owner=?',
                        (run_id, owner))
        self.db.execute('DELETE FROM continuous_campaign_shop_owners WHERE run_id=? AND owner=?',
                        (run_id, owner))  # Transient lease only, never a business record.

    def load(self, run_id):
        return json.loads(self.db.execute(
            'SELECT body FROM continuous_campaign_runs WHERE id=?', (run_id,)).fetchone()[0])

    def save(self, run_id, state):
        self.db.execute('UPDATE continuous_campaign_runs SET body=? WHERE id=?',
                        (json.dumps(state, ensure_ascii=False), run_id))

    def once(self, run_id, step, payload, transport):
        sha = fingerprint(payload)
        action_id = fingerprint([run_id, step, sha])
        cached = self.db.execute('SELECT status,result FROM continuous_campaign_actions WHERE id=?',
                                 (action_id,)).fetchone()
        if cached:
            if cached[0] != 'done':
                raise Blocked(step, f'previous_{cached[0]}_do_not_replay:{action_id}')
            return json.loads(cached[1])
        # Commit unknown BEFORE calling a transport that might write to Taobao.
        status = 'unknown' if step in WRITE_STEPS else 'interrupted_read'
        self.db.execute('INSERT INTO continuous_campaign_actions VALUES(?,?,?,?,?,NULL)',
                        (action_id, run_id, step, sha, status))
        try:
            result = transport.execute(step, action_id, deepcopy(payload))
        except Exception as exc:
            # No blind retry, including an exception after a successful save.
            raise Blocked(step, type(exc).__name__) from exc
        if not isinstance(result, dict) or result.get('human_gate'):
            raise Blocked(step, str((result or {}).get('human_gate') if isinstance(result, dict)
                                    else 'malformed_transport_result'))
        if result.get('status') != 'terminal' or not result.get('evidence'):
            raise Blocked(step, 'terminal_evidence_missing_do_not_replay')
        if result.get('action_id') != action_id:
            raise Blocked(step, 'receipt_action_mismatch')
        if step in WRITE_STEPS and result.get('request_sha') != sha:
            raise Blocked(step, 'receipt_payload_mismatch')
        self.db.execute('UPDATE continuous_campaign_actions SET status=?,result=? WHERE id=?',
                        ('done', json.dumps(result, ensure_ascii=False), action_id))
        return result


def _terminal_scope(result, requested):
    rows = result.get('items') or []
    if not result.get('batch'):
        raise Blocked('terminal', 'official_batch_missing')
    ids = [str(r.get('item') or '') for r in rows]
    if len(ids) != len(set(ids)) or set(ids) != set(requested):
        raise Blocked('terminal', 'official_terminal_item_scope_mismatch')
    if any(r.get('outcome') not in ('success', 'failed') for r in rows):
        raise Blocked('terminal', 'unknown_item_outcome_do_not_replay')
    return rows


def _validate_bundle(bundle, pending, rule_sha, price_version):
    if (bundle.get('validated_rule_sha') != rule_sha
            or bundle.get('price_version') != price_version
            or sorted(bundle.get('items') or []) != pending
            or not bundle.get('file_sha') or bundle.get('full_active_skus') is not True):
        raise Blocked('generate', 'generation_contract_incomplete')


def run(store, transport, page, *, expected_shop, observed_links):
    """Run continuously; return once finished or a real unrecoverable gate occurs.

    `execute` uses Web-Agent actions only. A stage acknowledgement or HTTP 200
    must never be normalized as `status=terminal` by its concrete adapter.
    """
    rules = load_rules()
    rule_sha = fingerprint(rules)
    identity = activity_identity(page, expected_shop=expected_shop, observed_links=observed_links)
    if not REQUIRED_CAPABILITIES.issubset(set(transport.capabilities())):
        return {'status': 'blocked', 'step': 'transport',
                'reason': 'verified_continuous_web_agent_transport_not_available',
                'platform_write': False}
    run_id = store.start(identity, rule_sha)
    owner = store.lock(run_id, expected_shop)
    state = store.load(run_id)
    base = {'identity': deepcopy(page), 'rule_sha': rule_sha}

    def call(step, values):
        return store.once(run_id, step, dict(base, **values), transport)

    def hold(items, reason):
        for item in items:
            state['exceptions'][item] = [{'action': 'manual', 'reason': reason}]

    try:
        if state['status'] == 'complete':
            return dict(state, run_id=run_id, all_signed_up=not state['exceptions'])
        if state['pending'] is None:
            scope = call('scope', {})
            initial_scope = signup_scope(scope['erp_sellable'], scope['platform_rows'],
                complete=scope.get('complete'), observed_item_count=scope.get('observed_item_count'))
            # Adapter must reconcile old authorities, not assume a new run is unwritten.
            prior = scope.get('prior_outcomes')
            if not isinstance(prior, dict) or not scope.get('prior_outcomes_evidence'):
                raise Blocked('scope', 'historical_authority_reconciliation_missing')
            if any(v not in ('success', 'failed', 'unknown') for v in prior.values()):
                raise Blocked('scope', 'historical_outcome_invalid')
            if not scope.get('price_version'):
                raise Blocked('scope', 'erp_price_version_missing')
            # Do not checkpoint a half-initialized scope that could skip the
            # history check on restart after a malformed adapter response.
            state['pending'] = initial_scope
            state['price_version'] = scope['price_version']
            state['initial_scope'] = list(initial_scope)
            for item in list(state['pending']):
                if prior.get(item) == 'success':
                    state['success'][item] = scope['prior_outcomes_evidence']
                    state['pending'].remove(item)
                elif prior.get(item) == 'unknown':
                    hold([item], 'previous_unknown_do_not_replay')
                    state['pending'].remove(item)
                elif prior.get(item) == 'failed':
                    # Legacy failed files are not automatically replayed. A separately
                    # normalized failure import must precede migration into this run.
                    hold([item], 'legacy_failure_needs_exact_report_import')
                    state['pending'].remove(item)
            store.save(run_id, state)
        while state['pending']:
            pending, round_no = sorted(state['pending']), state['round']
            # Current official template once per campaign; not again each repair round.
            template = call('template', {'items': state['initial_scope']})
            bundle = call('generate', {'items': pending, 'round': round_no,
                'template': template, 'price_version': state['price_version'],
                'corrections': state['corrections']})
            _validate_bundle(bundle, pending, rule_sha, state['price_version'])
            discount_items = bundle.get('discount_items')
            if (not isinstance(discount_items, list) or len(discount_items) != len(set(discount_items))
                    or not set(discount_items).issubset(pending)):
                raise Blocked('generate', 'discount_scope_invalid')
            if discount_items:
                discount = call('discount', {'bundle': bundle, 'items': discount_items})
                rows = _terminal_scope(discount, discount_items)
                failed = [r['item'] for r in rows if r['outcome'] == 'failed']
                hold(failed, 'single_discount_failed')
                pending = [i for i in pending if i not in failed]
                if failed and pending:
                    # Never send a full file with a smaller claimed scope.
                    bundle = call('generate', {'items': pending, 'round': round_no,
                        'template': template, 'price_version': state['price_version'],
                        'corrections': state['corrections'], 'phase': 'after_discount_partial'})
                    _validate_bundle(bundle, pending, rule_sha, state['price_version'])
            if not pending:
                state['pending'] = []
                break
            window = call('verify_discount_window', {'bundle': bundle, 'items': pending})
            if (window.get('start') != page['start'] or window.get('end') != page['end']
                    or window.get('all_correct') is not True
                    or sorted(window.get('items') or []) != pending):
                raise Blocked('verify_discount_window', 'actual_discount_time_or_scope_mismatch')
            signup = call('signup', {'bundle': bundle, 'items': pending})
            rows = _terminal_scope(signup, pending)
            failed = [r['item'] for r in rows if r['outcome'] == 'failed']
            for row in rows:
                if row['outcome'] == 'success':
                    state['success'][row['item']] = signup['evidence']
            if not failed:
                state['pending'] = []
                break
            report = call('report', {'batch': signup['batch'], 'items': failed})
            errors = report.get('errors') or []
            if (set(str(e.get('item')) for e in errors) != set(failed)
                    or any(e.get('batch') != signup['batch'] for e in errors)):
                raise Blocked('report', 'failure_report_scope_or_batch_mismatch')
            repairs, exceptions = classify_items(errors)
            state['exceptions'].update(exceptions)
            for item in list(repairs):
                # Batch/time/message noise is excluded from the no-progress identity.
                signature = fingerprint([d['repair'] for d in repairs[item]])
                if signature in state['repairs_seen'].get(item, []):
                    hold([item], 'same_correction_already_attempted_without_success')
                    del repairs[item]
            if not repairs:
                state['pending'] = []
                break
            repaired = call('repair', {'decisions': repairs, 'items': sorted(repairs),
                                       'failed_batch': signup['batch']})
            fixed_rows = _terminal_scope(repaired, sorted(repairs))
            next_items = []
            for row in fixed_rows:
                item = row['item']
                if row['outcome'] == 'success' and row.get('changed') is True:
                    next_items.append(item)
                    state['corrections'][item] = repairs[item]
                    state['repairs_seen'].setdefault(item, []).append(
                        fingerprint([d['repair'] for d in repairs[item]]))
                else:
                    hold([item], 'repair_failed_or_no_verified_change')
            state['pending'] = next_items
            state['round'] += 1
            store.save(run_id, state)
        state['status'] = 'complete'
        state.pop('blocker', None)
        store.save(run_id, state)
        # Complete means processed: exception count may be >0, never all-success by implication.
        return dict(state, run_id=run_id, all_signed_up=not state['exceptions'])
    except Blocked as exc:
        state['status'] = 'blocked'
        state['blocker'] = {'step': exc.step, 'reason': exc.reason}
        store.save(run_id, state)
        return dict(state, run_id=run_id, all_signed_up=False)
    except (KeyError, ValueError, TypeError) as exc:
        state['status'] = 'blocked'
        state['blocker'] = {'step': 'transport_contract', 'reason': type(exc).__name__}
        store.save(run_id, state)
        return dict(state, run_id=run_id, all_signed_up=False)
    finally:
        store.unlock(run_id, owner)
