"""Durable short-flow controller; all browser operations belong to one Web Agent.

The transport is an explicit program adapter, not an AI callback or a fallback
to legacy campaign_service.preflight. Integration must supply real receipts.
No real adapter is registered by this module: simulations are not readiness.
"""
from copy import deepcopy
from decimal import Decimal
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
          CREATE TABLE IF NOT EXISTS continuous_campaign_recoveries(
            action_id TEXT PRIMARY KEY, receipt_sha TEXT NOT NULL, receipt TEXT NOT NULL);
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

    def recover(self, action_id, receipt):
        """Accept an exact read-only reconciliation, never resubmit a write.

        The concrete transport must normalize the original official receipt;
        this method verifies binding and preserves the original action identity.
        Cannot race a live run or overwrite an already terminal receipt.
        """
        self.db.execute('BEGIN IMMEDIATE')
        try:
            row = self.db.execute('SELECT run_id,step,payload_sha,status FROM continuous_campaign_actions WHERE id=?',
                                  (action_id,)).fetchone()
            if row is None:
                raise Blocked('recovery', 'unknown_action')
            owner = self.db.execute('SELECT owner FROM continuous_campaign_runs WHERE id=?', (row[0],)).fetchone()[0]
            if owner is not None:
                raise Blocked('recovery', 'active_owner_cannot_reconcile')
            if (not isinstance(receipt, dict) or receipt.get('action_id') != action_id
                    or receipt.get('request_sha') != row[2] or receipt.get('status') != 'terminal'
                    or not receipt.get('evidence') or receipt.get('reconciled_readonly') is not True):
                raise Blocked('recovery', 'exact_readonly_terminal_receipt_required')
            previous = self.db.execute('SELECT receipt_sha FROM continuous_campaign_recoveries WHERE action_id=?',
                                       (action_id,)).fetchone()
            sha = fingerprint(receipt)
            if previous:
                if previous[0] != sha:
                    raise Blocked('recovery', 'recovery_receipt_is_immutable')
            elif row[3] == 'done':
                raise Blocked('recovery', 'completed_action_is_immutable')
            else:
                self.db.execute('INSERT INTO continuous_campaign_recoveries VALUES(?,?,?)',
                                (action_id, sha, json.dumps(receipt, ensure_ascii=False)))
                self.db.execute('UPDATE continuous_campaign_actions SET status=?,result=? WHERE id=?',
                                ('done', json.dumps(receipt, ensure_ascii=False), action_id))
            self.db.execute('COMMIT')
        except Exception:
            self.db.execute('ROLLBACK')
            raise

    def allow_read_retry(self, action_id):
        """Explicit recovery of an interrupted READ only, never an upload/edit."""
        self.db.execute('BEGIN IMMEDIATE')
        try:
            row = self.db.execute('SELECT a.step,a.status,r.owner FROM continuous_campaign_actions a '
                'JOIN continuous_campaign_runs r ON r.id=a.run_id WHERE a.id=?', (action_id,)).fetchone()
            if not row or row[0] in WRITE_STEPS or row[1] != 'interrupted_read' or row[2] is not None:
                raise Blocked('recovery', 'only_idle_interrupted_reads_can_be_retried')
            # Only a read-attempt checkpoint is removed, never a business claim.
            self.db.execute('DELETE FROM continuous_campaign_actions WHERE id=?', (action_id,))
            self.db.execute('COMMIT')
        except Exception:
            self.db.execute('ROLLBACK')
            raise

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


def _terminal_scope(result, requested, *, allow_partial_discount=False):
    rows = result.get('items') or []
    if not result.get('batch'):
        raise Blocked('terminal', 'official_batch_missing')
    ids = [str(r.get('item') or '') for r in rows]
    if len(ids) != len(set(ids)) or set(ids) != set(requested):
        raise Blocked('terminal', 'official_terminal_item_scope_mismatch')
    allowed=('success','failed','partial') if allow_partial_discount else ('success','failed')
    if any(r.get('outcome') not in allowed for r in rows):
        raise Blocked('terminal', 'unknown_item_outcome_do_not_replay')
    return rows


