"""Finite 2026-10-23 walnut grant. Only amend the original offer amounts."""
from pathlib import Path
from collections import defaultdict
from copy import deepcopy
from types import SimpleNamespace
from decimal import Decimal
from campaign_entry_authority import load, file_sha
from campaign_price_snapshot import digest
from campaign_product_scope import complete_scope
from campaign_walnut_amount_plan import calculate, OFFER, START, END
import campaign_cost_revision as core

ROOT = Path('D:/AI/畔色ERP系统/outputs/pricing-cost-sync-20261023')
TRANSFERS = Path('D:/AI/畔色ERP系统/Web-Agent程序/data/output/campaign-transfers')
PREFIX = 'walnut-list-k-amount-only-20261023:'
CAMPAIGN = '49646/49651/3555037899'
WINDOW = {'start': START, 'end': END}
PINS = {
    'current-48-official-evidence.json': '6f78cff6f4d0aed75f850b746a45a2d2e1b8f527bba2ab8c09330e75d6aeb7cb',
    'walnut-user-targets.json': '5d04c33833d228d85e11963f95309e69a931fd1e695825cd84d5160259acbfe9',
}
EXPORT_JOB = '13251d403ce7d78f2e09838d62038e8d97ea62de11304643a18af6732020f5fd'
PRICE_JOB = 'a1bb2c4efea223dbb7c6cbbbe734e55da2d2efe06b6ce004e188ce8f5999c131'
PRICE_SHA = '5369d29aed877e1bd52b4585ec4222b9ea0602687e0bdd3637b51f902f834677'


def derive_all():
    for name, sha in PINS.items():
        if file_sha(ROOT / name) != sha:
            raise ValueError('walnut_pinned_evidence_changed')
    evidence = load(ROOT / 'current-48-official-evidence.json')
    targets = load(ROOT / 'walnut-user-targets.json')['rows']
    source = Path('D:/SynologyDrive/2026/家具项目/3.供应链/20261007家具产品表.xlsx')
    if file_sha(source) != 'ffb7778eddfd47cff73271b51e22370a175745d77ccb5d7296662c06e288e9d7':
        raise ValueError('walnut_source_workbook_changed')
    price = TRANSFERS / PRICE_JOB / 'official-price-details.xlsx'
    if file_sha(price) != PRICE_SHA or evidence['source_price_sha256'] != PRICE_SHA:
        raise ValueError('walnut_official_prices_changed')
    export = load(TRANSFERS / EXPORT_JOB / 'atomic-result.json')
    if export['state'] != 'succeeded' or export['platform_write_started'] is not False:
        raise ValueError('walnut_product_export_not_readonly_success')
    result = export['result']
    scope = complete_scope([(Path(f['path']).read_bytes(), f['sha256']) for f in result['files']],
                           observed_item_ids=result['observed_item_ids'],
                           observed_page_count=result['page_count'], observed_total=result['observed_total'],
                           page_evidence=[f['scope'] for f in result['files']])
    facts = defaultdict(list)
    for entry in scope['sku_facts']:
        facts[entry['facts']['sku_code']].append(entry['facts'])
    target_map = {r['sku_code']: r for r in targets}
    if len(target_map) != len(targets):
        raise ValueError('walnut_duplicate_source_codes')
    rows, seen = [], set()
    for row in evidence['rows']:
        if not row.get('official_lines') or not row.get('platform_identity_currently_verified'):
            continue
        code = row['sku_code']
        if code in seen or code not in target_map or len(facts[code]) != 1:
            raise ValueError('walnut_code_mapping_not_unique')
        seen.add(code)
        fact, target = facts[code][0], target_map[code]
        if (fact['item'], fact['sku'], fact['sku_code']) != (row['current_item'], row['current_sku'], row['current_code']):
            raise ValueError('walnut_current_identity_changed')
        if fact['item'] != target['reference_item_id']:
            raise ValueError('walnut_product_identity_changed')
        if any(row[k] != target[k] for k in ('source_cell', 'target_buyer_price', 'product_code')):
            raise ValueError('walnut_source_target_changed')
        current = dict(row, reference_item_id=fact['item'], reference_sku_id=fact['sku'])
        offers = [r for r in row['official_lines'] if r['工具名称'] == '单品立减']
        if len(offers) != 1:
            raise ValueError('walnut_original_offer_not_unique')
        planned = calculate(current, offers[0]['优惠金额'])
        if Decimal(fact['price']) != Decimal(row['official_lines'][0]['一口价']):
            raise ValueError('walnut_product_price_changed_between_exports')
        rows.append(planned)
    if len(rows) != 48 or len({(r['item'], r['sku']) for r in rows}) != 48:
        raise ValueError('walnut_exact_48_scope_required')
    return rows


