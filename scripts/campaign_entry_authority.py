"""Local campaign authority shared by generation and guarded submission.

No browser, network, automatic retry, price update, or platform preflight.
SQLite records survive chat loss; byte-pinned receipts supply facts, not permission.
"""
from copy import deepcopy
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import uuid

from campaign_price_snapshot import digest
from campaign_price_basis import BasisCatalog
from campaign_reserve_policy import fixed_basis

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / 'docs/campaign-entry-sources.json'
STATE = Path('D:/AI/畔色ERP系统/活动准备/报名状态/campaign-entry.sqlite3')
FROZEN_RULE_DIGEST = '7ff08aaa1fe75eb1e7f63360f3688440caab51510207246d493b4f102ce34b04'


def load(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def file_sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def exact_campaign(value):
    if not re.fullmatch(r'[0-9]+/[0-9]+/[0-9]+', str(value)):
        raise ValueError('exact_campaign_required:campaignId/unitedActivityId/signRecordId')
    return value


def validate_price(row, daily, basis, *, lowering_authorized=False, failed_exact=False):
    price, daily = Decimal(str(row['activity_price'])), Decimal(str(daily))
    if not all(x.is_finite() and x > 0 and x == x.quantize(Decimal('.01')) for x in (price,daily)):
        raise ValueError('invalid_price')
    if row['custom'] is not True:
        if price != daily:raise ValueError('ordinary_signup_must_equal_erp_daily')
        return
    if basis:
        _, floor = fixed_basis(basis['original'], basis['floor'])
        if not basis.get('source'):raise ValueError('fixed_basis_source_missing')
        if price < floor:raise ValueError('below_first_original_twenty_percent')
    if price == daily:
        return  # Missing first original never blocks unchanged first submission.
    if price > daily:raise ValueError('custom_increase_not_authorized')
    if not lowering_authorized or not failed_exact:
        raise ValueError('exact_failed_custom_lowering_authority_required')
    if basis is None or basis.get('uncertain'):raise ValueError('exact_custom_lowering_basis_unknown')


class Authority:
    def __init__(self, state=STATE, manifest=MANIFEST):
        self.path=Path(state);self.path.parent.mkdir(parents=True,exist_ok=True)
        self.manifest=Path(manifest);self.config=load(manifest)
        rules=load(ROOT/'docs/campaign-signup-frozen-contract.json')
        required={'normal_signup_price':'erp_daily_price','custom_floor_ratio':'0.20',
                  'custom_floor_basis':'first_original_custom_price','replay_successful_scope':False,
                  'replay_unknown_outcome':False,'preflight_candidate_scan':False,
                  'preflight_price_evidence_refresh':False,'preflight_r16_r17_loop':False}
        if any(rules.get(k)!=v for k,v in required.items()):raise ValueError('frozen_entry_rule_changed')
        self.rule_sha=digest(rules)
        if self.rule_sha!=FROZEN_RULE_DIGEST:raise ValueError('frozen_contract_changed_requires_current_user_instruction')
        self.db=sqlite3.connect(str(self.path), timeout=10, isolation_level=None)
        self.db.row_factory=sqlite3.Row
        try:
            self.db.executescript('''
              CREATE TABLE IF NOT EXISTS bundles(id TEXT PRIMARY KEY, body TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS attempts(id TEXT PRIMARY KEY, bundle_id TEXT NOT NULL,
                campaign TEXT NOT NULL, phase TEXT NOT NULL, start TEXT NOT NULL, end TEXT NOT NULL,
                item TEXT NOT NULL, status TEXT NOT NULL, evidence TEXT,
                UNIQUE(bundle_id,phase,item));
              CREATE TABLE IF NOT EXISTS sources(path TEXT PRIMARY KEY, kind TEXT NOT NULL, sha256 TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS fixed_source_corrections(old_path TEXT PRIMARY KEY, old_sha256 TEXT NOT NULL,
                new_path TEXT NOT NULL, new_sha256 TEXT NOT NULL, reason TEXT NOT NULL, changed_rows TEXT NOT NULL, created_at TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS discount_amendment_batches(id TEXT PRIMARY KEY, body TEXT NOT NULL, claimed_at TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS discount_amendment_rows(claim_id TEXT NOT NULL, item TEXT NOT NULL, sku TEXT NOT NULL,
                failed_claim TEXT NOT NULL, offer_id TEXT NOT NULL, status TEXT NOT NULL, evidence TEXT,
                PRIMARY KEY(claim_id,item,sku), UNIQUE(failed_claim,offer_id,item,sku));
            ''')
        except BaseException:
            self.db.close();raise

    def close(self):self.db.close()

    def register_source(self, path, kind, expected_sha):
        path=Path(path).resolve()
        if kind not in ('fixed','mapping','outcome','rotation','discount'):raise ValueError('unsupported_authority_source')
        if file_sha(path)!=expected_sha:raise ValueError('source_version_mismatch')
        doc=load(path)
        if kind=='fixed':
            from campaign_price_basis import receipt_records
            if not receipt_records(doc,path):raise ValueError('fixed_source_requires_valid_basis_records')
        if kind=='outcome' and doc.get('schema')=='campaign_entry_outcome_v1':
            exact_campaign(doc['campaign'])
            if doc['phase'] not in ('signup','discount') or not doc.get('batch_id'):
                raise ValueError('invalid_outcome_evidence')
            if len({r['item'] for r in doc['items']})!=len(doc['items']) or any(r['status'] not in ('success','failed','unknown') for r in doc['items']):
                raise ValueError('invalid_outcome_scope')
            proof=doc.get('evidence_path')
            if not proof or file_sha(proof)!=doc.get('evidence_sha256'):raise ValueError('outcome_proof_missing_or_changed')
        existing=self.db.execute('SELECT * FROM sources WHERE path=?',(str(path),)).fetchone()
        if existing and (existing['sha256']!=expected_sha or existing['kind']!=kind):
            raise ValueError('source_is_immutable_use_new_receipt_path')
        self.db.execute('INSERT OR IGNORE INTO sources VALUES(?,?,?)',(str(path),kind,expected_sha))

    def sources(self):
        result=[];seen={}
        corrections={r['old_path']:dict(r) for r in self.db.execute('SELECT * FROM fixed_source_corrections')}
        for source in self.config['sources']+list(map(dict,self.db.execute('SELECT * FROM sources'))):
            source=dict(source)
            path=Path(source['path'])
            if not path.is_absolute():path=ROOT/path
            path=path.resolve();visited=set()
            while str(path) in corrections:
                if str(path) in visited:raise ValueError('fixed_source_correction_cycle')
                visited.add(str(path));fix=corrections[str(path)]
                if source['kind']!='fixed' or source['sha256']!=fix['old_sha256'] or file_sha(path)!=fix['old_sha256']:
                    raise ValueError('fixed_source_correction_audit_changed')
                path=Path(fix['new_path']);source=dict(path=str(path),kind='fixed',sha256=fix['new_sha256'])
            key=str(path)
            version=(source['kind'],source['sha256'])
            if key in seen:
                if seen[key]!=version:raise ValueError('source_descriptor_conflict')
                continue
            seen[key]=version
            try:
                if file_sha(path)!=source['sha256']:raise ValueError('source_version_mismatch:'+str(path))
                result.append({**source,'path':str(path),'document':load(path)})
            except (OSError,ValueError):
                if source['kind']!='fixed':raise
                result.append({**source,'path':str(path),'document':None,'unavailable':True})
        return result

    def correct_fixed_floor_source(self, old_path, old_sha, new_path, new_sha):
        """Audited precision-only replacement; cannot rebase originals or scope."""
        from datetime import datetime, timezone
        old_path=Path(old_path).resolve();new_path=Path(new_path).resolve()
        if old_path==new_path or file_sha(old_path)!=old_sha or file_sha(new_path)!=new_sha:
            raise ValueError('fixed_floor_correction_source_hash_or_path')
        old,new=load(old_path),load(new_path)
        if new.get('schema')!='campaign_user_established_fixed_baseline_v1' or old.get('schema')!=new['schema']:
            raise ValueError('fixed_floor_correction_schema')
        if Path(new.get('supersedes_receipt','')).resolve()!=old_path or not new.get('correction_reason'):
            raise ValueError('fixed_floor_correction_provenance')
        metadata={k:v for k,v in new.items() if k not in ('rows','supersedes_receipt','correction_reason')}
        if metadata!={k:v for k,v in old.items() if k!='rows'}:raise ValueError('fixed_floor_correction_authority_changed')
        if len(old['rows'])!=len(new['rows']) or not old['rows']:raise ValueError('fixed_floor_correction_scope_changed')
        identities=set();changed=[]
        for before,after in zip(old['rows'],new['rows']):
            identity=(after['item'],after['sku'],after['erp_code'])
            if identity in identities:raise ValueError('fixed_floor_correction_duplicate_identity')
            identities.add(identity)
            if {k:v for k,v in before.items() if k!='fixed_floor'}!={k:v for k,v in after.items() if k!='fixed_floor'}:
                raise ValueError('fixed_floor_correction_not_precision_only')
            fixed_basis(after['fixed_original_record'],after['fixed_floor'])
            if before['fixed_floor']!=after['fixed_floor']:
                try:fixed_basis(before['fixed_original_record'],before['fixed_floor'])
                except ValueError:pass
                else:raise ValueError('fixed_floor_correction_valid_basis_immutable')
                changed.append(dict(item=identity[0],sku=identity[1],erp_code=identity[2],old_floor=before['fixed_floor'],new_floor=after['fixed_floor']))
        if not changed:raise ValueError('fixed_floor_correction_no_changes')
        self.db.execute('BEGIN IMMEDIATE')
        try:
            existing=self.db.execute('SELECT * FROM fixed_source_corrections WHERE old_path=?',(str(old_path),)).fetchone()
            if existing:
                if (existing['old_sha256'],existing['new_path'],existing['new_sha256'])!=(old_sha,str(new_path),new_sha):
                    raise ValueError('fixed_floor_correction_conflict')
                self.db.execute('COMMIT');return dict(existing,idempotent=True)
            source=self.db.execute('SELECT * FROM sources WHERE path=?',(str(old_path),)).fetchone()
            if not source or source['kind']!='fixed' or source['sha256']!=old_sha:
                raise ValueError('fixed_floor_correction_old_registration_mismatch')
            self.register_source(new_path,'fixed',new_sha)
            self.db.execute('INSERT INTO fixed_source_corrections VALUES(?,?,?,?,?,?,?)',
                (str(old_path),old_sha,str(new_path),new_sha,new['correction_reason'],json.dumps(changed),datetime.now(timezone.utc).isoformat()))
            self.db.execute('COMMIT')
            return dict(old_path=str(old_path),old_sha256=old_sha,new_path=str(new_path),new_sha256=new_sha,changed_rows=changed,idempotent=False)
        except BaseException:
            self.db.execute('ROLLBACK');raise

    def resolve_snapshot(self, snapshot):
        from campaign_price_snapshot import apply_rotation_receipt
        if digest(snapshot['all_erp_rows'])!=snapshot['resolved_price_version_sha256']:
            raise ValueError('price_snapshot_changed')
        rows=deepcopy(snapshot['all_erp_rows']);sources=self.sources()
        for source in sources:
            if source['kind']=='rotation':
                doc=deepcopy(source['document'])
                known={str(s) for r in rows for s in [r.get('sku'),*(r.get('alt') or [])] if s}
                doc['new_sku_mapping']={old:new for old,new in doc.get('new_sku_mapping',{}).items() if old in known or new in known}
                if doc['new_sku_mapping']:rows=apply_rotation_receipt(rows,doc)
        for source in sources:
            if source['kind']!='mapping':continue
            doc=source['document']
            if doc.get('status') not in ('verified_partial_mapping_restored','prior_missing_mapping_classification_corrected'):
                raise ValueError('unverified_mapping_receipt')
            for m in doc['restored']:
                candidates=[r for r in rows if r['code']==m['erp_code'] and m['item'] in
                            {str(r.get('item')),str(r.get('product_item_id')),*map(str,r.get('product_alt_item_ids') or [])}]
                if not candidates:continue  # Unrelated current snapshots do not grow scope.
                if len(candidates)!=1:raise ValueError('verified_mapping_identity_conflict')
                conflicts=[r for r in rows if r['code']!=m['erp_code'] and str(r.get('item'))==m['item'] and
                           m['sku'] in {str(r.get('sku')),*map(str,r.get('alt') or [])}]
                if conflicts:raise ValueError('verified_mapping_conflicts_with_current_erp')
                r=candidates[0]
                r['alt']=list(dict.fromkeys([*map(str,r.get('alt') or []),m['sku']]))
        result=deepcopy(snapshot);result['all_erp_rows']=rows
        result['resolved_price_version_sha256']=digest(rows)
        result['entry_source_sha256']=digest([{k:s[k] for k in ('path','kind','sha256')} for s in sources])
        return result

    def bases(self, snapshot):
        sources=[{k:s[k] for k in ('path','kind','sha256')} for s in self.sources() if s['kind']=='fixed']
        catalog=BasisCatalog(sources)
        # Bind fixed source identities to logical ERP codes only through current or
        # verified receipt mappings. Then inherit to other physical IDs of that code.
        bindings={}
        for row in snapshot['all_erp_rows']:
            for item in {str(row.get('item')),str(row.get('product_item_id')),*map(str,row.get('product_alt_item_ids') or [])}:
                for sku in {str(row.get('sku')),*map(str,row.get('alt') or [])}:
                    bindings.setdefault((item,sku),set()).add(row['code'])
        logical={}
        for record in catalog.records:
            pair=record['item'],record['sku'];codes=bindings.get(pair,set())
            code=record['erp_code']
            if code and codes and codes!={code}:raise ValueError('basis_mapping_identity_conflict')
            if not code and len(codes)==1:code=next(iter(codes))
            if not code:continue
            key=record['item'],code;basis=record['basis']
            if key in logical and (Decimal(logical[key]['original']),Decimal(logical[key]['floor']))!=(Decimal(basis['original']),Decimal(basis['floor'])):
                raise ValueError('fixed_original_conflict_no_rebase')
            logical[key]={**basis,'erp_code':code,'original_source_sku':record['sku']}
        # Missing fixed sources are not a first-pass gate. Known floors remain
        # protective, but partial source evidence cannot authorize a reduction.
        uncertain=bool(catalog.errors or catalog.row_errors)
        return {pair:{**logical[(pair[0],next(iter(codes)))],'identity_binding':'verified_erp_business_code','uncertain':uncertain}
                for pair,codes in bindings.items() if len(codes)==1 and (pair[0],next(iter(codes))) in logical}

    def discount_offers(self):
        from campaign_discount_reuse import historical_offer
        offers={}
        for source in self.sources():
            doc=source['document']
            if source['kind']=='discount':
                offer=historical_offer(doc)
            elif source['kind']=='outcome' and doc.get('schema')=='campaign_entry_outcome_v1' and doc['phase']=='discount':
                offer=dict(doc,offer_id=doc['batch_id'],rows=doc.get('discount_rows',[]))
            else:continue
            old=offers.get(offer['offer_id'])
            if old is not None and old!=offer:raise ValueError('discount_offer_evidence_conflict')
            offers[offer['offer_id']]=offer
        for row in self.db.execute("SELECT * FROM attempts WHERE phase='discount' AND status IN ('success','unknown')"):
            key='bundle:'+row['bundle_id']
            if key not in offers:
                body=self.get_bundle(row['bundle_id'])
                offers[key]=dict(offer_id=key,start=row['start'],end=row['end'],rows=body['discount_rows'],items=[])
            offers[key]['items'].append(dict(item=row['item'],status=row['status']))
        from campaign_discount_amend import apply_confirmed
        return apply_confirmed(self,list(offers.values()))

    def blocked(self, campaign, phase, start, end):
        exact_campaign(campaign);result={}
        if phase=='discount':
            for offer in self.discount_offers():
                if offer['start']<=end and start<=offer['end']:
                    result.update({r['item']:r['status'] for r in offer['items'] if r['status'] in ('success','unknown')})
        for source in self.sources():
            if source['kind']!='outcome':continue
            doc=source['document']
            if doc.get('schema')=='campaign_entry_outcome_v1':
                if doc['phase']!=phase:continue
                if phase=='signup' and doc['campaign']!=campaign:continue
                if phase=='discount' and not (doc['start']<=end and start<=doc['end']):continue
                for r in doc['items']:
                    if r['status'] in ('success','unknown'):result[r['item']]=r['status']
            elif phase=='signup' and '/'.join(str(doc.get(k,'')) for k in ('campaign_id','united_activity_id','sign_record_id'))==campaign:
                items=doc.get('protected_successful_items',[])
                if items and doc.get('batch_id') and doc.get('published_readback',{}).get('all_five') is True:
                    result.update({i:'success' for i in items})
            elif phase=='signup' and doc.get('date')=='2026-09-06':
                # One-time migration of saved execution facts, not broad title matching.
                old=doc.get('autumn_signup',{})
                key='/'.join(str(old.get(k,'')) for k in ('campaignId','unitedActivityId','signRecordId'))
                if key==campaign and old.get('status')=='official_partial_success_7_published_50_failed' and old.get('batch_id'):
                    result.update({i:'success' for i in old['published_item_ids']})
                old=doc.get('super88_supplement_pending',{})
                key='/'.join(str(old.get(k,'')) for k in ('campaignId','unitedActivityId','signRecordId'))
                if key==campaign and old.get('status')=='official_signup_success_published_verified' and old.get('batch_id'):
                    result[old['item']]='success'
        for row in self.db.execute('SELECT * FROM attempts WHERE status IN (\'success\',\'unknown\')'):
            if phase=='signup' and row['phase']==phase and row['campaign']==campaign:
                result[row['item']]=row['status']
            elif phase=='discount' and row['phase']==phase and row['start']<=end and start<=row['end']:
                result[row['item']]=row['status']  # same product cannot overlap even across campaigns
        return result

    def save_bundle(self, body):
        identity=digest(body)
        self.db.execute('INSERT OR IGNORE INTO bundles VALUES(?,?)',(identity,json.dumps(body,ensure_ascii=False,sort_keys=True)))
        return identity

    def get_bundle(self, identity):
        row=self.db.execute('SELECT body FROM bundles WHERE id=?',(identity,)).fetchone()
        if not row:raise ValueError('bundle_not_registered_in_authority')
        body=json.loads(row[0])
        if digest(body)!=identity:raise ValueError('authority_bundle_changed')
        return body

    def claim(self, identity, phase):
        body=self.get_bundle(identity)
        from campaign_scoped_tolerance import validate_bundle_policy
        validate_bundle_policy(body)
        if phase not in ('signup','discount'):raise ValueError('invalid_phase')
        items=sorted({r['item'] for r in body[phase+'_rows']})
        if not items:raise ValueError('empty_phase')
        self.db.execute('BEGIN IMMEDIATE')
        try:
            blocked=self.blocked(body['campaign'],phase,body['start'],body['end'])
            if set(items)&blocked.keys():raise ValueError('successful_or_unknown_scope_must_not_replay')
            if phase=='signup' and body['discount_rows']:
                done={r['item'] for r in self.db.execute("SELECT item FROM attempts WHERE bundle_id=? AND phase='discount' AND status='success'",(identity,))}
                if done!={r['item'] for r in body['discount_rows']}:raise ValueError('discount_terminal_success_required')
            if phase=='signup':
                from campaign_discount_reuse import reconcile
                from campaign_official_template import discount_rate
                _,reuse,issues=reconcile(body['signup_rows'],body['planned_discount_rows'],self.discount_offers(),
                                         body['start'],body['end'],discount_rate(body['official_rate']),
                                         excluding_offer='bundle:'+identity,campaign=body['campaign'],target=body['target'])
                if issues:raise ValueError('actual_discount_reuse_invalid:'+issues[0]['error'])
                if reuse!=body.get('discount_reuse',[]):raise ValueError('discount_reuse_evidence_changed')
            claim=uuid.uuid4().hex
            for item in items:self.db.execute('INSERT INTO attempts VALUES(?,?,?,?,?,?,?,?,?)',
                (claim+':'+item,identity,body['campaign'],phase,body['start'],body['end'],item,'unknown',None))
            self.db.execute('COMMIT');return claim
        except BaseException:
            self.db.execute('ROLLBACK');raise

    def terminal(self, claim, result):
        # Explicit per-item official terminals only. Missing items remain unknown.
        if result.get('terminal') is not True or not result.get('batch_id') or not result.get('evidence_path'):
            return
        proof=Path(result['evidence_path']);proof_hash=file_sha(proof)
        entries=result.get('items',[])
        claimed=list(self.db.execute('SELECT * FROM attempts WHERE id LIKE ?',(claim+':%',)))
        allowed={r['item'] for r in claimed}
        if not claimed or len({r['item'] for r in entries})!=len(entries):raise ValueError('invalid_terminal_scope')
        if any(r['item'] not in allowed or r['status'] not in ('success','failed') for r in entries):raise ValueError('invalid_terminal_scope')
        document=load(proof)
        first=claimed[0];body=self.get_bundle(first['bundle_id'])
        filename={'signup':'活动报名.xlsx','discount':'单品立减.xlsx'}[first['phase']]
        files=[f for f in body['files'] if Path(f['path']).name==filename]
        if len(files)!=1 or any(document.get(k)!=v for k,v in {
            'schema':'campaign_entry_terminal_v1','claim_id':claim,'campaign':first['campaign'],
            'phase':first['phase'],'start':first['start'],'end':first['end'],
            'file_sha256':files[0]['sha256'],'batch_id':result['batch_id'],
            'terminal':True,'items':entries}.items()):raise ValueError('terminal_evidence_not_bound_to_claim')
        self.db.execute('BEGIN IMMEDIATE')
        try:
            for r in entries:
                old=self.db.execute('SELECT status FROM attempts WHERE id=?',(claim+':'+r['item'],)).fetchone()[0]
                if old!='unknown' and old!=r['status']:raise ValueError('terminal_conflict')
                self.db.execute('UPDATE attempts SET status=?,evidence=? WHERE id=?',
                    (r['status'],json.dumps({'batch':result['batch_id'],'path':str(proof),'sha256':proof_hash}),claim+':'+r['item']))
            self.db.execute('COMMIT')
        except BaseException:
            self.db.execute('ROLLBACK');raise