def _validate_bundle(bundle, pending, rule_sha, price_version, time_binding=None):
    if (bundle.get('validated_rule_sha') != rule_sha
            or bundle.get('price_version') != price_version
            or sorted(bundle.get('items') or []) != pending
            or not bundle.get('file_sha') or bundle.get('full_active_skus') is not True):
        raise Blocked('generate', 'generation_contract_incomplete')
    if time_binding is not None and bundle.get('time_binding') != time_binding:
        raise Blocked('generate', 'segmented_time_binding_missing_or_changed')


def repair_signature(decisions):
    return fingerprint(sorted([{'sku':str(d['sku']),'repair':d['repair']} for d in decisions],
                              key=lambda d:(d['sku'],fingerprint(d['repair']))))


def repair_already_attempted(state,item,decisions):
    seen=state['repairs_seen'].get(item,[])
    if repair_signature(decisions) in seen:return True
    # Older checkpoints omitted SKU from the signature. Preserve their no-replay
    # protection only for the physical SKU actually repaired, not a sibling SKU.
    if fingerprint([d['repair'] for d in decisions]) in seen:
        prior=state['corrections'].get(item,[])
        return any(any(str(d['sku'])==str(p['sku']) and d['repair']==p['repair'] for p in prior)
                   for d in decisions)
    return False


