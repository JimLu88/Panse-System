"""One-shot, durable, serial amount-only execution for the fixed walnut grant.

No signup, fresh export, new offer, rotation, ERP write or automatic write retry.
"""
import argparse
from contextlib import contextmanager
import json
from pathlib import Path
import sys

import campaign_cost_revision as core
from campaign_entry_authority import Authority, load, file_sha
from campaign_continuous_transport import CampaignTransport, persist
from campaign_continuous_policy import fingerprint
from campaign_edge_client import EdgeClient, EdgeJobError
from campaign_walnut_revision import Policy, derive_all, ROOT, START, END

RUN = ROOT / 'walnut-amount-batch-v1'
IDENTITY = dict(campaign_id='49646', phase_id='49651', sign_record_id='3555037899',
                title='2026年淘宝双11全球狂欢季', phase_title='双11现货', shop_name='畔色木作',
                start=START, end=END, rate_label='15%')


@contextmanager
def exclusive(root):
    root.mkdir(parents=True, exist_ok=True)
    handle = (root / 'controller.lock').open('a+b')
    if handle.tell() == 0:
        handle.write(b'0')
        handle.flush()
    handle.seek(0)
    if sys.platform == 'win32':
        import msvcrt
        acquire = lambda: msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        release = lambda: msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl
        acquire = lambda: fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        release = lambda: fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    try:
        acquire()
    except OSError as exc:
        handle.close()
        raise ValueError('walnut_batch_already_running') from exc
    try:
        yield
    finally:
        handle.seek(0)
        release()
        handle.close()


class BoundTransport(CampaignTransport):
    def job(self, step, payload, folder):
        if step != 'discount_reprice' or set(payload) != {'identity', 'claim_id'} or payload['identity'] != IDENTITY:
            raise ValueError('walnut_amount_only_transport_required')
        self.release_guard.verify()
        folder = Path(folder)
        jid = fingerprint(['discount_reprice', payload['claim_id']])
        intent = dict(job_id=jid, step=step, payload=payload)
        path = folder / 'dispatch-intent.json'
        if path.exists():
            if load(path) != intent:
                raise ValueError('walnut_dispatch_identity_changed')
            # Even a crash between the intent and HTTP may not cause a repost.
            job = self.edge.status(jid)
        else:
            persist(path, intent)
            job = self.edge.submit(step, payload)
        if job.get('job_id') != jid:
            raise EdgeJobError('walnut_returned_job_identity_changed', jid)
        if job.get('state') == 'running':
            job = self.edge.wait(jid, timeout=300)
        persist(folder / ('observation-' + fingerprint(job) + '.json'), job)
        if job.get('state') != 'finished':
            raise EdgeJobError('walnut_original_job_not_finished_no_replay', jid)
        return job


def existing_outcome(authority, policy):
    entry = authority.db.execute('SELECT * FROM campaign_cost_revisions WHERE grant_id=?', (policy.GRANT,)).fetchone()
    if not entry or entry['state'] == 'claimed_not_dispatched':
        return None
    if entry['state'] != 'verified':
        return dict(item=policy.ITEM, state='unknown_no_replay', job_id=entry['job_id'])
    body, ref = policy.derive(), json.loads(entry['receipt'])
    if json.loads(entry['body']) != body or file_sha(ref['path']) != ref['sha256']:
        raise ValueError('walnut_existing_verified_evidence_changed')
    policy.validate_proof(body, entry['id'], entry['job_id'], load(ref['path']))
    return dict(item=policy.ITEM, state='verified', reused_existing=True,
                sku_count=len(policy.SKUS), claim_id=entry['id'], job_id=entry['job_id'], receipt=ref)


def execute(edge, authority, *, root=RUN):
    root = Path(root)
    with exclusive(root):
        if (root / 'terminal.json').exists():
            return load(root / 'terminal.json')
        rows = derive_all()
        items = sorted({r['item'] for r in rows})
        persist(root / 'scope.json', dict(items=items, rows=rows, identity=IDENTITY))
        transport = BoundTransport(edge, authority, root=root, request={}, artifact_roots=[ROOT])
        persist(root / 'release.json', transport.release_guard.evidence())
        core.table(authority)
        outcomes, stopped = [], False
        for item in items:
            folder = root / 'items' / item
            terminal = folder / 'terminal.json'
            if terminal.exists():
                outcome = load(terminal)
                if outcome['item'] != item:
                    raise ValueError('walnut_item_terminal_identity_changed')
                outcomes.append(outcome)
                if outcome['state'] in ('unknown_no_replay', 'unverified_no_replay'):
                    stopped = True
                continue
            if stopped:
                outcomes.append(dict(item=item, state='not_started_after_transport_block'))
                continue
            dispatched = False
            try:
                policy = Policy(item)
                outcome = existing_outcome(authority, policy)
                if outcome is None:
                    claim = core.claim(authority, policy=policy)
                    persist(folder / 'claim.json', claim)
                    dispatched = True
                    job = transport.job('discount_reprice', dict(identity=IDENTITY, claim_id=claim['claim_id']), folder)
                    saved = core.record(authority, claim['claim_id'], job)
                    outcome = dict(item=item, state='verified', sku_count=len(policy.SKUS),
                                   claim_id=claim['claim_id'], job_id=job['job_id'], receipt=saved['receipt'])
            except EdgeJobError as exc:
                outcome = dict(item=item, state='unknown_no_replay', reason=exc.reason, job_id=exc.job_id)
                stopped = True  # The host might still be running. Do not launch another job.
            except (ValueError, OSError, KeyError, RuntimeError) as exc:
                outcome = dict(item=item, state='unverified_no_replay' if dispatched else 'blocked_before_dispatch', reason=str(exc))
                # Explicit completed platform failure isolates this item only.
                if dispatched and not list(folder.glob('observation-*.json')):
                    stopped = True
            persist(terminal, outcome)
            if outcome['state'] == 'unknown_no_replay':
                stopped = True
            outcomes.append(outcome)
            print(json.dumps(dict(item=item, state=outcome['state']), ensure_ascii=False), flush=True)
        verified = [o for o in outcomes if o['state'] == 'verified']
        result = dict(state='verified_all_48_amounts' if len(verified) == len(items) else 'partial_or_blocked',
                      items=outcomes, verified_items=len(verified),
                      verified_skus=sum(o.get('sku_count', 0) for o in verified),
                      requested_skus=len(rows), excluded_unmapped_skus=4,
                      signup_changed=False, erp_changed=False, automatic_write_retry=False)
        persist(root / 'terminal.json', result)
        return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['plan', 'run'])
    args = parser.parse_args()
    if args.action == 'plan':
        rows = derive_all()
        print(json.dumps(dict(items=sorted({r['item'] for r in rows}), sku_count=len(rows),
                              output=str(RUN), platform_write=False, claim_created=False)))
        return
    sys.path.insert(0, 'D:/AI/畔色ERP系统/Web-Agent程序')
    from app import config
    from app.vault import dpapi
    token_path = config.VAULT_DIR / 'api_token.bin'
    if not token_path.is_file():
        raise ValueError('existing_token_required_no_creation')
    token = dpapi.unprotect(token_path.read_bytes()).decode('utf-8')
    authority = Authority()
    try:
        result = execute(EdgeClient(token), authority)
        print(json.dumps(result, ensure_ascii=False), flush=True)
    finally:
        authority.close()


if __name__ == '__main__':
    main()