class Policy:
    OFFER, CAMPAIGN, WINDOW = OFFER, CAMPAIGN, WINDOW

    def __init__(self, item):
        all_rows = derive_all()
        self.ITEM = item
        self.GRANT = PREFIX + item
        self._rows = [r for r in all_rows if r['item'] == item]
        if not self._rows:
            raise ValueError('walnut_item_outside_current_grant')
        self.SKUS = tuple(sorted(r['sku'] for r in self._rows))
        self.VERSION = digest(all_rows)

    def derive(self):
        rows = [r for r in derive_all() if r['item'] == self.ITEM]
        if rows != self._rows:
            raise ValueError('walnut_claim_source_changed')
        return dict(grant_id=self.GRANT, campaign=CAMPAIGN, shop_name='畔色木作',
                    **WINDOW, price_version=self.VERSION, rows=rows,
                    sources=[{'path': str(ROOT / n), 'sha256': h} for n, h in PINS.items()],
                    automatic_retry=False, rotation_performed=False,
                    user_approval='2026-10-23 source List K exact event buyer target; original offer amount only; signup and ERP unchanged')

    def check_current(self, authority, body):
        # Reconcile only the in-memory current view using exact exported codes.
        # Never overwrite historical registration rows or erase pending states.
        offers = reconcile_view(authority.discount_offers(), body['rows'])
        proxy = SimpleNamespace(db=authority.db, blocked=authority.blocked,
                                discount_offers=lambda: offers)
        return core.check_current(proxy, body, policy=self)

    def validate_proof(self, body, cid, jid, proof):
        return core.validate_proof(body, cid, jid, proof, policy=self)


def engine(grant):
    if not grant.startswith(PREFIX):
        raise ValueError('walnut_grant_unknown')
    return Policy(grant[len(PREFIX):])


def reconcile_view(offers, rows):
    offers = deepcopy(offers)
    matches = [o for o in offers if o.get('platform_offer_id', o['offer_id']) == OFFER
               and (o['start'], o['end']) == (START, END)]
    if len(matches) != 1:
        raise ValueError('walnut_registered_original_offer_not_unique')
    offer = matches[0]
    for row in rows:
        candidates = [r for r in offer['rows'] if r['item'] == row['item']
                      and r.get('erp_code') == row['erp_code']]
        if len(candidates) != 1 or candidates[0].get('custom') is not False:
            raise ValueError('walnut_registered_ordinary_code_not_unique')
        old = candidates[0]
        if any(r is not old and (r['item'], r['sku']) == (row['item'], row['sku']) for r in offer['rows']):
            raise ValueError('walnut_current_pair_conflicts_with_other_code')
        offer.setdefault('historical_rows_before_current_fact_reconciliation', []).append(deepcopy(old))
        old.update(sku=row['sku'], daily=row['daily'], deduct=row['old_deduct'],
                   current_fact_source=str(ROOT / 'current-48-official-evidence.json'))
    return offers


def verified_overlay(authority, offers):
    if not authority.db.execute("SELECT 1 FROM sqlite_master WHERE name='campaign_cost_revisions'").fetchone():
        return offers
    for entry in authority.db.execute("SELECT * FROM campaign_cost_revisions WHERE state='verified' AND grant_id LIKE ?", (PREFIX + '%',)):
        import json
        policy = engine(entry['grant_id'])
        body = policy.derive()
        if json.loads(entry['body']) != body:
            raise ValueError('walnut_saved_claim_changed')
        ref = json.loads(entry['receipt'])
        if file_sha(ref['path']) != ref['sha256']:
            raise ValueError('walnut_saved_receipt_changed')
        policy.validate_proof(body, entry['id'], entry['job_id'], load(ref['path']))
        offers = reconcile_view(offers, body['rows'])
        for offer in offers:
            if offer.get('platform_offer_id', offer['offer_id']) != OFFER or (offer['start'], offer['end']) != (START, END):
                continue
            for change in body['rows']:
                selected = [r for r in offer['rows'] if (r['item'], r['sku']) == (change['item'], change['sku'])]
                if len(selected) != 1:
                    raise ValueError('walnut_verified_overlay_identity_missing')
                selected[0].update(deduct=change['new_deduct'], amendment_receipt=ref,
                                   cost_price_version=policy.VERSION)
    return offers


if __name__ == '__main__':
    import argparse
    import json
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=('plan', 'claim'))
    parser.add_argument('--item', required=True)
    args = parser.parse_args()
    policy = Policy(args.item)
    if args.action == 'plan':
        output = dict(policy.derive(), platform_write=False, claim_created=False)
    else:
        from campaign_entry_authority import Authority
        authority = Authority()
        try:
            output = core.claim(authority, policy=policy)
        finally:
            authority.close()
    print(json.dumps(output, ensure_ascii=False))