def run(store, transport, page, *, expected_shop, observed_links, time_binding=None):
    """Run continuously; return once finished or a real unrecoverable gate occurs.

    `execute` uses Web-Agent actions only. A stage acknowledgement or HTTP 200
    must never be normalized as `status=terminal` by its concrete adapter.
    """
    rules = load_rules()
    rule_sha = fingerprint(rules)
    identity = activity_identity(page, expected_shop=expected_shop, observed_links=observed_links)
    price_window = {'start':page['start'], 'end':page['end']}
    if time_binding is not None:
        from campaign_segmented_time import validate_binding
        segment=time_binding['segment']
        validate_binding(dict(time_binding=time_binding, continuous_rule_sha=rule_sha,
            campaign=segment['campaign'], target=segment['target'],
            official_rate=segment['official_rate'],price_version=segment['price_version'],
            **segment['price_window']))
        if (segment['shop_id'] != expected_shop
                or segment['official_window'] != price_window
                or segment['campaign'] != '/'.join(str(page.get(k,'')) for k in
                    ('campaign_id','phase_id','sign_record_id'))
                or Decimal(segment['official_rate']) != Decimal(str(page['official_rate']))):
            raise ValueError('time_segment_does_not_match_official_page')
        # A new price gap is new discount work, NOT a new campaign enrollment.
        identity=fingerprint({'activity':identity,'time_binding':time_binding})
        price_window=segment['price_window']
    if not REQUIRED_CAPABILITIES.issubset(set(transport.capabilities())):
        return {'status': 'blocked', 'step': 'transport',
                'reason': 'verified_continuous_web_agent_transport_not_available',
                'platform_write': False}
    run_id = store.start(identity, rule_sha)
    owner = store.lock(run_id, expected_shop)
    state = store.load(run_id)
    state.setdefault('discount_required', [])
    base = {'identity': deepcopy(page), 'rule_sha': rule_sha}
    if time_binding is not None:
        base['time_binding']=deepcopy(time_binding)

    def call(step, values):
        return store.once(run_id, step, dict(base, **values), transport)

    def hold(items, reason):
        for item in items:
            state['exceptions'][item] = [{'action': 'manual', 'reason': reason}]

    def repair_failed(errors, batch, phase):
        repairs, exceptions = classify_items(errors)
        state['exceptions'].update(exceptions)
        for item in list(repairs):
            if repair_already_attempted(state,item,repairs[item]):
                hold([item], 'same_correction_already_attempted_without_success')
                del repairs[item]
        if not repairs:
            return []
        repaired = call('repair', {'decisions': repairs, 'items': sorted(repairs),
                                   'failed_batch': batch, 'failed_phase': phase})
        fixed_rows = _terminal_scope(repaired, sorted(repairs))
        next_items = []
        for row in fixed_rows:
            item = row['item']
            if row['outcome'] == 'success' and row.get('changed') is True:
                next_items.append(item)
                state['exceptions'].pop(item, None)
                prior={(d['sku'],d['repair']['kind']):d for d in state['corrections'].get(item,[])}
                prior.update({(d['sku'],d['repair']['kind']):d for d in repairs[item]})
                state['corrections'][item] = list(prior.values())
                state['repairs_seen'].setdefault(item, []).append(repair_signature(repairs[item]))
            else:
                hold([item], 'repair_failed_or_no_verified_change')
        return next_items

    def failed_report(batch, items, phase):
        report = call('report', {'batch': batch, 'items': items, 'failed_phase': phase})
        errors = report.get('errors') or []
        if (set(str(e.get('item')) for e in errors) != set(items)
                or any(e.get('batch') != batch for e in errors)):
            raise Blocked('report', 'failure_report_scope_or_batch_mismatch')
        return errors

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
            if time_binding is not None and scope['price_version'] != time_binding['segment']['price_version']:
                raise Blocked('scope', 'time_plan_price_version_changed')
            # Do not checkpoint a half-initialized scope that could skip the
            # history check on restart after a malformed adapter response.
            state['pending'] = initial_scope
            state['price_version'] = scope['price_version']
            state['initial_scope'] = list(initial_scope)
            imported = scope.get('prior_failures') or {}
            state['legacy_failures'] = {}
            for item in list(state['pending']):
                if prior.get(item) == 'success':
                    state['success'][item] = scope['prior_outcomes_evidence']
                    if time_binding is not None and time_binding['segment']['kind']=='daily':
                        state.setdefault('previously_enrolled',[]).append(item)
                    else:
                        state['pending'].remove(item)
                elif prior.get(item) == 'unknown':
                    hold([item], 'previous_unknown_do_not_replay')
                    state['pending'].remove(item)
                elif prior.get(item) == 'failed':
                    legacy = imported.get(item)
                    if (isinstance(legacy, dict) and legacy.get('batch') and legacy.get('errors')
                            and all(str(e.get('item')) == item and e.get('batch') == legacy['batch']
                                    and e.get('terminal') == 'failed' and e.get('official_evidence')
                                    for e in legacy['errors'])):
                        state['legacy_failures'][item] = legacy
                    else:
                        hold([item], 'legacy_failure_needs_exact_report_import')
                    state['pending'].remove(item)
            store.save(run_id, state)
        for item, legacy in list(state.get('legacy_failures', {}).items()):
            fixed = repair_failed(legacy['errors'], legacy['batch'], 'signup')
            state['pending'] = sorted(set(state['pending']) | set(fixed))
            del state['legacy_failures'][item]
            store.save(run_id, state)
        while state['pending']:
            pending, round_no = sorted(state['pending']), state['round']
            discount_retries = []
            # Current official template once per campaign; not again each repair round.
            template = call('template', {'items': state['initial_scope']})
            registered=set(template.get('registered_items',[]))
            if not registered.issubset(state['initial_scope']):
                raise Blocked('template','registered_scope_not_in_requested_products')
            for item in registered:
                state['success'].setdefault(item,template['evidence'])
            if time_binding is not None and time_binding['segment']['kind']=='daily':
                state['previously_enrolled']=sorted(set(state.get('previously_enrolled',[]))|registered)
            else:
                pending=[i for i in pending if i not in registered]
                state['pending']=pending
                if not pending:
                    store.save(run_id,state)
                    continue
            bundle = call('generate', {'items': pending, 'round': round_no,
                'template': template, 'price_version': state['price_version'],
                'corrections': state['corrections'], 'required_discount_items': state['discount_required']})
            if bundle.get('input_issues'):
                issues=bundle['input_issues']
                bad={str(r.get('item','')) for r in issues}
                if not bad or not bad.issubset(pending):
                    raise Blocked('generate','input_issue_scope_missing_or_invalid')
                for item in bad:
                    state['exceptions'][item]=[{'action':'manual','reason':r.get('error','input_unknown'),
                        'sku':str(r.get('sku',''))} for r in issues if str(r.get('item'))==item]
                state['pending']=[i for i in pending if i not in bad]
                state['discount_required']=[i for i in state['discount_required'] if i not in bad]
                state['round']+=1
                store.save(run_id,state)
                continue  # Local generation only; the cached template is not downloaded again.
            _validate_bundle(bundle, pending, rule_sha, state['price_version'], time_binding)
            discount_items = bundle.get('discount_items')
            if (not isinstance(discount_items, list) or len(discount_items) != len(set(discount_items))
                    or not set(discount_items).issubset(pending)):
                raise Blocked('generate', 'discount_scope_invalid')
            if not set(state['discount_required']).issubset(discount_items):
                raise Blocked('generate', 'failed_discount_must_not_be_skipped')
            if discount_items:
                discount = call('discount', {'bundle': bundle, 'items': discount_items})
                rows = _terminal_scope(discount, discount_items,allow_partial_discount=True)
                partial = [r['item'] for r in rows if r['outcome']=='partial']
                hold(partial,'partial_discount_import_preserved_no_whole_product_replay')
                failed = [r['item'] for r in rows if r['outcome'] == 'failed']
                if failed:
                    errors = failed_report(discount['batch'], failed, 'discount')
                    discount_retries = repair_failed(errors, discount['batch'], 'discount')
                state['discount_required'] = discount_retries
                pending = [i for i in pending if i not in failed and i not in partial]
                if (failed or partial) and pending:
                    # Never send a full file with a smaller claimed scope.
                    bundle = call('generate', {'items': pending, 'round': round_no,
                        'template': template, 'price_version': state['price_version'],
                        'corrections': state['corrections'], 'phase': 'after_discount_partial'})
                    _validate_bundle(bundle, pending, rule_sha, state['price_version'], time_binding)
            if not pending:
                state['pending'] = discount_retries
                state['round'] += 1
                store.save(run_id, state)
                continue
            window = call('verify_discount_window', {'bundle': bundle, 'items': pending})
            if (window.get('start') != price_window['start'] or window.get('end') != price_window['end']
                    or window.get('all_correct') is not True
                    or sorted(window.get('items') or []) != pending):
                raise Blocked('verify_discount_window', 'actual_discount_time_or_scope_mismatch')
            enrolled=set(state.get('previously_enrolled',[]))
            signup_pending=[i for i in pending if i not in enrolled]
            if not signup_pending:
                state['pending']=discount_retries
                state['round']+=1
                store.save(run_id,state)
                continue
            if signup_pending != pending:
                pending=signup_pending
                bundle=call('generate', {'items':pending,'round':round_no,
                    'template':template,'price_version':state['price_version'],
                    'corrections':state['corrections'],'phase':'signup_excluding_enrolled'})
                _validate_bundle(bundle,pending,rule_sha,state['price_version'],time_binding)
            signup = call('signup', {'bundle': bundle, 'items': pending})
            rows = _terminal_scope(signup, pending)
            failed = [r['item'] for r in rows if r['outcome'] == 'failed']
            for row in rows:
                if row['outcome'] == 'success':
                    state['success'][row['item']] = signup['evidence']
            if not failed:
                state['pending'] = discount_retries
            else:
                errors = failed_report(signup['batch'], failed, 'signup')
                next_items = repair_failed(errors, signup['batch'], 'signup')
                state['pending'] = sorted(set(discount_retries + next_items))
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
